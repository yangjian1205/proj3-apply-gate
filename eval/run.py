#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L3 · 模型行为指标：跑评测集出数字。

⚠️ 这一层**只出数字，不设门禁**，这是本项目和「调参到好看为止」类项目的分界线：

    受模型影响的指标不适合当门禁 —— LLM 输出受模型版本、温度、系统提示多重影响，
    单次结果不稳定。混着跑的直接后果是：你改了一行正则导致准确率波动，
    分不清是正则的锅还是模型随机性的锅，然后就会去调 prompt，把本来正确的东西调坏。

    所以：①②③ 是门禁（scripts/gate.py，纯代码，每次改代码都跑）；
        ④ 和观测项是指标（就是这个文件，只在改了 prompt / 换了模型之后跑）。

四个硬指标：
    ① 硬门槛漏放率      = 0%（这里复算一遍，与 gate.py 互为双保险）
    ② 事实可溯源率      = 100%
    ③ 未审批不放行率    = 0%
    ④ 三分类准确率      ≥ 85%（边界召回 ≥ 80%）
观测项：误杀率 / 单题成本 / 单题耗时 / 平均重写轮数
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfg  # noqa: E402
from core import store  # noqa: E402


def evaluate_one(graph, jd: dict, *, trace_id: str) -> dict:
    """跑一条 JD 的完整链路，返回可比对的摘要。"""
    thread_id = f"ev-{trace_id}-{jd['jd_id']}"
    init = {
        "jd": jd, "trace_id": trace_id, "thread_id": thread_id,
        "batch_mode": True,             # 批跑不挂审批：③ 要测的是「没审批会不会放行」
        "usage_records": [], "audit_trail": [], "steps": [],
    }
    started = time.perf_counter()
    state = graph.invoke(init, {"configurable": {"thread_id": thread_id}, "recursion_limit": 30})
    elapsed = time.perf_counter() - started

    m = state.get("match_result") or {}
    hard = state.get("hard_result") or {}
    prov = state.get("provenance_result") or {}
    out = state.get("outcome") or {}
    hard_reject = hard.get("hard_verdict") == cfg.REJECT

    verdict = m.get("verdict") or (cfg.REJECT if hard_reject else None)
    stage5 = m.get("stage_5") or ("真实缺口" if hard_reject else None)
    usage = state.get("usage_records") or []
    approval = out.get("approval_status") or (
        cfg.APPROVAL_NOT_NEEDED if verdict != cfg.APPLY else cfg.APPROVAL_PENDING
    )
    released = bool(out.get("resume_path"))  # 有没有真的写出「可投递稿」

    atom_total = prov.get("atom_count", 0)
    atom_found = len(prov.get("evidence_map", [])) if prov else 0

    return {
        "jd_id": jd["jd_id"],
        "gold_label": jd["gold_label"],
        "gold_label_5": jd["gold_label_5"],
        "pred_label": verdict,
        "pred_label_5": stage5,
        "correct": verdict == jd["gold_label"],
        "hard_expected": bool(jd.get("is_hard_threshold")),
        "hard_flags": hard.get("flags", []),
        "hard_leaked": bool(jd.get("is_hard_threshold")) and not hard.get("flags"),
        "provenance_passed": prov.get("passed") if prov else None,
        "atom_total": atom_total,
        "atom_found": atom_found,
        "stage_violations": len(prov.get("stage_violations", [])) if prov else 0,
        "rewrite_round": state.get("rewrite_round", 0) or 0,
        "approval_status": approval,
        "released_without_approval": released and approval != cfg.APPROVAL_APPROVED,
        "cost_cny": round(sum(u.get("cost_cny", 0) for u in usage), 6),
        "calls": len(usage),
        "elapsed": round(elapsed, 3),
        "degraded": bool(m.get("degraded")),
        "error": None,
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"total": 0}

    gold = Counter(r["gold_label"] for r in rows)
    # ① 漏放率
    hard_rows = [r for r in rows if r["hard_expected"]]
    leaked = [r for r in hard_rows if r["hard_leaked"]]
    # ② 事实可溯源率：只统计**最终放行**的稿子。
    #    没通过溯源的稿子会被拦下、不产出可投递稿（这正是设计要的行为），
    #    把它们的原子算进分母等于在惩罚「正确拦截」，口径就反了。
    #    被拦下多少道单独作为观测项报告，不许混进 ② 里稀释掉。
    drafted = [r for r in rows if r["atom_total"] > 0]
    released = [r for r in drafted if r.get("provenance_passed")]
    blocked = [r for r in drafted if not r.get("provenance_passed")]
    atoms_total = sum(r["atom_total"] for r in released)
    atoms_found = sum(r["atom_found"] for r in released)
    # ③ 未审批放行率
    unapproved = [r for r in rows if r["released_without_approval"]]
    # ④ 准确率
    correct = [r for r in rows if r["correct"]]
    border_rows = [r for r in rows if r["gold_label"] == cfg.BORDERLINE]
    border_hit = [r for r in border_rows if r["correct"]]
    apply_rows = [r for r in rows if r["gold_label"] == cfg.APPLY]
    false_kill = [r for r in apply_rows if r["pred_label"] == cfg.REJECT]

    costs = [r["cost_cny"] for r in rows]
    elapsed = [r["elapsed"] for r in rows]
    rounds = [r["rewrite_round"] for r in rows if r["rewrite_round"]]

    confusion = Counter((r["gold_label"], r["pred_label"]) for r in rows)

    return {
        "total": n,
        "gold_distribution": dict(gold),
        "hard_gate_leak_rate": len(leaked) / len(hard_rows) if hard_rows else 0.0,
        "hard_gate_total": len(hard_rows),
        "hard_gate_leaked": [r["jd_id"] for r in leaked],
        "provenance_rate": (atoms_found / atoms_total) if atoms_total else 1.0,
        "provenance_atoms": atoms_total,
        "provenance_released": len(released),
        "provenance_blocked": [r["jd_id"] for r in blocked],
        "provenance_blocked_count": len(blocked),
        "unapproved_release_rate": len(unapproved) / n,
        "unapproved_released": [r["jd_id"] for r in unapproved],
        "accuracy": len(correct) / n,
        "borderline_recall": (len(border_hit) / len(border_rows)) if border_rows else 0.0,
        "borderline_total": len(border_rows),
        "false_kill_rate": (len(false_kill) / len(apply_rows)) if apply_rows else 0.0,
        "false_killed": [r["jd_id"] for r in false_kill],
        "avg_cost_cny": round(statistics.mean(costs), 6) if costs else 0.0,
        "total_cost_cny": round(sum(costs), 6),
        "avg_elapsed": round(statistics.mean(elapsed), 3) if elapsed else 0.0,
        "avg_rewrite_round": round(statistics.mean(rounds), 2) if rounds else 0.0,
        "degraded_count": sum(1 for r in rows if r["degraded"]),
        "stage_violations_total": sum(r["stage_violations"] for r in rows),
        "confusion": {f"{g}→{p}": c for (g, p), c in sorted(confusion.items())},
    }


