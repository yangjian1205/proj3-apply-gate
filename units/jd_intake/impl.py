# -*- coding: utf-8 -*-
"""units/jd-intake · JD 接入（纯代码）

职责：把「粘贴的 JD 文本 / JD JSON 文件 / 批量目录」统一成一种结构。
边界：不清洗文本、不猜缺失字段、不做爬虫、不判断值不值得投。

最硬的一条：**`text` 原样保留**。
任何清洗（合并空格 / 删换行 / 全角转半角）都会让 `quote` 不再是它的子串，
硬指标①的自动校验立刻失效 —— 高亮失效还是小事，门禁跑出来是假数字才是大事。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core import config as cfg  # noqa: E402

MIN_TEXT_LEN = 20


class IntakeError(RuntimeError):
    """输入不可用时的硬失败。"""


def _city_of(text: str) -> str:
    from units.hard_gate.impl import CITY_KEYWORDS

    for c in CITY_KEYWORDS:
        if c in text:
            return c
    return ""


def _make_jd_id(text: str) -> str:
    digest = hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:8]
    return f"jd-raw-{digest}"


def _parse_fetched_at(value: str | None) -> date:
    if not value:
        return date.today()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
        raise IntakeError(f"fetched_at 必须是 YYYY-MM-DD，收到 {value!r}")
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise IntakeError(f"fetched_at 不是合法日期：{value!r}") from exc


def _normalize(payload: dict, *, source: str) -> dict:
    text = payload.get("text")
    if not isinstance(text, str) or len(text.strip()) < MIN_TEXT_LEN:
        raise IntakeError(
            f"{source} 的 text 为空或少于 {MIN_TEXT_LEN} 字 —— 拒绝给出判定（空输入不产生结论）"
        )

    fetched = _parse_fetched_at(payload.get("fetched_at"))
    days_old = (date.today() - fetched).days
    stale_days = cfg.load_rules()["thresholds"].get("jd_stale_days", 90)

    return {
        "jd_id": payload.get("jd_id") or _make_jd_id(text),
        "company": payload.get("company") or "未标注",
        "title": payload.get("title") or "未标注",
        "text": text,  # ⚠️ 原样保留，禁止清洗
        "source_url": payload.get("source_url") or "",
        "fetched_at": fetched.isoformat(),
        "days_old": days_old,
        "stale": days_old > stale_days,
        "city": payload.get("city") or _city_of(text),
        "source": source,
    }


# 纯文本 JD 顶部的元信息行：`# 公司: 某某科技` / `# 岗位: AI 应用工程师`
_META_RE = re.compile(r"^#\s*([A-Za-z_]+|[^\s:：#]{2,8})\s*[:：]\s*(.+)$")

_META_KEYS = {
    "company": ("company", "公司", "公司名"),
    "title": ("title", "岗位", "职位", "岗位名"),
    "source_url": ("url", "link", "链接", "来源", "source_url"),
    "city": ("city", "城市", "地点", "工作地点"),
}


def _split_meta(text: str) -> tuple[dict, str]:
    """把 txt 顶部的 `# key: value` 行拆成元信息，其余是 JD 正文。

    只在正文开始前识别 —— 否则 JD 正文里以 # 开头的行会被误吃。
    """
    meta: dict[str, str] = {}
    body: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        m = _META_RE.match(stripped) if not body else None
        if m:
            key = m.group(1).strip().lower()
            meta[key] = m.group(2).strip()
        elif not stripped and not body:
            continue  # 跳过正文前的空行
        else:
            body.append(line)
    return meta, "\n".join(body).strip()


def _pick(meta: dict, field: str) -> str:
    for alias in _META_KEYS[field]:
        if meta.get(alias):
            return meta[alias]
    return ""


def _read_jd_file(path: Path) -> dict:
    """读一个 JD 文件：`.txt` / `.md` 直接当 JD 正文，`.json` 走结构化字段。

    为什么必须支持纯文本：使用者的真实动作是「在招聘网站选中 JD → Ctrl+C → 存成文件」。
    逼他为此手写 JSON 是没必要的摩擦 —— 拿到的 JD 本来就只是一段文字。

    txt 的字段来源优先级：文件头 `# 公司: xxx` 元信息 > 文件名（当岗位名）> 「未标注」。
    """
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".text"):
        raw = path.read_text(encoding="utf-8-sig")
        meta, body = _split_meta(raw)
        text = body or raw
        if len(text.strip()) < MIN_TEXT_LEN:
            raise IntakeError(
                f"{path.name} 正文只有 {len(text.strip())} 字，少于 {MIN_TEXT_LEN} 字 —— "
                f"如果文件里只写了 `# 公司:` 这类元信息，把 JD 正文也贴进去。"
            )
        # 没写元信息时，用文件名当岗位名 —— 至少列表里认得出是哪个岗位
        return _normalize({
            "text": text,
            "jd_id": path.stem,
            "company": _pick(meta, "company") or "未标注",
            "title": _pick(meta, "title") or path.stem,
            "source_url": _pick(meta, "source_url"),
            "city": _pick(meta, "city"),
        }, source=str(path))

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise IntakeError(
            f"{path.name} 不是合法 JSON：{exc}\n"
            f"提示：如果这就是一段 JD 原文，把扩展名改成 .txt 即可（不用写 JSON）。"
        ) from exc
    payload.setdefault("jd_id", path.stem)
    return _normalize(payload, source=str(path))


def intake(source: str | Path | dict) -> dict:
    """统一入口。

    - 传 dict      → 直接归一化（批量模式与评测集走这条路）
    - 传 Path      → 读文件（.json 走结构化字段；.txt / .md 当 JD 正文）
    - 传 str       → 若是存在的文件路径则读文件，否则当成粘贴的 JD 文本
    """
    if isinstance(source, dict):
        return _normalize(dict(source), source="dict")

    if isinstance(source, Path):
        if not source.exists():
            raise IntakeError(f"找不到 JD 文件：{source}")
        return _read_jd_file(source)

    if isinstance(source, str):
        candidate = Path(source)
        if candidate.exists() and candidate.is_file():
            return _read_jd_file(candidate)
        return _normalize({"text": source}, source="pasted-text")

    raise IntakeError(f"不支持的输入类型：{type(source).__name__}")


def intake_batch(directory: Path | str | None = None) -> list[dict]:
    """批量读目录下的 JD 文件：`jd-*.json`（评测集口径）+ `*.txt`（收件箱口径）。

    校验失败的文件不会静默跳过，会带上错误一起返回 —— 静默跳过等于让你以为「都判过了」。
    """
    directory = Path(directory or cfg.JD_DIR)
    files = sorted(set(directory.glob("jd-*.json")) | set(directory.glob("*.txt")))
    if not files:
        raise IntakeError(f"{directory} 下没有 jd-*.json 或 *.txt")
    out = []
    for path in files:
        try:
            out.append(intake(path))
        except IntakeError as exc:
            out.append({
                "jd_id": path.stem, "company": "读取失败", "title": "读取失败",
                "text": "", "source_url": "", "fetched_at": "", "stale": False,
                "city": "", "source": str(path), "error": str(exc),
            })
    return out


def age_note(jd: dict) -> str:
    """给报告用的一句提示。超期只提示，不影响判定。"""
    if jd.get("stale"):
        return f"⚠️ 该 JD 抓取于 {jd.get('fetched_at')}（{jd.get('days_old')} 天前），可能已失效，建议以官网为准"
    return ""


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


if __name__ == "__main__":
    demo = "岗位职责：负责 RAG 检索链路开发。任职要求：本科及以上学历，base 广州，3 年以上经验。"
    print(intake(demo))
