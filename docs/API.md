# API 约定（前后端接口）

> 点子定了以后第 1 小时内填完。改接口时先改这里，然后在群里 @ 对方。
> Base URL（本地）：`http://localhost:8000`　（线上）：待填

## 模板
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
