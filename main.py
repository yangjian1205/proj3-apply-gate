#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""proj3-apply-gate · 投递决策闸门 CLI

    python main.py doctor                       自检：密钥、简历、五套资料、规则文件齐不齐
    python main.py judge --jd "JD 全文"          判一个岗位（粘贴文本）
    python main.py judge --jd data/jd/jd-007.json
    python main.py judge --batch data/jd/        批量判（只对「该投」跑改写）
    python main.py pending                      看有哪些稿子等着点头
    python main.py approve t-xxx                批准出稿
    python main.py reject  t-xxx --reason "..."  驳回（不出稿）
    python main.py dashboard                    刷新看板

附加（给开发和验收用）：
    python main.py gate                         L1 确定性门禁
    python main.py eval                         L3 指标（60 题，会花钱）

设计上的三条边界，在这里就能看出来：
    · 判「不该投」的岗位**不会**进入改写环节 —— 一分钱不花
    · 出稿前必须人工点头，没点头拿不到 output/<jd_id>/resume.md
    · 不做爬虫、不填表、不提交 —— 这些动作不在本项目范围内
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:  # Windows 控制台默认是 GBK，不转一下中文会炸
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from core import config as cfg  # noqa: E402
from core import store  # noqa: E402

LINE = "─" * 62
VERDICT_CN = cfg.VERDICT_CN


def _hr(title: str = "") -> None:
    print(LINE)
    if title:
        print(title)
        print(LINE)


def _trace_id() -> str:
    return f"tr-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


