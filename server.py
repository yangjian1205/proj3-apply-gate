#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 层：把判定/审批能力暴露成接口。

复用 proj2 的审批三道关，并且把那条最关键的坑固化在接口层：
    **不能靠看板快照判断审批状态。**
    快照只是某一刻的照片，服务可能重启过，会话早就不在挂起态了。
    所以批准动作每次都必须重新查会话是不是真的挂着（第 2 关）。

启动：
    python -m uvicorn server:app --port 8103
    ⚠️ 不要加 --workers 2 —— SqliteSaver 的锁不跨进程，多 worker 会出问题。

接口：
    GET  /health                    健康检查
    POST /judge                     提交一条 JD 做判定（可能挂起等审批）
    GET  /approvals                 待办列表
    POST /approvals/{ticket_id}     批准 / 驳回
    GET  /sessions/{thread_id}      查会话状态（是否真挂着）
    GET  /judgements                历史判定记录
    GET  /cost                      成本汇总
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import config as cfg  # noqa: E402
from core import resume_io  # noqa: E402
from core import store  # noqa: E402
from graph.builder import build_with_sqlite  # noqa: E402
from units.approval import impl as approval_unit  # noqa: E402

app = FastAPI(
    title="proj3-apply-gate",
    description="投递决策闸门：硬门槛走纯代码（零漏放），改写稿逐事实原子溯源，出稿前必须人工点头。",
    version="1.0.0",
)

_graph = None
_db = None


def get_graph():
    global _graph, _db
    if _graph is None:
        _graph, _db = build_with_sqlite()
    return _graph


# ── 请求模型 ──────────────────────────────────────────────────────────
class JudgeRequest(BaseModel):
    jd_text: str = Field(description="JD 原文全文，一个字都不要清洗")
    company: str = ""
    title: str = ""
    city: str = ""
    jd_id: str = ""
    source_url: str = ""
    fetched_at: str = ""


class ApprovalRequest(BaseModel):
    approved: bool = Field(description="true=批准出稿，false=驳回不出稿")
    thread_id: str = Field(description="会话 id（必须在待办上对得上）")
    reason: str = ""


# ── 接口 ──────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, Any]:
    conn = store.connect()
    try:
        counts = {t: store.row_count(conn, t) for t in ("audit", "cost", "tickets", "judgements")}
    finally:
        conn.close()
    rules = cfg.load_rules()
    return {
        "ok": True,
        "rules_version": rules.get("rules_version"),
        "rules_count": len(rules["rules"]),
        "db": counts,
        "note": "硬门槛判定不经过模型；本服务不要用 --workers 2（SqliteSaver 锁不跨进程）",
    }


