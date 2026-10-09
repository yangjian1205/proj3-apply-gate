#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两组对照实验 —— 本项目最有说服力的部分。

实验一：溯源校验到底有没有用？
    A 组：tailor → provenance →（不过就回退重写）
    B 组：tailor 直接出稿，不校验
    测：编造率（无出处原子数 ÷ 总原子数）、平均重写轮数、单题成本
    预期：B 组编造率显著高于 A 组。这个数字就是「溯源校验」这个功能的价格标签。

实验二：多节点分工 vs 一个大 prompt 一把梭？
    A 组：本项目的确定性硬门槛 + 五态分类 + 子图改写
    B 组：一个大 prompt 让模型一次输出三分类 + 改写稿
    测：三分类准确率、**硬门槛漏放率**、单题成本、单题耗时
    预期：多节点更贵、更慢 —— 这个实验就是要**诚实报告代价**，
         并说清「什么时候值得」：硬门槛部分必须走确定性代码（零漏放），软性部分才值得上多节点。

为什么这两张表值钱：同类项目里几乎没人做对照实验，大家都在比功能数量。
会做实验、敢报告自己更贵更慢，比多写两个功能更能说明工程判断力。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, Field  # noqa: E402

from core import config as cfg  # noqa: E402
from core.llm import structured_call  # noqa: E402
from units.provenance.impl import verify  # noqa: E402
from units.tailor.impl import tailor  # noqa: E402

LINE = "─" * 66


# ── 实验一 ────────────────────────────────────────────────────────────
def exp1_one(jd: dict, *, group: str) -> dict:
    """group = 'A'（带溯源回退） / 'B'（不校验直接出稿）"""
    resume = cfg.load_resume()
    bank = cfg.load_experience_bank()
    match_stub = {"stage_5": "已匹配", "score": {"relevant": 8, "level": 7, "skill": 8},
                  "score_total": 23, "reasons": ["对照实验：判定部分固定为同一输入"],
                  "evidence": [], "gap_anchor": ""}

    rounds = 0
    cost = 0.0
    missed: list[str] = []
    draft_text = ""
    prov = None
    started = time.perf_counter()

    while True:
        out = tailor(jd, match_stub, resume, experience_bank=bank,
                     missed=missed, rewrite_round=rounds)
        rounds += 1
        cost += (out.get("usage") or {}).get("cost_cny", 0.0)
        draft_text = out["resume_md"]

        if group == "B":
            break

        prov = verify(draft_text, resume, jd_text=jd["text"])
        if prov["passed"] or rounds >= 3:
            break
        missed = prov["missed"]

    if group == "B":
        prov = verify(draft_text, resume, jd_text=jd["text"])  # 事后统计用，不参与回退

    total = prov["atom_count"] or 1
    return {
        "jd_id": jd["jd_id"], "group": group,
        "fabricated": len(prov["missed"]),
        "atom_total": total,
        "fabrication_rate": len(prov["missed"]) / total,
        "stage_violations": len(prov["stage_violations"]),
        "rounds": rounds,
        "cost_cny": round(cost, 6),
        "elapsed": round(time.perf_counter() - started, 3),
    }


def run_exp1(files: list[Path]) -> dict:
    rows = []
    print(f"\n{LINE}\n实验一 · 溯源校验有没有用（各跑 {len(files)} 题）\n{LINE}")
    for group in ("A", "B"):
        for path in files:
            jd = cfg.load_jd(path)
            label = "带溯源回退" if group == "A" else "不校验直接出稿"
            try:
                row = exp1_one(jd, group=group)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{group}] {jd['jd_id']} 失败：{exc}")
                continue
            rows.append(row)
            print(f"  [{group}·{label}] {jd['jd_id']}  编造 {row['fabricated']}/{row['atom_total']}"
                  f"  轮数 {row['rounds']}  ¥{row['cost_cny']}")

    out = {}
    for group in ("A", "B"):
        g = [r for r in rows if r["group"] == group]
        if not g:
            continue
        out[group] = {
            "label": "带溯源回退" if group == "A" else "不校验直接出稿",
            "n": len(g),
            "fabrication_rate": round(statistics.mean(r["fabrication_rate"] for r in g), 4),
            "stage_violation_rate": round(
                statistics.mean(r["stage_violations"] / max(r["atom_total"], 1) for r in g), 4),
            "avg_rounds": round(statistics.mean(r["rounds"] for r in g), 2),
            "avg_cost_cny": round(statistics.mean(r["cost_cny"] for r in g), 6),
            "avg_elapsed": round(statistics.mean(r["elapsed"] for r in g), 2),
        }
    return {"rows": rows, "summary": out}


