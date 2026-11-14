# API 约定（后端 ↔ Mac 客户端）

> 改接口时先改这里，提交后在群里 @ 对方，再改代码。
> Base URL（本地）：`http://localhost:8000`（客户端用环境变量 `SHOWME_API` 覆盖）
> 所有错误统一返回：`{ "error": "给用户看的中文说明" }` + HTTP 4xx/5xx。客户端直接把 `error` 显示在面板上。
> 客户端调用超时：`/api/next-step` 45 秒，`/api/speak` 20 秒，`/api/guide` 60 秒。

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
  "guide": ["插入 > 图表", "选择二维柱状图"]
}
```
  - `history`：已经给出过的步骤，最早的在前，最多传最近 10 条
  - `expect`：上一步返回的 `expect`；第一步传 `null`
  - `guide`：`/api/guide` 返回的 `outline`；没有就传 `null`
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
