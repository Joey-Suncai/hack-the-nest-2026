"""FastAPI 入口。启动：cd backend && uv run uvicorn app.main:app --reload --port 8000

加新接口：照着下面 /api/generate 抄一个，然后同步更新 docs/API.md。
"""

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from typing import Literal

from pydantic import BaseModel

from app import gemini, steps, voice

app = FastAPI(title="Hack The Nest 2026 API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 统一错误格式：{"error": "..."} ----------
@app.exception_handler(gemini.GeminiError)
async def _gemini_error(_: Request, e: gemini.GeminiError):
    return JSONResponse({"error": e.message}, status_code=e.status)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, e: RequestValidationError):
    return JSONResponse({"error": f"请求格式不对：{e.errors()[0].get('msg', '')}"}, status_code=422)


@app.exception_handler(Exception)
async def _unexpected(_: Request, e: Exception):
    print(f"[server] unexpected error: {type(e).__name__}: {e}")
    return JSONResponse({"error": "服务器出错了，请稍后再试"}, status_code=500)


def bad_request(msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=400)


# ---------- 接口 ----------
@app.get("/api/health")
def health():
    return {"status": "ok", "model": gemini.MODEL, "mock": gemini.MOCK}


class GenerateIn(BaseModel):
    prompt: str


class GenerateOut(BaseModel):
    result: str


@app.post("/api/generate", response_model=GenerateOut)
def generate(body: GenerateIn):
    if not body.prompt.strip():
        return bad_request("prompt 不能为空")
    text = gemini.generate_text(gemini.load_prompt("example", prompt=body.prompt))
    return {"result": text}


MAX_FILE_MB = 15
ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "application/pdf"}


@app.post("/api/analyze-file", response_model=GenerateOut)
async def analyze_file(file: UploadFile = File(...), prompt: str = Form("描述这个文件的主要内容")):
    """示例：上传图片 / PDF，让 Gemini 看图回答。"""
    if file.content_type not in ALLOWED_MIME:
        return bad_request(f"不支持的文件类型：{file.content_type}（支持 png/jpg/webp/pdf）")
    data = await file.read()
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        return bad_request(f"文件太大（上限 {MAX_FILE_MB}MB）")
    text = gemini.generate_text(prompt, files=[(data, file.content_type)])
    return {"result": text}


# ---------- ShowMe ----------
@app.post("/api/next-step", response_model=steps.NextStepOut)
def next_step(body: steps.NextStepIn):
    if not body.goal.strip():
        return bad_request("goal 不能为空")
    try:
        steps.decode_screenshot(body.screenshot)
    except ValueError as e:
        return bad_request(str(e))
    body.history = body.history[-10:]
    out = steps.mock_next_step(body) if gemini.MOCK else steps.next_step(body)
    return steps.finalize(out)


class SpeakIn(BaseModel):
    text: str
    lang: Literal["zh", "en"] = "zh"


@app.post("/api/speak")
def speak(body: SpeakIn):
    text = body.text.strip()
    if not text:
        return bad_request("text 不能为空")
    if len(text) > voice.MAX_CHARS:
        return bad_request(f"text 太长了（上限 {voice.MAX_CHARS} 字）")
    mp3 = voice.speak(text, body.lang)
    if mp3 is None:
        return Response(status_code=204)
    return Response(content=mp3, media_type="audio/mpeg")
