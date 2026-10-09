# -*- coding: utf-8 -*-
"""文本规范化 —— 溯源匹配的前置步骤。

为什么必须做这一层：
    改写稿写「约 3 年经验」，原简历写「3 年经验」；改写稿写「LANGGRAPH」，原简历写「LangGraph」；
    改写稿写「一百多个知识块」，原简历写「214 个知识块」。
    不做规范化，这些全都会被判成「找不到出处」→ 误拦 → 硬指标②变成假数字。
"""
from __future__ import annotations

import re
import unicodedata

# ── 中文数字 → 阿拉伯数字 ──────────────────────────────────────────────
_CN_DIGIT = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}


def _cn_segment_to_int(seg: str) -> int | None:
    """把纯中文数字段（如「三十五」「两百」）转成整数；转不了返回 None。"""
    if not seg:
        return None
    total = 0
    section = 0
    number = 0
    for ch in seg:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if unit >= 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        else:
            return None
    return total + section + number


_CN_NUM_RE = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+")


def cn_to_arabic(text: str) -> str:
    """把文本里的中文数字替换成阿拉伯数字。"""
    def repl(m: re.Match) -> str:
        value = _cn_segment_to_int(m.group(0))
        return str(value) if value is not None else m.group(0)

    return _CN_NUM_RE.sub(repl, text)


