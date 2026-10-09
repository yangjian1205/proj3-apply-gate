# -*- coding: utf-8 -*-
"""图状态定义。

⚠️ 状态必须能被 checkpointer 序列化，所以这里**不放 CostLedger 对象**，
   只用 list[dict] 累积用量记录。
"""
from __future__ import annotations

from typing import Any, TypedDict


class TeamState(TypedDict, total=False):
    # ── 输入 ──────────────────────────────────────────────────────────
    jd: dict[str, Any]
    trace_id: str
    thread_id: str
    batch_mode: bool                  # 批量模式：reject 题直接落库，不挂审批

    # ── 判定 ──────────────────────────────────────────────────────────
    hard_result: dict[str, Any]
    match_result: dict[str, Any]
    final_verdict: str

    # ── 改写 ──────────────────────────────────────────────────────────
    resume_base: str
    draft: dict[str, Any]
    provenance_result: dict[str, Any]
    rewrite_round: int
    verify_failed: bool

    # ── 成本与审计 ────────────────────────────────────────────────────
    usage_records: list[dict[str, Any]]
    audit_trail: list[dict[str, Any]]

    # ── 人工 ──────────────────────────────────────────────────────────
    approval_status: str
    ticket_id: str
    approval_decision: dict[str, Any]

    # ── 调度 ──────────────────────────────────────────────────────────
    next_action: str
    planner_reason: str
    planner_used_model: bool
    steps: list[str]
    outcome: str
    record: dict[str, Any]


class TailorState(TypedDict, total=False):
    """tailor ⇄ provenance 子图的状态。

    子图只看见「改写这件事」，主图只把它当一个节点 —— 这就是用 subgraph 而不是平铺的原因：
    循环边被封装在内部，主图的条件边不会越来越乱。
    """
    jd: dict[str, Any]
    match_result: dict[str, Any]
    resume_base: str
    experience_bank: str
    routing: str
    draft: dict[str, Any]
    provenance_result: dict[str, Any]
    rewrite_round: int
    usage_records: list[dict[str, Any]]
    audit_trail: list[dict[str, Any]]
    trace_id: str
