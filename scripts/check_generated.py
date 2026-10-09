#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""防漂移检查：generated/ 里的产物必须等于「从真源重新生成」的结果。

抄的是 Asu-skills 的 `sync:skills:check` 思路：
    真源改了但忘了重新生成 → 产物是旧的 → 报错；
    有人手改了产物 → 也报错。

为什么必须在提交前跑：
    规则说明书、prompt 片段、账本校验结果，这三样一旦和真源不一致，
    你后面所有指标都是在错误的规则上跑出来的 —— 而且看不出来。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfg  # noqa: E402
from scripts import gen_from_rules, validate_ledger  # noqa: E402


def _ledger_check_payload() -> str:
    ledger_file = cfg.ledger_path()  # 真实账本优先，缺失时回退模板
    document, errors = validate_ledger.load(ledger_file)
    count = 0
    if not errors:
        count, errors = validate_ledger.validate_ledger(document)
    stage_dist: dict[str, int] = {}
    if not errors and isinstance(document, dict):
        for c in document.get("claims", []):
            key = c.get("delivery_stage", "?")
            stage_dist[key] = stage_dist.get(key, 0) + 1
    return json.dumps(
        {
            "ok": not errors,
            "layer": "L2 · 结构校验（账本）",
            "path": str(ledger_file),
            "claim_count": count,
            "delivery_stage_distribution": stage_dist,
            "errors": errors,
        },
        ensure_ascii=False,
        indent=2,
    )


def _first_diff(a: str, b: str) -> str:
    a_lines, b_lines = a.splitlines(), b.splitlines()
    for i in range(max(len(a_lines), len(b_lines))):
        la = a_lines[i] if i < len(a_lines) else "<缺行>"
        lb = b_lines[i] if i < len(b_lines) else "<缺行>"
        if la != lb:
            return f"第 {i + 1} 行不同\n      磁盘: {la[:120]}\n      重生: {lb[:120]}"
    return "内容长度不同但逐行相同（可能是行尾差异）"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="检查 generated/ 是否与真源一致")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    expected = dict(gen_from_rules.render_all())
    expected["ledger_check.json"] = _ledger_check_payload()

    drift: list[dict] = []
    for name, content in expected.items():
        path = cfg.GENERATED_DIR / name
        if not path.exists():
            drift.append({"file": name, "reason": "缺失", "detail": "产物不存在，需要跑生成命令"})
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != content:
            drift.append({"file": name, "reason": "不一致", "detail": _first_diff(actual, content)})

    report = {
        "ok": not drift,
        "layer": "防漂移检查",
        "generated_dir": str(cfg.GENERATED_DIR),
        "checked": sorted(expected),
        "drift": drift,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"防漂移检查：{cfg.GENERATED_DIR}")
        for name in sorted(expected):
            mark = "OK  " if not any(d["file"] == name for d in drift) else "DRIFT"
            print(f"  {mark} {name}")
        for d in drift:
            print(f"  FAIL  {d['file']} —— {d['reason']}")
            if d["reason"] == "不一致":
                print(f"      {d['detail']}")
        if drift:
            print("\n  修法：python scripts/gen_from_rules.py && "
                  "python scripts/validate_ledger.py --json --out data/generated/ledger_check.json")
        print("  结果：" + ("PASS" if report["ok"] else "FAIL"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
