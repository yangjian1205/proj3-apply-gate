#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑各能力单元自己的用例（不调模型，不花钱，秒级）。

为什么要有这个文件：
    主评测集（60 题）是端到端的，改一个单元就得跑全量、花全量的钱。
    单元用例的作用是「改哪儿测哪儿」—— 改规则词表只跑 hard-gate 的 18 条，
    改溯源逻辑只跑 provenance 的 12 条。这样改一个单元不会污染全局指标。

    python tests/run_unit_cases.py           跑全部
    python tests/run_unit_cases.py provenance 只跑一个单元
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config as cfg  # noqa: E402
from units.approval.impl import scan_output  # noqa: E402
from units.hard_gate.impl import check_hard_rules  # noqa: E402
from units.jd_intake.impl import IntakeError, intake  # noqa: E402
from units.provenance.impl import ProvenanceError, guard_readonly_base, verify  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def _record(case_id: str, ok: bool, detail: str = "") -> None:
    results.append((case_id, PASS if ok else FAIL, detail))


def run_jd_intake() -> None:
    data = json.loads((ROOT / "units" / "jd_intake" / "cases.json").read_text(encoding="utf-8"))
    for c in data["cases"]:
        try:
            out, err = intake(c["input"]), None
        except IntakeError as exc:
            out, err = None, str(exc)

        if c.get("expect_error_contains"):
            ok = err is not None and c["expect_error_contains"] in err
            detail = "" if ok else f"期望抛错含 {c['expect_error_contains']!r}，实际 err={err!r}"
        else:
            ok = err is None
            detail = "" if ok else f"不该报错，实际 {err}"
            if ok and "expect_city" in c:
                ok = out["city"] == c["expect_city"]
                detail = "" if ok else f"city 期望 {c['expect_city']!r}，实际 {out['city']!r}"
            if ok and "expect_stale" in c:
                ok = out["stale"] == c["expect_stale"]
                detail = "" if ok else f"stale 期望 {c['expect_stale']}，实际 {out['stale']}"
            if ok and c.get("expect_text_unchanged"):
                src = c["input"] if isinstance(c["input"], str) else c["input"]["text"]
                ok = out["text"] == src
                detail = "" if ok else "text 被改动了 —— 绝对禁止清洗 JD 原文（引文会因此对不上）"
        _record(c["id"], ok, detail)


def run_hard_gate() -> None:
    data = json.loads((ROOT / "units" / "hard_gate" / "cases.json").read_text(encoding="utf-8"))
    profile = data["profile"]
    for c in data["cases"]:
        res = check_hard_rules(c["jd_text"], profile=profile)
        flags = set(res["flags"])
        expected = set(c["expect_flags"])
        ok = flags == expected
        detail = "" if ok else f"期望 {sorted(expected)}，实际 {sorted(flags)}"

        if ok and c.get("expect_hints"):
            ok = set(c["expect_hints"]) <= {h["rule_id"] for h in res["hints"]}
            detail = "" if ok else f"期望提示 {c['expect_hints']}，实际 {[h['rule_id'] for h in res['hints']]}"

        if ok and c.get("expect_quote_contains"):
            needle = c["expect_quote_contains"]
            quotes = " ".join(res["quotes"].values())
            ok = needle in quotes
            detail = "" if ok else f"引文里没有 {needle!r}：{quotes!r}"

        # 引文必须是原文子串 —— 这条对每一题都验
        if ok:
            for rid, q in res["quotes"].items():
                if q not in c["jd_text"]:
                    ok, detail = False, f"{rid} 的引文不是 JD 子串：{q!r}"
                    break

        _record(c["id"], ok, detail)