# ── doctor ────────────────────────────────────────────────────────────
def cmd_doctor(args) -> int:
    _hr("proj3-apply-gate · 自检")
    ok = True

    # 0 档案来源 —— 最先说。用示例档案时，后面所有判定都不代表「你」。
    # 别人 clone 下来还没填档案时就是这种状态，必须明说，不能让人误以为在判自己的简历。
    for loader in (cfg.load_profile, cfg.load_resume, cfg.load_ledger,
                   cfg.load_experience_bank):
        try:
            loader()
        except Exception:  # noqa: BLE001 —— 缺失交给下面的逐项检查报，这里只负责触发回退检测
            pass
    used = cfg.using_examples()
    if used:
        print()
        print(f"  ⚠️  你当前用的是**示例档案**，不是你的：{'、'.join(used)}")
        print("      示例档案来自仓库里的 *.example.* 模板（已脱敏）。")
        print("      换成你自己的做法：把下面这几个复制成去掉 .example 的名字，再填你的信息")
        print("        data/profile/resume.example.md              -> resume.md")
        print("        data/profile/candidate_profile.example.json -> candidate_profile.json")
        print("        data/ledger.example.json                    -> ledger.json")
        print("        （其余 *.example.md 同理）")
        print("      在此之前，所有判定只演示流程，不代表你的真实情况。")
        print()

    def check(label: str, passed: bool, detail: str = "", fix: str = "") -> None:
        nonlocal ok
        mark = "✅" if passed else "❌"
        print(f"  {mark} {label}" + (f" —— {detail}" if detail else ""))
        if not passed:
            ok = False
            if fix:
                print(f"      修法：{fix}")

    # 1 密钥
    env_path = ROOT / ".env"
    check(".env 文件", env_path.exists(), str(env_path), "复制 .env.example 为 .env 并填 key")
    import os

    from dotenv import load_dotenv

    load_dotenv(env_path)
    key = os.getenv("DEEPSEEK_API_KEY", "")
    check("DEEPSEEK_API_KEY", bool(key), f"已配置（{key[:6]}…）" if key else "未配置",
          "在 .env 里填 DEEPSEEK_API_KEY=你的key")
    print(f"     模型：{os.getenv('DEEPSEEK_MODEL', 'deepseek-flash')}"
          f"（价格随高峰/空闲时段不同，见 README）")

    # 2 真源
    try:
        rules = cfg.load_rules()
        check("rules.json", True,
              f"{len(rules['rules'])} 条规则 / 三档 / 版本 {rules.get('rules_version')}")
    except Exception as exc:  # noqa: BLE001
        check("rules.json", False, str(exc), "python scripts/gen_from_rules.py")

    try:
        ledger = cfg.load_ledger()
        claims = ledger.get("claims", [])
        with_stage = sum(1 for c in claims if c.get("delivery_stage"))
        check("ledger.json", len(claims) >= 10 and with_stage == len(claims),
              f"{len(claims)} 条主张，{with_stage} 条带 delivery_stage",
              "python scripts/validate_ledger.py")
    except Exception as exc:  # noqa: BLE001
        check("ledger.json", False, str(exc))

    # 3 简历与五套资料
    profile_files = ["resume.md", "candidate_profile.md", "candidate_profile.json",
                     "application_rules.md", "resume_routing.md", "answer_bank.md",
                     "experience_bank.md"]
    missing = [f for f in profile_files if not (cfg.PROFILE_DIR / f).exists()]
    if missing and cfg.using_examples():
        # 有模板可回退 → 程序能跑通，但判定的是示例人物，不是你。
        # 措辞要跟上面的警告一致，别一边说「用的是模板」一边说「缺文件」。
        check("五套素材库 + 简历", False,
              f"有 {len(missing)} 个还没换成你自己的（当前用示例模板跑）",
              "按上面的说明，把 *.example.* 复制成去掉 .example 的名字再填你的信息")
    else:
        check("五套素材库 + 简历", not missing,
              "全部就位" if not missing else f"缺 {missing}",
              "见 data/profile/ 下的模板")

    try:
        profile = cfg.load_profile()
        confirmed = profile.get("_confirmed")
        todo = profile.get("_todo", [])
        check("档案已确认", bool(confirmed),
              "已确认" if confirmed else f"还有 {len(todo)} 项待你确认",
              "填 data/profile/candidate_profile.json 后把 _confirmed 改成 true")
        if todo:
            for t in todo[:3]:
                print(f"      · {t}")
    except Exception as exc:  # noqa: BLE001
        check("candidate_profile.json", False, str(exc))

    # 4 评测集
    jd_files = sorted(cfg.JD_DIR.glob("jd-*.json"))
    check("评测集", len(jd_files) >= 45, f"{len(jd_files)} 题（铁律：宁可 45 题扎实，不要 60 题注水）")

    # 5 生成物
    gen_files = ["rules_book.md", "prompt_rules.txt", "rule_ids.json", "ledger_check.json"]
    gen_missing = [f for f in gen_files if not (cfg.GENERATED_DIR / f).exists()]
    check("生成物", not gen_missing, "全部就位" if not gen_missing else f"缺 {gen_missing}",
          "python scripts/gen_from_rules.py && "
          "python scripts/validate_ledger.py --json --out data/generated/ledger_check.json")

    # 6 数据库
    try:
        conn = store.connect()
        counts = {t: store.row_count(conn, t) for t in ("audit", "cost", "tickets", "judgements")}
        conn.close()
        check("audit.db", True, f"表就绪 {counts}")
    except Exception as exc:  # noqa: BLE001
        check("audit.db", False, str(exc))

    print(LINE)
    print("自检结果：" + ("全部就绪，可以开工 ✅" if ok else "有项目未就绪 ❌（按上面的修法处理）"))
    return 0 if ok else 1


# ── judge ─────────────────────────────────────────────────────────────
def _print_interrupt(payload: dict) -> None:
    _hr("⏸ 稿件待审批（不是「可投递」）")
    print(f"  单号：{payload.get('ticket_id')}")
    print(f"  岗位：{payload.get('company')} · {payload.get('title')}")
    print(f"  判定：{VERDICT_CN.get(payload.get('verdict'), payload.get('verdict'))}"
          f"（对内五态：{payload.get('verdict_5')}）")
    print(f"  溯源校验：{'✅ 通过' if payload.get('provenance_passed') else '❌ 未通过'}"
          f"，重写 {payload.get('rewrite_round')} 轮")
    changes = payload.get("changes") or []
    if changes:
        print("  本次改写做的事：")
        for c in changes[:8]:
            print(f"    · {c}")
    print(LINE)
    print("  下一步（二选一）：")
    print(f"    python main.py approve {payload.get('ticket_id')}")
    print(f"    python main.py reject  {payload.get('ticket_id')} --reason \"原因\"")
    print("  注意：批准后稿子写到 output/<jd_id>/，提交动作由你自己在平台上点。")