@app.post("/judge")
def judge(req: JudgeRequest) -> dict[str, Any]:
    graph = get_graph()
    trace_id = f"http-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    thread_id = f"th-{trace_id}"

    jd: dict[str, Any] = {"text": req.jd_text}
    for key in ("company", "title", "city", "jd_id", "source_url", "fetched_at"):
        value = getattr(req, key)
        if value:
            jd[key] = value

    init = {"jd": jd, "trace_id": trace_id, "thread_id": thread_id,
            "usage_records": [], "audit_trail": [], "steps": []}
    state = graph.invoke(init, {"configurable": {"thread_id": thread_id}, "recursion_limit": 30})

    interrupted = bool(state.get("__interrupt__"))
    hard = state.get("hard_result") or {}
    m = state.get("match_result") or {}
    prov = state.get("provenance_result") or {}
    usage = state.get("usage_records") or []

    hard_reject = hard.get("hard_verdict") == cfg.REJECT
    verdict = m.get("verdict") or (cfg.REJECT if hard_reject else None)

    return {
        "trace_id": trace_id,
        "thread_id": thread_id,
        "jd_id": (state.get("jd") or {}).get("jd_id"),
        "verdict": verdict,
        "verdict_cn": cfg.VERDICT_CN.get(verdict),
        "verdict_5": m.get("stage_5") or ("真实缺口" if hard_reject else None),
        "hard_flags": [{"rule_id": r["rule_id"], "quote": r["quote"]} for r in hard.get("reasons", [])],
        "hints": [{"rule_id": h["rule_id"], "quote": h["quote"]} for h in hard.get("hints", [])],
        "score": m.get("score"),
        "reasons": m.get("reasons", []),
        "missing_info": m.get("missing_info", []),
        "provenance": {
            "passed": prov.get("passed"),
            "atom_count": prov.get("atom_count"),
            "missed": prov.get("missed", []),
            "stage_violations": prov.get("stage_violations", []),
        } if prov else None,
        "awaiting_approval": interrupted,
        "approval_payload": state["__interrupt__"][0].value if interrupted else None,
        "cost_cny": round(sum(u.get("cost_cny", 0) for u in usage), 6),
        "calls": len(usage),
        # ↓ 「这份 JD 该怎么改简历」—— 判完最该立刻看到的东西。
        #   走不到改写环节（不该投 / 边界）时是空列表，页面据此显示「不适用」。
        "jd_mapping": (state.get("draft") or {}).get("jd_mapping", []),
        "changes": (state.get("draft") or {}).get("changes", []),
        "cover_letter": (state.get("draft") or {}).get("cover_letter_md", ""),
    }


@app.get("/approvals")
def list_approvals(status: str | None = None) -> dict[str, Any]:
    """列出**真的还挂着**的待办。

    ⚠️ 只查 tickets 表是不够的。待办表会积累历史记录（调试、重跑、批量实验），
       这些会话其实早就结束了 —— 界面照单全收就会显示一堆「待审批」，
       点进去全部 409。这正是三道关②（会话真挂着）必须在**列表层**就体现的原因：
       三道关是给接口兜底的，不该让使用者靠「点了才知道是假的」来发现问题。
    """
    conn = store.connect()
    try:
        rows = store.list_tickets(conn, status or cfg.APPROVAL_PENDING)
    finally:
        conn.close()

    graph = get_graph()
    alive: list[dict[str, Any]] = []
    stale: list[str] = []
    for t in rows:
        try:
            snapshot = graph.get_state({"configurable": {"thread_id": t["thread_id"]}})
            if "approval" in tuple(snapshot.next or ()):
                alive.append(t)
            else:
                stale.append(t["ticket_id"])
        except Exception:  # noqa: BLE001 —— 查不到状态一律当失效，宁可少列不可误列
            stale.append(t["ticket_id"])

    return {
        "count": len(alive),
        "items": alive,
        "stale_count": len(stale),
        "stale_ticket_ids": stale,
        "note": "只列出会话**真挂着**的待办（三道关②）；待办表里的历史记录不算，那些点了会 409。",
    }


@app.post("/approvals/{ticket_id}")
def resolve_approval(ticket_id: str, req: ApprovalRequest) -> dict[str, Any]:
    from langgraph.types import Command

    conn = store.connect()
    graph = get_graph()
    try:
        config = {"configurable": {"thread_id": req.thread_id}}
        snapshot = graph.get_state(config)

        # 三道关：待办 404 → 会话真挂起 409 → 单号一致 409
        gate = approval_unit.check_gates(conn, ticket_id, req.thread_id, snapshot.next)
        if not gate.ok:
            raise HTTPException(status_code=gate.code, detail=gate.msg)

        result = graph.invoke(
            Command(resume={"approved": req.approved, "reason": req.reason}), config
        )
        out = result.get("outcome") or {}
        return {
            "ok": True,
            "ticket_id": ticket_id,
            "approved": req.approved,
            "approval_status": out.get("approval_status"),
            "resume_path": out.get("resume_path"),
            "written": out.get("written", []),
            "message": "已批准，稿件已写出，请自行复制到招聘平台提交（本系统不碰你的账号）"
            if req.approved else "已驳回，本次不出稿",
        }
    finally:
        conn.close()


