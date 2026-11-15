# API 约定（后端 ↔ Mac 客户端）

> 改接口时先改这里，提交后在群里 @ 对方，再改代码。
> Base URL（本地）：`http://localhost:8000`（客户端用环境变量 `SHOWME_API` 覆盖）
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
  "user_note": "用户说点错了，打开的是格式菜单"
}
```
  - `history`：已经给出过的步骤，最早的在前，最多传最近 10 条
  - `expect`：上一步返回的 `expect`；第一步传 `null`
  - `guide`：`/api/guide` 返回的 `outline`；没有就传 `null`
  - `user_note`（可选）：`/api/chat` 返回的 `note`（用户刚说的话的要点，比如「点错了」「这步做完了」），Gemini 判断下一步时会考虑；没有就不传或传 `null`
- 返回：
```json
{
  "status": "next",
  "previous_ok": true,
  "instruction": "在菜单里点击『二维』下面的第一个柱状图",
  "target_label": "柱形图",
  "box": [212, 455, 268, 512],
  "expect": "表格旁边出现一个柱状图"
}
```
  - `status`：
    - `"next"`：有下一步，`box` 一定有值
    - `"done"`：任务完成，`instruction` 是一句祝贺，`box` 为 `null`
    - `"unsure"`：看不出该点哪，`instruction` 告诉用户该怎么办（比如「请先打开 Numbers」），`box` 为 `null`
  - `previous_ok`：上一步是否按预期完成；第一步为 `null`。为 `false` 时 `instruction` 先说明哪里不对
  - `box`：`[ymin, xmin, ymax, xmax]`，整数，**相对截图 0–1000 归一化**；客户端乘以主屏逻辑尺寸换算
  - `target_label`：元素上能看到的文字（没有文字就写简短描述），客户端用来做 OCR 吸附
- 错误：
  - `400` `{ "error": "screenshot 不是有效的图片" }`、`{ "error": "goal 不能为空" }`
  - `422` 缺字段
  - `429 / 502 / 504` Gemini 相关（同 `backend/app/gemini.py` 里的说明）
- mock（`GEMINI_MOCK=1`）：按 `history` 长度依次返回 3 个固定步骤（框在屏幕中央附近），第 4 次返回 `done`

## `POST /api/chat`（对话模式）
- 用途：用户在面板里用自然语言（打字或语音转文字）跟 ShowMe 对话：开始任务、中途提问、说做完 / 跳过 / 点错、换目标、停止。后端判断该怎么做（`action`），并给一句回复
- 请求：
```json
{
  "lang": "zh",
  "message": "我想在 Numbers 里把这些数据做成柱状图",
  "screenshot": "<base64 JPEG，和 next-step 一样>",
  "messages": [{ "role": "user", "text": "..." }, { "role": "assistant", "text": "..." }],
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