# ── 全角 → 半角 ────────────────────────────────────────────────────────
def to_halfwidth(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def strip_control(text: str) -> str:
    """去掉不可见字符（PDF 提取常带 \u200b、软连字符），但保留换行。"""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Cc") and ch not in ("\n", "\t"):
            continue
        out.append(ch)
    return "".join(out)


# ── 模糊词 ────────────────────────────────────────────────────────────
_FUZZY_RE = re.compile(r"(约|大约|超过|超|近|左右|上下|余|多|不到|接近|将近)")
_GOAL_FUZZY_RE = re.compile(r"(以上|以下|及以上|及以下|>=|<=)")


def strip_fuzzy(text: str) -> str:
    """去掉模糊限定词。「以上 / 以下」不删 —— 它们改变语义方向。"""
    return _FUZZY_RE.sub("", text)


# ── 同义词表（写成一张扁平表，命中即双向归一）─────────────────────────
SYNONYMS: dict[str, str] = {
    "pytorch": "pytorch", "torch": "pytorch",
    "langgraph": "langgraph", "lg": "langgraph",
    "langchain": "langchain",
    "fastapi": "fastapi", "fast api": "fastapi",
    "llm": "llm", "大模型": "llm", "大语言模型": "llm",
    "rag": "rag", "检索增强": "rag", "检索增强生成": "rag",
    "agent": "agent", "智能体": "agent", "代理": "agent",
    "prompt": "prompt", "提示词": "prompt",
    "embedding": "embedding", "向量化": "embedding", "嵌入": "embedding",
    "向量数据库": "vector-db", "向量库": "vector-db",
    "chroma": "chroma", "chromadb": "chroma",
    "bm25": "bm25", "rrf": "rrf",
    "hit@5": "hit@k", "hit@3": "hit@k", "hit@10": "hit@k", "命中率": "hit@k",
    "mrr": "mrr",
    "function calling": "function-calling", "函数调用": "function-calling", "工具调用": "function-calling",
    "human-in-the-loop": "hitl", "human in the loop": "hitl", "人工审批": "hitl", "人审": "hitl", "人工审核": "hitl",
    "interrupt": "interrupt", "挂起": "interrupt", "中断": "interrupt",
    "checkpointer": "checkpointer", "检查点": "checkpointer",
    "sqlite": "sqlite", "sqlite3": "sqlite",
    "postgres": "postgres", "postgresql": "postgres",
    "mcp": "mcp", "model context protocol": "mcp",
    "docker": "docker", "容器化": "docker",
    "docker-compose": "docker", "docker compose": "docker",
    "k8s": "kubernetes", "kubernetes": "kubernetes",
    "asyncio": "asyncio", "异步": "asyncio",
    "pydantic": "pydantic",
    "numpy": "numpy",
    "git": "git", "github": "github",
    "deepseek": "deepseek",
    "qwen": "qwen", "通义千问": "qwen", "通义": "qwen",
    "bert": "bert", "seqeval": "seqeval", "lora": "lora",
    "transformers": "transformers", "hugging face": "transformers", "huggingface": "transformers",
    "kafka": "kafka", "redis": "redis", "spring": "spring", "k8s生态": "k8s",
    "react": "react", "vue": "vue",
    "linux": "linux", "windows": "windows",
    "jsonl": "jsonl", "json": "json",
    "sql": "sql", "mysql": "mysql", "excel": "excel", "tableau": "tableau",
    "pmp": "pmp", "cisp": "cisp", "cpa": "cpa", "注册会计师": "cpa",
    "cet-6": "cet6", "cet6": "cet6", "英语六级": "cet6", "大学英语六级": "cet6",
    "n1": "jlpt-n1", "日语n1": "jlpt-n1", "日语一级": "jlpt-n1",
}

# 这些词在语义上是「同一类能力」，用于 B 类能力断言的宽松匹配
_ALIAS_ORDER = sorted(SYNONYMS.items(), key=lambda kv: -len(kv[0]))


def apply_synonyms(text: str) -> str:
    for raw, canon in _ALIAS_ORDER:
        if raw in text:
            text = text.replace(raw, canon)
    return text


# ── 主入口 ────────────────────────────────────────────────────────────
def normalize(text: str, *, fuzzy: bool = True, synonyms: bool = True) -> str:
    """完整规范化链路：控制字符 → 全角转半角 → 大小写 → 同义词 → 中文数字 → 去模糊词。

    ⚠️ 顺序很关键：**同义词必须先于中文数字转换**。
    否则「英语六级」会先被转成「英语6级」，同义词表里的「英语六级」再也匹配不上，
    等级类表述（六级 / 一级 / 八级）会整批失效 —— 这是实测踩过的坑。

    ⚠️ 用途限制：本函数**会改变字符串长度**（中文数字→阿拉伯、多空格→单空格），
    所以它的输出**不能用来做引文切片**（切片会错位）。
    引文一律在原文上做，见 `units/hard_gate/impl.py`。

    匹配时两端都过这一遍，就能容忍「约 3 年」vs「3 年」、「LANGGRAPH」vs「langgraph」这类差异。
    """
    out = strip_control(text)
    out = to_halfwidth(out)
    out = out.lower()
    out = re.sub(r"\s+", " ", out)
    if synonyms:
        out = apply_synonyms(out)
    out = cn_to_arabic(out)
    if fuzzy:
        out = strip_fuzzy(out)
    return out.strip()


def sentence_of(text: str, start: int, end: int) -> str:
    """取包含 [start, end) 的那一整句 —— 用来生成「JD 原句引文」。

    引文必须是 jd_text 的子串，这是硬指标①的自动校验前提，
    所以这里只能做切片，绝不能重写、清洗或拼接。
    """
    bounds = "。！？；\n"
    left = max((text.rfind(ch, 0, start) for ch in bounds), default=-1)
    right_candidates = [text.find(ch, end) for ch in bounds if text.find(ch, end) != -1]
    right = min(right_candidates) if right_candidates else len(text)
    return text[left + 1:right + 1].strip() if right > left else text[start:end]


def snippet(text: str, needle: str, width: int = 40) -> str:
    """没匹配到句子时的兜底：以 needle 为中心切一小段原文（仍是子串）。"""
    idx = text.find(needle)
    if idx == -1:
        return needle
    lo = max(0, idx - width)
    hi = min(len(text), idx + len(needle) + width)
    return text[lo:hi].strip()