def run_provenance() -> None:
    data = json.loads((ROOT / "units" / "provenance" / "cases.json").read_text(encoding="utf-8"))
    resume = data["resume"]
    for c in data["cases"]:
        res = verify(c["draft"], resume)
        ok = res["passed"] == c["expect_passed"]
        detail = "" if ok else f"期望 passed={c['expect_passed']}，实际 {res['passed']} missed={res['missed']}"

        if ok and c.get("expect_missed_contains"):
            joined = " ".join(res["missed"])
            missing = [m for m in c["expect_missed_contains"] if m not in joined]
            ok = not missing
            detail = "" if ok else f"missed 里没有 {missing}，实际 {res['missed']}"

        if ok and "expect_stage_violations" in c:
            got = [v["atom"] for v in res["stage_violations"]]
            missing = [m for m in c["expect_stage_violations"] if m not in got]
            ok = not missing
            detail = "" if ok else f"性质升格里没有 {missing}，实际 {got}"

        _record(c["id"], ok, detail)

    for c in data.get("path_cases", []):
        try:
            guard_readonly_base(c["draft_path"], cfg.RESUME_PATH)
            raised = None
        except ProvenanceError as exc:
            raised = str(exc)
        if c["expect_error"]:
            ok = raised is not None and c["expect_error"] in raised
            detail = "" if ok else f"期望抛错含 {c['expect_error']!r}，实际 {raised!r}"
        else:
            ok = raised is None
            detail = "" if ok else f"不该抛错，实际抛了 {raised!r}"
        _record(c["id"], ok, detail)


def run_approval() -> None:
    data = json.loads((ROOT / "units" / "approval" / "cases.json").read_text(encoding="utf-8"))
    for c in data["cases"]:
        hits = scan_output(c["text"])
        expected = set(c["expect_forbidden"])
        ok = expected <= set(hits)
        _record(c["id"], ok, "" if ok else f"期望命中 {sorted(expected)}，实际 {sorted(hits)}")


def run_match_score() -> None:
    """五态→三分类的映射是代码常量，不经过模型，可以离线断言。"""
    data = json.loads((ROOT / "units" / "match_score" / "cases.json").read_text(encoding="utf-8"))
    for c in data["mapping_cases"]:
        got = cfg.STAGE_TO_VERDICT.get(c["stage_5"])
        ok = got == c["expect_verdict"]
        _record(f"ms-map-{c['stage_5']}", ok,
                "" if ok else f"期望 {c['expect_verdict']}，实际 {got}")

    ok = len(cfg.STAGES) == 5
    _record("ms-inv-001", ok, "" if ok else f"五态应该是 5 个，实际 {len(cfg.STAGES)}")

    ok = all(v in cfg.VERDICTS for v in cfg.STAGE_TO_VERDICT.values())
    _record("ms-inv-002", ok, "" if ok else "有某个五态映射到了非法三分类值")

    # 硬门槛 reject 必须以代码结论为准（这条在 match() 里强制，这里验它有对应的代码分支）
    src = (ROOT / "units" / "match_score" / "impl.py").read_text(encoding="utf-8")
    ok = "hard_result.get(\"hard_verdict\") == cfg.REJECT" in src
    _record("ms-inv-003", ok, "" if ok else "match() 里缺少「硬门槛结论优先」的强制分支")

    ok = "待确认" in src and "degraded" in src
    _record("ms-inv-004", ok, "" if ok else "缺少「不合 schema → 降级待确认」的处理")


