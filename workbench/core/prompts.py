"""提示词模板加载与渲染。不引 jinja2，只用 {{var}} 正则替换。"""
from __future__ import annotations

import re

from . import paths

_FRONT_RE = re.compile(r"^---[ \t]*\n(.*?)\n---[ \t]*\n", re.S)
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def load(task: str) -> tuple[dict, str]:
    """返回 (front-matter 字典, 正文)。模板缺失时返回空串，由调用方降级。"""
    p = paths.PROMPTS_DIR / f"{task}.md"
    if not p.exists():
        return {"id": task}, ""
    text = p.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    m = _FRONT_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        text = text[m.end():]
    return meta, text.strip()


def render(text: str, variables: dict) -> str:
    def _rep(m: re.Match) -> str:
        v = variables.get(m.group(1))
        if v is None:
            return "（未填）"
        if isinstance(v, (list, tuple)):
            return "、".join(str(x) for x in v) if v else "（未填）"
        return str(v)

    return _VAR_RE.sub(_rep, text)


def est_tokens(chars: int) -> int:
    """中文粗估：1 字 ≈ 0.65 token。"""
    return int(chars * 0.65)


def available() -> list[str]:
    return sorted(p.stem for p in paths.PROMPTS_DIR.glob("*.md"))
