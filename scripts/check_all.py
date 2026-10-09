#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键跑全部**不花钱**的门禁。

    python scripts/check_all.py

包含：
    L1  硬门槛门禁（gate.py）—— 漏放 = 0 是硬线
    L2  账本结构校验（validate_ledger.py）
    L2  评测集结构校验（validate_eval_set.py）
    防漂移检查（check_generated.py）
    单元用例（tests/run_unit_cases.py）

**不包含** L3（eval/run.py）—— 那一层会调模型花钱，而且只出数字不设门禁。
提交前跑这个；改了 prompt / 换了模型之后才需要另外跑 L3。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STEPS = [
    ("L1 · 确定性门禁（硬门槛 + 矛盾检查）", [sys.executable, "scripts/gate.py"]),
    ("L2 · 账本结构校验", [sys.executable, "scripts/validate_ledger.py"]),
    ("L2 · 评测集结构校验", [sys.executable, "scripts/validate_eval_set.py"]),
    ("防漂移检查（产物 vs 真源）", [sys.executable, "scripts/check_generated.py"]),
    ("单元用例（各能力单元自己的用例）", [sys.executable, "tests/run_unit_cases.py"]),
]

# 产物缺失时先补生成一次。
# 为什么需要：`data/generated/` 里的 dashboard.html / ledger_check.json / eval_report.json
# 被 .gitignore 排除了（它们含个人信息或依赖个人档案），所以**别人 clone 下来这几份不存在**。
# 缺失时补生成，fresh clone 才能一键验收通过；
# **已存在则一律不动** —— 否则防漂移检查就没意义了（它要能抓到「产物被手工改过」）。
ENSURE_STEPS = [
    ([sys.executable, "scripts/gen_from_rules.py"], "data/generated/rules_book.md"),
    ([sys.executable, "scripts/validate_ledger.py", "--json",
      "--out", "data/generated/ledger_check.json"], "data/generated/ledger_check.json"),
]


def ensure_generated() -> None:
    made = []
    for argv, output in ENSURE_STEPS:
        if (ROOT / output).exists():
            continue  # 已存在就不重跑，保住防漂移检查的效力
        subprocess.run(argv, cwd=str(ROOT), capture_output=True)
        made.append(output)
    if made:
        print("（首次运行，已补生成产物）" + "、".join(made))


def main() -> int:
    print("=" * 66)
    print("proj3-apply-gate · 一键门禁（不含会花钱的 L3）")
    print("=" * 66)
    ensure_generated()
    failed = []
    for title, argv in STEPS:
        print(f"\n▶ {title}")
        proc = subprocess.run(argv, cwd=str(ROOT))
        if proc.returncode != 0:
            failed.append(title)
            print(f"  ✗ 未通过（exit {proc.returncode}）")
        else:
            print("  ✓ 通过")

    print("\n" + "=" * 66)
    if failed:
        print("结果：FAIL")
        for f in failed:
            print(f"  ✗ {f}")
        print("\n提示：改规则词表后记得重跑生成命令：")
        print("  python scripts/gen_from_rules.py")
        print("  python scripts/validate_ledger.py --json --out data/generated/ledger_check.json")
        return 1
    print("结果：全部通过 ✅")
    print("说明：L1/L2 是门禁（必须 100%）；L3（eval/run.py）只出指标、不设门禁。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
