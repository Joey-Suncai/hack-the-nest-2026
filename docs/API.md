# API 约定（后端 ↔ Mac 客户端）

> 改接口时先改这里，提交后在群里 @ 对方，再改代码。
> Base URL（本地）：`http://localhost:8000`（客户端用环境变量 `SHOWME_API` 覆盖）
> **口令**：后端 `.env` 设了 `SHOWME_TOKEN` 时，除 `GET /api/health` 外的所有请求都要带请求头 `X-ShowMe-Token: <同一个值>`，否则返回 `401` `{ "error": "口令不对：请设置和后端一样的 SHOWME_TOKEN" }`。客户端从环境变量 `SHOWME_TOKEN` 读；没设就不带（后端没设口令时不检查）。
> 所有错误统一返回：`{ "error": "给用户看的中文说明" }` + HTTP 4xx/5xx。客户端直接把 `error` 显示在面板上。
> 客户端调用超时：`/api/next-step` 45 秒，`/api/speak` 20 秒，`/api/guide` 60 秒，`/api/chat` 30 秒，`/api/transcribe` 30 秒。

## `GET /api/health`
- 用途：客户端启动时检查后端是否在线、当前模型、是否 mock
- 返回：
```json
{ "status": "ok", "model": "gemini-3.6-flash", "mock": false }
```

## `POST /api/next-step`（核心）
- 用途：给当前截图和任务进度，返回下一步要点哪里
- 请求：
```json
{
  "app": "Numbers",
  "goal": "把 A1:B6 的数据做成柱状图",
  "lang": "zh",
  "screenshot": "<base64 JPEG，长边约 1280px，不带 data: 前缀>",
  "history": [
    { "instruction": "点击上方工具栏的『图表』按钮", "ok": true }
  ],
  "expect": "弹出图表类型菜单",
  "guide": ["插入 > 图表", "选择二维柱状图"],
  "user_note": "用户说点错了，打开的是格式菜单",
  "frontmost_app": "Numbers",
  "elements": [
    { "id": 1, "role": "AXButton", "name": "New Tab", "box": [40, 950, 62, 966] },
    { "id": 2, "role": "AXTextField", "name": null, "box": [120, 300, 140, 700] }
  ]
}
```
  - `history`：已经给出过的步骤，最早的在前，最多传最近 10 条
  - `expect`：上一步返回的 `expect`；第一步传 `null`
  - `guide`：`/api/guide` 返回的 `outline`；没有就传 `null`
  - `user_note`（可选）：`/api/chat` 返回的 `note`（用户刚说的话的要点，比如「点错了」「这步做完了」），Gemini 判断下一步时会考虑；没有就不传或传 `null`
  - `frontmost_app`（可选）：客户端用 NSWorkspace 读到的当前最前面的 App 名字（截图前读），作为「用户现在在哪个软件」的参考；和 `app` 不一样时（用户切到别的软件了），后端按截图判断，必要时返回 `unsure` 并提示用户切回 `app`。读不到就不传或传 `null`
  - `elements`（可选）：客户端用 macOS 辅助功能（Accessibility）读到的屏幕上的真实控件，每个控件的框是精确的。Gemini 优先按编号选其中一个，后端直接用它的框
    - `id`：整数，同一个请求里唯一
    - `role`：macOS AX role 字符串（如 `"AXButton"`、`"AXTextField"`）
    - `name`：屏幕上的文字或辅助功能标签，可以为 `null`
    - `box`：`[ymin, xmin, ymax, xmax]` 整数，相对**同一张全屏截图** 0–1000 归一化（和返回的 `box` 同一个约定）
    - 最多 250 个，按从上到下、再从左到右排序
    - 辅助功能不可用时不传或传 `null`：行为和以前完全一样（prompt 一字不差）
