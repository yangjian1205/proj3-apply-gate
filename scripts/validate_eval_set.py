#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L2 · 结构校验：60 题评测集

标注铁律（v1 保留 + v2 加严）：
    ① 依据引文写不出来的题，不进评测集 —— 引文必须是 JD 原文的子串，写不出就没有判据。
    ② must_not 写不出来的题也不收 —— 说不出失败模式，等于没有判据。

这道校验是两个门禁的入口：
    - 没有它，`gate.py` 跑的可能是题面有毛病的评测集，跑出 100% 也是假的；
    - 没有它，「硬门槛题 ≥15 道」这个数量要求没人管，砍题砍到 5 道也发现不了。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfg  # noqa: E402

REQUIRED_FIELDS = (
    "jd_id", "company", "title", "text", "source_url", "fetched_at",
    "gold_label", "gold_label_5", "gold_reasons", "difficulty",
    "required_sections", "must_not", "is_hard_threshold",
)
MIN_HARD_THRESHOLD = 15


def validate_one(jd: dict, path: Path, errors: list) -> None:
    jd_id = jd.get("jd_id", path.stem)

    missing = [f for f in REQUIRED_FIELDS if f not in jd]
    if missing:
        errors.append(f"{jd_id} 缺字段：{missing}")
        return

    if not isinstance(jd["text"], str) or len(jd["text"].strip()) < 20:
        errors.append(f"{jd_id} 的 text 太短或不是字符串（JD 原文必须完整保留）")

    if jd["gold_label"] not in cfg.VERDICTS:
        errors.append(f"{jd_id} gold_label 非法：{jd['gold_label']!r}，必须是 {list(cfg.VERDICTS)}")
    if jd["gold_label_5"] not in cfg.STAGES:
        errors.append(f"{jd_id} gold_label_5 非法：{jd['gold_label_5']!r}，必须是 {list(cfg.STAGES)}")

    # 五态与三分类必须自洽 —— 这是最容易写错、也最容易被面试官抓的地方
    if jd["gold_label_5"] in cfg.STAGE_TO_VERDICT:
        expect = cfg.STAGE_TO_VERDICT[jd["gold_label_5"]]
        if jd["gold_label"] != expect:
            errors.append(
                f"{jd_id} 口径打架：gold_label_5={jd['gold_label_5']!r} 按映射应为 "
                f"{expect!r}，但 gold_label={jd['gold_label']!r}"
            )

    # 引文铁律
    reasons = jd["gold_reasons"]
    if not isinstance(reasons, list) or not reasons:
        errors.append(f"{jd_id} gold_reasons 不能为空（依据写不出来的题不收）")
    else:
        for i, r in enumerate(reasons):
            q = (r.get("quote") or "")
            if not q.strip():
                errors.append(f"{jd_id} gold_reasons[{i}] 引文为空")
            elif q not in jd["text"]:
                errors.append(f"{jd_id} gold_reasons[{i}] 引文不是 JD 原文子串：{q!r}")

    # 硬门槛题必须能指向具体规则
    if jd["is_hard_threshold"]:
        hard_ids = [r.get("rule_id") for r in reasons if r.get("type") == "hard"]
        if not hard_ids:
            errors.append(f"{jd_id} 标了 is_hard_threshold 但没有 type=hard 的依据")

    # must_not / required_sections 铁律
    for f in ("must_not", "required_sections"):
        if not isinstance(jd[f], list) or not jd[f]:
            errors.append(f"{jd_id} {f} 不能为空（说不出失败模式 = 没有判据）")

    # expected_stage 允许 null（不出稿的题），但要合法
    stage = jd.get("expected_stage")
    if stage is not None and stage not in cfg.DELIVERY_STAGES:
        errors.append(f"{jd_id} expected_stage 非法：{stage!r}")


def run(eval_dir: Path | None = None) -> dict:
    directory = eval_dir or cfg.JD_DIR
    files = sorted(directory.glob("jd-*.json"))
    errors: list[str] = []

    labels: Counter = Counter()
    stages: Counter = Counter()
    hard = 0
    seen_ids: set = set()

    for path in files:
        try:
            jd = cfg.load_jd(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.name} 读取失败：{exc}")
            continue
        if jd.get("jd_id") in seen_ids:
            errors.append(f"{path.name} jd_id 重复：{jd.get('jd_id')}")
        seen_ids.add(jd.get("jd_id"))
        validate_one(jd, path, errors)
        labels[jd.get("gold_label")] += 1
        stages[jd.get("gold_label_5")] += 1
        hard += 1 if jd.get("is_hard_threshold") else 0

    if hard < MIN_HARD_THRESHOLD:
        errors.append(f"硬门槛题只有 {hard} 道，少于下限 {MIN_HARD_THRESHOLD} —— 指标①没有足够样本支撑")

    return {
        "ok": not errors,
        "layer": "L2 · 结构校验（评测集）",
        "eval_dir": str(directory),
        "total": len(files),
        "hard_threshold": hard,
        "gold_label_distribution": dict(labels),
        "gold_label_5_distribution": dict(stages),
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L2 结构校验：评测集 schema")
    ap.add_argument("--eval-dir", type=Path, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = run(args.eval_dir)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"评测集校验：{report['total']} 题，硬门槛题 {report['hard_threshold']} 道")
        print(f"  三分类：{report['gold_label_distribution']}")
        print(f"  五态：{report['gold_label_5_distribution']}")
        for e in report["errors"]:
            print(f"  FAIL  {e}")
        print("  结果：" + ("PASS" if report["ok"] else "FAIL"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
