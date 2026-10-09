#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自研 MCP Server：把硬门槛规则引擎暴露给任何 MCP 客户端。

为什么要包成 MCP Server（而不是留一个 Python 函数）：
    硬规则引擎是这个项目里最确定、最可复用的部分。包成 MCP 之后，任何支持 MCP 的客户端
    （Claude Desktop、其他 Agent 框架）都能独立调用「这条 JD 有没有硬门槛冲突」，
    而不需要装这个项目的其余部分。

这是**自己写 Server**，不是只当客户端 —— 两个工具：
    check_hard_rules(jd_text)    跑硬门槛规则
    explain_rule(rule_id)        查某条规则的完整定义

运行（stdio 模式）：
    python mcp_server.py

在 MCP 客户端里配置：
    {
      "mcpServers": {
        "proj3-apply-gate": {
          "command": "E:/agent-learning/day1-first-llm/venv/Scripts/python.exe",
          "args": ["E:/agent-learning/proj3-apply-gate/mcp_server.py"]
        }
      }
    }

注意：全程不调模型。硬规则本来就是确定性的，走 MCP 也一样不花钱。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import config as cfg  # noqa: E402
from units.hard_gate.impl import check_hard_rules  # noqa: E402

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover
    print("缺少 mcp 包：pip install mcp", file=sys.stderr)
    raise SystemExit(1)

mcp = FastMCP("proj3-apply-gate")


@mcp.tool()
def check_hard_rules_tool(jd_text: str) -> str:
    """跑硬门槛规则引擎，判断这条 JD 与候选人档案是否存在客观事实冲突。

    Args:
        jd_text: JD 原文全文。一个字都不要清洗 —— 返回的引文要能对得上原文。

    Returns:
        JSON 字符串，含 hard_verdict（pass/reject）、命中的 rule_id 与**JD 原句引文**、
        只提示不拦的项、以及第三档命中的词（那些只影响打分，不拦人）。
    """
    result = check_hard_rules(jd_text)
    payload = {
        "hard_verdict": result["hard_verdict"],
        "flags": result["flags"],
        "reasons": [
            {"rule_id": r["rule_id"], "tier": r["tier"], "label": r["label"], "quote": r["quote"]}
            for r in result["reasons"]
        ],
        "hints": [{"rule_id": h["rule_id"], "quote": h["quote"]} for h in result["hints"]],
        "soft_hits": result["soft_hits"],
        "rules_version": result["rules_version"],
        "note": "判定完全由确定性代码给出，不经过大模型；quote 一定是传入 jd_text 的子串。",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


@mcp.tool()
def explain_rule(rule_id: str) -> str:
    """查某条硬门槛规则的完整定义：档位、触发词、排除词、建议、以及它的能力边界。

    Args:
        rule_id: 规则 ID，例如 no_sponsorship / onsite_only / years_gap_reject。
    """
    rules = cfg.load_rules()
    for r in rules["rules"]:
        if r["rule_id"] == rule_id:
            return json.dumps({
                "rule_id": r["rule_id"], "tier": r["tier"], "label": r.get("label", ""),
                "check": r.get("check"), "patterns": r.get("patterns", []),
                "exclude_patterns": r.get("exclude_patterns", []),
                "advice": r.get("advice", ""), "boundary": r.get("boundary", ""),
            }, ensure_ascii=False, indent=2)
    return json.dumps(
        {"error": f"没有规则 {rule_id}", "available": [r["rule_id"] for r in rules["rules"]]},
        ensure_ascii=False, indent=2,
    )


@mcp.tool()
def list_rules() -> str:
    """列出全部硬门槛规则（按三档分组），不返回词表细节。"""
    rules = cfg.load_rules()
    grouped: dict[str, list] = {t: [] for t in (cfg.TIER_ABSOLUTE, cfg.TIER_THRESHOLD, cfg.TIER_SOFT)}
    for r in rules["rules"]:
        grouped[r["tier"]].append({
            "rule_id": r["rule_id"], "label": r.get("label", ""),
            "hint_only": bool(r.get("hint_only")),
        })
    return json.dumps({
        "rules_version": rules.get("rules_version"),
        "tiers": {k: v for k, v in grouped.items()},
        "thresholds": rules.get("thresholds", {}),
        "note": "absolute=命中即拒；threshold=按阈值，可能只提示；soft=只打分，永不拦人。",
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
