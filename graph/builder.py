# -*- coding: utf-8 -*-
"""主图装配。

结构：
    START → planner ⇄ {intake, hard_gate, match_score, draft, approval, report} → END

    planner 是唯一的入口与回程点，它只决定「下一步调谁」；
    draft 节点内部是 tailor ⇄ provenance 子图（回退边被封装在子图里，主图看不见循环）。

为什么用 conditional_edges 而不是把顺序写死：
    v1 是「一个工头 + 七个工位」的固定流水线，顺序写死。
    v2 把顺序拆掉 —— 硬门槛判死时根本不会经过 match_score，边界题不会经过 draft，
    路径由状态决定，不由代码顺序决定。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.graph import END, START, StateGraph  # noqa: E402

from graph.nodes import (approval_node, draft_node, hard_gate_node,  # noqa: E402
                         intake_node, match_score_node, planner_node,
                         report_node, route_planner)
from graph.state import TeamState  # noqa: E402

NODES = {
    "intake": intake_node,
    "hard_gate": hard_gate_node,
    "match_score": match_score_node,
    "draft": draft_node,
    "approval": approval_node,
    "report": report_node,
}

# 显式给递归上限：不设的话默认 10007，一旦出问题会白跑两分多钟才报错
DEFAULT_RECURSION_LIMIT = 30


def build_graph(checkpointer=None, *, interrupt_before=None):
    g = StateGraph(TeamState)
    g.add_node("planner", planner_node)
    for name, fn in NODES.items():
        g.add_node(name, fn)

    g.add_edge(START, "planner")
    g.add_conditional_edges(
        "planner", route_planner,
        {**{name: name for name in NODES}, "stop": END},
    )
    for name in NODES:
        g.add_edge(name, "planner")

    return g.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)


def make_config(thread_id: str) -> dict:
    """带 checkpointer 的图，第二参必须是这个形状的 dict —— 传字符串会报
    `AttributeError: 'str' object has no attribute 'items'`。"""
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": DEFAULT_RECURSION_LIMIT,
    }


def build_with_sqlite():
    """生产用：SQLite checkpointer（跨进程可恢复）。

    ⚠️ SqliteSaver 的锁不跨进程，所以 FastAPI 侧不要开 `--workers 2`。
    """
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    cfg_db = sqlite3.connect(str(__import__("core.config", fromlist=["config"]).CHECKPOINT_DB),
                             check_same_thread=False)
    saver = SqliteSaver(cfg_db)  # 自动建表，不要手写 setup()
    return build_graph(checkpointer=saver), cfg_db
