#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L1 · 确定性门禁：硬门槛闸 + 跨字段矛盾检查

这是全项目唯一一条「必须 100% 通过」的命令：
    python scripts/gate.py --json     → exit 0 才算过；exit 1 直接拦

它守三件事：
  1. 漏放 = 0     评测集里所有硬门槛题，规则闸必须全部命中（gold 的 rule_id 集合是系统的子集）
  2. 误杀 = 0     非 reject 的题（该投 / 边界），规则闸不许命中任何「绝对硬」规则
  3. 矛盾检查     结论与依据互相打脸的四类情况

为什么它敢承诺 0%：
    全程不调模型。同样的输入永远给同样的输出，所以这个数字是「可重跑验证的」，
    不是「某次跑出来好看的」。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfg  # noqa: E402
from units.hard_gate.impl import HardGateError, check_hard_rules  # noqa: E402


def run(eval_dir: Path | None = None) -> dict:
    # 门禁必须跑固定档案：它要回答的是「代码有没有退化」，不是「使用者偏好变没变」。
    # 踩过的坑：使用者把期望城市从广州改成上海，一道 base 上海的题不再拦（正确行为），
    # 但评测集 gold 是按广州标的 → 漏放率无端从 0% 跳到 5%。那是档案变了，不是代码坏了。
    cfg.use_eval_profile()
    rules = cfg.load_rules()
    profile = cfg.load_profile()
    known_rule_ids = {r["rule_id"] for r in rules["rules"]}
    absolute_ids = {r["rule_id"] for r in rules["rules"] if r["tier"] == cfg.TIER_ABSOLUTE}

    files = sorted((eval_dir or cfg.JD_DIR).glob("jd-*.json"))
    if not files:
        raise HardGateError(f"评测集为空：{(eval_dir or cfg.JD_DIR)} 下没有 jd-*.json")

    fails: list[dict] = []
    warns: list[dict] = []
    hard_total = 0
    hard_hit = 0
    leaked: list[dict] = []
    false_positives: list[dict] = []

    for path in files:
        jd = cfg.load_jd(path)
        jd_id = jd["jd_id"]
        text = jd["text"]
        gold_reasons = jd.get("gold_reasons", [])
        gold_hard = {r["rule_id"] for r in gold_reasons if r.get("type") == "hard"}
        gold_soft = [r for r in gold_reasons if r.get("type") == "soft"]

        res = check_hard_rules(text, rules=rules, profile=profile)
        fired = set(res["flags"])

        # ── 检查 3：gold 里的 rule_id 必须真实存在于词表 ──────────────
        # 只校验 hard / threshold 类型 —— match / missing 类型本来就不指向规则
        for r in gold_reasons:
            rid = r.get("rule_id")
            if r.get("type") in ("hard", "threshold") and rid not in known_rule_ids:
                fails.append({
                    "check": "rule_id_not_in_rules_json", "jd_id": jd_id, "rule_id": rid,
                    "detail": f"gold 引用了 rules.json 里不存在的规则 {rid!r}",
                })

        # ── 检查 1：gold 引文不能为空 ────────────────────────────────
        for r in gold_reasons:
            if not (r.get("quote") or "").strip():
                fails.append({
                    "check": "empty_gold_quote", "jd_id": jd_id, "rule_id": r.get("rule_id"),
                    "detail": "gold_reasons 里有引文为空 —— 依据写不出来，这题不该进评测集",
                })
            elif r["quote"] not in text:
                fails.append({
                    "check": "gold_quote_not_substring", "jd_id": jd_id, "rule_id": r.get("rule_id"),
                    "detail": f"gold 引文不是 JD 原文子串：{r['quote']!r}",
                })

        # ── 检查 2：系统产出的引文必须是 JD 原文子串 ──────────────────
        for rid, quote in res["quotes"].items():
            if quote not in text:
                fails.append({
                    "check": "runtime_quote_not_substring", "jd_id": jd_id, "rule_id": rid,
                    "detail": f"运行期引文不是 JD 原文子串：{quote!r}",
                })

        # ── 指标①：硬门槛题必须命中（漏放 = 0）────────────────────────
        if jd.get("is_hard_threshold"):
            hard_total += 1
            missed = gold_hard - fired
            if missed:
                leaked.append({"jd_id": jd_id, "missed_rule_ids": sorted(missed)})
                fails.append({
                    "check": "hard_rule_leaked", "jd_id": jd_id,
                    "detail": f"硬门槛题放过：应命中 {sorted(gold_hard)}，实际命中 {sorted(fired)}",
                })
            else:
                hard_hit += 1

        # ── 误杀检查：非 reject 题不许被绝对硬规则拦 ──────────────────
        if jd.get("gold_label") != cfg.REJECT:
            hit_absolute = fired & absolute_ids
            if hit_absolute:
                false_positives.append({"jd_id": jd_id, "rule_ids": sorted(hit_absolute)})
                fails.append({
                    "check": "false_positive", "jd_id": jd_id,
                    "detail": f"{jd['gold_label']} 题被绝对硬规则拦下：{sorted(hit_absolute)}",
                })

        # ── 检查 4：判了 reject 但既无硬依据也无软扣分 → WARN ──────────
        if jd.get("gold_label") == cfg.REJECT and not gold_hard and not gold_soft:
            warns.append({
                "check": "reject_without_evidence", "jd_id": jd_id,
                "detail": "gold 判 reject，但没有硬规则依据也没有软条件扣分依据",
            })
        if jd.get("gold_label") == cfg.REJECT and not jd.get("is_hard_threshold") and not gold_soft:
            warns.append({
                "check": "soft_reject_without_reason", "jd_id": jd_id,
                "detail": "软条件 reject 题没写出扣分依据",
            })

    ok = not fails
    return {
        "ok": ok,
        "layer": "L1 · 确定性门禁",
        "rules_version": rules.get("rules_version"),
        "rules_count": len(rules["rules"]),
        "eval_dir": str(eval_dir or cfg.JD_DIR),
        "eval_files": len(files),
        "hard_threshold": {
            "total": hard_total,
            "hit": hard_hit,
            "leak_rate": (hard_total - hard_hit) / hard_total if hard_total else 0.0,
            "leaked": leaked,
        },
        "false_positives": false_positives,
        "contradictions": {"fails": fails, "warns": warns},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L1 确定性门禁：硬门槛闸 + 矛盾检查")
    ap.add_argument("--eval-dir", type=Path, default=None, help="评测集目录（默认 data/jd）")
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = ap.parse_args(argv)

    try:
        report = run(args.eval_dir)
    except HardGateError as exc:
        print(f"[gate] 硬失败：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    ht = report["hard_threshold"]
    print(f"L1 确定性门禁 · rules {report['rules_version']}（{report['rules_count']} 条）")
    print(f"  评测集：{report['eval_dir']}  共 {report['eval_files']} 题")
    print(f"  硬门槛题命中：{ht['hit']}/{ht['total']}   漏放率：{ht['leak_rate']:.1%}")
    if ht["leaked"]:
        for item in ht["leaked"]:
            print(f"    MISS  {item['jd_id']}  应命中未命中 {item['missed_rule_ids']}")
    if report["false_positives"]:
        for item in report["false_positives"]:
            print(f"    FP    {item['jd_id']}  误杀 {item['rule_ids']}")
    for item in report["contradictions"]["fails"]:
        print(f"    FAIL  [{item['check']}] {item['jd_id']}  {item['detail']}")
    for item in report["contradictions"]["warns"]:
        print(f"    WARN  [{item['check']}] {item['jd_id']}  {item['detail']}")
    print("  结果：" + ("PASS  exit 0" if report["ok"] else "FAIL  exit 1"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
