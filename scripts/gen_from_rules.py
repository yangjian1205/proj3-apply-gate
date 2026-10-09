#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单一事实源 → 生成物。

rules.json 是**唯一真源**。规则说明书、注入 prompt 的片段、规则 ID 词表，全部由它生成，
不许手工维护第二份。理由：v1 的坑就是同一条规则活在五个地方，改一处漏一处。

产物（全部落在 data/generated/，禁止手工编辑）：
    rules_book.md      规则说明书（人看）
    prompt_rules.txt   注入 planner / match-score 的规则片段（按需注入，压 token）
    rule_ids.json      规则元数据（代码与其他校验脚本消费）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfg  # noqa: E402

BANNER = (
    "本文件由 scripts/gen_from_rules.py 从 data/rules.json 生成。\n"
    "禁止手工编辑 —— 改了会被 scripts/check_generated.py 判定为漂移。\n"
    "要改规则请改 data/rules.json，然后重跑：python scripts/gen_from_rules.py\n"
)


def _rules_book(rules: dict) -> str:
    lines = [
        "# 硬门槛规则说明书",
        "",
        f"> {BANNER.strip().splitlines()[0]}",
        f"> {BANNER.strip().splitlines()[1]}",
        "",
        f"- 规则版本：`{rules.get('rules_version')}`",
        f"- 更新日期：`{rules.get('updated_at')}`",
        f"- 规则条数：**{len(rules['rules'])}**",
        "",
        "## 阈值",
        "",
        "| 阈值 | 值 |",
        "|---|---|",
    ]
    for k, v in rules.get("thresholds", {}).items():
        lines.append(f"| `{k}` | {v} |")

    for tier_key in (cfg.TIER_ABSOLUTE, cfg.TIER_THRESHOLD, cfg.TIER_SOFT):
        tier = rules["tiers"][tier_key]
        lines += ["", f"## {tier['name']}", "", f"**行为**：{tier['behavior']}", "", f"**为什么**：{tier['why']}", ""]
        lines += ["| rule_id | 判定 | 判定器 | 命中即 | 边界（不归我管的部分） |", "|---|---|---|---|---|"]
        for r in rules["rules"]:
            if r["tier"] != tier_key:
                continue
            behavior = {"absolute": "reject", "threshold": "按阈值", "soft": "只打分"}[tier_key]
            if r.get("hint_only"):
                behavior = "只提示不拦"
            lines.append(
                f"| `{r['rule_id']}` | {r.get('label', '')} | `{r.get('check')}` "
                f"| {behavior} | {r.get('boundary', '—')} |"
            )
        lines.append("")
        lines.append("词表：")
        lines.append("")
        for r in rules["rules"]:
            if r["tier"] != tier_key:
                continue
            lines.append(f"- `{r['rule_id']}` 触发词：{', '.join('`' + p + '`' for p in r.get('patterns', []))}")
            if r.get("exclude_patterns"):
                lines.append(f"  - 排除词（写这些不算）：{', '.join('`' + p + '`' for p in r['exclude_patterns'])}")
    return "\n".join(lines) + "\n"


def _prompt_rules(rules: dict) -> str:
    """注入模型的片段：只说「边界」和「什么不许做」，不塞正则长尾。

    这是抄 Asu-skills 的 references/ 渐进式披露做法：
    长尾词表不进 prompt，只在命中候选时才按需取，直接压单题 token 成本。
    """
    lines = [
        "# 注入 planner / match-score 的规则片段",
        f"# {BANNER.splitlines()[0]}",
        "",
        "你能看到的规则清单（判定逻辑不在你手里，你只能转述它们的结论）：",
        "",
    ]
    for r in rules["rules"]:
        lines.append(f"- {r['rule_id']}（{r['tier']}）：{r.get('label', '')}")
    lines += [
        "",
        "四条硬规矩：",
        "1. 不许自己下判定 —— 任何「该投 / 不该投 / 边界」的结论必须来自 hard-gate 或 match-score 的返回值，你只能转述。",
        "2. 不许跳过 provenance —— 只要产出了改写稿，必须先调 provenance 且拿到 passed=true，否则不许进入 approval。",
        "3. 不许自己决定停 —— rewrite_round >= 3 时由代码强制退出并标 verify_failed，你没有「再试一次」的权限。",
        "4. 不许出成稿 —— approval_status != approved 时调 report 只能拿草稿，拿不到可投递稿。",
        "",
        "不归你判定的三类（只打分，永不拦人）：职级高低、技能覆盖度、行业背景。",
    ]
    return "\n".join(lines) + "\n"


def _rule_ids(rules: dict) -> str:
    payload = {
        "rules_version": rules.get("rules_version"),
        "updated_at": rules.get("updated_at"),
        "thresholds": rules.get("thresholds", {}),
        "rules": [
            {
                "rule_id": r["rule_id"],
                "tier": r["tier"],
                "check": r.get("check"),
                "label": r.get("label", ""),
                "hint_only": bool(r.get("hint_only")),
                "pattern_count": len(r.get("patterns", [])),
                "boundary": r.get("boundary", ""),
            }
            for r in rules["rules"]
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_all(rules: dict | None = None) -> dict[str, str]:
    """生成全部产物（内存里），check_generated.py 也用这个函数比对。"""
    rules = rules or cfg.load_rules()
    return {
        "rules_book.md": _rules_book(rules),
        "prompt_rules.txt": _prompt_rules(rules),
        "rule_ids.json": _rule_ids(rules),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 rules.json 生成 rules_book / prompt_rules / rule_ids")
    ap.add_argument("--out-dir", type=Path, default=cfg.GENERATED_DIR)
    ap.add_argument("--dry-run", action="store_true", help="只打印将要写什么，不落盘")
    args = ap.parse_args(argv)

    rendered = render_all()
    if args.dry_run:
        for name, content in rendered.items():
            print(f"[dry-run] {name}  {len(content)} 字节")
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in rendered.items():
        (args.out_dir / name).write_text(content, encoding="utf-8")
        print(f"[gen_from_rules] 已生成 {args.out_dir / name}  ({len(content)} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
