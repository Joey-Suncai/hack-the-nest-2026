# 用法：make setup（第一次）→ make dev（同时起后端和客户端）
.PHONY: setup backend client dev mock

setup:
	@test -f .env || (cp .env.example .env && echo "已创建 .env，记得填 GEMINI_API_KEY")
	cd backend && uv sync
	cd client && uv sync

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

# 项目放在 iCloud 同步的文件夹里时，iCloud 会把 .venv 里的文件标成「隐藏」，Qt 就找不到 cocoa 插件
client:
	@chflags -R nohidden client/.venv 2>/dev/null || true
	cd client && uv run python -m showme

# 同时起后端和客户端，Ctrl+C 一起停
dev:
	@trap 'kill 0' INT TERM EXIT; \
	$(MAKE) backend & \
	$(MAKE) client & \
	wait

# 不用 API key，用假数据起后端和客户端
mock:
	GEMINI_MOCK=1 $(MAKE) dev