# ── 实验二 ────────────────────────────────────────────────────────────
class OneShot(BaseModel):
    verdict_5: str = Field(description="五态之一：已匹配/表达缺口/证据不足/真实缺口/待确认")
    verdict: str = Field(description="三分类：apply/reject/borderline")
    hard_rule_hit: str = Field(description="这条 JD 是否触发了硬门槛（签证/户籍/坐班城市/学历/证书/应届/语言/年限差≥3年）。填命中的规则名，没有填 none")
    resume_tailored: str = Field(description="针对该 JD 的简历改写稿，只重排与重述，不新增事实")
    reason: str = Field(description="一句话判定理由")


ONESHOT_SYSTEM = """你是一个投递决策助手。请**一次**完成全部工作：判定岗位是否值得投、并给出改写后的简历。

候选人档案：期望城市广州（接受远程、不接受异地）；本科；非应届；实际工作年限 1 年；
已持有证书：华为 HCIP-AI-EI 备考中（未取得）；无语言等级证书。

硬门槛判断标准（命中任一即判 reject）：
- JD 明写不提供工作签证支持
- JD 要求本地户籍 / 已有本地工作许可
- JD 要求全职坐班且城市不在广州
- JD 要求硕士及以上 / 博士学历
- JD 必须持有候选人没有的证书
- JD 仅面向应届毕业生
- JD 要求英语六级 / 日语 N1 等候选人没有的语言证书
- JD 要求年限比 1 年多 3 年及以上

改写要求：只重排与重述，不许新增简历里没有的事实，不许把「原型/技术验证」写成「已上线生产」。"""


def exp2_one(jd: dict) -> dict:
    """一个大 prompt 一把梭：模型自己判定 + 自己改写。"""
    resume = cfg.load_resume()
    prompt = f"【JD 原文】\n{jd['text']}\n\n【候选人简历】\n{resume[:5000]}\n\n请一次给出五态、三分类、硬门槛命中情况、改写稿与理由。"
    started = time.perf_counter()
    result, usage, err = structured_call(
        [("system", ONESHOT_SYSTEM), ("human", prompt)],
        OneShot, node="oneshot", retries=1,
    )
    elapsed = time.perf_counter() - started
    if result is None:
        return {"jd_id": jd["jd_id"], "error": err, "cost_cny": 0, "elapsed": elapsed,
                "pred": None, "hard_hit": "", "correct": False, "hard_leaked": False,
                "fabrication_rate": 0.0, "atom_total": 0}

    pred = result.verdict if result.verdict in cfg.VERDICTS else None
    # 硬门槛：gold 说是硬门槛题，但一次性模型没报出规则名 → 算漏放
    hard_hit = (result.hard_rule_hit or "").strip().lower()
    hard_leaked = bool(jd.get("is_hard_threshold")) and hard_hit in ("", "none", "无", "没有")

    prov = verify(result.resume_tailored or "。", resume, jd_text=jd["text"])
    total = prov["atom_count"] or 1

    return {
        "jd_id": jd["jd_id"],
        "pred": pred,
        "pred_stage5": result.verdict_5,
        "gold": jd["gold_label"],
        "correct": pred == jd["gold_label"],
        "hard_hit": hard_hit,
        "hard_expected": bool(jd.get("is_hard_threshold")),
        "hard_leaked": hard_leaked,
        "fabrication_rate": len(prov["missed"]) / total,
        "atom_total": prov["atom_count"],
        "cost_cny": (usage.as_dict().get("cost_cny", 0) if usage else 0),
        "elapsed": round(elapsed, 3),
        "error": None,
    }


def run_exp2(files: list[Path]) -> dict:
    from graph.builder import build_graph

    print(f"\n{LINE}\n实验二 · 多节点 vs 一个大 prompt（各跑 {len(files)} 题）\n{LINE}")
    graph = build_graph()

    rows_a, rows_b = [], []
    for path in files:
        jd = cfg.load_jd(path)

        # A 组：本项目链路
        trace = f"cmp-{int(time.time())}-{jd['jd_id']}"
        init = {"jd": jd, "trace_id": trace, "thread_id": f"th-{trace}",
                "batch_mode": True, "usage_records": [], "audit_trail": [], "steps": []}
        t0 = time.perf_counter()
        try:
            st = graph.invoke(init, {"configurable": {"thread_id": init["thread_id"]},
                                     "recursion_limit": 30})
            m = st.get("match_result") or {}
            hard = st.get("hard_result") or {}
            hard_reject = hard.get("hard_verdict") == cfg.REJECT
            pred = m.get("verdict") or (cfg.REJECT if hard_reject else None)
            rows_a.append({
                "jd_id": jd["jd_id"], "pred": pred, "gold": jd["gold_label"],
                "correct": pred == jd["gold_label"],
                "hard_leaked": bool(jd.get("is_hard_threshold")) and not hard.get("flags"),
                "cost_cny": round(sum(u.get("cost_cny", 0) for u in st.get("usage_records") or []), 6),
                "elapsed": round(time.perf_counter() - t0, 3),
            })
        except Exception as exc:  # noqa: BLE001
            print(f"  [A] {jd['jd_id']} 失败：{exc}")

        # B 组：一把梭
        try:
            rows_b.append(exp2_one(jd))
        except Exception as exc:  # noqa: BLE001
            print(f"  [B] {jd['jd_id']} 失败：{exc}")

        print(f"  {jd['jd_id']}  A：{rows_a[-1]['pred'] if rows_a else '—':<10}"
              f" B：{(rows_b[-1].get('pred') if rows_b else '—')}  gold={jd['gold_label']}")

    def agg(rows, key_correct="correct"):
        rows = [r for r in rows if not r.get("error")]
        if not rows:
            return {}
        hard_rows = [r for r in rows if r.get("hard_expected", True)]
        out = {
            "n": len(rows),
            "accuracy": round(sum(1 for r in rows if r.get(key_correct)) / len(rows), 4),
            "avg_cost_cny": round(statistics.mean(r.get("cost_cny", 0) for r in rows), 6),
            "avg_elapsed": round(statistics.mean(r.get("elapsed", 0) for r in rows), 2),
        }
        if hard_rows:
            out["hard_leak_rate"] = round(
                sum(1 for r in hard_rows if r.get("hard_leaked")) / len(hard_rows), 4)
            out["hard_total"] = len(hard_rows)
        else:
            out["hard_leak_rate"] = None
        return out

    return {
        "A": {"label": "本项目（确定性硬门槛 + 五态 + 子图改写）", **agg(rows_a)},
        "B": {"label": "一个大 prompt 一次输出三分类 + 改写稿", **agg(rows_b)},
        "rows": {"A": rows_a, "B": rows_b},
    }