- 返回：
```json
{
  "status": "next",
  "previous_ok": true,
  "instruction": "在菜单里点击『二维』下面的第一个柱状图",
  "target_label": "柱形图",
  "box": [212, 455, 268, 512],
  "expect": "表格旁边出现一个柱状图",
  "next_guess": { "label": "二维", "instruction": "Click 2D in the chart menu.", "expect": "2D chart styles appear" },
  "element_id": null
}
```
  - `status`：
    - `"next"`：有下一步，`box` 一定有值
    - `"done"`：任务完成，`instruction` 是一句祝贺，`box` 为 `null`
    - `"unsure"`：看不出该点哪，`instruction` 告诉用户该怎么办（比如「请先打开 Numbers」），`box` 为 `null`
  - `previous_ok`：上一步是否按预期完成；第一步为 `null`。为 `false` 时 `instruction` 先说明哪里不对
  - `box`：`[ymin, xmin, ymax, xmax]`，整数，**相对截图 0–1000 归一化**；客户端乘以主屏逻辑尺寸换算
  - `target_label`：元素上能看到的文字（没有文字就写简短描述），客户端用来做 OCR 吸附
  - `next_guess`（可选，可能为 `null`）：预测**这一步点完之后**，下一步要点的元素。`label` 是那个元素上的原文（一定有文字），`instruction` / `expect` 同上。只在比较有把握时给。用法：画面一变，客户端先在新截图里用本地 OCR 找 `label`，找到就立刻画框、念 `instruction`；同时照常调 `/api/next-step` 核对，结果不一样再更正框和指令
  - `element_id`（`int | null`，默认 `null`）：选中的 `elements` 里的 `id`；`null` 表示框是 Gemini 自己给的坐标（没传 `elements`、目标不在列表里、或者模型给的编号对不上）。有值时 `box` 就是那个控件的 `box`（经过同样的取整 / 裁剪），`target_label` 是它的 `name`（`name` 为空时保留模型的描述），并且不做裁剪放大精修。只有 `status = "next"` 时可能有值
- 错误：
  - `400` `{ "error": "screenshot 不是有效的图片" }`、`{ "error": "goal 不能为空" }`
  - `422` 缺字段
  - `429 / 502 / 504` Gemini 相关（同 `backend/app/gemini.py` 里的说明）
- mock（`GEMINI_MOCK=1`）：按 `history` 长度依次返回 3 个固定步骤（框在屏幕中央附近），第 4 次返回 `done`；传了 `elements` 时，前 3 次改为返回第一个有 `name` 的控件（`element_id`、`box`、`target_label` 都取自它）

## `POST /api/chat`（对话模式）
- 用途：用户在面板里用自然语言（打字或语音转文字）跟 ShowMe 对话：开始任务、中途提问、说做完 / 跳过 / 点错、换目标、停止。后端判断该怎么做（`action`），并给一句回复
- 请求：
```json
{
  "lang": "zh",
  "message": "我想在 Numbers 里把这些数据做成柱状图",
  "screenshot": "<base64 JPEG，和 next-step 一样>",
  "messages": [{ "role": "user", "text": "..." }, { "role": "assistant", "text": "..." }],
  "frontmost_app": "Numbers",
  "task": {
    "app": "Numbers",
    "goal": "把 A1:B6 的数据做成柱状图",
    "outline": ["选中 A1:B6", "点击工具栏『图表』"],
    "history": [{ "instruction": "选中 A1:B6", "ok": true }],
    "current": {
      "instruction": "点击上方工具栏的『图表』按钮",
      "target_label": "图表",
      "box": [40, 480, 90, 510],
      "expect": "弹出图表类型菜单"
    }
  }
}
```
  - `messages`：最近 10 条对话，最早的在前，**不含**本次的 `message`；`role` 是 `"user"` 或 `"assistant"`
  - `frontmost_app`（可选）：同 `/api/next-step`，当前最前面的 App 名字。`start` 时后端结合 `message`、截图和它判断 `app`：用户没说软件名、但能从截图和 `frontmost_app` 看出来时直接 `start`，不追问
  - `task`：没有进行中的任务时传 `null`；`outline` 可以为 `null`；`current`（当前这一步，就是上一次 `/api/next-step` 的返回）可以为 `null`
- 返回：
```json
{
  "reply": "好的，我们分 4 步来……",
  "action": "start",
  "app": "Numbers",
  "goal": "把 A1:B6 的数据做成柱状图",
  "outline": ["选中 A1:B6", "点击工具栏『图表』", "选择柱状图"],
  "note": null
}
```
  - `reply`：显示在对话里，并用语音读出来（语言跟 `lang` 一致）
  - `action`：
    - `"start"`：开始新任务，或者换成新目标。客户端清空 history，设置 `app` / `goal` / `outline`，然后调 `/api/next-step`
    - `"resume"`：只是回答了问题，当前这一步不变。客户端保留框，继续检测画面变化
    - `"advance"`：用户的话改变了进度（做完了 / 跳过 / 点错了 / 卡住了）。客户端重新截图，带上 `user_note = note` 调 `/api/next-step`
    - `"stop"`：结束任务，清掉框
    - `"clarify"`：信息不够（比如不知道是哪个软件），`reply` 里追问；客户端等用户下一句，不开始任务
  - `app` / `goal`：只在 `start` 时有值，其他为 `null`
  - `outline`：`start` 时尽量给；可以为 `null`，客户端会再调 `/api/guide`。其他 action 为 `null`
  - `note`：`advance` 时给（用户说了什么、和当前步骤的关系），客户端原样传给 `/api/next-step` 的 `user_note`；其他 action 为 `null`
