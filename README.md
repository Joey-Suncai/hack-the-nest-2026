# Hack The Nest 2026 — Team NEST-TYSS

Joey Sun · Teresa Wang (University of Chicago)

> Project description goes here (also used for Devpost).

技术栈：FastAPI + Google Gemini（`google-genai`）后端 · Vite + React + TypeScript 前端

## 快速开始
**需要装：** [uv](https://docs.astral.sh/uv/)（`brew install uv`）、Node 20+、make

```bash
cp .env.example .env   # 填 GEMINI_API_KEY（make setup 也会自动复制）
make setup             # 装后端（Python 3.12，uv 会自动下载）+ 前端依赖
make dev               # 同时启动：后端 http://localhost:8000 · 前端 http://localhost:5173
```
- 只起一个：`make backend` / `make frontend`
- **没 key / 没网 / 省额度：** `make mock`（或 `.env` 里设 `GEMINI_MOCK=1`），后端返回假数据
- **后端还没好，前端先做：** `frontend/.env.local` 里写 `VITE_USE_MOCKS=1`，用 `frontend/src/mocks/` 的假数据
- 后端接口文档（自动生成）：http://localhost:8000/docs
- 注意：如果 shell 里已经 `export GEMINI_API_KEY=...`，它会优先于 `.env`

## 在哪里改
| 想做什么 | 改哪里 |
|---|---|
| 加一个后端接口 | `backend/app/main.py`（照 `/api/generate` 抄）+ 同步更新 `docs/API.md` |
| 改 / 加 prompt | `backend/prompts/*.txt`，代码里 `gemini.load_prompt("名字", 变量=...)` |
| 调 Gemini（文本 / JSON 结构化 / 图片 PDF 输入）| 只用 `backend/app/gemini.py` 里的 `generate_text` / `generate_json` |
| 换模型 | `.env` 里的 `GEMINI_MODEL` |
| 前端调用新接口 | `frontend/src/api.ts` 加一个函数，假数据加在 `frontend/src/mocks/` |
| 改页面 | `frontend/src/App.tsx`、`frontend/src/index.css` |

## 团队文档
- **任务分工（Google Doc）：** https://docs.google.com/document/d/1zOFvMK21tq3OHowYNdMt2NikQcE9c1DU3L-RKZY8VN4/edit
- **点子 & 启动流程：** [docs/IDEA.md](docs/IDEA.md)
- [Event info](docs/00_比赛信息.md) · [Plan](docs/01_比赛流程规划.md) · [Workflow](docs/02_团队协作工作流.md) · [API](docs/API.md) · [Tasks](docs/TASKS.md)
