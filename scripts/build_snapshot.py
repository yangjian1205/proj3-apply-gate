#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 audit.db 生成工作台快照（严格按 workbench/CONTRACT.md 的字段）。

为什么要这一步而不是直接读库渲染：
    看板和后端之间需要一份**冻结的契约**。后端只负责产出这份结构，工作台只负责渲染它。
    谁都不许偷偷加字段 —— 加字段要走 CONTRACT 第五节的流程（先答「这个字段谁产出？」）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfg  # noqa: E402
from core import store  # noqa: E402


def build() -> dict:
    conn = store.connect()
    try:
        rows = store.fetch_judgements(conn)
        cost = store.cost_summary(conn)
    finally:
        conn.close()

    # 记录结构由 store.to_workbench_record 统一产出 —— 网页工作台用的是同一个函数，
    # 避免「看板有数据、页面没数据」这种字段错位。
    records = [store.to_workbench_record(r) for r in rows]

    stats = {
        "total": len(records),
        "apply": sum(1 for r in records if r["verdict"] == cfg.APPLY),
        "reject": sum(1 for r in records if r["verdict"] == cfg.REJECT),
        "borderline": sum(1 for r in records if r["verdict"] == cfg.BORDERLINE),
        "pending": sum(1 for r in records if r["approval_status"] == cfg.APPROVAL_PENDING),
        "cost": round(cost.get("cost_cny", 0.0), 6),
    }

    rules = cfg.load_rules()
    return {
        "generated_at": store.now_iso(),
        "source": {
            "db": str(cfg.AUDIT_DB),
            "rules_version": f"{len(rules['rules'])} 条规则 / {rules.get('rules_version')}",
            "records": len(records),
            "calls": cost.get("calls", 0),
            "tokens": {"input": cost.get("input_tokens", 0), "output": cost.get("output_tokens", 0)},
        },
        "stats": stats,
        "cost_by_node": cost.get("by_node", []),
        "records": records,
    }


def main() -> int:
    snapshot = build()
    cfg.WORKBENCH_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    cfg.WORKBENCH_SNAPSHOT.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    s = snapshot["stats"]
    print(f"[build_snapshot] 已生成 {cfg.WORKBENCH_SNAPSHOT}")
    print(f"  {snapshot['generated_at']}　记录 {s['total']} 条"
          f"（{cfg.VERDICT_CN[cfg.APPLY]} {s['apply']} / {cfg.VERDICT_CN[cfg.REJECT]} {s['reject']}"
          f" / {cfg.VERDICT_CN[cfg.BORDERLINE]} {s['borderline']}）")
    print(f"  待审批 {s['pending']} 条　累计花费 ¥{s['cost']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
