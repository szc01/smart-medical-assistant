# frontend · 智能医疗助手前端

Vue 3 + TypeScript + Vite + Element Plus 单页应用。完整的项目介绍与安装步骤见仓库根目录的 [README](../README.md)。

## 快速启动

```bash
npm install
copy .env.example .env      # macOS / Linux: cp .env.example .env
npm run dev                 # http://localhost:5173
```

后端默认地址为 `http://localhost:8000`，可通过 `VITE_BACKEND_URL` 覆盖。

## 脚本

| 命令 | 说明 |
| --- | --- |
| `npm run dev` | 启动开发服务器（端口 5173） |
| `npm run build` | 类型检查（`tsc`）+ 生产构建到 `dist/` |
| `npm run preview` | 预览构建产物 |
| `npm run lint` | ESLint 检查 |
| `npm run format` | Prettier 格式化 `src/` |

## 页面

| 路由 | 文件 | 说明 |
| --- | --- | --- |
| `/login` | `views/LoginView.vue` | 登录，成功后写入 localStorage 的 token 与 user |
| `/register` | `views/RegisterView.vue` | 注册 |
| `/` | `views/HomeView.vue` | 对话主界面：会话列表、消息流、PDF 上传（需登录） |
| `/preferences` | `views/PreferencesView.vue` | 回答风格、过敏史等偏好（需登录） |
| `/about` | `views/AboutView.vue` | 项目介绍 |
