#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""L2 · 结构校验：简历事实基线（ledger.json）

结构照抄 Asu-skills 的 validate_claim_ledger.py，只加两处本项目必需的扩展：
  1. 新增必填字段 delivery_stage（四选一枚举）—— C 类「性质断言」判定的档位来源；
  2. 加严一条跨字段检查：verification_status = 已确认 时 last_verified 不许为空。

保留 Asu 那条最关键的设计：**查的不是「字段有没有」，而是「两个字段有没有互相打脸」**。
比如「已确认」的主张里不许出现【待补】占位符 —— 状态说有，正文说没有，这就是自相矛盾。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfg  # noqa: E402

SUPPORTED_SCHEMA_VERSION = 1
RESPONSIBILITY_LEVELS = {"参与", "负责模块", "主导方案或交付", "项目负责人"}
VERIFICATION_STATUSES = {"已确认", "待确认", "已过期", "不采用"}
DELIVERY_STAGES = set(cfg.DELIVERY_STAGES)

REQUIRED_PROFILE_FIELDS = {"candidate_id", "target_roles", "updated_at"}
REQUIRED_CLAIM_FIELDS = {
    "id", "source_fact", "candidate_wording", "sources",
    "responsibility_level", "verification_status", "delivery_stage",
    "allowed_uses", "interview_details", "boundary", "risk_notes", "last_verified",
}
REQUIRED_SOURCE_FIELDS = {"type", "location", "public"}
REQUIRED_INTERVIEW_FIELDS = {"decisions", "difficulties", "verification", "result"}


def _fmt(keys) -> str:
    return "[" + ", ".join(sorted(repr(k) for k in keys)) + "]"


def _require_fields(value: dict, required: set, path: str, errors: list) -> None:
    missing = required - set(value)
    if missing:
        errors.append(f"{path} 缺少字段：{_fmt(missing)}")


def _nonempty_str(value: Any, path: str, errors: list) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path} 必须是非空字符串")


def _str_list(value: Any, path: str, errors: list) -> None:
    if not isinstance(value, list):
        errors.append(f"{path} 必须是数组")
        return
    for i, item in enumerate(value):
        _nonempty_str(item, f"{path}[{i}]", errors)


def _date(value: Any, path: str, errors: list, *, nullable: bool) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        errors.append(f"{path} 必须是 YYYY-MM-DD 日期" + ("或 null" if nullable else ""))
        return
    try:
        date.fromisoformat(value)
    except ValueError:
        errors.append(f"{path} 必须是合法的 YYYY-MM-DD 日期")


def _source(value: Any, path: str, errors: list) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _require_fields(value, REQUIRED_SOURCE_FIELDS, path, errors)
    for f in ("type", "location"):
        if f in value:
            _nonempty_str(value[f], f"{path}.{f}", errors)
    if "public" in value and not isinstance(value["public"], bool):
        errors.append(f"{path}.public 必须是布尔值")
    if "note" in value and not isinstance(value["note"], str):
        errors.append(f"{path}.note 必须是字符串")


def _interview(value: Any, path: str, errors: list) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return
    _require_fields(value, REQUIRED_INTERVIEW_FIELDS, path, errors)
    for f in ("decisions", "difficulties", "verification"):
        if f in value:
            _str_list(value[f], f"{path}.{f}", errors)
    if "result" in value and value["result"] is not None and not isinstance(value["result"], str):
        errors.append(f"{path}.result 必须是字符串或 null")


def _claim(value: Any, index: int, seen_ids: set, errors: list) -> None:
    path = f"claims[{index}]"
    if not isinstance(value, dict):
        errors.append(f"{path} 必须是对象")
        return

    _require_fields(value, REQUIRED_CLAIM_FIELDS, path, errors)
    for f in ("id", "source_fact", "candidate_wording", "boundary"):
        if f in value:
            _nonempty_str(value[f], f"{path}.{f}", errors)

    claim_id = value.get("id")
    if isinstance(claim_id, str) and claim_id.strip():
        if claim_id in seen_ids:
            errors.append(f"{path}.id 与其他主张重复：{claim_id!r}")
        seen_ids.add(claim_id)

    resp = value.get("responsibility_level")
    if "responsibility_level" in value and (not isinstance(resp, str) or resp not in RESPONSIBILITY_LEVELS):
        errors.append(f"{path}.responsibility_level 必须是：{_fmt(RESPONSIBILITY_LEVELS)}")

    status = value.get("verification_status")
    if "verification_status" in value and (not isinstance(status, str) or status not in VERIFICATION_STATUSES):
        errors.append(f"{path}.verification_status 必须是：{_fmt(VERIFICATION_STATUSES)}")

    stage = value.get("delivery_stage")
    if "delivery_stage" in value and (not isinstance(stage, str) or stage not in DELIVERY_STAGES):
        errors.append(f"{path}.delivery_stage 必须是：{_fmt(DELIVERY_STAGES)}")

    sources = value.get("sources")
    if isinstance(sources, list):
        if not sources:
            errors.append(f"{path}.sources 不能为空数组（没有出处的说法不许进账本）")
        for si, s in enumerate(sources):
            _source(s, f"{path}.sources[{si}]", errors)
    elif "sources" in value:
        errors.append(f"{path}.sources 必须是数组")

    for f in ("allowed_uses", "risk_notes"):
        if f in value:
            _str_list(value[f], f"{path}.{f}", errors)

    if "interview_details" in value:
        _interview(value["interview_details"], f"{path}.interview_details", errors)
    if "last_verified" in value:
        _date(value["last_verified"], f"{path}.last_verified", errors, nullable=True)

    # ── 跨字段矛盾检查（照抄 Asu 思路 + 本项目加严）────────────────────
    wording = value.get("candidate_wording")
    if status == "已确认" and isinstance(wording, str) and "【待补" in wording:
        errors.append(f"{path}.candidate_wording：已确认主张不能包含【待补】占位符")
    if isinstance(wording, str) and "【待补" in wording and status != "待确认":
        errors.append(f"{path}：正文含【待补】占位符时，verification_status 必须是『待确认』")
    if status == "已确认" and value.get("last_verified") in (None, ""):
        errors.append(f"{path}.last_verified：状态为『已确认』时必须填核验日期")
    if status == "已确认" and not value.get("allowed_uses"):
        errors.append(f"{path}.allowed_uses：状态为『已确认』时必须写明允许用在哪些材料里")


