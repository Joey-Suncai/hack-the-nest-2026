"""/api/guide：用 Gemini + Google 搜索读官方文档，生成步骤大纲。"""

from __future__ import annotations

import re

from app import gemini

MAX_STEPS = 8
LANGUAGES = {"zh": "简体中文 (Simplified Chinese)", "en": "English"}

# 行首的编号 / 列表符号：「1.」「2)」「(3)」「第4步：」「- 」「* 」「• 」「Step 5:」
_PREFIX = re.compile(
    r"^\s*(?:[-*•·]+\s*|\(?\d+[.)、:：]\s*|第\s*\d+\s*步[:：、]?\s*|step\s*\d+\s*[:.)-]?\s*)+",
    re.IGNORECASE,
)


def parse_outline(text: str) -> list[str]:
    """按行切分，去掉编号 / 列表符号和空行，最多 MAX_STEPS 步。"""
    steps = []
    for line in text.splitlines():
        line = _PREFIX.sub("", line).strip().strip("*").strip()
        if line:
            steps.append(line)
    return steps[:MAX_STEPS]


def make_guide(app: str, goal: str, lang: str) -> dict:
    if gemini.MOCK:
        return {"outline": ["[MOCK] 第一步", "[MOCK] 第二步"], "sources": []}
    prompt = gemini.load_prompt(
        "guide",
        app=app.strip() or "the app",
        goal=goal.strip(),
        language=LANGUAGES.get(lang, "English"),
    )
    text, sources = gemini.generate_grounded(prompt)
    outline = parse_outline(text)
    if not outline:
        raise gemini.GeminiError("没有生成出步骤大纲，请重试", 502)
    return {"outline": outline, "sources": sources}