def _cost_from_usage(records: list | None) -> dict:
    """挂起时 outcome 还没写入，成本要从 usage_records 现算 —— 否则界面显示 ¥0，是假数字。"""
    records = records or []
    return {
        "calls": len(records),
        "cost_cny": round(sum(r.get("cost_cny", 0) for r in records), 6),
        "input_tokens": sum(r.get("input_tokens", 0) for r in records),
        "output_tokens": sum(r.get("output_tokens", 0) for r in records),
        "elapsed": round(sum(r.get("elapsed", 0) for r in records), 3),
    }


def _print_outcome(state: dict, *, verbose: bool = True) -> None:
    """打印判定摘要。

    ⚠️ 图在 approval 节点会 interrupt，此时 outcome 字段还没被 report 节点写入，
       所以要能从 state 的中间结果兜底派生 —— 否则挂起时界面会显示「判定：None」，误导人。
    """
    out = state.get("outcome") or {}
    jd = state.get("jd") or {}
    hard = state.get("hard_result") or {}
    m = state.get("match_result") or {}
    prov = state.get("provenance_result") or {}
    interrupted = bool(state.get("__interrupt__"))

    hard_reject = hard.get("hard_verdict") == cfg.REJECT
    verdict = out.get("verdict") or m.get("verdict") or (cfg.REJECT if hard_reject else None)
    stage_5 = out.get("stage_5") or m.get("stage_5") or ("真实缺口" if hard_reject else None)
    approval = out.get("approval_status") or state.get("approval_status")
    if not approval:
        approval = cfg.APPROVAL_PENDING if interrupted else cfg.APPROVAL_NOT_NEEDED
    cost = out.get("cost") or _cost_from_usage(state.get("usage_records"))

    if not verbose:
        print(f"  {jd.get('jd_id')}  {jd.get('company')}/{jd.get('title')}  →  "
              f"{VERDICT_CN.get(verdict, verdict)}（{stage_5}）  ¥{cost.get('cost_cny', 0)}")
        return

    _hr(f"判定：{VERDICT_CN.get(verdict, verdict)}（{stage_5}）")
    if out.get("age_note"):
        print(f"  {out['age_note']}")

    if hard.get("flags"):
        print(f"  命中硬规则 {len(hard['flags'])} 条：")
        for i, r in enumerate(hard.get("reasons", []), 1):
            tier = "绝对硬" if r["tier"] == "absolute" else "阈值硬"
            print(f"    {i}. rule_id: {r['rule_id']}（{tier}）")
            print(f"       {r['label']}")
            print(f"       JD 原句：「{r['quote']}」")
        print(LINE)
        print("  后续：未进入打分与改写环节（本次 0 次模型调用，未产生费用）")
    else:
        print("  未命中任何硬规则。")
        if hard.get("hints"):
            for h in hard["hints"]:
                print(f"  ⚠️ {h['label']}")
                print(f"     JD 原句：「{h['quote']}」")

    if m:
        score = m.get("score", {})
        print(LINE)
        print(f"  分项打分：相关度 {score.get('relevant')}/10　"
              f"职级 {score.get('level')}/10　技能 {score.get('skill')}/10"
              f"　合计 {m.get('score_total')}/30")
        for r in m.get("reasons", [])[:6]:
            print(f"    · {r}")
        if m.get("missing_info"):
            print("  缺什么信息（所以交给你判断）：")
            for mi in m["missing_info"][:6]:
                print(f"    · {mi}")

    prov = state.get("provenance_result") or {}
    if prov and prov.get("atom_count") is not None:
        print(LINE)
        print(f"  溯源校验：{'✅ 通过' if prov.get('passed') else '❌ 未通过'}"
              f"　{prov.get('atom_count', 0)} 个事实原子"
              f"（A {prov.get('by_type', {}).get('A', 0)} / B {prov.get('by_type', {}).get('B', 0)}"
              f" / C {prov.get('by_type', {}).get('C', 0)}）")
        for mis in (prov.get("missed") or [])[:6]:
            print(f"    MISS  {mis}")

    print(LINE)
    print(f"  成本：{cost.get('calls', 0)} 次模型调用，¥{cost.get('cost_cny', 0)}，"
          f"耗时 {cost.get('elapsed', 0)} 秒")
    print(f"  状态：{approval}"
          + (f"　单号 {state.get('ticket_id')}" if state.get("ticket_id") else ""))
    if out.get("resume_path"):
        print(f"  稿件：{out['resume_path']}")
    print(f"  报告：{cfg.OUTPUT_DIR / jd.get('jd_id', '')}/verdict.md")


