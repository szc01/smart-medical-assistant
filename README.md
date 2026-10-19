# 小滴 · 智能医疗助手（Smart Medical Assistant）

> 基于 **LangGraph 多智能体协作**的医疗咨询系统：支持导诊分流、多轮问诊、PDF 病历结构化提取与跨会话记忆，前后端分离，开箱可跑。

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python\&logoColor=white)

![FastAPI](https://img.shields.io/badge/FastAPI-0.135-009688?logo=fastapi\&logoColor=white)

![LangGraph](https://img.shields.io/badge/LangGraph-1.0-1C3C3C)

![Vue](https://img.shields.io/badge/Vue-3.5-42B883?logo=vuedotjs\&logoColor=white)

![License](https://img.shields.io/badge/License-MIT-green)

[![CI](https://github.com/szc01/smart-medical-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/szc01/smart-medical-assistant/actions/workflows/ci.yml)

---

## 这是什么

一个「AI 导诊台」：用户像在医院挂号一样描述症状，系统内部由 **4 个角色智能体**分工协作——私人医疗顾问负责接待与分流，体检员负责追问补齐信息，主治医生负责分析与建议，药师负责用药指导。用户还可以上传 PDF 病历，系统会用 LLM 把非结构化文本抽成结构化字段，写进**跨会话的长期记忆**，下次对话时智能体自动带上这些背景。

区别于「一个大模型 + 一个聊天框」的写法，本项目的重点在于**状态机编排**与**记忆分层**：

- 用 LangGraph 把「谁该回答」变成图上的条件边，而不是在一段 prompt 里塞所有规则；
- 用 PostgresSaver（短记忆）+ PostgresStore（长记忆）把「本轮上下文」和「这个人的病史」拆开存；
- 用 Pydantic + `with_structured_output` 把 PDF 文本变成可校验的结构化数据，而不是把整页文本丢给模型。

## 功能

| 模块     | 说明                                                          |
| ------ | ----------------------------------------------------------- |
| 多智能体导诊 | supervisor 用 LLM 判定路由，分派给体检员 / 主治医生 / 药师，各角色独立 prompt 与职责边界 |
| 双记忆体系  | 短记忆按 `thread_id` 隔离会话；长记忆按用户存画像、病史、过敏史，跨会话生效                |
| 病历结构化  | 上传 PDF → 文本抽取 → Pydantic 结构化输出 → 写入长记忆 + 落库，前端自动触发 AI 解读    |
| 会话管理   | 新建 / 列表 / 详情 / 删除，会话与消息记录落 PostgreSQL                       |
| 用户体系   | 注册、登录、bcrypt 密码哈希、JWT 鉴权，路由级登录守卫                            |
| 偏好设置   | 回答风格、过敏史等偏好写入长记忆，与对话流程共用同一份存储                               |
| 联网检索   | 通过 Tavily 为智能体提供实时信息检索工具                                    |
| 流式响应   | `/chat/stream` 走 SSE，token 级增量返回；首 token 延迟 3–5 s（路由关闭 thinking 后从 12–16 s 降下来） |

## 效果（自建评测集）

仓库内附 25 条评测 case，覆盖五类，全部走真实 HTTP 链路（`POST /chat/stream`），一条命令复现：

| 类别 | 通过 | 测什么 |
| --- | --- | --- |
| A 路由正确性 | 5/5 | supervisor 是否把问题分给正确的角色 |
| B 检索问答 | 8/8 | 能否从两页病历里答出正确数值，并标注页码 |
| C 无关问题不硬扯 | 6/6 | 问天气、问用药时不应引用病历里的检查指标 |
| D 兜底不幻觉 | 3/3 | 没有文档的用户问指标，应明确说明没有数据 |
| E 越权拦截 | 3/3 | 跨用户读取 / 删除会话应返回 403 或 404 |
| **合计** | **25/25** | 单次运行约 3 分 40 秒，含自动建号、灌数据、清理 |

```bash
cd backend && .venv/Scripts/python.exe scripts/eval_suite.py      # --only A 只跑某一类
```

结果落在 `backend/results/`（JSON + Markdown 各一份），脚本会自建测试账号与两页固定病历，跑完自动清理用户、会话、向量库与 checkpoint。

几点如实说明：

- **B 类用 `index_pages()` 直接灌向量索引**，不覆盖「上传 → PDF 解析 → 索引」这一段（那条链路另有验证），所以 B 类衡量的是检索与回答质量，不是文件解析。
- **A2 是唯一放宽了判据的一条**：「我最近老头晕」在有长记忆时接受 `medical_examiner` 或 `attending_doctor`——体检员追问症状、医生结合既往病史直接分析，两种都合理。其余 24 条均为精确判定。
- **指标会随模型版本波动**。C 类曾经是 4/6（问天气会引用血糖），修法分两步才干净：把检查数值从长记忆里拆出去（只走检索）+ 让药师节点不再检索病历（药师只需要过敏史和用药记录）。评测正是用来验证这两步的。

## 架构

```mermaid
flowchart LR
    U[浏览器 Vue 3 SPA] -->|JWT| API[FastAPI]
    API --> AUTH[auth / conversations / documents / preferences]
    API --> G[LangGraph StateGraph]

    G --> S[私人医疗顾问<br/>supervisor · LLM 路由]
    S -->|信息不全| E[体检员<br/>medical_examiner]
    S -->|信息充足| D[主治医生<br/>attending_doctor]
    S -->|药品问题| P[药师<br/>pharmacist]
    S -->|问候闲聊| R[直接回复]
    E --> END1[(END)]
    D --> END2[(END)]
    P --> END3[(END)]

    S -.读写.-> SM[(PostgreSQL<br/>smart_short<br/>Checkpoint)]
    S -.读写.-> LM[(PostgreSQL<br/>smart_long<br/>Store)]
    API -.读写.-> SES[(PostgreSQL<br/>smart_session)]
    AUTH -.读写.-> MY[(MySQL<br/>users)]
    API --> RAG[PDF 抽取<br/>PyPDFLoader + 结构化输出]
    RAG --> LM
```

一次请求的完整链路：

```
POST /chat/stream
  → 校验 JWT，更新会话 last_message
  → 组装初始 State（messages / user_id / thread_id）
  → graph.astream(stream_mode=["messages","values","custom"])：
      supervisor 先读长记忆 → LLM 输出单词语义路由 → 命中子智能体
      节点入口发 custom 事件（角色名）→ 该节点带自己的 system prompt + 工具执行
  → token 级 SSE 推送给前端，子节点执行完 → END
  → 短记忆由 checkpointer 自动落库（无需手写 INSERT）
```

更细的设计说明、状态定义与扩展方式见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

流式改造的完整复盘（含被数据推翻的三个判断）见 [`docs/CASE_STUDY_SSE.md`](docs/CASE_STUDY_SSE.md)。

## 技术栈

**后端**：Python 3.12 · FastAPI · LangGraph 1.0 · LangChain 1.0 · SQLAlchemy 2.0 · PostgreSQL（长期记忆 / 会话 / 医疗文档）· MySQL（用户账号）· Pydantic 2 · python-jose + bcrypt  
**前端**：Vue 3.5（Composition API）· TypeScript · Vite 6 · Element Plus · Pinia · Vue Router 4 · Axios · marked  
**模型与外部服务**：阿里云百炼（DashScope，OpenAI 兼容端点，默认 `qwen3.5-plus`）· Tavily 联网检索 · LangSmith 追踪（可选）

## 目录结构

```text
smart-medical-assistant/
├── backend/
│   ├── app/
│   │   ├── agents/            # 多智能体：supervisor / examiner / doctor / pharmacist
│   │   │   ├── multi_agent.py # StateGraph 组装（节点 + 条件边）
│   │   │   ├── memory.py      # 连接池 + 长短记忆读写
│   │   │   ├── state.py       # 共享 State 定义
│   │   │   └── tools.py       # 联网检索、写入用户信息 / 病史
│   │   ├── api/               # auth / chat / conversations / medical_upload / preferences
│   │   ├── config/            # 环境变量加载
│   │   ├── database/          # MySQL / PostgreSQL 引擎与会话
│   │   ├── models/            # users（MySQL）/ conversations、medical_documents（PostgreSQL）
│   │   ├── rag/               # PDF 解析 + 结构化信息提取
│   │   └── schemas/           # 请求 / 响应模型
│   ├── sql/                   # MySQL / PostgreSQL 初始化脚本
│   ├── uploads/               # 上传文件落盘目录（内容不入库）
│   ├── main.py                # 应用入口，启动时自动建表
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── router/            # 路由与登录守卫
│   │   ├── views/             # 登录 / 注册 / 对话 / 偏好 / 关于
│   │   ├── App.vue
│   │   └── main.ts
│   ├── .env.example
│   ├── package.json
│   └── vite.config.ts
├── docs/ARCHITECTURE.md       # 架构与设计取舍
├── docs/CASE_STUDY_SSE.md     # 流式改造复盘（12s → 3s 的完整过程）
├── docker-compose.yml         # 一键起 MySQL + PostgreSQL
├── .gitignore
└── LICENSE
```

## 快速开始

```bash
git clone https://github.com/szc01/smart-medical-assistant.git
cd smart-medical-assistant
```

### 前置条件

- Python **3.12**（3.10+ 亦可，依赖按 3.12 锁定）
- Node.js **18+**
- MySQL **8.0**、PostgreSQL **14+**（或用下面的 Docker 方式，无需本机安装）

### 第 1 步：准备数据库

**方式 A · Docker（推荐，一条命令）**

```bash
docker compose up -d
```

容器把 MySQL 映射到 **3307**、PostgreSQL 映射到 **5433**，避免与本机已有的 3306 / 5432 冲突。密码统一为 `dev_password`，首次启动会自动执行 `backend/sql/` 下的初始化脚本。

**方式 B · 本机已有数据库**

```bash
# MySQL：建库 + users 表（Windows 下把 `<` 换成 `-e` 或用 PowerShell 管道）
mysql -u root -p --default-character-set=utf8mb4 < backend/sql/init_mysql.sql

# PostgreSQL：建 smart_short / smart_long / smart_session 三个库
psql -U postgres -f backend/sql/init_postgres.sql
```

> 三个库里的业务表由应用启动时自动创建（`create_all` 与 `PostgresSaver.setup()`），无需手工建表。

### 第 2 步：启动后端

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env    # Windows: copy .env.example .env
```

编辑 `backend/.env`，至少填 `DASHSCOPE_API_KEY` 与三个数据库连接串。用 Docker 起的数据库，连接串这样写：

```env
DATABASE_URL=mysql+mysqlconnector://root:dev_password@localhost:3307/medical_assistant
POSTGRES_SHORT_TERM_URL=postgresql://postgres:dev_password@localhost:5433/smart_short
POSTGRES_LONG_TERM_URL=postgresql://postgres:dev_password@localhost:5433/smart_long
POSTGRES_SESSION_URL=postgresql+psycopg://postgres:dev_password@localhost:5433/smart_session
```

启动：

```bash
python main.py
```

- API 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/>

### 运行后端测试

```bash
cd backend && pip install -r requirements-dev.txt && pytest tests/ -q
```

> 测试使用假 embedding 与临时目录，不需要 API Key，也不需要数据库。

### 持续集成

`.github/workflows/ci.yml` 在 push / PR 到 `main` 时触发，包含两个 job：

| Job                           | 实际执行内容                                                                                          |
| ----------------------------- | ----------------------------------------------------------------------------------------------- |
| `Backend syntax check`        | `python -m compileall -q backend/app backend/main.py`                                           |
| `Frontend type-check & build` | `npm ci` + `npm run build`，其中 `vue-tsc --noEmit -p tsconfig.app.json` 做全量类型检查，再 `vite build` 产出 |

> 后端 pytest 尚未接入 CI（需要在 workflow 里安装 `requirements-dev.txt` 并执行 `pytest tests/ -q`），目前靠本地手动运行。

### 第 3 步：启动前端

```bash
cd frontend
npm install
cp .env.example .env    # Windows: copy .env.example .env
npm run dev
```

打开 <http://localhost:5173>，先注册账号再登录。

### 环境变量一览（`backend/.env`）

| 变量                                           | 必填 | 说明                                              |
| -------------------------------------------- | -- | ----------------------------------------------- |
| `DASHSCOPE_API_KEY`                          | ✅  | 阿里云百炼 Key，用于对话与 PDF 抽取                          |
| `TAVILY_API_KEY`                             | ⬜  | 联网检索工具，缺失时搜索工具报错但不影响主流程                         |
| `DATABASE_URL`                               | ✅  | MySQL DSN，前缀必须是 `mysql+mysqlconnector://`       |
| `POSTGRES_SHORT_TERM_URL`                    | ✅  | 短记忆库，前缀必须是 `postgresql://`（走 psycopg_pool）      |
| `POSTGRES_LONG_TERM_URL`                     | ✅  | 长记忆库，同上                                         |
| `POSTGRES_SESSION_URL`                       | ✅  | 会话库，前缀必须是 `postgresql+psycopg://`（走 SQLAlchemy） |
| `SECRET_KEY` / `ALGORITHM`                   | ✅  | JWT 签名密钥与算法                                     |
| `ACCESS_TOKEN_EXPIRE_MINUTES`                | ⬜  | Token 有效期，默认 1440 分钟                            |
| `LANGCHAIN_TRACING_V2` / `LANGCHAIN_API_KEY` | ⬜  | LangSmith 追踪，不填则关闭                              |
| `HOST` / `PORT`                              | ⬜  | 默认 `0.0.0.0:8000`                               |

> 三种 PostgreSQL 前缀不一样是**故意**的：连接池库（LangGraph 内部）只认纯 `postgresql://`，而 SQLAlchemy ORM 必须带驱动名。

## API 一览

除注册、登录与根路径外，其余接口都需要 `Authorization: Bearer <token>`。

| 方法       | 路径                                       | 说明                                                                    |
| -------- | ---------------------------------------- | --------------------------------------------------------------------- |
| `POST`   | `/auth/register`                         | 注册（JSON：username / password / email）                                  |
| `POST`   | `/auth/token`                            | 登录（`application/x-www-form-urlencoded`），返回 `access_token` 与 `user_id` |
| `POST`   | `/chat/stream`                           | 发送消息，以 SSE（`text/event-stream`）流式返回 meta / token / done / error 事件         |
| `POST`   | `/chat/send`                             | 发送消息，返回多智能体最终回复；deprecated（保留兼容，将移除），请改用 `/chat/stream`            |
| `POST`   | `/conversations/create?user_id=`         | 新建会话（前端生成 id；`user_id` 可省略，服务端以登录态为准，传入他人 ID 返回 403）               |
| `GET`    | `/conversations/list`                    | 会话列表（仅返回当前登录用户的会话，忽略 `user_id` 查询参数）                                      |
| `GET`    | `/conversations/get?conversation_id=`    | 会话详情（含历史消息）                                                           |
| `DELETE` | `/conversations/delete?conversation_id=` | 删除会话                                                                  |
| `POST`   | `/documents/upload`                      | 上传 PDF 病历（multipart：file / document_type / user_id）                   |
| `POST`   | `/preferences/save`                      | 保存偏好（JSON：key / value）                                                |
| `GET`    | `/preferences/list`                      | 读取偏好                                                                  |

登录示例：

```bash
curl -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=your_name&password=your_password"
```

## 设计要点

**1. 路由而不是堆 prompt。** 四个角色各自持有 system prompt，supervisor 只做一件事：输出一个单词（`supervisor` / `medical_examiner` / `attending_doctor` / `pharmacist`）。`add_conditional_edges` 把这个单词映射成图上的边。新增角色 = 新增一个节点 + 一条边，不用动其它角色的 prompt。

**2. 子节点执行完直接 END。** 当前是「一问一答」模式，子智能体不会回到 supervisor 再循环，避免单次请求无限轮转、延迟不可控。代价是复杂病例需要用户多轮输入——这是有意的取舍，不是漏写。

**3. 短记忆与长记忆分库。** 短记忆是 LangGraph 的 checkpoint（对话状态快照），按 `thread_id` 隔离；长记忆是键值 Store（`user_preferences/{user_id}`、`user_medical_history/{user_id}`），跨会话可读。两者物理分库，互不干扰，也便于分别扩容。

**4. PDF 抽取落成强类型对象。** `MedicalRecord`（Pydantic）定义 basic_info / symptoms / allergies / medications 等字段，`with_structured_output` 让模型直接产出可校验结构，再由 `save_extracted_info_to_store` 写入长记忆并记录来源文件名。

## 已知限制

诚实列出，避免误解：

- **单轮路由**：子智能体执行完即结束，不做工具调用的多轮自循环（见设计要点 2）。
- **PDF 仅支持文本层**：扫描件需先 OCR；正文读取上限 8000 字符。
- **模型强绑定 DashScope**：换模型需改 `app/agents/base.py` 与 `app/rag/document_extractor.py` 的模型名与 endpoint。
- **自动化测试覆盖有限**：已有 12 条单测（RAG 检索层 7 条，假 embedding，不连网络也不连数据库；体检员转接判断 2 条；流式事件 3 条）；前端在 CI 里做 `vue-tsc` 类型检查与生产构建，**后端 pytest 尚未接入 CI**。
- **检索相似度的区分度有限**：`text-embedding-v3` 在中文短文本上，「相关片段」与「无关片段」的余弦相似度有重叠（实测相关 top1 0.44–0.78、无关 top1 0.40–0.50）。阈值 `0.43` 只能减少误注入，不能完全消除；更彻底的做法是按问题意图门控检索。
- **旧记忆不回填**：把检查结果从长记忆拆出去的规则只对新写入生效，升级前写入的记录仍混在「医疗信息」里（当前仓库无历史数据，不影响使用）。
- **未做数据迁移**：表结构由 `create_all` 生成，字段变更没有 Alembic 迁移脚本。

## Roadmap

- [x] 对话改为 SSE 流式输出
- [x] 补充 pytest（已完成 RAG 检索层与转接判断；待补记忆读写、路由决策、PDF 抽取的 mock 测试）
- [ ] 引入 Alembic 管理表结构变更
- [ ] PDF 增加 OCR：扫描件目前没有文本层，`PyPDFLoader` 提取为空，索引不到内容；分块检索已完成（`RecursiveCharacterTextSplitter` + 页码/chunk 元数据）
- [ ] 前端会话列表虚拟滚动、错误态与空态统一
- [ ] Docker Compose 增加后端与前端服务，一键起全栈

## 安全说明

- 仓库内**不含**任何真实密钥、生产数据库地址或用户数据，`backend/.env`、`backend/uploads/*` 均已被 `.gitignore` 忽略。
- 用户数据接口（偏好、文档、会话、聊天）一律以 JWT 登录态为准；请求中的 `user_id` 与登录用户不一致时返回 403，会话不属于当前用户时返回 403 或 404。
- 自行部署到公网前请务必：替换 `SECRET_KEY` 为随机串、把 CORS 的 `allow_origins=["*"]` 收窄到前端域名、不要让数据库端口对公网开放、为上传目录设置大小与类型校验。
- 上传的医疗文档含个人隐私，生产环境应加密存储并设置访问控制与保留期限。

## 免责声明

本项目为**技术演示**，输出内容由大语言模型生成，可能存在错误，**不构成任何医疗诊断或治疗建议**。身体不适请及时就医，遵医嘱用药。

## License

[MIT](LICENSE) © 2026 szc01

安全问题请按 [SECURITY.md](SECURITY.md) 私下报告；贡献流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。
