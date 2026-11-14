# Hack The Nest 2026 — 项目说明（给 AI 编程助手看）

## 比赛背景
- 24 小时黑客松，2 人队（Joey、Teresa）。Devpost 草稿截止周日 10:00 AM，最终提交周日 12:00 PM（美东时间）
- 目标赛道：AI/ML、Best use of ElevenLabs、Computer Science Department（+ Devpost 勾 MLH Best Use of Gemini API）。核心技术是 **Google Gemini API**
- 优先级：**demo 能稳定跑通 > 功能多**。代码要简单直接，不要过度设计

## 项目
- 点子：**ShowMe**（名字暂定）—— Mac 桌面工具。用户说出「软件 + 目标」，Gemini 看屏幕截图，在屏幕上框亮下一步要点的元素，逐步引导直到完成；ElevenLabs 读出每一步。教你做，不替你做
- 文档：`docs/IDEA.md`（一页概览）、`docs/superpowers/specs/2026-11-14-showme-design.md`（设计）、`docs/superpowers/plans/2026-11-14-showme.md`（**开发流程 + 分工 + 任务**，按任务编号 A0–A6 / C0–C9 做）
- 技术栈：全部 Python 3.12（uv 管理）
  - 后端 `backend/`：FastAPI + `google-genai`（端口 8000）+ ElevenLabs。Gemini 调用只走 `backend/app/gemini.py`；`GEMINI_MOCK=1` 返回假数据
  - 客户端 `client/`：PyQt6（浮动面板 + 透明鼠标穿透高亮层）、PyObjC、mss / `screencapture`、numpy、macOS Vision OCR、httpx
- 关键约定：框坐标 `[ymin, xmin, ymax, xmax]` 0–1000 归一化，客户端按主屏**逻辑尺寸**换算；截图前先隐藏高亮层
- 启动：`make setup` / `make backend` / `make client` / `make mock`（客户端任务 C0 完成后可用）

## 目录与归属
- `backend/`：API 服务 + Gemini / ElevenLabs 调用，负责人 **A（Joey）**。prompt 放在 `backend/prompts/`，评测在 `backend/eval/`
- `client/`：Mac 客户端，负责人 **B（Teresa）**。只通过后端调用 Gemini / ElevenLabs，客户端不出现任何 API key
- `frontend/`：旧的 Vite 骨架，不再使用，由 B 在任务 C0 删除
- `docs/API.md`：后端 ↔ 客户端接口约定，**以这里为准**。改接口要同步改这个文件
- 任务板：Google Doc（见 `docs/02_团队协作工作流.md`）

## 规则
- 只修改用户指定的目录。没被要求时，不要改对方负责的目录
- 密钥从 `.env` 读（见 `.env.example`），绝不写进代码，也不提交
- Gemini 调用要有错误处理和超时，失败时返回友好的错误信息（demo 现场不能崩）
- 提交信息前缀：feat / fix / docs / chore

## 已安装的 skill（`.claude/skills/`，来源见 THIRD_PARTY.md）
| 什么时候用 | Skill |
|---|---|
| 想点子 / 设计功能之前 | `brainstorming` |
| 定好方案，拆成实施步骤 | `writing-plans` |
| 遇到 bug，先找根因再修 | `systematic-debugging` |
| 说「做完了」之前先自己验证一遍 | `verification-before-completion` |
| 几个互不相关的任务并行做 | `dispatching-parallel-agents` |
| 做前端界面，要好看、不像 AI 模板 | `frontend-design`、`unslop`（demo 前打磨 UI）|
| 用 Playwright 测网页、截图 | `webapp-testing` |

黑客松时间紧：skill 里如果要求写完整测试或走很长的流程，可以简化，**先保证 demo 能跑通**。