@app.get("/sessions/{thread_id}")
def session_state(thread_id: str) -> dict[str, Any]:
    graph = get_graph()
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"会话 {thread_id} 不存在")
    return {
        "thread_id": thread_id,
        "next": list(snapshot.next or []),
        "is_pending": approval_unit.is_pending(snapshot),
        "values_keys": sorted((snapshot.values or {}).keys()),
    }


@app.get("/judgements")
def judgements(limit: int = 50) -> dict[str, Any]:
    conn = store.connect()
    try:
        rows = store.fetch_judgements(conn)[:limit]
        # 与离线看板共用同一个转换函数 —— 字段名对不上就会整块数据消失
        return {"count": len(rows), "items": [store.to_workbench_record(r) for r in rows]}
    finally:
        conn.close()


@app.get("/cost")
def cost() -> dict[str, Any]:
    conn = store.connect()
    try:
        return store.cost_summary(conn)
    finally:
        conn.close()


# ── 简历上传（工作台上直接传，不用手改文件）──────────────────────────
@app.get("/resume")
def resume_status() -> dict[str, Any]:
    """当前工作台用的是哪份简历。is_example=True 说明还是仓库自带模板。"""
    return resume_io.current()


@app.post("/resume/upload")
async def resume_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传简历（PDF / txt / md）→ 解析 → 备份旧的 → 写入。

    之后所有判定与改写都用这一份 —— 溯源对照物换成了它。
    """
    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=400, detail="文件是空的")
    if len(blob) > resume_io.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"文件 {len(blob) // 1024} KB，超过上限 {resume_io.MAX_UPLOAD_BYTES // 1024} KB",
        )
    try:
        text, method = resume_io.extract_text(file.filename or "", blob)
        resume_io.check_text(text)
        saved = resume_io.save_resume(text, filename=file.filename or "（未命名）", method=method)
    except resume_io.ResumeIOError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "message": f"简历已更新：{saved['chars']} 字 / {saved['lines']} 行"
                   + (f"（上一版已备份为 {saved['backup']}）" if saved["backup"] else ""),
        **saved,
        "current": resume_io.current(),
    }


class ProfileUpdate(BaseModel):
    """工作台能改的档案字段 —— 只暴露真正影响判定的那几项，不把整个 JSON 抛给页面。"""

    expected_cities: list[str] = Field(default_factory=list, description="期望工作城市，至少一个")
    accept_remote: bool = True
    accept_relocate: bool = False
    expected_salary: str = ""
    highest_degree: str | None = None
    degree_level: int | None = None
    years_experience: float | None = None
    certificates: list[str] | None = None
    need_sponsorship: bool | None = None
    work_auth_local: bool | None = None


def _profile_fields(p: dict) -> dict[str, Any]:
    return {
        "expected_cities": p.get("expected_cities") or [],
        "accept_remote": bool(p.get("accept_remote")),
        "accept_relocate": bool(p.get("accept_relocate")),
        "expected_salary": p.get("expected_salary") or "",
        "highest_degree": p.get("highest_degree") or "",
        "degree_level": p.get("degree_level"),
        "years_experience": p.get("years_experience"),
        "certificates": p.get("certificates") or [],
        "need_sponsorship": bool(p.get("need_sponsorship")),
        "work_auth_local": bool(p.get("work_auth_local")),
    }


@app.get("/profile")
def get_profile() -> dict[str, Any]:
    """当前档案。

    ⚠️ `using_example` 用**实时文件判断**，不看进程内的回退记录 ——
       服务是长期跑的，回退记录会跨请求累积，拿它当状态会越用越不准。
    """
    exists = cfg.PROFILE_JSON.exists()
    p = cfg.load_profile()
    return {
        "exists": exists,
        "using_example": not exists,
        "confirmed": bool(p.get("_confirmed")),
        "todo": p.get("_todo") or [],
        "path": str(cfg.PROFILE_JSON.relative_to(cfg.ROOT).as_posix()),
        "fields": _profile_fields(p),
    }


@app.post("/profile")
def save_profile(req: ProfileUpdate) -> dict[str, Any]:
    """保存档案（写真实档案文件，不是模板）。

    ⚠️ 用模板当前值时，这一次保存会自动「转正」：写出真实 `candidate_profile.json` 并置
       `_confirmed=true`。不这么做的话，使用者以为填好了，其实改的还是模板，判定照旧跑模板值。
    """
    cities = [c.strip() for c in req.expected_cities if c and c.strip()]
    if not cities:
        raise HTTPException(
            status_code=400,
            detail="期望工作城市不能为空 —— 它直接决定「base 某地 + 坐班」这类硬门槛判不判",
        )

    base = dict(cfg.load_profile())  # 可能是 example 的内容，作为基底保留其他字段
    fields = _profile_fields(base)
    fields.update({"expected_cities": cities})
    for key in ("accept_remote", "accept_relocate", "expected_salary"):
        fields[key] = getattr(req, key)
    for key in ("highest_degree", "degree_level", "years_experience",
                "certificates", "need_sponsorship", "work_auth_local"):
        value = getattr(req, key)
        if value is not None:
            fields[key] = value

    base.update(fields)
    base["_confirmed"] = True
    base["_todo"] = []
    base["updated_at"] = time.strftime("%Y-%m-%d")
    cfg.PROFILE_JSON.parent.mkdir(parents=True, exist_ok=True)
    cfg.PROFILE_JSON.write_text(
        json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "ok": True,
        "message": f"档案已保存（期望城市：{'、'.join(cities)}），后续判定就按这个来",
        "profile": get_profile(),
    }


@app.post("/resume/restore/{filename}")
def resume_restore(filename: str) -> dict[str, Any]:
    """恢复某一版简历备份（备份存在的意义就是能恢复）。"""
    try:
        result = resume_io.restore_backup(filename)
    except resume_io.ResumeIOError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "message": f"已恢复为 {result['restored']}", **result}


# ── 工作台页面 ────────────────────────────────────────────────────────
WEBAPP_DIR = ROOT / "webapp"


@app.get("/", include_in_schema=False)
def workbench():
    """工作台首页：粘贴 JD → 判定 → 批准，全在浏览器里点。"""
    path = WEBAPP_DIR / "index.html"
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"找不到工作台页面 {path}")
    return FileResponse(path)


if WEBAPP_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEBAPP_DIR)), name="static")


if __name__ == "__main__":
    import socket
    import webbrowser

    import uvicorn

    port = 8103
    # 先探一下端口，别让使用者双击 bat 之后看到一堆英文报错
    with socket.socket() as probe:
        busy = probe.connect_ex(("127.0.0.1", port)) == 0
    if busy:
        print(f"\n  ⚠️ 端口 {port} 已被占用 —— 很可能已经有一个工作台窗口开着了。\n")
        print("     怎么办（任选一个）：")
        print("       1) 找一下有没有别的黑色命令行窗口在跑工作台，关掉它再双击本文件")
        print(f"       2) 或者换个端口：python main.py workbench --port {port + 7}")
        print(f"       3) 如果那个服务本来就是你要的，直接开浏览器访问 http://127.0.0.1:{port}\n")
        input("  按回车键退出…")
        raise SystemExit(1)

    url = f"http://127.0.0.1:{port}"
    print(f"\n  工作台已启动：{url}")
    print("  浏览器没自动打开的话，手动访问这个地址。")
    print("  关掉这个窗口（或 Ctrl+C）即停止服务。")
    print("  ⚠️ 不要加 --workers 2 —— SqliteSaver 的锁不跨进程。\n")
    webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=port)
