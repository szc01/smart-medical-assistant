# 架构与设计取舍

本文补充 README 里没展开的实现细节，方便二次开发或代码评审。所有描述均对应仓库中的实际代码，路径可直接跳转。

## 1. 请求全链路

以一次聊天请求为例：

```text
frontend/src/views/HomeView.vue
  └─ POST {VITE_BACKEND_URL}/chat/send  {user_id, message, thread_id}
       └─ backend/app/api/chat.py :: send_message
            ├─ 依赖注入：get_current_user（JWT）→ User
            ├─ 更新 Conversation.last_message / last_active
            ├─ create_medical_agent_system() 每次请求重建图
            ├─ 组 State：messages / user_id / thread_id / medical_documents
            └─ graph.invoke(state, config={user_id, thread_id})
                 ├─ supervisor_node
                 │    ├─ get_user_long_memory(user_id) → 拼进 prompt
                 │    ├─ LLM 输出单词 → next
                 │    └─ 若 next == supervisor：自己用 create_agent 回复
                 ├─ medical_examiner_node / attending_doctor_node / pharmacist_node
                 └─ END
```

短记忆由 `checkpointer` 在 `invoke` 过程中自动写入，业务代码里没有手写「插入消息」的 SQL；这也是把 `thread_id` 放进 `configurable` 的原因。

## 2. State 与路由

`backend/app/agents/state.py` 定义共享状态：

```python
class MedicalAgentState(TypedDict):
    messages: Annotated[list, add_messages]  # add_messages 负责追加而非覆盖
    user_id: str
    thread_id: str
    medical_documents: List
    diagnosis: Optional[str]
    prescription: Optional[str]
    next: Optional[str]                      # 路由目标
```

图结构（`backend/app/agents/multi_agent.py`）：

```python
builder.add_conditional_edges(
    "supervisor",
    lambda state: state.get("next", "__end__"),
    {"attending_doctor": ..., "medical_examiner": ..., "pharmacist": ..., "__end__": END},
)
builder.add_edge("attending_doctor", END)
builder.add_edge("medical_examiner", END)
builder.add_edge("pharmacist", END)
```

**为什么路由不用 LLM 的 function calling / structured output？** 当前实现是「让模型只回一个单词，再用字符串包含判断」。好处是便宜、稳、易调试；代价是模型可能输出解释性文字。解析逻辑对四种角色名做包含匹配，命中即用，否则回落 supervisor 自己回复。若要做严格化，可换成 `with_structured_output` 返回枚举——这是 Roadmap 里的候选。

## 3. 记忆分层

| | 短记忆 | 长记忆 |
| --- | --- | --- |
| 实现 | `PostgresSaver`（LangGraph checkpointer） | `PostgresStore`（LangGraph store） |
| 库 | `smart_short` | `smart_long` |
| 作用域 | 单个 `thread_id`（一次会话） | 单个 `user_id`（跨会话） |
| 写入时机 | 图执行时自动 | 显式调用 `store.put()` |
| 命名空间 | 由 checkpointer 管理 | `user_preferences/{user_id}`、`user_medical_history/{user_id}` |

连接池在 `backend/app/agents/memory.py` 中延迟初始化，参数上有两处关键设置：

- `autocommit=True` + `prepare_threshold=0`：避免连接池复用时的预编译语句缓存冲突（LangGraph 官方推荐组合）。
- `keepalives` 系列参数：长时间空闲后连接被中间设备断开时能自动感知，减少「第一次请求必失败」的问题。

`get_user_long_memory()` 会把偏好与病史拼成一段人类可读文本注入 prompt。偏好按 `item_id` 去重取最新，病史按 category 分组**全部保留**（append 语义），因为病史是累积事实，不该被覆盖。

## 4. PDF 结构化提取

`backend/app/rag/document_extractor.py`：

1. `PyPDFLoader` 逐页读取，合并文本；
2. 文本截断到 8000 字符（控制 token 成本，也是已知限制）；
3. `ChatOpenAI(...).with_structured_output(MedicalRecord)` 得到 Pydantic 对象；
4. `save_extracted_info_to_store()` 把 basic_info 写入 `user_preferences`，其余医疗字段合并成一条写入 `user_medical_history`，并记录 `source=文档: {filename}`；
5. `medical_documents` 表记录文件元数据，原始文件落在 `backend/uploads/`。

字段类型刻意放宽为 `Any`：模型返回的 symptoms 有时是字符串、有时是字符串数组，强类型会直接抛解析异常。宽类型 + 保存前归一化（`str` → 单元素 list）比反复调 prompt 更稳。

## 5. 数据库职责划分

| 库 | 表 | 谁建 | 用途 |
| --- | --- | --- | --- |
| MySQL `medical_assistant` | `users` | `main.py` 启动时 `create_all` / `sql/init_mysql.sql` | 账号、bcrypt 哈希 |
| PostgreSQL `smart_short` | LangGraph checkpoint 表 | `PostgresSaver.setup()` | 会话状态快照 |
| PostgreSQL `smart_long` | LangGraph store 表 | `PostgresStore.setup()` | 用户画像、病史 |
| PostgreSQL `smart_session` | `conversations`、`medical_documents` | `PostgresBase.metadata.create_all` | 会话列表、文档元数据 |

`user_id` 有两种形态，注意区分：MySQL 主键是整数 `id`；长记忆使用字符串 `user_{id}`（由后端在各处拼接，`preferences` 接口会用登录态校验，防止越权读写他人记忆）。

## 6. 鉴权

- 密码：bcrypt，取前 72 字节（bcrypt 的输入上限，超长会直接报错，代码里做了截断）。
- Token：HS256 JWT，payload 含 `sub`（用户名）与 `exp`。
- 前端：登录后把 token 与 user 存 localStorage，路由守卫检查 token 是否存在；后端每个受保护接口用 `Depends(get_current_user)` 校验。

**已知缺口**：前端守卫只判断 token 是否存在，不校验是否过期；过期后由后端返回 401，前端尚未做统一拦截跳转。生产环境建议增加 axios 响应拦截器 + refresh token。

## 7. 前端结构

- 单页应用，路由：`/login`、`/register`、`/`（对话，需登录）、`/preferences`（需登录）、`/about`。
- 对话页是「薄客户端」：消息、会话列表、上传都直接打后端，没有额外的状态管理层；Pinia 已安装但暂未承载业务状态。
- 后端地址统一读 `VITE_BACKEND_URL`，缺省回落 `http://localhost:8000`。
- 打字机效果是前端逐字渲染完整回复，不是流式接收（见 README 已知限制）。

## 8. 如果要继续扩展

| 目标 | 改动点 |
| --- | --- |
| 加一个「营养师」角色 | `agents/` 新增节点文件 → `multi_agent.py` 加节点与边 → `supervisor.py` 的 `ROUTE_PROMPT` 增加角色说明与决策规则 |
| 换成别的模型 | `agents/base.py`、`rag/document_extractor.py` 中的 `model` / `base_url`，以及 `requirements.txt` 里的 provider 包 |
| 改成流式 | `graph.astream_events()` + `sse-starlette`（已在依赖里），前端改 `fetch` + `ReadableStream` |
| 换成向量检索 | `rag/` 引入 `langchain-chroma`（已在依赖里），把「截断全文」改为「分块 + 召回」 |
