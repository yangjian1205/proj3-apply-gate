# -*- coding: utf-8 -*-
"""图节点：planner（只调度）+ 六个能力单元 + 报告。

planner 的边界（v2 的核心设计）：
    **只决定下一步调谁，一个判断题都不做。**
    任何「该投 / 不该投 / 边界」的结论必须来自 hard-gate 或 match-score 的返回值，planner 只能转述。

四条硬规则由**代码**强制，不写进提示词：
    ① 不许自己下判定 —— 判定值只从 hard_result / match_result 取
    ② 不许跳过 provenance —— 没拿到 passed=true 走不到 approval
    ③ 不许自己决定停 —— rewrite_round >= 3 由子图代码强制退出
    ④ 不许出成稿 —— approval_status != approved 时 report 只写草稿

省钱的一处设计：候选动作只有一条合法路径时**不调模型**。
调度权交给模型的地方，是真正存在多个合理下一步的地方（比如改写策略选择）。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.types import interrupt  # noqa: E402

from core import config as cfg  # noqa: E402
from core import store  # noqa: E402
from core.llm import structured_call  # noqa: E402
from units import approval as approval_unit  # noqa: E402
from units.cost_report import impl as report_unit  # noqa: E402
from units.hard_gate.impl import check_hard_rules  # noqa: E402
from units.jd_intake.impl import age_note, intake  # noqa: E402
from units.match_score.impl import match  # noqa: E402

# ── 改写策略（planner 真正行使调度权的地方）──────────────────────────
DRAFT_STRATEGIES: dict[str, str] = {
    "project_first": "把项目经历提到最前面，工作经历压缩成简短一段 —— 适合看重项目完整度、年限要求宽松的岗位",
    "experience_first": "先写工作经历再写项目 —— 适合明确卡年限、重视在职经历的岗位",
    "balanced": "工作经历与项目经历各占一半 —— 适合两者都与岗位高度相关的情况",
}


def _trail(state, from_node: str, to_node: str, reason: str, payload=None) -> list:
    trail = list(state.get("audit_trail") or [])
    trail.append({
        "ts": store.now_iso(), "from": from_node, "to": to_node,
        "reason": reason, "payload_hash": store.payload_hash(payload or {}),
    })
    return trail


def _add_usage(state, usage: dict | None) -> list:
    usage_records = list(state.get("usage_records") or [])
    if usage:
        usage_records.append(usage)
    return usage_records


# ── planner ───────────────────────────────────────────────────────────
def compute_allowed_actions(state: dict) -> list[str]:
    """代码算出「合法动作集合」。判断题一个都不在这里做，只做流程约束。

    ⚠️ 终止判断必须放在最前面 —— 否则「硬门槛判死 → report」这类提前收口的分支
       会在 report 之后再次命中，把 report 无限重跑下去。
    """
    if state.get("record"):
        return ["stop"]                       # 报告已出，全流程结束

    # jd 可能是「粘贴的文本 / 文件路径 / 裸 dict」——intake 的产物一定带 jd_id。
    # ⚠️ 不能只用 isinstance(dict) 判断：调用方（HTTP 层）会传一个只有 text/company/title 的裸 dict，
    #    那样会被误判成「已归一化」，跳过 intake，后面 write_outputs 取 jd["jd_id"] 直接 KeyError。
    jd = state.get("jd")
    if not (isinstance(jd, dict) and jd.get("jd_id") and jd.get("text")):
        return ["intake"]
    if not state.get("hard_result"):
        return ["hard_gate"]

    hard = state["hard_result"]
    if hard.get("hard_verdict") == cfg.REJECT:
        return ["report"]                     # 硬门槛判死 → 不进打分与改写，一分钱不花

    if not state.get("match_result"):
        return ["match_score"]
    if state["match_result"].get("verdict") != cfg.APPLY:
        return ["report"]                     # 边界/不该投 → 不产出改写稿

    if state.get("verify_failed"):
        return ["report"]                     # 溯源 3 轮未过 → 不带病出稿

    if not state.get("draft"):
        # ← 这里存在多个合理策略，是 planner 真正该做调度的地方
        return ["draft"]

    prov = state.get("provenance_result") or {}
    if not prov.get("passed"):
        return ["report"]                     # 子图已把回退用完，出口只有报告

    if not state.get("approval_decision"):
        return ["approval"] if not state.get("batch_mode") else ["report"]

    if not state.get("record"):
        return ["report"]

    return ["stop"]


class _StrategyChoice(Any):
    pass


def _choose_strategy(state: dict, usage_sink: list) -> tuple[str, str]:
    """多个改写策略都合理时，交给模型选 —— 这才是「Agent 的调度价值」该出现的地方。"""
    from pydantic import BaseModel, Field

    class Strategy(BaseModel):
        strategy: str = Field(description="三选一：project_first / experience_first / balanced")
        reason: str = Field(description="一句话说明为什么选这个，不超过 40 字")

    jd = state["jd"]
    m = state.get("match_result") or {}
    hint = state["hard_result"].get("hints", [])
    prompt = (
        f"岗位：{jd.get('company')} · {jd.get('title')}\n"
        f"JD 年限相关提示：{[h['quote'] for h in hint] or '无'}\n"
        f"匹配分项：{m.get('score')}（相关度/职级/技能）\n\n"
        f"可选改写策略：\n"
        + "\n".join(f"- {k}：{v}" for k, v in DRAFT_STRATEGIES.items())
        + "\n\n这个岗位该用哪种策略改写简历？"
    )
    try:
        choice, usage, err = structured_call(
            [("system", "你是简历改写策略调度员，只输出策略选择，不改写内容。"), ("human", prompt)],
            Strategy, node="planner", retries=0,
        )
    except Exception as exc:  # noqa: BLE001
        choice, usage, err = None, None, str(exc)

    if usage:
        usage_sink.append(usage.as_dict())
    if choice is None or choice.strategy not in DRAFT_STRATEGIES:
        return "balanced", f"策略选择失败（{err}），回退到 balanced"
    return choice.strategy, choice.reason


def planner_node(state: dict) -> dict:
    allowed = compute_allowed_actions(state)
    steps = list(state.get("steps") or [])
    used_model = False
    extra_usage: list = []
    reason = ""

    if len(allowed) == 1:
        action = allowed[0]
        reason = f"唯一合法动作（代码判定）：{action}"
    else:
        action = allowed[0]
        reason = f"多候选 {allowed}，取默认 {action}"

    # 真正的调度决策：改写策略
    patch: dict[str, Any] = {}
    if action == "draft" and not state.get("draft_strategy"):
        strategy, why = _choose_strategy(state, extra_usage)
        patch["draft_strategy"] = strategy
        patch["strategy_reason"] = why
        used_model = True
        reason += f"；改写策略由模型选定：{strategy}（{why}）"
        steps.append("planner:choose_strategy")

    usage_records = list(state.get("usage_records") or []) + extra_usage
    steps.append(action)

    return {
        **patch,
        "next_action": action,
        "planner_reason": reason,
        "planner_used_model": used_model,
        "steps": steps,
        "usage_records": usage_records,
        "audit_trail": _trail(state, "planner", action, reason),
    }


def route_planner(state: dict) -> str:
    return state.get("next_action") or "stop"


# ── 各能力单元节点 ────────────────────────────────────────────────────
def intake_node(state: dict) -> dict:
    jd = intake(state["jd"]) if isinstance(state.get("jd"), (str, Path)) else intake(state["jd"])
    return {
        "jd": jd,
        "audit_trail": _trail(state, "intake", "planner",
                              f"JD 归一化完成：{jd['company']} / {jd['title']}"
                              + ("（超期）" if jd["stale"] else "")),
    }


def hard_gate_node(state: dict) -> dict:
    jd = state["jd"]
    result = check_hard_rules(jd["text"])
    return {
        "hard_result": result,
        "audit_trail": _trail(
            state, "hard_gate", "planner",
            f"硬门槛判定：{result['hard_verdict']}，命中 {len(result['flags'])} 条"
            + (f"，只提示 {len(result['hints'])} 条" if result["hints"] else ""),
            {"flags": result["flags"]},
        ),
    }


def match_score_node(state: dict) -> dict:
    jd = state["jd"]
    profile = cfg.load_profile()
    resume = cfg.load_resume()
    bank = cfg.load_experience_bank()

    result = match(jd, profile, resume,
                   hard_result=state.get("hard_result"),
                   experience_bank=bank,
                   trace_id=state.get("trace_id", ""))

    return {
        "match_result": result,
        "final_verdict": result["verdict"],
        "usage_records": _add_usage(state, result.get("usage")),
        "audit_trail": _trail(
            state, "match_score", "planner",
            f"五态={result['stage_5']} → 三分类={result['verdict']}"
            + ("（降级：模型输出不合 schema）" if result["degraded"] else ""),
            {"score": result["score"]},
        ),
    }


def draft_node(state: dict) -> dict:
    """调 tailor ⇄ provenance 子图，把结果并回主状态。"""
    from units.tailor.impl import TailorError
    from graph.subgraph_tailor import build_tailor_subgraph

    jd = state["jd"]
    strategy = state.get("draft_strategy", "balanced")
    routing = ""
    if cfg.PROFILE_DIR.joinpath("resume_routing.md").exists():
        routing = cfg.PROFILE_DIR.joinpath("resume_routing.md").read_text(encoding="utf-8")
    routing += f"\n\n【本次改写策略（planner 指定）】{strategy}：{DRAFT_STRATEGIES.get(strategy, '')}"
    routing += f"\n【策略选择理由】{state.get('strategy_reason', '—')}"

    sub = build_tailor_subgraph()
    sub_state = {
        "jd": jd,
        "match_result": state.get("match_result") or {},
        "resume_base": cfg.load_resume(),
        "experience_bank": cfg.load_experience_bank(),
        "routing": routing,
        "rewrite_round": 0,
        "usage_records": [],
        "audit_trail": [],
        "trace_id": state.get("trace_id", ""),
    }

    try:
        # 显式设 recursion_limit：默认 10007，一旦死循环会白跑两分多钟才报错
        out = sub.invoke(sub_state, {"recursion_limit": 12})
    except TailorError as exc:
        return {
            "provenance_result": {"passed": False, "missed": [str(exc)], "atoms": [],
                                  "atom_count": 0, "by_type": {"A": 0, "B": 0, "C": 0},
                                  "stage_violations": [], "ceileng": ""},
            "verify_failed": True,
            "audit_trail": _trail(state, "tailor_subgraph", "planner", f"改写失败：{exc}"),
        }

    prov = out.get("provenance_result") or {}
    verify_failed = not prov.get("passed")
    usage_records = list(state.get("usage_records") or []) + list(out.get("usage_records") or [])
    trail = list(state.get("audit_trail") or []) + list(out.get("audit_trail") or [])

    return {
        "draft": out.get("draft"),
        "provenance_result": prov,
        "rewrite_round": out.get("rewrite_round", 0),
        "verify_failed": verify_failed,
        "usage_records": usage_records,
        "audit_trail": trail + [{
            "ts": store.now_iso(), "from": "tailor_subgraph", "to": "planner",
            "reason": f"子图退出：{out.get('rewrite_round', 0)} 轮，"
                      f"溯源{'通过' if not verify_failed else '未通过'}",
            "payload_hash": store.payload_hash(prov.get("missed", [])),
        }],
    }


def approval_node(state: dict) -> dict:
    """interrupt 挂起等人点头。

    ⚠️ interrupt() 恢复时本节点会**从头重跑** —— 所以这里的副作用必须幂等。
       create_ticket 用 INSERT OR REPLACE，重复执行不会产生第二条待办。
    """
    jd = state["jd"]
    trace_id = state.get("trace_id", "")
    thread_id = state.get("thread_id", "")
    # 单号要稳定（interrupt 恢复时节点会从头重跑，不能每次都生成新号）
    ticket_id = state.get("ticket_id") or f"t-{jd['jd_id']}-{trace_id[-6:]}"

    conn = store.connect()
    try:
        store.create_ticket(conn, ticket_id, trace_id, jd["jd_id"], thread_id)
        counts = {
            "audit": store.row_count(conn, "audit"),
            "tickets": store.row_count(conn, "tickets"),
        }
    finally:
        conn.close()

    payload = {
        "ticket_id": ticket_id,
        "jd_id": jd["jd_id"],
        "company": jd.get("company"),
        "title": jd.get("title"),
        "verdict": (state.get("match_result") or {}).get("verdict"),
        "verdict_5": (state.get("match_result") or {}).get("verdict_5")
        or (state.get("match_result") or {}).get("stage_5"),
        "provenance_passed": (state.get("provenance_result") or {}).get("passed"),
        "rewrite_round": state.get("rewrite_round"),
        "changes": (state.get("draft") or {}).get("changes", []),
        "message": "稿件状态：待审批（不是「可投递」）。批准后才会写出 output/<jd_id>/resume.md",
    }

    decision = interrupt(payload)  # ← 恢复时从这个节点开头重跑

    approved = bool((decision or {}).get("approved"))
    reason = (decision or {}).get("reason", "")

    return {
        "ticket_id": ticket_id,
        "approval_decision": decision or {},
        "approval_status": cfg.APPROVAL_APPROVED if approved else cfg.APPROVAL_REJECTED,
        "audit_trail": _trail(
            state, "approval", "planner",
            f"人工{'批准' if approved else '驳回'}：{reason or '（无理由）'}"
            + f"（待办 {counts['tickets']} 条）",
            decision or {},
        ),
    }


def report_node(state: dict) -> dict:
    jd = state["jd"]
    hard = state.get("hard_result") or {}
    m = state.get("match_result") or {}
    # 硬门槛判死的题不会进打分环节，但结论必须照样是个三分类 —— 由代码补，不交给模型
    if not m and hard.get("hard_verdict") == cfg.REJECT:
        m = {
            "verdict": cfg.REJECT, "stage_5": "真实缺口", "score": {},
            "score_total": 0, "reasons": [], "evidence": [], "missing_info": [],
            "gap_anchor": "", "degraded": False,
        }
    prov = state.get("provenance_result") or {}
    draft = state.get("draft")
    trace_id = state.get("trace_id", "")

    # 成本直接从 usage_records 汇总 —— 状态里存的就是 dict，不要再包装成假对象
    usage_records = list(state.get("usage_records") or [])
    cost = {
        "calls": len(usage_records),
        "cost_cny": round(sum(r.get("cost_cny", 0) for r in usage_records), 6),
        "input_tokens": sum(r.get("input_tokens", 0) for r in usage_records),
        "output_tokens": sum(r.get("output_tokens", 0) for r in usage_records),
        "elapsed": round(sum(r.get("elapsed", 0) for r in usage_records), 3),
        "by_node": usage_records,
    }

    # 审批状态：批量模式（评测/对照实验）从来不出稿、也不挂审批，必须标 not_needed。
    # ⚠️ 早先漏了这个分支，导致批跑出来的稿子在看板上全显示「待审批」——
    #    但 tickets 表里根本没有对应待办，是纯误导（正好违反了 CONTRACT.md 那条
    #    「审批状态以 audit.db 为准，不以快照为准」的设计初衷）。
    if state.get("approval_status"):
        approval_status = state["approval_status"]
    elif state.get("batch_mode"):
        approval_status = cfg.APPROVAL_NOT_NEEDED
    elif hard.get("hard_verdict") == cfg.REJECT or m.get("verdict") != cfg.APPLY:
        approval_status = cfg.APPROVAL_NOT_NEEDED
    else:
        approval_status = cfg.APPROVAL_PENDING

    meta = report_unit.write_outputs(
        jd, hard, m, draft=draft, provenance_result=prov or None,
        approval_status=approval_status, ticket_id=state.get("ticket_id"),
        cost=cost, trace_id=trace_id,
    )

    record = report_unit.build_record(
        jd, hard, m, draft=draft, provenance_result=prov or None,
        approval_status=approval_status, ticket_id=state.get("ticket_id"),
        cost=cost, elapsed=cost["elapsed"], audit=state.get("audit_trail") or [],
        trace_id=trace_id,
    )

    conn = store.connect()
    try:
        store.save_judgement(conn, record)
        for u in usage_records:
            store.log_cost(conn, trace_id, u)
        for entry in state.get("audit_trail") or []:
            store.log_audit(conn, trace_id, entry.get("from", ""), entry.get("to", ""),
                            entry.get("reason", ""), entry.get("payload_hash", ""))
        persisted = {"audit": store.row_count(conn, "audit"),
                     "cost": store.row_count(conn, "cost"),
                     "judgements": store.row_count(conn, "judgements")}
    finally:
        conn.close()

    outcome = {
        "verdict": m.get("verdict"),
        "stage_5": m.get("stage_5"),
        "approval_status": approval_status,
        "verify_failed": bool(state.get("verify_failed")),
        "cost": cost,
        "persisted": persisted,
        "written": meta["written"],
        "resume_path": meta["resume_path"],
        "age_note": age_note(jd),
    }

    return {
        "record": record,
        "outcome": outcome,
        "audit_trail": _trail(state, "report", "END",
                              f"落库完成：{persisted}，审批状态 {approval_status}"),
    }
