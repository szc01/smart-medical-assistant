"""数据库连接池和记忆管理模块

负责：
- 管理 PostgreSQL 连接池
- 提供长记忆/短记忆实例
- 用户长记忆的读取和保存
"""

from psycopg_pool import ConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore

from app.config.settings import settings


# ==================== 数据库连接池管理 ====================
# 全局变量 - 延迟初始化（连接池模式，自动重连、并发安全）
# 短记忆数据库连接池
short_term_pool = None
# 长记忆数据库连接池
long_term_pool = None
# 短记忆存储器实例（PostgresSaver，用于对话历史）
short_term_memory = None
# 长记忆存储器实例（PostgresStore，用于用户信息）
long_term_memory = None


def init_memory_system():
    """初始化记忆系统（连接池模式，延迟加载）"""
    global short_term_pool, long_term_pool, short_term_memory, long_term_memory
    
    if short_term_memory is None:
        try:
            # 短记忆连接池（对话历史）
            print("初始化短记忆 PostgreSQL 连接池...")
            short_term_pool = ConnectionPool(
                conninfo=settings.POSTGRES_SHORT_TERM_URL,
                min_size=2,
                max_size=10,
                kwargs={
                    # 启用自动提交模式,每条SQL语句执行后立即提交事务,避免事务阻塞
                    "autocommit": True,
                    # 禁用预编译语句优化,避免连接池复用时的缓存冲突问题
                    "prepare_threshold": 0,
                    # 启用TCP连接保活机制,检测连接是否仍然有效,防止僵尸连接
                    "keepalives": 1,
                    # 连接空闲30秒后开始发送保活探测包,平衡资源消耗和检测及时性
                    "keepalives_idle": 30,
                    # 每隔10秒发送一次保活探测包,快速发现连接断开
                    "keepalives_interval": 10,
                    # 最多发送3次探测包,30秒无响应则判定连接失效并自动重连
                    "keepalives_count": 3
                }
            )
            short_term_memory = PostgresSaver(short_term_pool)
            short_term_memory.setup()
            print("[OK] 短记忆连接池初始化成功")
            
            # 长记忆连接池（用户个人信息、医疗记录）
            print("初始化长记忆 PostgreSQL 连接池...")
            long_term_pool = ConnectionPool(
                conninfo=settings.POSTGRES_LONG_TERM_URL,
                min_size=2,
                max_size=10,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                    "keepalives": 1,
                    "keepalives_idle": 30,
                    "keepalives_interval": 10,
                    "keepalives_count": 3
                }
            )
            long_term_memory = PostgresStore(long_term_pool)
            long_term_memory.setup()
            print("[OK] 长记忆连接池初始化成功")
            
            print("\n===== 记忆系统初始化完成（连接池模式） =====")
        except Exception as e:
            print(f"[ERROR] 记忆系统初始化失败: {e}")
            print("请确保已创建以下 PostgreSQL 数据库:")
            print("  CREATE DATABASE smart_short;")
            print("  CREATE DATABASE smart_long;")
            raise


def get_short_term_memory():
    """获取短记忆（PostgresSaver）实例"""
    global short_term_memory
    if short_term_memory is None:
        init_memory_system()
    return short_term_memory


def get_long_term_memory():
    """获取长记忆（PostgresStore）实例"""
    global long_term_memory
    if long_term_memory is None:
        init_memory_system()
    return long_term_memory


# ==================== 长记忆管理函数 ====================

# 保存用户数据到长期记忆（个人信息、医疗记录等）
# 参数说明：
#   store - 长记忆存储器实例（可选，为None时使用全局实例）
#   namespace - 命名空间元组（用于分类存储，如 ("user_preferences", "user_3")）
#   item_id - 数据项ID（如 "basic_info"、"medical_history"）
#   data - 要保存的数据字典
def save_user_long_memory(store, namespace: tuple, item_id: str, data: dict):
    # 如果未传入 store，使用全局长记忆存储器
    if store is None:
        store = get_long_term_memory()
    try:
        # 将数据保存到 PostgreSQL 长记忆数据库
        store.put(namespace, item_id, data)
    except Exception as e:
        print(f"保存用户长期记忆时出错: {e}")