def _read_clipboard() -> str:
    """从剪贴板读 JD。

    使用者的真实动作是「在招聘网站选中 JD → Ctrl+C → 回终端跑命令」。
    逼他先把 JD 存成文件、或处理命令行里的引号转义，都是没必要的摩擦。
    tkinter 是标准库，不额外依赖；Windows 上 clipborad_get 返回的是 str。
    """
    try:
        import tkinter
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("当前 Python 缺少 tkinter，无法读剪贴板；请改用 --jd \"JD 全文\"") from exc
    root = tkinter.Tk()
    root.withdraw()
    try:
        text = root.clipboard_get()
    except tkinter.TclError as exc:
        raise RuntimeError(f"剪贴板里没有可读的文本：{exc}") from exc
    finally:
        root.destroy()
    return text


def cmd_judge(args) -> int:
    from graph.builder import build_with_sqlite

    graph, db = build_with_sqlite()
    try:
        if args.batch:
            from units.jd_intake.impl import intake_batch

            items = intake_batch(args.batch)
            _hr(f"批量判定 · {len(items)} 个岗位")
            records = []
            for item in items:
                if item.get("error"):
                    print(f"  SKIP  {item['jd_id']}：{item['error']}")
                    continue
                state = _invoke(graph, item, batch_mode=True)
                _print_outcome(state, verbose=False)
                records.append(state)
            _batch_summary(records)
            return 0

        if args.clip:
            try:
                jd_input = _read_clipboard()
            except RuntimeError as exc:
                print(f"读剪贴板失败：{exc}", file=sys.stderr)
                return 2
            print(f"从剪贴板读到 {len(jd_input)} 字：")
            preview = jd_input.strip()[:180].replace("\n", " / ")
            print(f"  {preview}{'…' if len(jd_input) > 180 else ''}")
            print(LINE)
        else:
            jd_input = args.jd
        if not jd_input:
            print("需要 --jd <JD 文本 或 文件路径>、--clip（读剪贴板）、或 --batch <目录>",
                  file=sys.stderr)
            return 2
        state = _invoke(graph, jd_input, batch_mode=bool(args.no_approval))
        _print_outcome(state)
        if state.get("__interrupt__"):
            payload = state["__interrupt__"][0].value
            _print_interrupt(payload)
        return 0
    finally:
        db.close()


def _invoke(graph, jd_input, *, batch_mode: bool = False) -> dict:
    from graph.builder import make_config

    trace_id = _trace_id()
    thread_id = f"th-{trace_id}"
    init = {"jd": jd_input, "trace_id": trace_id, "thread_id": thread_id,
            "batch_mode": batch_mode, "usage_records": [], "audit_trail": [], "steps": []}
    result = graph.invoke(init, make_config(thread_id))
    result["_thread_id"] = thread_id
    return result