def validate_ledger(document: Any) -> tuple[int, list]:
    errors: list = []
    if not isinstance(document, dict):
        return 0, ["文档根节点必须是对象"]

    if document.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        errors.append(f"schema_version 必须是 {SUPPORTED_SCHEMA_VERSION}")

    profile = document.get("profile")
    if not isinstance(profile, dict):
        errors.append("profile 必须是对象")
    else:
        _require_fields(profile, REQUIRED_PROFILE_FIELDS, "profile", errors)
        if "candidate_id" in profile:
            _nonempty_str(profile["candidate_id"], "profile.candidate_id", errors)
        if "target_roles" in profile:
            _str_list(profile["target_roles"], "profile.target_roles", errors)
        if "updated_at" in profile:
            _date(profile["updated_at"], "profile.updated_at", errors, nullable=False)

    claims = document.get("claims")
    if not isinstance(claims, list):
        errors.append("claims 必须是数组")
        return 0, errors
    if len(claims) < 10:
        errors.append(f"claims 至少 10 条（当前 {len(claims)} 条）—— v2 方案 M0 的验收线")

    seen: set = set()
    for i, c in enumerate(claims):
        _claim(c, i, seen, errors)
    return len(claims), errors


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), []
    except OSError as exc:
        return None, [f"无法读取 {path}：{exc}"]
    except UnicodeError as exc:
        return None, [f"{path} 不是 UTF-8 编码：{exc}"]
    except json.JSONDecodeError as exc:
        return None, [f"{path} 不是合法 JSON：{exc}"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L2 结构校验：简历事实基线")
    ap.add_argument("ledger", nargs="?", type=Path, default=None,
                    help="默认 data/ledger.json；缺失时回退 data/ledger.example.json")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path, default=None, help="把 JSON 结果写到文件（用于 generated/）")
    args = ap.parse_args(argv)

    # 真实账本优先；没填过就用模板 —— 保证别人 clone 下来能跑通验收
    target = args.ledger or cfg.ledger_path()
    if args.ledger is None and target == cfg.LEDGER_EXAMPLE:
        print(f"  提示：{cfg.LEDGER_PATH.name} 不存在，本次校验的是模板 {target.name}"
              f"（clone 下来还没填自己档案时就是这种状态）")
    document, errors = load(target)
    count = 0
    if not errors:
        count, errors = validate_ledger(document)

    stage_dist: dict[str, int] = {}
    if not errors and isinstance(document, dict):
        for c in document.get("claims", []):
            stage_dist[c.get("delivery_stage", "?")] = stage_dist.get(c.get("delivery_stage", "?"), 0) + 1

    payload = {
        "ok": not errors,
        "layer": "L2 · 结构校验（账本）",
        # ⚠️ 必须写 target 而不是 args.ledger —— args.ledger 在「没显式传路径」时是 None，
        #    会往产物里写一个 "path": "None"（已踩过），而 check_generated 重算时拿的是真实路径
        #    → 两边永远对不上，防漂移无端变红。
        "path": str(target),
        "claim_count": count,
        "delivery_stage_distribution": stage_dist,
        "errors": errors,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # 产物是生成物，加个说明头（JSON 里不能加注释，所以额外写一个 .md 头）
        (args.out.with_suffix(".md")).write_text(
            "# ledger_check.json 说明\n\n"
            "> 本文件由 `python scripts/validate_ledger.py --json --out data/generated/ledger_check.json` 生成。\n"
            "> **禁止手工编辑** —— 改了会被 `scripts/check_generated.py` 判定为漂移。\n",
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif errors:
        print(f"ledger validation: {len(errors)} error(s)", file=sys.stderr)
        for e in errors:
            print(f"  FAIL  {e}", file=sys.stderr)
    else:
        print(f"ledger validation: {count} claims passed")
        for k, v in stage_dist.items():
            print(f"  delivery_stage {k}: {v}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
