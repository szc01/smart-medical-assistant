# 贡献指南

感谢你愿意花时间改进这个项目。下面是最小必要的约定。

## 提交前

1. **不要提交任何密钥**：`backend/.env`、`frontend/.env` 均已在 `.gitignore` 中，新增配置请同步更新对应的 `.env.example`。
2. **不要提交运行产物**：`__pycache__/`、`node_modules/`、`dist/`、`backend/uploads/` 下的用户文件。
3. 本地自检：

   ```bash
   # 后端语法检查
   python -m compileall -q backend/app backend/main.py

   # 前端类型检查 + 构建
   cd frontend && npm ci && npm run build
   ```

## 提交信息

使用 Conventional Commits 前缀，中文描述即可：

```text
feat: 新增营养师角色与路由规则
fix: 修复偏好设置使用随机 user_id 导致智能体读不到的问题
docs: 补充 PostgreSQL 三个库的职责说明
refactor: 提取长记忆读取逻辑
chore: 升级 langgraph 到 1.0.3
```

一个提交只做一件事。格式化类的改动（如全量 Prettier）请单独提交，不要混在功能提交里。

## 新增智能体

1. 在 `backend/app/agents/` 下新建节点文件，导出 `xxx_node(state)`；
2. 在 `multi_agent.py` 中 `add_node` 并补 `add_conditional_edges` 的映射；
3. 在 `supervisor.py` 的 `ROUTE_PROMPT` 中补充该角色的职责与路由规则（否则永远不会被选中）；
4. 在 README 的「功能」与 `docs/ARCHITECTURE.md` 的扩展表中登记。

## 新增接口

1. 在 `backend/app/api/` 下新建模块并定义 `router`；
2. 在 `main.py` 中注册前缀；
3. 需要鉴权的接口加上 `Depends(get_current_user)`，并校验 `user_id` 归属（参考 `api/preferences.py` 的 `_resolve_user_id`）；
4. 更新 README 的 API 表。

## 报告问题

请附上：复现步骤、期望结果、实际结果、`main.py` 启动日志中相关片段（**先删掉密钥与数据库地址**）、Python / Node 版本。