- 后端保证：坏的 `action` 会降级成 `resume`（有任务时）或 `clarify`（没任务时）；`start` 但没有 `goal` 也会降级成 `clarify`；不会把坏数据传给客户端
- 错误：`400` `{ "error": "message 不能为空" }`、`{ "error": "screenshot 不是有效的图片" }`；`422` 缺字段；`429 / 502 / 504` Gemini 相关
- mock（`GEMINI_MOCK=1`）：
  - `task` 为 `null` → `start`，`app` 填 `"Numbers"`，`goal` = `message`，`outline` 给 3 条固定步骤
  - `message` 含「跳过 / 做完 / 好了 / skip / done」→ `advance`（`note` = `message`）
  - 含「停 / stop」→ `stop`
  - 其他 → `resume`，`reply` 为「（mock）你问的是：<message>」
  - `lang = "en"` 时用英文回复

## `POST /api/transcribe`（语音输入）
- 用途：把用户说的话转成文字（ElevenLabs Speech-to-Text），客户端再把文字发给 `/api/chat`
- 请求：multipart/form-data
  - `file`：音频（客户端发 16 kHz 单声道 WAV，最长 30 秒）
  - `lang`：`zh` / `en`
- 返回：
```json
{ "text": "这个按钮是干嘛的？" }
```
  - 没听清（静音、太短）就返回 `{ "text": "" }`，不算错误
- 没配 `ELEVENLABS_API_KEY`：`503` `{ "error": "语音输入没有配置，请直接打字" }`
- mock（`GEMINI_MOCK=1`）：返回固定的一句话（`zh`：「这个按钮是干嘛的？」；`en`：「What does this button do?」）
- 错误：`400` 文件为空或超过 5 MB；`502` ElevenLabs 拒绝；`504` 超时

## `GET /api/agent-session`（实时语音，ElevenLabs Agent）
- 用途：客户端开始实时语音对话前调用，拿一个一次性的 signed URL 连 ElevenLabs Agent（WebSocket）。ElevenLabs key 只留在后端
- 返回：
```json
{ "signed_url": "wss://api.elevenlabs.io/v1/convai/conversation?agent_id=...&conversation_signature=...", "agent_id": "agent_..." }
```
- 没配好（没有 `ELEVENLABS_API_KEY` / `ELEVENLABS_AGENT_ID`）或 mock：`503` `{ "error": "实时语音没有配置" }`，客户端退回打字 + `/api/chat` + `/api/speak`
- 错误：`502` ElevenLabs 拒绝；`504` 超时
- 开会话时可以覆盖 `agent.language`（`"zh"` / `"en"`，默认 `en`）和 `agent.first_message`

### 分工：步骤由客户端念，Agent 只负责对话
- **客户端**：每一步画框的同一刻，自己调 `/api/speak` 念这一步的 `instruction`（框和声音同步）
- **Agent**：开始任务、回答问题、处理「做完了 / 跳过 / 点错了 / 卡住了」，**不念步骤、不自己猜下一步**
- `/api/speak` 和 Agent 用同一个声音（`ELEVENLABS_VOICE_ID`，没设时两边都用预置的 Rachel）、同一档模型（英文 `eleven_flash_v2`），听起来是同一个人

### Agent 的两个 client tool（在客户端执行，结果以字符串返回给 Agent）
- `show_next_step(goal: string, note?: string, app?: string)`：`app` 是 Agent 判断的任务软件（如 `"Google Chrome"`），客户端开始新任务时用它当 `/api/next-step` 的 `app`；没有就传通用的「当前软件」，**不要用 frontmost_app 当任务软件**（用户可能正在别的窗口里跟 ShowMe 说话）。客户端截图（先隐藏高亮层）→ 调 `/api/next-step`（`app` 用当前任务的软件或 `frontmost_app`，`goal` = 参数 goal，`user_note` = 参数 note，`history` / `expect` 照常带）→ 画框 + 调 `/api/speak` 念 `instruction` → 返回给 Agent 的字符串（**前缀必须一字不差**）：

