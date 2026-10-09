# -*- coding: utf-8 -*-
"""tailor ⇄ provenance 子图。

为什么封成子图而不是平铺节点：
    ① 平铺的话，「改写不过就回退重写」这条循环边会把主图的条件边搅乱；
       子图把「改写这件事」封成一个可复用单元，主图只看见一个节点。
    ② 循环上限（3 轮）在子图内部由**代码**强制，不依赖模型自觉。

两个必须记住的点：
    ⚠️ interrupt 恢复时节点会从头重跑 —— 本子图没有 interrupt，不受影响。
    ⚠️ 回退边必须带轮数上限，否则假模型会让它死循环（递归上限默认 10007，能白跑两分多钟）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.graph import END, START, StateGraph  # noqa: E402

from core import config as cfg  # noqa: E402
from core import store  # noqa: E402
from graph.state import TailorState  # noqa: E402
from units.provenance.impl import ProvenanceError, verify  # noqa: E402
from units.tailor.impl import TailorError, tailor  # noqa: E402

MAX_ROUND = None  # 运行时从 rules.json 的 thresholds.rewrite_max_round 取


def _max_round() -> int:
    return int(cfg.load_rules()["thresholds"].get("rewrite_max_round", 3))


def _audit(state: TailorState, from_node: str, to_node: str, reason: str, payload=None) -> list:
    trail = list(state.get("audit_trail") or [])
    trail.append({
        "ts": store.now_iso(), "from": from_node, "to": to_node,
        "reason": reason, "payload_hash": store.payload_hash(payload or {}),
    })
    return trail


def tailor_node(state: TailorState) -> dict:
    """改写节点。带上一轮的 missed 清单重写。"""
    round_no = int(state.get("rewrite_round") or 0)
    missed = (state.get("provenance_result") or {}).get("missed", [])

    out = tailor(
        state["jd"], state.get("match_result") or {}, state["resume_base"],
        experience_bank=state.get("experience_bank", ""),
        routing=state.get("routing", ""),
        missed=missed, rewrite_round=round_no,
        trace_id=state.get("trace_id", ""),
    )

    usage = list(state.get("usage_records") or [])
    if out.get("usage"):
        usage.append(out["usage"])

    return {
        "draft": out,
        "rewrite_round": round_no + 1,
        "usage_records": usage,
        "audit_trail": _audit(state, "tailor", "provenance",
                              f"第 {round_no + 1} 轮改写完成，带 {len(missed)} 项未命中清单" if missed
                              else f"第 {round_no + 1} 轮改写完成",
                              {"changes": out.get("changes", [])}),
    }


def verify_node(state: TailorState) -> dict:
    """溯源节点。纯代码，不调模型。"""
    draft = state.get("draft") or {}
    result = verify(
        draft.get("resume_md", ""),
        state["resume_base"],
        jd_text=(state.get("jd") or {}).get("text", ""),
    )
    result["rewrite_round"] = int(state.get("rewrite_round") or 0)

    return {
        "provenance_result": result,
        "audit_trail": _audit(
            state, "provenance",
            "tailor" if not result["passed"] else "EXIT",
            f"溯源{'通过' if result['passed'] else '未通过'}：{result['atom_count']} 个原子，"
            f"未命中 {len(result['missed'])} 项，性质升格 {len(result['stage_violations'])} 处",
            {"missed": result["missed"]},
        ),
    }


def route_after_verify(state: TailorState) -> str:
    """回退判定 —— 上限由代码强制，模型没有「再试一次」的权限。"""
    result = state.get("provenance_result") or {}
    if result.get("passed"):
        return "exit"
    if int(state.get("rewrite_round") or 0) >= _max_round():
        return "exit"  # 轮数用完 → 退出子图并标记未通过，不产出可用稿
    return "again"


def build_tailor_subgraph():
    g = StateGraph(TailorState)
    g.add_node("tailor", tailor_node)
    g.add_node("verify", verify_node)
    g.add_edge(START, "tailor")
    g.add_edge("tailor", "verify")
    g.add_conditional_edges("verify", route_after_verify, {"again": "tailor", "exit": END})
    return g.compile()


__all__ = [
    "build_tailor_subgraph", "tailor_node", "verify_node", "route_after_verify",
    "TailorError", "ProvenanceError", "_max_round",
]
