# -*- coding: utf-8 -*-
"""units/approval · 审批门（图结构 + 三道关 + 产物扫描）

硬指标③（未审批不放行率 = 0%）由本单元守。

这里最关键的不是 interrupt 本身，而是 proj2 踩出来的那条坑：
    往一个「其实已经不在挂起态」的会话传普通输入，中断状态会**静默丢失** ——
    next 变空、__interrupt__ 消失、还不报错，审批单就这么没了。
所以接口层必须先查会话是不是真挂着，**不能只看待办表**。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core import config as cfg  # noqa: E402
from core import store  # noqa: E402


class GateCheck(NamedTuple):
    ok: bool
    code: int
    msg: str


def scan_output(text: str) -> list[str]:
    """产物扫描：成稿里出现这些字样 → 不许标成可投递。"""
    if not text:
        return []
    return [w for w in cfg.FORBIDDEN_IN_FINAL if w in text]


def scan_draft_files(paths: list[Path]) -> list[dict]:
    """扫描输出目录下的所有稿件。"""
    hits = []
    for path in paths:
        if not path.exists() or path.suffix not in (".md", ".txt"):
            continue
        words = scan_output(path.read_text(encoding="utf-8", errors="ignore"))
        if words:
            hits.append({"file": str(path), "forbidden_words": words})
    return hits


def check_gates(
    conn,
    ticket_id: str,
    thread_id: str,
    state_next: tuple | list | None,
) -> GateCheck:
    """三道关，顺序不能换：待办存在(404) → 会话真挂起(409) → 单号一致(409)。"""
    ticket = store.get_ticket(conn, ticket_id)
    if not ticket:
        return GateCheck(False, 404, f"待办 {ticket_id} 不存在")

    # 第 2 关：会话是不是真的挂在挂起态。这一关最关键 —— 只查待办表会误判。
    if not state_next:
        return GateCheck(
            False, 409,
            "会话未处于挂起状态（可能服务重启过，或中断状态已丢失）—— 请重新发起判定",
        )

    # 第 3 关：单号与会话必须对得上
    if ticket.get("thread_id") != thread_id:
        return GateCheck(
            False, 409,
            f"单号 {ticket_id} 绑定的会话是 {ticket.get('thread_id')}，与请求的 {thread_id} 不一致",
        )

    if ticket.get("status") != cfg.APPROVAL_PENDING:
        return GateCheck(False, 409, f"待办 {ticket_id} 当前状态是 {ticket.get('status')}，不是待审批")

    return GateCheck(True, 200, "ok")


def resolve(conn, ticket_id: str, approved: bool, reason: str = "", product_files: list[Path] | None = None) -> dict:
    """批准或驳回。驳回就不出稿。"""
    if approved and product_files:
        hits = scan_draft_files(product_files)
        if hits:
            return {
                "ok": False, "status": cfg.APPROVAL_REJECTED,
                "reason": f"产物扫描未通过，不许标记为可投递：{hits}",
            }
    status = cfg.APPROVAL_APPROVED if approved else cfg.APPROVAL_REJECTED
    changed = store.resolve_ticket(conn, ticket_id, status, reason)
    return {
        "ok": changed, "status": status,
        "reason": reason or ("已批准" if approved else "已驳回"),
    }


def is_pending(state_snapshot) -> bool:
    """判断一个图状态是不是真的挂着 —— 接口层每次都必须调这个，不许靠猜。"""
    if state_snapshot is None:
        return False
    nxt = getattr(state_snapshot, "next", None)
    return bool(nxt)