def _batch_summary(states: list[dict]) -> None:
    if not states:
        return
    from collections import Counter

    verdicts = Counter((s.get("outcome") or {}).get("verdict") for s in states)
    cost = sum((s.get("outcome") or {}).get("cost", {}).get("cost_cny", 0) for s in states)
    _hr("批量结果")
    print(f"  共 {len(states)} 个岗位："
          + "　".join(f"{VERDICT_CN.get(k, k)} {v}" for k, v in verdicts.items()))
    print(f"  合计花费：¥{round(cost, 6)}")
    print("  提示：只有「该投」的岗位会进入改写环节，其余在硬门槛/打分阶段就停住了（这是省钱的关键设计）")


# ── pending / approve / reject ────────────────────────────────────────
def cmd_pending(args) -> int:
    conn = store.connect()
    try:
        rows = store.list_tickets(conn, cfg.APPROVAL_PENDING)
        _hr(f"待审批 {len(rows)} 条")
        if not rows:
            print("  没有待办。")
            return 0
        for r in rows:
            print(f"  {r['ticket_id']}  {r['jd_id']}  会话 {r['thread_id']}  创建于 {r['created_at']}")
        print(LINE)
        print("  提醒：审批状态以 data/audit.db 为准，不以看板快照为准 ——")
        print("        服务重启过的话，会话可能早就不在挂起态了，approve 会返回 409。")
    finally:
        conn.close()
    return 0


def _resolve(ticket_id: str, approved: bool, reason: str) -> int:
    from graph.builder import build_with_sqlite
    from langgraph.types import Command
    from units.approval import impl as approval_unit

    conn = store.connect()
    graph, db = build_with_sqlite()
    try:
        ticket = store.get_ticket(conn, ticket_id)
        if not ticket:
            print(f"404 待办 {ticket_id} 不存在", file=sys.stderr)
            return 2
        thread_id = ticket["thread_id"]

        config = {"configurable": {"thread_id": thread_id}}
        snapshot = graph.get_state(config)

        # 三道关：待办存在 → 会话真挂起 → 单号一致（由 check_gates 统一实现）
        gate = approval_unit.check_gates(conn, ticket_id, thread_id, snapshot.next)
        if not gate.ok:
            print(f"{gate.code} {gate.msg}", file=sys.stderr)
            return 2

        result = graph.invoke(
            Command(resume={"approved": approved, "reason": reason}), config
        )
        out = result.get("outcome") or {}
        _hr(f"{'✅ 已批准' if approved else '🚫 已驳回'}　{ticket_id}")
        if approved:
            print(f"  稿件已写出：{out.get('resume_path')}")
            print(f"  目录：{cfg.OUTPUT_DIR / (result.get('jd') or {}).get('jd_id', '')}/")
            print("  下一步：复制内容到招聘平台，**由你自己点提交** —— 本项目不碰你的账号。")
        else:
            print(f"  理由：{reason}")
            print("  稿件未写出，记录已落库。")
        return 0
    finally:
        db.close()
        conn.close()


def cmd_approve(args) -> int:
    return _resolve(args.ticket_id, True, args.reason or "")


def cmd_reject(args) -> int:
    return _resolve(args.ticket_id, False, args.reason or "（未填理由）")


