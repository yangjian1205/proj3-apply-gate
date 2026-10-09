# -*- coding: utf-8 -*-
"""units/cost-report · 记账与出稿（纯代码）

价格按官方的高峰 / 空闲两档分别计费（见 core/llm.py 的价格表与出处）。
记账失败必须报错，不许静默 —— 记账丢了，成本指标就是假的。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core import config as cfg  # noqa: E402
from core import store  # noqa: E402

VERDICT_CN = cfg.VERDICT_CN


def render_verdict_md(
    jd: dict,
    hard_result: dict,
    match_result: dict,
    *,
    provenance_result: dict | None = None,
    approval_status: str = cfg.APPROVAL_NOT_NEEDED,
    ticket_id: str | None = None,
    cost: dict | None = None,
    trace_id: str = "",
) -> str:
    verdict = match_result.get("verdict", cfg.BORDERLINE)
    lines = [
        f"# 判定报告 · {jd.get('company')} / {jd.get('title')}",
        "",
        f"- **判定：{VERDICT_CN.get(verdict, verdict)}**（对内五态：{match_result.get('stage_5')}）",
        f"- 岗位 ID：`{jd.get('jd_id')}`　抓取于 {jd.get('fetched_at')}",
        f"- trace_id：`{trace_id}`",
    ]
    if jd.get("stale"):
        lines.append(f"- ⚠️ 该 JD 已抓取 {jd.get('days_old')} 天，可能已失效，建议以官网为准")

    lines += ["", "## 硬门槛闸（纯代码判定，不经过模型）", ""]
    if hard_result.get("flags"):
        lines.append(f"命中 **{len(hard_result['flags'])}** 条硬规则：")
        lines.append("")
        for i, r in enumerate(hard_result.get("reasons", []), 1):
            lines += [
                f"**{i}. `{r['rule_id']}`（{'绝对硬' if r['tier'] == 'absolute' else '阈值硬'}）**",
                "",
                f"- 规则：{r['label']}",
                f"- JD 原句：「{r['quote']}」",
                f"- 建议：{r['advice']}",
                "",
            ]
        lines.append("> 后续：未进入打分与改写环节（本次 0 次模型调用，未产生费用）")
    else:
        lines.append("未命中任何硬规则。")
        if hard_result.get("hints"):
            lines.append("")
            lines.append("以下只提示、不拦截：")
            for h in hard_result["hints"]:
                lines += ["", f"- `{h['rule_id']}`：{h['label']}", f"  - JD 原句：「{h['quote']}」", f"  - {h['advice']}"]

    if match_result.get("degraded"):
        lines += ["", "## ⚠️ 降级说明", "", "模型未给出合规的结构化结果，按设计降级为「待确认」交人工，**没有猜测分类**。"]

    lines += ["", "## 分项打分", ""]
    score = match_result.get("score", {})
    lines += [
        "| 维度 | 分数 | 说明 |", "|---|---|---|",
        f"| 相关度 | {score.get('relevant', '—')}/10 | 经历与岗位方向的贴合程度 |",
        f"| 职级匹配 | {score.get('level', '—')}/10 | 要求职级与本人层级的差距（只影响打分，不影响是否投） |",
        f"| 技能覆盖 | {score.get('skill', '—')}/10 | JD 要求的技术栈在简历里的覆盖比例 |",
        f"| **合计** | **{match_result.get('score_total', '—')}/30** | |",
        "",
        "## 判定理由",
        "",
    ]
    lines += [f"- {r}" for r in match_result.get("reasons", [])] or ["- （无）"]
    if match_result.get("missing_info"):
        lines += ["", "## 缺什么信息（这就是为什么交给你判断）", ""]
        lines += [f"- {m}" for m in match_result["missing_info"]]
    if match_result.get("gap_anchor"):
        lines += ["", "## 判定锚点", "", f"- {match_result['gap_anchor']}"]

    if provenance_result:
        lines += [
            "", "## 溯源校验", "",
            f"- 事实原子 {provenance_result.get('atom_count')} 个"
            f"（A {provenance_result['by_type']['A']} / B {provenance_result['by_type']['B']} / C {provenance_result['by_type']['C']}）",
            f"- 结果：**{'✅ 全部找到出处' if provenance_result.get('passed') else '❌ 未通过'}**",
            f"- 改写稿性质档位上限：{provenance_result.get('ceiling')}",
        ]
        if provenance_result.get("missed"):
            lines.append("- 未命中清单：")
            lines += [f"  - {m}" for m in provenance_result["missed"]]

    lines += ["", "## 状态", "",
              f"- 审批状态：**{approval_status}**",
              f"- 审批单号：{ticket_id or '—'}"]
    if cost:
        lines += [
            "", "## 成本", "",
            f"- 模型调用：{cost.get('calls')} 次",
            f"- 输入 tokens：{cost.get('input_tokens')}　输出 tokens：{cost.get('output_tokens')}",
            f"- 费用：**¥{cost.get('cost_cny')}**",
            f"- 耗时：{cost.get('elapsed')} 秒",
        ]
    lines += ["", "---", "", f"> 生成于 {store.now_iso()}　价格口径见 README（含出处链接）"]
    return "\n".join(lines) + "\n"


def render_rewrite_guide_md(jd: dict, match_result: dict, draft: dict) -> str:
    """「这份 JD 该怎么改简历」—— 使用者真正要拿去做事的那份文件。

    与 resume.md 的区别（这条很关键）：
      - resume.md / cover_letter.md 是**成品稿**，属于「可投递材料」→ 必须审批后才写；
      - 本文件是**指导材料**（你该怎么改），不含可直接投递的成品 → 不需要审批，判定完就能看。
    所以它不违反「未经审批不出稿」，反而让人在审批前就看清稿子是怎么来的。
    """
    mapping = draft.get("jd_mapping") or []
    changes = draft.get("changes") or []
    verdict = VERDICT_CN.get(match_result.get("verdict"), "")

    lines = [
        f"# 这份 JD 该怎么改简历 · {jd.get('company')} / {jd.get('title')}",
        "",
        f"- 判定：**{verdict}**（对内五态：{match_result.get('stage_5')}）",
        f"- 岗位 ID：`{jd.get('jd_id')}`　逐条处方：{len(mapping)} 条",
        "",
        "> 下面建议里出现的每一个数字、年限、公司名、技术名都过了代码核对 —— 能在你简历原文里找到出处。",
        "> 核不上的一律按「真实缺口」处理，**不会教你把没做过的事写上去**。",
        "",
    ]

    if not mapping:
        lines += ["（本条没有生成逐条处方。）", ""]
        return "\n".join(lines) + "\n"

    gaps = [x for x in mapping if x["status"] == "真实缺口"]
    if gaps:
        lines += ["## ⚠️ 先看真实缺口 —— 这几条别编", ""]
        for x in gaps:
            lines += [
                f"- **{x['jd_requirement']}**",
                f"  - JD 原句：「{x['jd_quote']}」",
                f"  - 怎么办：{x['suggestion']}",
            ]
        lines.append("")

    lines += ["## 逐条对照（该强化的排前面）", ""]
    order = {"可强化": 0, "已覆盖": 1, "真实缺口": 2}
    for i, x in enumerate(sorted(mapping, key=lambda v: order.get(v["status"], 9)), 1):
        lines += [f"### {i}. [{x['status']}] {x['jd_requirement']}", ""]
        lines.append(
            f"- **JD 原句**：「{x['jd_quote']}」"
            + ("" if x["quote_verified"] else "　⚠️ 未能与 JD 原文核对上")
        )
        lines.append(
            "- **你简历里的素材**："
            + (f"「{x['resume_evidence']}」" if x["resume_evidence"] else "（没有 —— 这条是真实缺口）")
        )
        lines.append(f"- **怎么改**：{x['suggestion']}")
        if x["note"]:
            lines.append(f"- ⚠️ 代码核对提示：{x['note']}")
        lines.append("")

    if changes:
        lines += ["## 这次做了哪些重排与重述", ""] + [f"- {c}" for c in changes] + [""]

    if (draft.get("cover_letter_md") or "").strip():
        lines += ["## 招呼语（可直接用，也可以自己改）", "", "```text",
                  draft["cover_letter_md"].strip(), "```", ""]

    lines += [
        "---", "",
        f"> 生成于 {store.now_iso()}。本文件不经过审批 —— 它不是可投递稿件；"
        "改写后的成品稿（resume.md / cover_letter.md）必须由你批准才会写出。",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(
    jd: dict,
    hard_result: dict,
    match_result: dict,
    *,
    draft: dict | None = None,
    provenance_result: dict | None = None,
    approval_status: str = cfg.APPROVAL_NOT_NEEDED,
    ticket_id: str | None = None,
    cost: dict | None = None,
    trace_id: str = "",
) -> dict:
    """写 output/<jd_id>/ 下的四个文件。驳回或未批准时只写 verdict.md（作为记录）。"""
    from units.provenance.impl import to_evidence_markdown

    out_dir = cfg.OUTPUT_DIR / jd["jd_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    verdict_path = out_dir / "verdict.md"
    verdict_path.write_text(
        render_verdict_md(jd, hard_result, match_result,
                          provenance_result=provenance_result,
                          approval_status=approval_status, ticket_id=ticket_id,
                          cost=cost, trace_id=trace_id),
        encoding="utf-8",
    )
    written.append(str(verdict_path))

    # 逐条改写处方：走出改写环节就写，**不等审批** —— 它不是可投递稿件。
    if draft and (draft.get("jd_mapping") or draft.get("changes")):
        guide_path = out_dir / "rewrite_guide.md"
        guide_path.write_text(render_rewrite_guide_md(jd, match_result, draft), encoding="utf-8")
        written.append(str(guide_path))

    if draft and approval_status == cfg.APPROVAL_APPROVED:
        (out_dir / "resume.md").write_text(draft["resume_md"], encoding="utf-8")
        (out_dir / "cover_letter.md").write_text(draft["cover_letter_md"], encoding="utf-8")
        written += [str(out_dir / "resume.md"), str(out_dir / "cover_letter.md")]
        if provenance_result:
            (out_dir / "evidence.md").write_text(
                to_evidence_markdown(provenance_result, jd), encoding="utf-8"
            )
            written.append(str(out_dir / "evidence.md"))

    meta = {
        "jd_id": jd["jd_id"], "trace_id": trace_id,
        "approval_status": approval_status, "ticket_id": ticket_id,
        "written": written,
        # ⚠️ 判断「本次是否写出了可投递稿」只能看审批状态，**不能看文件是否存在** ——
        #    同一 jd_id 重跑时旧稿还在，用 exists() 会把「没批准」误报成「未审批就放行」。
        "resume_path": (
            str(out_dir / "resume.md")
            if (draft and approval_status == cfg.APPROVAL_APPROVED)
            else None
        ),
        "resume_file_exists": (out_dir / "resume.md").exists(),
    }
    (out_dir / "run_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    written.append(str(out_dir / "run_meta.json"))
    return meta


def build_record(
    jd: dict,
    hard_result: dict,
    match_result: dict,
    *,
    draft: dict | None = None,
    provenance_result: dict | None = None,
    approval_status: str = cfg.APPROVAL_NOT_NEEDED,
    ticket_id: str | None = None,
    cost: dict | None = None,
    elapsed: float = 0.0,
    audit: list[dict] | None = None,
    trace_id: str = "",
) -> dict:
    """构造快照记录 —— 字段严格按 workbench/CONTRACT.md，不许多一个少一个。"""
    resume_text = ""
    if cfg.RESUME_PATH.exists():
        resume_text = cfg.RESUME_PATH.read_text(encoding="utf-8")
    resume_lines = [
        {"n": i, "text": ln} for i, ln in enumerate(resume_text.splitlines(), 1) if ln.strip()
    ]
    draft_lines = []
    if draft:
        draft_lines = [
            {"n": i, "text": ln, "atoms": [
                a["atom"] for a in (provenance_result or {}).get("atoms", [])
                if a["atom"] in ln
            ]}
            for i, ln in enumerate(draft["resume_md"].splitlines(), 1) if ln.strip()
        ]

    return {
        "jd_id": jd["jd_id"],
        "company": jd.get("company", ""),
        "title": jd.get("title", ""),
        "city": jd.get("city", ""),
        "fetched_at": jd.get("fetched_at", ""),
        "stale": bool(jd.get("stale")),
        "verdict": match_result.get("verdict"),
        "verdict_5": match_result.get("stage_5"),
        "hard_flags": [
            {"rule_id": r["rule_id"], "quote": r["quote"]} for r in hard_result.get("reasons", [])
        ],
        "hints": [
            {"rule_id": h["rule_id"], "quote": h["quote"]} for h in hard_result.get("hints", [])
        ],
        "score": match_result.get("score", {}),
        "reasons": match_result.get("reasons", []),
        "missing_info": match_result.get("missing_info", []),
        "jd_text": jd["text"],
        "draft_path": (
            str(cfg.OUTPUT_DIR / jd["jd_id"] / "resume.md")
            if draft and approval_status == cfg.APPROVAL_APPROVED else None
        ),
        "resume_base": resume_lines,
        "draft_lines": draft_lines,
        "atoms": (provenance_result or {}).get("atoms", []),
        "evidence_map": (provenance_result or {}).get("evidence_map", []),
        "missed": (provenance_result or {}).get("missed", []),
        # ↓ 「怎么改简历」：逐条处方 / 修改说明 / 招呼语。
        #   工作台与看板的「怎么改」视图都读这三个字段。
        "jd_mapping": (draft or {}).get("jd_mapping", []),
        "changes": (draft or {}).get("changes", []),
        "cover_letter": (draft or {}).get("cover_letter_md", ""),
        "rewrite_round": (provenance_result or {}).get("rewrite_round", (draft or {}).get("rewrite_round", 0)),
        "cost": (cost or {}).get("cost_cny", 0.0),
        "elapsed": round(elapsed, 3),
        "ticket_id": ticket_id,
        "approval_status": approval_status,
        "audit": audit or [],
        "trace_id": trace_id,
    }
