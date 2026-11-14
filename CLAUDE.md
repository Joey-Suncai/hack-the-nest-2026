# Hack The Nest 2026 — 项目说明（给 AI 编程助手看）

## 比赛背景
- 24 小时黑客松，2 人队（Joey、Teresa）。Devpost 草稿截止周日 10:00 AM，最终提交周日 12:00 PM（美东时间）
- 目标赛道：AI/ML（+ 待定）。核心技术是 **Google Gemini API**
- 优先级：**demo 能稳定跑通 > 功能多**。代码要简单直接，不要过度设计

## 项目
- 点子：（待定，定了以后马上填）
- 技术栈：（待定）

## 目录与归属
- `backend/`：API 服务 + Gemini 调用，负责人 A。prompt 放在 `backend/prompts/`
- `frontend/`：界面，负责人 B。只通过后端调用 Gemini，前端不出现 API key
- `docs/API.md`：前后端接口约定，**以这里为准**。改接口要同步改这个文件
- `docs/TASKS.md`：任务板

## 规则
- 只修改用户指定的目录。没被要求时，不要改对方负责的目录
- 密钥从 `.env` 读（见 `.env.example`），绝不写进代码，也不提交
- Gemini 调用要有错误处理和超时，失败时返回友好的错误信息（demo 现场不能崩）
- 提交信息前缀：feat / fix / docs / chore