# 获取用户的长期记忆（个人信息、医疗记录等）
def get_user_long_memory(user_id: str, store=None) -> str:
    # 如果未传入 store，使用全局长记忆存储器
    if store is None:
        store = get_long_term_memory()
    
    # 初始化用户信息列表
    user_info = []
    
    try:
        # 获取用户个人信息（基本信息）- 按字段去重，每个字段只保留最新
        preferences = store.search(("user_preferences", user_id))
        # 如果有个人信息
        if preferences:
            # 使用字典按字段去重，相同 item_id 只保留最后一个（最新的）
            pref_dict = {}
            # 遍历所有偏好数据
            for item in preferences:
                # 从 key 中提取 item_id (LangGraph 存储数据时，会自动用 | 连接 key 的各个部分格式: namespace|user_id|item_id)
                key_parts = item.key.split('|') if '|' in item.key else [item.key]
                # 取最后一部分作为 item_id
                item_id = key_parts[-1] if key_parts else item.key
                # 相同 item_id 会覆盖，实现去重
                pref_dict[item_id] = item.value
            
            # 输出最新的值
            pref_text = "\n".join([
                f"{value.get('key')}: {value.get('value')}"
                for value in pref_dict.values()
            ])
            # 将基本信息添加到用户信息列表
            user_info.append("【用户基本信息】\n" + pref_text)
        
        # 获取用户医疗历史 - 全部追加显示，不删除
        medical_history = store.search(("user_medical_history", user_id))
        # 如果有医疗历史
        if medical_history:
            # 按类别分组
            medical_by_category = {}
            # 遍历所有医疗历史
            for item in medical_history:
                # 获取类别（如"过敏史"、"手术史"）
                category = item.value.get("category", "unknown")
                # 获取内容
                content = item.value.get("content", "")
                
                # 如果类别不存在，初始化为空列表
                if category not in medical_by_category:
                    medical_by_category[category] = []
                # 将内容添加到对应类别
                medical_by_category[category].append(content)
            
            # 格式化输出
            history_lines = []
            # 遍历每个类别及其内容
            for category, contents in medical_by_category.items():
                # 如果只有一条记录，直接显示
                if len(contents) == 1:
                    history_lines.append(f"{category}: {contents[0]}")
                else:
                    # 多条记录，编号显示
                    items_text = "\n".join([f"  {i+1}. {c}" for i, c in enumerate(contents)])
                    history_lines.append(f"{category}:\n{items_text}")
            
            # 拼接所有医疗历史
            medical_text = "\n".join(history_lines)
            # 将医疗历史添加到用户信息列表
            user_info.append("【医疗历史】\n" + medical_text)
        
        # # 获取用户体检报告
        # physical_exam = store.search(("user_medical_records", user_id, "physical_exam"))
        # # 如果有体检报告
        # if physical_exam:
        #     # 提取所有体检报告内容，用双换行分隔
        #     exam_text = "\n\n".join([item.value.get("content", "") for item in physical_exam])
        #     # 将体检报告添加到用户信息列表
        #     user_info.append("【体检报告】\n" + exam_text)
        
        # # 获取用户检测报告
        # test_report = store.search(("user_medical_records", user_id, "test_report"))
        # # 如果有检测报告
        # if test_report:
        #     # 提取所有检测报告内容，用双换行分隔
        #     report_text = "\n\n".join([item.value.get("content", "") for item in test_report])
        #     # 将检测报告添加到用户信息列表
        #     user_info.append("【检测报告】\n" + report_text)
        
        # # 获取用户病历本
        # medical_record = store.search(("user_medical_records", user_id, "medical_record"))
        # # 如果有病历本
        # if medical_record:
        #     # 提取所有病历本内容，用双换行分隔
        #     record_text = "\n\n".join([item.value.get("content", "") for item in medical_record])
        #     # 将病历本添加到用户信息列表
        #     user_info.append("【病历本】\n" + record_text)
    except Exception as e:
        # 如果发生异常，打印错误信息
        print(f"获取用户长期记忆时出错: {e}")
    
    # 将所有用户信息用双换行拼接，如果没有信息则返回空字符串
    return "\n\n".join(user_info) if user_info else ""
