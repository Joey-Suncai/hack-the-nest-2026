# ShowMe — 设计文档（2026-11-14）

> 名字暂定，可以改。实施计划见 `docs/superpowers/plans/2026-11-14-showme.md`。

## 1. 意图
第一次用一个软件时，最难的是「不知道下一步点哪」。现在的做法是截图问 AI、看教程视频、或者打视频电话让别人一步步教。
**ShowMe**：你说出想做什么，Gemini 实时看你的屏幕，**直接在屏幕上把下一步要点的按钮框亮**，你点完它确认、再框下一个，直到做完。

- 给谁：第一次用某个 Mac 软件的人（学生学 Figma/Numbers、转行的人、长辈）
- 核心卖点：**教你做，不替你做**（区别于 computer-use agent）；**任何软件都能用，不需要厂商预先配置**（区别于 WalkMe / Whatfix 这类引导平台）
- 赛道：AI/ML、Best use of ElevenLabs、Computer Science Department；Devpost 勾 MLH Best Use of Gemini API

## 2. 目标 / 非目标 / 反目标
**MVP（按优先级）**
1. 说出目标：浮动面板输入「软件 + 想做什么」
2. 高亮下一步、逐步推进：截屏 → Gemini 返回说明 + 元素框 → 屏幕上画发光框和提示 → 检测到屏幕变化后确认 → 下一步，直到完成
3. 语音讲解：每一步用 ElevenLabs 读出来（中 / 英）

**加分项**：任务开始前用 Gemini + Google 搜索读官方帮助文档，生成步骤大纲（`/api/guide`）

**非目标**：替用户点击、手机端、Windows、多显示器、一直在后台监控（只在任务进行中截图）、登录 / 账号、部署上线

**反目标（技术上能跑也算失败）**：demo 现场框错位置却很自信 —— 不确定时宁可说「我不确定，请看提示文字」（`status: "unsure"`）；高亮框挡住用户要点的东西或拦截点击；截图被发到后端以外的地方。

## 3. 约束
- 截止：功能冻结 **周日 08:00**，Devpost 草稿 **10:00**，最终提交 **12:00**（美东）。开工时间：周六 17:00
- 机器：macOS 26（Darwin 25），Retina 屏。Python 3.12（uv 管理；系统自带 3.15 太新）
- Gemini 只从后端调用（`backend/app/gemini.py`），key 只在 `.env`；ElevenLabs key 同样只在后端
- 两个人都只熟 Python → 客户端也用 Python

## 4. 方案比较（已选 B）
| | 方案 | 取舍 |
|---|---|---|
| A | 一个 Python 程序直接调 Gemini | 最简单；但两人挤一份代码，key 在客户端 |
| **B ✅** | **Mac 客户端（PyQt6）+ FastAPI 后端** | 复用已搭好的后端骨架；分工清晰（A 后端 / B 客户端）；key 只在后端 |
| C | Electron 客户端 + 后端 | 要写 JS，两人不熟 |

平台：选 **Mac 桌面应用**（能教任何软件）而不是 Chrome 插件（只能教网页，但定位更准）。

## 5. 已定的技术决定
- **客户端** `client/`（Python 3.12 + uv）：PyQt6（浮动面板 + 全屏透明、鼠标穿透的高亮层）、PyObjC（设置 NSWindow 层级 / 所有桌面可见 / 忽略鼠标）、`mss` 截屏（失败时退回 `screencapture -x`）、numpy 比较画面变化、macOS Vision 框架做 OCR、httpx 调后端、`afplay` 播放音频
- **截屏前先隐藏高亮层**再截（不依赖 `NSWindowSharingNone`，新版 macOS 上它不一定生效）
- **坐标**：Gemini 原生格式 `[ymin, xmin, ymax, xmax]`，0–1000 归一化；客户端乘以主屏 **逻辑尺寸（point）** 换算，不是像素
- **步骤完成检测**：画面变化后又稳定下来 → 请求下一步；下一步请求里带上一步的 `expect`，由 Gemini 判断上一步是否成功
- **定位精度**：后端两步定位（粗框 → 裁剪放大 → 精框）；客户端 OCR 吸附（`target_label` 能在屏幕文字里找到时，把框吸到那段文字上）
- **后端** `backend/`：沿用 FastAPI 骨架，新增 `/api/next-step`、`/api/speak`、（加分）`/api/guide`。接口以 `docs/API.md` 为准
- **mock**：`GEMINI_MOCK=1` 时 `/api/next-step` 返回固定的 3 步假数据，`/api/speak` 返回 204（客户端跳过播放）
- 原来的 Vite + React `frontend/` 不再使用，由 B 在 C0 任务里删除

## 6. 留给实现者决定
- 高亮框的视觉样式（颜色、动画）、面板布局
- 截图缩放尺寸（建议长边 1280、JPEG 质量 70，可调）
- 画面变化阈值、稳定判定时长（建议 0.8 秒）
- ElevenLabs 的声音 ID 和模型
- 3 个 demo 任务具体选哪个软件（候选见计划）