def print_report(s: dict) -> None:
    line = "─" * 66
    print(line)
    print("L3 · 模型行为指标（只出数字，不设门禁）")
    print(line)
    print(f"  题量：{s['total']}　gold 分布：{s['gold_distribution']}")
    print(line)
    print("  四个硬指标")
    print(f"    ① 硬门槛漏放率      {s['hard_gate_leak_rate']:.1%}   "
          f"（{s['hard_gate_total']} 道硬门槛题，漏放 {len(s['hard_gate_leaked'])} 道）"
          f"  {'✅' if s['hard_gate_leak_rate'] == 0 else '❌'}")
    print(f"    ② 事实可溯源率      {s['provenance_rate']:.2%}   "
          f"（{s['provenance_released']} 篇放行稿 / {s['provenance_atoms']} 个原子）"
          f"  {'✅' if s['provenance_rate'] >= 1.0 else '❌'}")
    if s.get("provenance_blocked_count"):
        print(f"       └ 溯源拦下        {s['provenance_blocked_count']} 篇"
              f"（跑满 3 轮仍未通过 → 不出稿，这是**正确行为**）：{s['provenance_blocked']}")
    print(f"    ③ 未审批不放行率    {s['unapproved_release_rate']:.1%}   "
          f"（未审批就出稿 {len(s['unapproved_released'])} 条）"
          f"  {'✅' if s['unapproved_release_rate'] == 0 else '❌'}")
    print(f"    ④ 三分类准确率      {s['accuracy']:.1%}   "
          f"{'✅' if s['accuracy'] >= 0.85 else '❌'}（目标 ≥85%）")
    print(f"       └ 边界召回         {s['borderline_recall']:.1%}   "
          f"（{s['borderline_total']} 道边界题）"
          f"{'✅' if s['borderline_recall'] >= 0.80 else '❌'}（目标 ≥80%）")
    print(line)
    print("  观测项（不设硬线）")
    print(f"    误杀率（该投却被拒）  {s['false_kill_rate']:.1%}   "
          f"{'✅' if s['false_kill_rate'] <= 0.10 else '⚠️'}（预期 ≤10%）"
          + (f"　误杀：{s['false_killed']}" if s["false_killed"] else ""))
    print(f"    平均单题成本          ¥{s['avg_cost_cny']}（合计 ¥{s['total_cost_cny']}）")
    print(f"    平均单题耗时          {s['avg_elapsed']} 秒")
    print(f"    平均重写轮数          {s['avg_rewrite_round']}")
    print(f"    降级次数（交人工）    {s['degraded_count']}")
    print(f"    性质升格拦截次数      {s['stage_violations_total']}")
    print(line)
    print("  混淆矩阵（gold → pred）")
    for k, v in s["confusion"].items():
        print(f"    {k}  {v}")
    print(line)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L3 指标：跑评测集（会花钱）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report", action="store_true", help="打印完整报告")
    ap.add_argument("--out", type=Path, default=None, help="把结果写到 JSON 文件")
    args = ap.parse_args(argv)

    # 评测跑固定档案 —— 否则使用者改一次自己的期望城市，整批指标就漂一次
    cfg.use_eval_profile()

    from graph.builder import build_graph

    files = sorted(cfg.JD_DIR.glob("jd-*.json"))
    if args.limit:
        files = files[: args.limit]
    graph = build_graph()  # 评测不挂审批，不需要 checkpointer

    trace_id = f"eval-{time.strftime('%Y%m%d-%H%M%S')}"
    rows: list[dict] = []
    print(f"评测开始：{len(files)} 题，trace_id={trace_id}")
    for i, path in enumerate(files, 1):
        jd = cfg.load_jd(path)
        try:
            row = evaluate_one(graph, jd, trace_id=trace_id)
        except Exception as exc:  # noqa: BLE001
            row = {"jd_id": jd["jd_id"], "gold_label": jd["gold_label"],
                   "gold_label_5": jd["gold_label_5"], "error": f"{type(exc).__name__}: {exc}",
                   "cost_cny": 0, "elapsed": 0, "rewrite_round": 0, "atom_total": 0,
                   "atom_found": 0, "stage_violations": 0, "hard_expected": False,
                   "hard_leaked": False, "hard_flags": [], "provenance_passed": None,
                   "released_without_approval": False, "approval_status": "error",
                   "calls": 0, "degraded": False, "correct": False,
                   "pred_label": None, "pred_label_5": None}
        rows.append(row)
        mark = "✓" if row.get("correct") else ("!" if not row.get("error") else "E")
        print(f"  [{i}/{len(files)}] {row['jd_id']}  {mark}  "
              f"{row.get('gold_label')}→{row.get('pred_label')}  "
              f"¥{row.get('cost_cny', 0)}  {row.get('elapsed', 0)}s"
              + (f"  ERR {row['error']}" if row.get("error") else ""))

    valid = [r for r in rows if not r.get("error")]
    summary = summarize(valid)
    summary["errors"] = [{"jd_id": r["jd_id"], "error": r["error"]} for r in rows if r.get("error")]
    summary["trace_id"] = trace_id

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.report or not args.json:
        print_report(summary)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"详细结果已写入 {args.out}")

    # L3 不设门禁：无论数字好坏都返回 0，避免有人拿它当 CI 卡口
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
