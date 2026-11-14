# Joey 开工指南（A：后端 + AI）

> 详细任务（文件、函数签名、测试、验证命令）在 `docs/superpowers/plans/2026-11-14-showme.md` 的**第二部分 A0–A6**。这页只是你的路线图。
> 接口以 `docs/API.md` 为准，**你负责维护它**；demo 和提交见 `pitch/demo-plan.md`。

## 1. 第一次准备
```bash
git switch main && git pull
make setup                    # C0 合进来之前，setup 里还有 npm install，报错可以忽略
```
- **Gemini 预付**：AI Studio → 你的 key 所在项目 → Set up prepay / Buy credits（至少 $5），或者领取 AI Pro 的每月 $10 额度（developers.google.com/program/my-benefits）。状态不再是「No credits」才算好
- **ElevenLabs**：注册 → 复制 API key → 在 Voice Library 挑一个支持中英文的声音，复制 voice id
- 两个 key 都写进根目录 `.env`；`~/.zshrc` 里的旧 `GEMINI_API_KEY` 已经注释掉了，保持这样

## 2. 开工提示词（在 Claude Code 里贴这段）
```
读 CLAUDE.md、docs/API.md、docs/START_Joey_后端.md 和 docs/superpowers/plans/2026-11-14-showme.md。
我是 A（Joey），负责 backend/。从 Task A0 开始，按计划逐个任务做，只修改 backend/、.env.example，以及（接口有变化时）docs/API.md。
每个任务做完先跑它的测试 / 验证命令，告诉我结果，再提交。A1 完成后提醒我合进 main 并通知 Teresa。
```

## 3. 你的任务和时间
| 时间 | 任务 | 完成标准 |
|---|---|---|
| 17:00–17:45 | **A0** 账号与依赖 | `/api/generate` 用真实 key 返回正常文本（不是 402）→ 17:45 站会 |
| 17:45–19:00 | **A1** `/api/next-step` 数据结构 + mock | 测试通过，**19:00 前合进 main，群里通知 Teresa** |
| 19:00–21:00 | **A2** 真实 Gemini + 准确度评测（截 8–10 张图、标注、跑 `eval/eval_boxes.py`） | 21:00 站会报命中率和平均耗时 |
| 21:00–01:00 | **A3** 两步定位、**A4** `/api/speak` | **M2（01:00）**：和 Teresa 的客户端一起跑通 Numbers 主任务；命中率 ≥ 80% |
| 01:30–04:00 | **睡觉** | 睡前 push，群里说进度 |
| 04:00–06:30 | **A5**（加分）`/api/guide`、**A6** 针对 demo 任务调优 | eval 命中率 ≥ 90%，单步平均 ≤ 4 秒 |
| 06:30–07:30 | 和 Teresa 彩排 | **M3** |
| 08:00 之后 | 只修 bug；画架构图、写 README 技术部分、录视频里「原理」那段旁白、准备评委问答 | 见 `pitch/demo-plan.md` 第 3、4 节 |

## 4. 给 Teresa 联调用
- 让她连你的后端：查你的局域网 IP（`ipconfig getifaddr en0`），启动时绑定所有网卡：`cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`
- 在 `backend/eval/cases/` 放一张「标准截图」和它的正确返回，方便她核对坐标换算

## 5. 一定要注意
- Gemini 只能通过 `backend/app/gemini.py` 调用；ElevenLabs 只在 `backend/app/voice.py` 调用；key 只放在 `.env`
- 坏框（顺序反了、超出 0–1000、`next` 却没框）必须在后端修正或降级成 `unsure`，不能传给客户端
- 想改接口：先改 `docs/API.md` → 提交 → 群里 @ Teresa → 再改代码
- 分支名用 `joey/xxx`；每 1–2 小时合进 main