| 情况 | 返回给 Agent | Agent 会怎么做 |
|---|---|---|
| `status = next`，框已画好、客户端正在念 | `"SHOWN: " + instruction` | 不重复步骤，最多说 "Okay." 或不说话 |
| `status = done` | `"DONE: " + instruction` | 简短祝贺（客户端不用再念 done 的那句） |
| `status = unsure` | `"UNSURE: " + instruction` | 用一句话转告该怎么办（客户端不用念） |
| 用户在面板上点了停止 / 任务已结束 | `"STOPPED: " + 简短说明` | 简短回应，等用户 |
| 截图失败、后端报错、超时 | `"ERROR: " + 英文错误说明` | 说没成功，请用户再试 |

- `look_at_screen(question: string)`：客户端截图 → 调 `/api/chat`（`message` = question，带上当前 `task`）→ 返回 `reply`（只用回答，不按 `action` 改进度）；回答由 Agent 念

### 背景消息 `[step]`
- 客户端用 ElevenLabs 的 **contextual update**（WebSocket 消息 `{"type": "contextual_update", "text": "..."}`）告诉 Agent 用户进行到哪一步，文字以 `[step]` 开头，比如 `"[step] Step 3 shown: Click Clone from URL in the dropdown."`
- 三种格式（前缀一字不差）：
  - `"[step] Step N shown: <instruction>"`：出了新框（客户端同时念这一步）
  - `"[step] Task done: <instruction>"`：画面变化后 `/api/next-step` 返回 `done`
  - `"[step] Unsure: <instruction>"`：画面变化后 `/api/next-step` 返回 `unsure`
- Agent 不会回复、不会念出来，只用来理解上下文（用户接着问「这一步什么意思」时它知道是哪一步）
- 框还在、用户也没开口时，Agent 调 `show_next_step` 且**没有 note**：客户端直接返回 `"SHOWN: <当前这一步>"`，不截图、不出新框。用户说了 done / wrong button 等，Agent 一定带 note 调用，客户端才重新截图
- 后端：上一步 `ok = false`（用户没点中 / 说点错了）时，`/api/next-step` 会自动做一次「裁剪放大再定位」（多约 2–3 秒），日历、图标栏这类差一格的情况重试时更准

### 客户端流程（参考）
1. 用户开口说任务 → Agent 说一句短确认并调 `show_next_step` → 客户端画框、**自己念**这一步 → 返回 `SHOWN: ...` → Agent 不再重复
2. 用户点完、画面一变 → 客户端先用上一次的 `next_guess` 本地 OCR 秒出框并念出来 → 后台调 `/api/next-step` 核对，不一样就更正框并念更正后的指令 → 每次出新框都发一条 `[step]` contextual update 给 Agent
3. 用户问问题 → Agent 调 `look_at_screen` 并回答；用户说做完 / 跳过 / 点错 → Agent 调 `show_next_step`（带 note）

## `POST /api/speak`
- 用途：把一句话转成语音（ElevenLabs）
- 请求：
```json
{ "text": "先点上方工具栏的『图表』按钮", "lang": "zh" }
```
- 返回：`200`，`Content-Type: audio/mpeg`，body 是 mp3
- 没配 `ELEVENLABS_API_KEY` 或 mock 模式：返回 `204`（无 body），客户端跳过播放，**不算错误**
- 错误：`400` text 为空或超过 300 字；`502` ElevenLabs 拒绝；`504` 超时

## `POST /api/guide`（加分项）
- 用途：任务开始前，用 Gemini + Google 搜索读官方文档，生成步骤大纲
- 请求：
```json
{ "app": "Numbers", "goal": "把 A1:B6 的数据做成柱状图", "lang": "zh" }
```
- 返回：
```json
{
  "outline": ["选中 A1:B6", "点击工具栏『图表』", "选择『二维』里的柱状图"],
  "sources": [{ "title": "Add a chart in Numbers on Mac", "url": "https://support.apple.com/..." }]
}
```
- 失败时客户端直接跳过（`guide` 传 `null`），不影响主流程

## 骨架自带的示例接口（保留，可以不用）
- `POST /api/generate`：`{ "prompt": "..." }` → `{ "result": "..." }`
- `POST /api/analyze-file`：multipart `file`（+ 可选 `prompt`）→ `{ "result": "..." }`