def run_tailor_mapping() -> None:
    """逐条改写处方的代码核对（纯代码，离线可断言）。

    这是「教人改简历」最容易出事的地方：模型很乐意替你润色出一条你没做过的经历。
    核不上出处的素材必须被降级为「真实缺口」并抹掉 —— 宁可少一条「可以这样写」，
    也不能给一条不存在的素材。
    """
    from types import SimpleNamespace

    from units.tailor.impl import verify_mapping

    data = json.loads((ROOT / "units" / "tailor" / "cases.json").read_text(encoding="utf-8"))
    mc = data["mapping_cases"]

    for c in mc["cases"]:
        kw = {"jd_requirement": "", "jd_quote": "", "resume_evidence": "",
              "suggestion": "", "status": "可强化"}
        kw.update(c["item"])
        got = verify_mapping([SimpleNamespace(**kw)], mc["jd"], mc["resume"])[0]

        problems = []
        if got["status"] != c["expect_status"]:
            problems.append(f"status 期望 {c['expect_status']}，实际 {got['status']}")
        if "expect_evidence" in c and got["resume_evidence"] != c["expect_evidence"]:
            problems.append(f"素材 期望 {c['expect_evidence']!r}，实际 {got['resume_evidence']!r}")
        if "expect_quote_verified" in c and got["quote_verified"] != c["expect_quote_verified"]:
            problems.append(
                f"quote 核对 期望 {c['expect_quote_verified']}，实际 {got['quote_verified']}"
            )
        _record(f"tl-{c['id']}", not problems, "；".join(problems))

    # 处方条数上限：再多也不许无限膨胀（prompt 与代码都限 12 条）
    many = [SimpleNamespace(jd_requirement="x", jd_quote="本科及以上学历", resume_evidence="本科",
                            suggestion="", status="已覆盖") for _ in range(20)]
    ok = len(verify_mapping(many, mc["jd"], mc["resume"])) <= 12
    _record("tl-inv-001", ok, "" if ok else "处方条数没有上限，会无限膨胀")

    # 非法 status 必须被规范到三值之一，不许原样透传给页面
    bad = SimpleNamespace(jd_requirement="x", jd_quote="本科及以上学历", resume_evidence="本科",
                          suggestion="", status="随便写的状态")
    ok = verify_mapping([bad], mc["jd"], mc["resume"])[0]["status"] in ("已覆盖", "可强化", "真实缺口")
    _record("tl-inv-002", ok, "" if ok else "非法 status 没有被规范到三值之一")


def run_approval_gates() -> None:
    """审批三道关：待办 404 → 会话真挂起 409 → 单号一致 409。用临时库跑，不碰真数据。"""
    import tempfile

    from core import store as store_mod
    from units.approval.impl import check_gates

    tmp_db = Path(tempfile.mkdtemp(prefix="proj3-gate-")) / "t.db"
    conn = store_mod.connect(tmp_db)
    try:
        store_mod.create_ticket(conn, "t-1", "tr-1", "jd-1", "th-1")

        g = check_gates(conn, "t-none", "th-1", ("approval",))
        _record("ap-gate-001", g.code == 404, f"不存在的单号应 404，实际 {g.code}：{g.msg}")

        g = check_gates(conn, "t-1", "th-1", ())
        ok = g.code == 409 and "挂起" in g.msg
        _record("ap-gate-002", ok, f"会话没挂起应 409，实际 {g.code}：{g.msg}")

        g = check_gates(conn, "t-1", "th-OTHER", ("approval",))
        _record("ap-gate-003", g.code == 409, f"单号不一致应 409，实际 {g.code}：{g.msg}")

        g = check_gates(conn, "t-1", "th-1", ("approval",))
        _record("ap-gate-004", g.ok and g.code == 200, f"三关全过应 200，实际 {g.code}：{g.msg}")

        store_mod.resolve_ticket(conn, "t-1", "approved", "test")
        g = check_gates(conn, "t-1", "th-1", ("approval",))
        _record("ap-gate-005", g.code == 409, f"已解决的单号不该再次批准，实际 {g.code}：{g.msg}")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    targets = argv or sys.argv[1:]
    runners = {"jd-intake": run_jd_intake, "jd_intake": run_jd_intake,
               "hard-gate": run_hard_gate, "hard_gate": run_hard_gate,
               "provenance": run_provenance, "approval": run_approval,
               "approval-gates": run_approval_gates,
               "match-score": run_match_score, "match_score": run_match_score,
               "tailor-mapping": run_tailor_mapping, "tailor_mapping": run_tailor_mapping}

    if not targets or "all" in targets:
        for fn in (run_jd_intake, run_hard_gate, run_provenance,
                   run_approval, run_approval_gates, run_match_score, run_tailor_mapping):
            fn()
    else:
        for t in targets:
            if t not in runners:
                print(f"未知单元 {t!r}，可选：{list(runners)}")
                return 2
            runners[t]()

    failed = [r for r in results if r[1] == FAIL]
    print(f"单元用例：{len(results)} 条，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for cid, status, detail in results:
        if status == FAIL:
            print(f"  FAIL  {cid}  {detail}")
    if not failed:
        print("  全部通过 ✅（改规则/改判定后先跑这个，再跑 scripts/gate.py 的全量门禁）")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