# ── dashboard ─────────────────────────────────────────────────────────
def cmd_dashboard(args) -> int:
    import subprocess

    snapshot_script = ROOT / "scripts" / "build_snapshot.py"
    build_script = ROOT / "scripts" / "build_workbench.py"
    py = sys.executable

    for script in (snapshot_script, build_script):
        if not script.exists():
            print(f"缺少 {script}", file=sys.stderr)
            return 2

    r1 = subprocess.run([py, str(snapshot_script)], cwd=str(ROOT), capture_output=True, text=True)
    print(r1.stdout.strip() or r1.stderr.strip())
    if r1.returncode != 0:
        return r1.returncode

    r2 = subprocess.run(
        [py, str(build_script), "--in", str(cfg.WORKBENCH_SNAPSHOT), "--out", str(cfg.DASHBOARD_HTML)],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    print(r2.stdout.strip() or r2.stderr.strip())
    if r2.returncode != 0:
        return r2.returncode
    print(f"\n用浏览器打开：{cfg.DASHBOARD_HTML}")
    return 0


def cmd_workbench(args) -> int:
    """打开网页工作台：粘贴 JD → 判定 → 批准，全在浏览器里点。

    这就是「不想敲命令」时的入口。起的是本地服务，数据全在你机器上，
    除了调 DeepSeek API 之外不往外发任何东西。
    """
    import socket
    import webbrowser

    import uvicorn

    port = args.port
    url = f"http://127.0.0.1:{port}"

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            print(f"\n  ⚠️ 端口 {port} 已被占用 —— 很可能已经有一个工作台开着了。\n")
            print(f"     直接用：{url}")
            print(f"     或换端口：python main.py workbench --port {port + 7}\n")
            return 1

    print("\n" + "=" * 56)
    print(f"  工作台：{url}")
    print("  浏览器没自动打开的话，手动访问上面这个地址。")
    print("  按 Ctrl+C 停止服务。")
    print("  ⚠️ 不要加 --workers 2 —— SqliteSaver 的锁不跨进程。")
    print("=" * 56 + "\n")
    webbrowser.open(url)
    uvicorn.run("server:app", host="127.0.0.1", port=port, log_level="warning")
    return 0


# ── gate / eval ───────────────────────────────────────────────────────
def cmd_gate(args) -> int:
    import subprocess

    return subprocess.run([sys.executable, str(ROOT / "scripts" / "gate.py")], cwd=str(ROOT)).returncode


def cmd_eval(args) -> int:
    import subprocess

    argv = [sys.executable, str(ROOT / "eval" / "run.py")]
    if args.report:
        argv.append("--report")
    if args.limit:
        argv += ["--limit", str(args.limit)]
    if args.dry_run:
        argv.append("--dry-run")
    return subprocess.run(argv, cwd=str(ROOT)).returncode


# ── 参数解析 ──────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="main.py", description="proj3-apply-gate · 投递决策闸门",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="自检：密钥、简历、五套资料、规则文件齐不齐").set_defaults(func=cmd_doctor)

    p_judge = sub.add_parser("judge", help="判一个岗位 / 批量判")
    p_judge.add_argument("--jd", help="JD 全文，或 .json / .txt 文件路径")
    p_judge.add_argument("--clip", action="store_true",
                         help="直接从剪贴板读 JD（在网页上选中 JD 按 Ctrl+C 后跑这个最省事）")
    p_judge.add_argument("--batch", help="批量目录，如 data/jd/inbox/（读 jd-*.json 与 *.txt）")
    p_judge.add_argument("--no-approval", action="store_true",
                         help="不挂审批（只写报告不出稿，用于批量/实验）")
    p_judge.set_defaults(func=cmd_judge)

    sub.add_parser("pending", help="看有哪些稿子等着点头").set_defaults(func=cmd_pending)

    p_ap = sub.add_parser("approve", help="批准出稿")
    p_ap.add_argument("ticket_id")
    p_ap.add_argument("--reason", default="")
    p_ap.set_defaults(func=cmd_approve)

    p_rj = sub.add_parser("reject", help="驳回（不出稿）")
    p_rj.add_argument("ticket_id")
    p_rj.add_argument("--reason", default="")
    p_rj.set_defaults(func=cmd_reject)

    sub.add_parser("dashboard", help="刷新看板").set_defaults(func=cmd_dashboard)

    p_wb = sub.add_parser("workbench", help="打开网页工作台：粘贴 JD → 判定 → 批准，点着用")
    p_wb.add_argument("--port", type=int, default=8103)
    p_wb.set_defaults(func=cmd_workbench)

    sub.add_parser("gate", help="L1 确定性门禁（硬门槛 + 矛盾检查）").set_defaults(func=cmd_gate)

    p_eval = sub.add_parser("eval", help="L3 指标：跑评测集（会花钱）")
    p_eval.add_argument("--report", action="store_true", help="出完整报告")
    p_eval.add_argument("--limit", type=int, default=0, help="只跑前 N 题（调试用）")
    p_eval.add_argument("--dry-run", action="store_true", help="只跑不调模型的部分，验证链路")
    p_eval.set_defaults(func=cmd_eval)

    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
