# -*- coding: utf-8 -*-
"""模型调用封装 + 成本记账。

两个必须记住的坑（都是实测踩过的）：

1. **`with_structured_output` 必须显式传 `method="function_calling"`**
   —— 默认走 json_schema，DeepSeek 会直接返回 400。

2. **要拿 token 用量必须传 `include_raw=True`**
   —— 否则 invoke 只返回 pydantic 对象，usage 直接丢掉，成本就记不了。

价格出处：https://api-docs.deepseek.com/zh-cn/quick_start/pricing
    读取日期 2026-10-06。价格以官方页面为准，本文件只是本地副本；
    官方调整价格后，改这里（或改 .env 覆盖）即可，不用动业务代码。
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config as cfg  # noqa: E402

load_dotenv(cfg.ROOT / ".env")

T = TypeVar("T", bound=BaseModel)

# ── 价格：元 / 百万 tokens ────────────────────────────────────────────
# 出处：https://api-docs.deepseek.com/zh-cn/quick_start/pricing（2026-10-06 读取）
PRICES_CNY_PER_MTOKEN: dict[str, dict[str, dict[str, float]]] = {
    "deepseek-flash": {
        "peak":    {"input_cache_hit": 0.04, "input_cache_miss": 2.0, "output": 8.0},
        "offpeak": {"input_cache_hit": 0.02, "input_cache_miss": 1.0, "output": 4.0},
    },
    "deepseek-v4-pro": {
        "peak":    {"input_cache_hit": 0.30, "input_cache_miss": 9.0, "output": 27.0},
        "offpeak": {"input_cache_hit": 0.15, "input_cache_miss": 4.5, "output": 13.5},
    },
}
# 旧模型名仍可调用但已下线，按 flash 档计价
PRICES_CNY_PER_MTOKEN["deepseek-chat"] = PRICES_CNY_PER_MTOKEN["deepseek-flash"]

BJ = timezone(timedelta(hours=8))
PEAK_WINDOWS = ((9, 0, 12, 0), (14, 0, 18, 0))


def is_peak(dt: datetime | None = None) -> bool:
    """北京时间周一至周五 9:00-12:00、14:00-18:00 为高峰时段，其余为空闲时段。

    ⚠️ 已知不精确：「不含中国法定节假日」这一条没有实现（需要节假日日历），
    所以节假日会被误判成高峰 → 成本**略微高估**。宁可高估不可低估。
    """
    now = (dt or datetime.now(tz=BJ)).astimezone(BJ)
    if now.weekday() >= 5:
        return False
    hm = (now.hour, now.minute)
    return any((h1, m1) <= hm < (h2, m2) for h1, m1, h2, m2 in PEAK_WINDOWS)


def compute_cost(model: str, input_tokens: int, output_tokens: int,
                 cache_hit_tokens: int = 0, *, when: datetime | None = None) -> tuple[float, bool]:
    """返回 (成本元, 是否高峰时段)。未知模型按 flash 计价，不静默给 0。"""
    table = PRICES_CNY_PER_MTOKEN.get(model) or PRICES_CNY_PER_MTOKEN["deepseek-flash"]
    peak = is_peak(when)
    unit = table["peak" if peak else "offpeak"]
    miss = max(input_tokens - cache_hit_tokens, 0)
    cost = (
        miss / 1_000_000 * unit["input_cache_miss"]
        + cache_hit_tokens / 1_000_000 * unit["input_cache_hit"]
        + output_tokens / 1_000_000 * unit["output"]
    )
    return round(cost, 6), peak


# ── 用量记录 ──────────────────────────────────────────────────────────
@dataclass
class Usage:
    node: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cost_cny: float = 0.0
    peak: bool = False
    elapsed: float = 0.0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "node": self.node, "model": self.model,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_hit_tokens": self.cache_hit_tokens, "cost_cny": self.cost_cny,
            "peak": self.peak, "elapsed": round(self.elapsed, 3), "note": self.note,
        }


@dataclass
class CostLedger:
    """一次 judge 跑完全程的账本。记账失败不阻塞主流程，但写库失败要报错（不许静默）。"""
    trace_id: str = ""
    usages: list[Usage] = field(default_factory=list)

    def add(self, usage: Usage) -> None:
        self.usages.append(usage)

    @property
    def total_cost(self) -> float:
        return round(sum(u.cost_cny for u in self.usages), 6)

    @property
    def total_calls(self) -> int:
        return len(self.usages)

    def summary(self) -> dict[str, Any]:
        return {
            "calls": self.total_calls,
            "cost_cny": self.total_cost,
            "input_tokens": sum(u.input_tokens for u in self.usages),
            "output_tokens": sum(u.output_tokens for u in self.usages),
            "elapsed": round(sum(u.elapsed for u in self.usages), 3),
            "by_node": [u.as_dict() for u in self.usages],
        }


class LLMError(RuntimeError):
    """模型侧不可用 / 返回不合规。"""


def get_model_name() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-flash")


def get_llm(temperature: float = 0.0, model: str | None = None, *, thinking: bool = False):
    """构造模型客户端。

    ⚠️ **默认关闭思考模式**，这是实测逼出来的决定（2026-10-06）：
        deepseek-flash 默认开思考模式（effort=high），而思考模式不支持 `tool_choice`，
        于是 `with_structured_output` 一定报 400：
            「Thinking mode does not support this tool_choice」
        关掉之后顺带解决另外两件事：
          ① `temperature` 才生效（思考模式会忽略它）→ 五态判定才稳定；
          ② 省掉 reasoning token 的输入输出费用与等待时间。
    出处：https://api-docs.deepseek.com/zh-cn/guides/thinking_mode

    需要长链推理的场景可以传 thinking=True 打开，但那时不能用结构化输出。
    """
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise LLMError("缺少 DEEPSEEK_API_KEY —— 检查项目根目录 .env（该文件不入库）")
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {}
    if not thinking:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    return ChatOpenAI(
        model=model or get_model_name(),
        api_key=key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        temperature=temperature,
        timeout=120,
        max_retries=1,
        **kwargs,
    )


def _usage_from_raw(raw: Any, node: str, model: str, elapsed: float) -> Usage:
    meta = getattr(raw, "usage_metadata", None) or {}
    response_meta = getattr(raw, "response_metadata", None) or {}
    token_usage = response_meta.get("token_usage", {}) if isinstance(response_meta, dict) else {}
    input_tokens = int(meta.get("input_tokens") or token_usage.get("prompt_tokens") or 0)
    output_tokens = int(meta.get("output_tokens") or token_usage.get("completion_tokens") or 0)
    details = meta.get("input_token_details") or token_usage.get("prompt_tokens_details") or {}
    cache_hit = int(details.get("cache_read") or details.get("cached_tokens") or 0)
    cost, peak = compute_cost(model, input_tokens, output_tokens, cache_hit)
    return Usage(node=node, model=model, input_tokens=input_tokens,
                 output_tokens=output_tokens, cache_hit_tokens=cache_hit,
                 cost_cny=cost, peak=peak, elapsed=elapsed)


def structured_call(
    messages: list,
    schema: type[T],
    *,
    node: str,
    ledger: CostLedger | None = None,
    llm=None,
    retries: int = 1,
) -> tuple[T | None, Usage | None, str]:
    """带 schema 的模型调用。

    返回 (解析结果, 用量, 错误信息)。
    解析失败会重试 `retries` 次；仍然失败 **不抛异常**，返回 (None, usage, 错误)，
    由调用方决定降级策略（match-score 会降级成「待确认」交人工，而不是猜一个答案）。
    """
    llm = llm or get_llm()
    model = getattr(llm, "model_name", None) or getattr(llm, "model", None) or get_model_name()
    # ⚠️ method="function_calling" 必须显式传，否则 DeepSeek 报 400
    runnable = llm.with_structured_output(schema, method="function_calling", include_raw=True)

    last_err = ""
    for attempt in range(retries + 1):
        started = time.perf_counter()
        try:
            result = runnable.invoke(messages)
        except Exception as exc:  # noqa: BLE001 —— 网络/400 都归这一类，交调用方降级
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt >= retries:
                return None, None, last_err
            time.sleep(1.5)
            continue

        elapsed = time.perf_counter() - started
        raw = result.get("raw")
        usage = _usage_from_raw(raw, node, model, elapsed) if raw is not None else None
        if ledger is not None and usage is not None:
            ledger.add(usage)

        parsed = result.get("parsed")
        if parsed is not None:
            return parsed, usage, ""

        last_err = f"schema 解析失败：{result.get('parsing_error')}"
        if attempt >= retries:
            return None, usage, last_err

    return None, None, last_err


def text_call(
    messages: list,
    *,
    node: str,
    ledger: CostLedger | None = None,
    llm=None,
) -> tuple[str, Usage | None]:
    """纯文本调用（不要求结构化输出），用于 tailor 生成改写稿。"""
    llm = llm or get_llm()
    model = getattr(llm, "model_name", None) or getattr(llm, "model", None) or get_model_name()
    started = time.perf_counter()
    resp = llm.invoke(messages)
    elapsed = time.perf_counter() - started
    usage = _usage_from_raw(resp, node, model, elapsed)
    if ledger is not None:
        ledger.add(usage)
    content = resp.content
    if isinstance(content, list):  # 某些返回是 content block 列表
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return content, usage