def stratified_sample(n: int) -> list[Path]:
    """按 gold 标签分层抽样，保证「不该投 / 该投 / 边界」三类都有。

    ⚠️ 踩过的坑：最初直接取前 N 道，结果前 12 道全是硬门槛 reject 题 ——
       实验二两边都是 100% 准确率、A 组成本还是 ¥0，结论完全没有信息量。
       抽样不分层，实验就是自娱自乐。
    """
    from collections import defaultdict

    buckets: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(cfg.JD_DIR.glob("jd-*.json")):
        jd = cfg.load_jd(path)
        buckets[jd["gold_label"]].append(path)

    per = max(1, n // 3)
    out: list[Path] = []
    for label in (cfg.REJECT, cfg.APPLY, cfg.BORDERLINE):
        out += buckets[label][:per]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="两组对照实验（会花钱）")
    ap.add_argument("--n", type=int, default=15, help="总题数，按三类均分（默认 15 → 每类 5）")
    ap.add_argument("--which", choices=["1", "2", "both"], default="both")
    ap.add_argument("--out", type=Path, default=cfg.GENERATED_DIR / "compare_report.json")
    args = ap.parse_args(argv)

    # 与 eval/run.py 一样跑固定档案 —— 两组实验要可比，也必须与使用者的档案解耦
    cfg.use_eval_profile()

    files = stratified_sample(args.n)
    labels = [cfg.load_jd(p)["gold_label"] for p in files]
    report: dict = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_per_group": args.n,
        "sampling": "按 gold 分层抽样（每类均分）",
        "sample": [{"jd_id": p.stem, "gold_label": lb} for p, lb in zip(files, labels)],
    }
    print(f"分层抽样：{len(files)} 题 → {dict(sorted((l, labels.count(l)) for l in set(labels)))}")

    if args.which in ("1", "both"):
        report["exp1"] = run_exp1(files)
    if args.which in ("2", "both"):
        report["exp2"] = run_exp2(files)

    print(f"\n{LINE}\n汇总\n{LINE}")
    if "exp1" in report:
        print("实验一 · 溯源校验的价值")
        for k, v in report["exp1"]["summary"].items():
            print(f"  {k} {v['label']}：编造率 {v['fabrication_rate']:.2%}"
                  f"　性质升格率 {v['stage_violation_rate']:.2%}"
                  f"　平均轮数 {v['avg_rounds']}　单题成本 ¥{v['avg_cost_cny']}")
    if "exp2" in report:
        print("\n实验二 · 多节点 vs 一把梭")
        for k in ("A", "B"):
            v = report["exp2"][k]
            print(f"  {k} {v.get('label')}：准确率 {v.get('accuracy', 0):.1%}"
                  f"　硬门槛漏放率 {v.get('hard_leak_rate')}"
                  f"　单题成本 ¥{v.get('avg_cost_cny')}　单题耗时 {v.get('avg_elapsed')}s")
        a, b = report["exp2"]["A"], report["exp2"]["B"]
        if a.get("avg_cost_cny") and b.get("avg_cost_cny"):
            print(f"  → 多节点成本是一把梭的 {a['avg_cost_cny'] / max(b['avg_cost_cny'], 1e-9):.2f} 倍，"
                  f"耗时 {a.get('avg_elapsed', 0) / max(b.get('avg_elapsed', 1e-9), 1e-9):.2f} 倍"
                  f"（**如实报告代价**，见 README）")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
