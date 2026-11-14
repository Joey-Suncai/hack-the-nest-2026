# API 约定（前后端接口）

> 点子定了以后第 1 小时内填完。改接口时先改这里，然后在群里 @ 对方。
> Base URL（本地）：`http://localhost:8000`　（线上）：待填
> 所有错误统一返回：`{ "error": "给用户看的中文说明" }` + HTTP 4xx/5xx。前端直接把 `error` 显示出来即可。

## 已有接口（脚手架自带，可删可改）

### `GET /api/health`
- 用途：前端启动时检查后端是否在线、当前模型、是否 mock 模式
- 返回：
```json
{ "status": "ok", "model": "gemini-3.6-flash", "mock": false }
```

### `POST /api/generate`
- 用途：示例接口，把用户输入套进 `backend/prompts/example.txt`，调用 Gemini 返回文本
- 请求：
```json
{ "prompt": "给我写一句口号" }
```
- 返回：
```json
{ "result": "……Gemini 的回答……" }
```
- 错误：
  - `400` `{ "error": "prompt 不能为空" }`
  - `422` `{ "error": "请求格式不对：Field required" }`（缺字段）
  - `429` Gemini 调用太频繁 / 配额用完
  - `500` 服务端没配置 key
  - `502` Gemini 拒绝请求（key 无效、额度用完、模型名错）
  - `504` Gemini 超时 / 网络问题（已自动重试 1 次）

### `POST /api/analyze-file`
- 用途：示例接口，上传图片 / PDF，让 Gemini 看文件回答
- 请求：`multipart/form-data`，字段 `file`（png/jpg/webp/pdf，≤15MB），可选字段 `prompt`
- 返回：
```json
{ "result": "……" }
```
- 错误：`400` 文件类型不支持 / 文件太大；其余同上

## 模板（新接口照这个格式写）
### `POST /api/example`
- 用途：
- 请求：
```json
{ "input": "..." }
```
- 返回：
```json
{ "result": "..." }
```
- 错误：`{ "error": "message" }` + HTTP 4xx/5xx
