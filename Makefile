# 用法：make setup（第一次）→ make dev（同时起前后端）
.PHONY: setup backend frontend dev mock

setup:
	@test -f .env || (cp .env.example .env && echo "已创建 .env，记得填 GEMINI_API_KEY")
	cd backend && uv sync
	cd frontend && npm install

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

# 同时起前后端，Ctrl+C 一起停
dev:
	@trap 'kill 0' INT TERM EXIT; \
	$(MAKE) backend & \
	$(MAKE) frontend & \
	wait

# 不用 API key，用假数据起前后端
mock:
	GEMINI_MOCK=1 $(MAKE) dev
