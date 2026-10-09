# -*- coding: utf-8 -*-
"""SQLite 存储层：审计表 / 成本表 / 审批待办 / 判定结果。

直接复用 proj2 的做法，并把它踩过的坑固化在这里：

⚠️ 坑 1：`with closing(conn) as c` **只 close 不 commit**。
    漏了 `conn.commit()` 会静默丢数据 —— 另一个连接查回来是 0 行，还不报错。
    所以本模块所有写操作都显式 commit，并且提供 `row_count()` 让你能当场验证。

⚠️ 坑 2：游标带出 `with` 之外会导致连接泄漏 → 后续报 `database is locked`。
    本模块所有查询都在函数内取完数据再返回。

⚠️ 坑 3：SqliteSaver 的锁不跨进程，所以 FastAPI 侧不要开 `--workers 2`。
    （这条写在 README 的已知限制里。）
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config as cfg  # noqa: E402

BJ = timezone(timedelta(hours=8))

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT NOT NULL,
    ts           TEXT NOT NULL,
    from_node    TEXT NOT NULL,
    to_node      TEXT NOT NULL,
    reason       TEXT NOT NULL,
    payload_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cost (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id      TEXT NOT NULL,
    ts            TEXT NOT NULL,
    node          TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_cny      REAL NOT NULL DEFAULT 0,
    elapsed       REAL NOT NULL DEFAULT 0,
    peak          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id   TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    jd_id       TEXT NOT NULL,
    thread_id   TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    resolved_at TEXT,
    reason      TEXT
);

CREATE TABLE IF NOT EXISTS judgements (
    jd_id        TEXT PRIMARY KEY,
    trace_id     TEXT NOT NULL,
    company      TEXT,
    title        TEXT,
    city         TEXT,
    fetched_at   TEXT,
    stale        INTEGER DEFAULT 0,
    verdict      TEXT,
    verdict_5    TEXT,
    hard_flags   TEXT,
    hints        TEXT,
    score        TEXT,
    reasons       TEXT,
    missing_info TEXT,
    jd_text      TEXT,
    draft_path   TEXT,
    rewrite_round INTEGER DEFAULT 0,
    cost_cny     REAL DEFAULT 0,
    elapsed      REAL DEFAULT 0,
    ticket_id    TEXT,
    approval_status TEXT,
    updated_at   TEXT,
    -- ↓ 工作台视图 4（溯源对比）与视图 6（审计时间线）需要的字段。
    --   必须有地方存，否则快照只能给空数组、那两个视图就是白的。
    resume_base  TEXT,
    draft_lines  TEXT,
    atoms        TEXT,
    evidence_map TEXT,
    missed       TEXT,
    audit_trail  TEXT,
    -- ↓ 工作台视图 2（怎么改简历）需要的字段。
    --   早先 changes 只写进了 verdict.md，没落库 —— 于是「告诉我怎么改」这件事
    --   在页面上完全看不到。算出来的东西不落库 = 没算。
    jd_mapping   TEXT,
    changes      TEXT,
    cover_letter TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_trace ON audit(trace_id);
CREATE INDEX IF NOT EXISTS idx_cost_trace ON cost(trace_id);
"""

# 旧库缺列时的幂等迁移：CREATE TABLE IF NOT EXISTS 不会给已存在的表加列
MIGRATIONS: dict[str, dict[str, str]] = {
    "judgements": {
        "hints": "TEXT",
        "missing_info": "TEXT",
        "resume_base": "TEXT",
        "draft_lines": "TEXT",
        "atoms": "TEXT",
        "evidence_map": "TEXT",
        "missed": "TEXT",
        "audit_trail": "TEXT",
        "jd_mapping": "TEXT",
        "changes": "TEXT",
        "cover_letter": "TEXT",
    },
}

JSON_COLUMNS = ("hard_flags", "hints", "score", "reasons", "missing_info",
                "resume_base", "draft_lines", "atoms", "evidence_map", "missed", "audit_trail",
                "jd_mapping", "changes")


def _as_list(value) -> list:
    """容错取列表：库里存的是 JSON 字符串，也可能有人直接把反序列化好的传进来。

    这是个共用入口，被喂什么都得稳 —— 拿不到就返回空列表，绝不让页面因为
    一个字段的类型不对而整块崩掉（已经因为字段不一致吃过两次亏）。
    """
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, coltype in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")
    conn.commit()


def now_iso() -> str:
    return datetime.now(tz=BJ).strftime("%Y-%m-%d %H:%M:%S")


def payload_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or cfg.AUDIT_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)   # 旧库补列（幂等）
    conn.commit()  # ⚠️ 必须手动 commit
    return conn


# ── 审计 ──────────────────────────────────────────────────────────────
def log_audit(conn: sqlite3.Connection, trace_id: str, from_node: str,
              to_node: str, reason: str, payload: Any = None) -> None:
    conn.execute(
        "INSERT INTO audit (trace_id, ts, from_node, to_node, reason, payload_hash) VALUES (?,?,?,?,?,?)",
        (trace_id, now_iso(), from_node, to_node, reason, payload_hash(payload or {})),
    )
    conn.commit()  # 显式 commit —— 这条漏了就是静默丢数据


def fetch_audit(conn: sqlite3.Connection, trace_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT ts, from_node, to_node, reason, payload_hash FROM audit WHERE trace_id=? ORDER BY id",
        (trace_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── 成本 ──────────────────────────────────────────────────────────────
def log_cost(conn: sqlite3.Connection, trace_id: str, usage: dict) -> None:
    conn.execute(
        "INSERT INTO cost (trace_id, ts, node, model, input_tokens, output_tokens, cost_cny, elapsed, peak) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            trace_id, now_iso(), usage.get("node", ""), usage.get("model", ""),
            int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
            float(usage.get("cost_cny", 0)), float(usage.get("elapsed", 0)),
            1 if usage.get("peak") else 0,
        ),
    )
    conn.commit()


def cost_summary(conn: sqlite3.Connection, trace_id: str | None = None) -> dict:
    where, params = ("WHERE trace_id=?", (trace_id,)) if trace_id else ("", ())
    row = conn.execute(
        f"SELECT COUNT(*) AS calls, COALESCE(SUM(cost_cny),0) AS cost, "
        f"COALESCE(SUM(input_tokens),0) AS in_tok, COALESCE(SUM(output_tokens),0) AS out_tok, "
        f"COALESCE(SUM(elapsed),0) AS elapsed FROM cost {where}",
        params,
    ).fetchone()
    by_node = [
        dict(r) for r in conn.execute(
            f"SELECT node, COUNT(*) AS calls, ROUND(SUM(cost_cny),6) AS cost, "
            f"SUM(input_tokens) AS in_tok, SUM(output_tokens) AS out_tok FROM cost {where} GROUP BY node",
            params,
        ).fetchall()
    ]
    return {
        "calls": row["calls"], "cost_cny": round(row["cost"], 6),
        "input_tokens": row["in_tok"], "output_tokens": row["out_tok"],
        "elapsed": round(row["elapsed"], 3), "by_node": by_node,
    }


# ── 审批待办 ──────────────────────────────────────────────────────────
def create_ticket(conn: sqlite3.Connection, ticket_id: str, trace_id: str,
                  jd_id: str, thread_id: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO tickets (ticket_id, trace_id, jd_id, thread_id, status, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (ticket_id, trace_id, jd_id, thread_id, cfg.APPROVAL_PENDING, now_iso()),
    )
    conn.commit()


def list_tickets(conn: sqlite3.Connection, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM tickets WHERE status=? ORDER BY created_at", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tickets ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
    return dict(row) if row else None


def resolve_ticket(conn: sqlite3.Connection, ticket_id: str, status: str, reason: str = "") -> bool:
    cur = conn.execute(
        "UPDATE tickets SET status=?, resolved_at=?, reason=? WHERE ticket_id=?",
        (status, now_iso(), reason, ticket_id),
    )
    conn.commit()
    return cur.rowcount > 0


# ── 判定结果 ──────────────────────────────────────────────────────────
def save_judgement(conn: sqlite3.Connection, record: dict) -> None:
    """落库一条判定记录。

    ⚠️ 这里必须把工作台要用的字段**全存下来**。之前的版本只存了列表页要的字段，
       把 resume_base / draft_lines / atoms / audit_trail 落下了，
       结果快照只能给空数组 —— 工作台的「溯源对比」和「审计时间线」两个视图直接是白的。
       算出来的东西不落库 = 没算。
    """
    def js(value, default):
        return json.dumps(value if value is not None else default, ensure_ascii=False)

    payload = {
        "jd_id": record.get("jd_id"),
        "trace_id": record.get("trace_id", ""),
        "company": record.get("company", ""),
        "title": record.get("title", ""),
        "city": record.get("city", ""),
        "fetched_at": record.get("fetched_at", ""),
        "stale": 1 if record.get("stale") else 0,
        "verdict": record.get("verdict"),
        "verdict_5": record.get("verdict_5"),
        "hard_flags": js(record.get("hard_flags"), []),
        "hints": js(record.get("hints"), []),
        "score": js(record.get("score"), {}),
        "reasons": js(record.get("reasons"), []),
        "missing_info": js(record.get("missing_info"), []),
        "jd_text": record.get("jd_text", ""),
        "draft_path": record.get("draft_path"),
        "rewrite_round": int(record.get("rewrite_round", 0) or 0),
        "cost_cny": float(record.get("cost_cny", record.get("cost", 0)) or 0),
        "elapsed": float(record.get("elapsed", 0) or 0),
        "ticket_id": record.get("ticket_id"),
        "approval_status": record.get("approval_status", cfg.APPROVAL_NOT_NEEDED),
        "updated_at": now_iso(),
        "resume_base": js(record.get("resume_base"), []),
        "draft_lines": js(record.get("draft_lines"), []),
        "atoms": js(record.get("atoms"), []),
        "evidence_map": js(record.get("evidence_map"), []),
        "missed": js(record.get("missed"), []),
        "audit_trail": js(record.get("audit_trail", record.get("audit")), []),
        # ↓ 「怎么改简历」的数据源。不存这几列，工作台就只能给你打分、给不出处方。
        "jd_mapping": js(record.get("jd_mapping"), []),
        "changes": js(record.get("changes"), []),
        "cover_letter": record.get("cover_letter") or "",
    }
    cols = ", ".join(payload)
    marks = ", ".join("?" * len(payload))
    conn.execute(f"INSERT OR REPLACE INTO judgements ({cols}) VALUES ({marks})",
                 tuple(payload.values()))
    conn.commit()


def fetch_judgements(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM judgements ORDER BY updated_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for key in JSON_COLUMNS:
            raw = d.get(key)
            default = {} if key == "score" else []
            try:
                d[key] = json.loads(raw) if raw else default
            except (json.JSONDecodeError, TypeError):
                d[key] = default
        out.append(d)
    return out


def to_workbench_record(r: dict) -> dict:
    """把一条 judgements 行转成 `workbench/CONTRACT.md` 规定的记录结构。

    **离线看板（build_snapshot）和网页工作台（server 的 /judgements）共用这一个函数。**
    两处各写一份的话，迟早出现「看板有数据、页面没数据」这种不一致 —— 已经踩过一次了：
    页面要 `audit`、库里存的是 `audit_trail`，字段名错开就整块数据消失。
    """
    return {
        "jd_id": r.get("jd_id"),
        "company": r.get("company") or "",
        "title": r.get("title") or "",
        "city": r.get("city") or "",
        "fetched_at": r.get("fetched_at") or "",
        "stale": bool(r.get("stale")),
        "verdict": r.get("verdict"),
        "verdict_5": r.get("verdict_5"),
        "hard_flags": r.get("hard_flags") or [],
        "hints": r.get("hints") or [],
        "score": r.get("score") or {},
        "reasons": r.get("reasons") or [],
        "missing_info": r.get("missing_info") or [],
        "jd_text": r.get("jd_text") or "",
        "draft_path": r.get("draft_path"),
        "resume_base": r.get("resume_base") or [],
        "draft_lines": r.get("draft_lines") or [],
        "atoms": r.get("atoms") or [],
        "evidence_map": r.get("evidence_map") or [],
        "missed": r.get("missed") or [],
        "audit": r.get("audit_trail") or [],
        # ↓ 工作台「怎么改」视图的数据源（逐条处方 / 修改说明 / 招呼语）
        "jd_mapping": _as_list(r.get("jd_mapping")),
        "changes": _as_list(r.get("changes")),
        "cover_letter": r.get("cover_letter") or "",
        "rewrite_round": r.get("rewrite_round") or 0,
        "cost": r.get("cost_cny") or 0.0,
        "elapsed": r.get("elapsed") or 0.0,
        "ticket_id": r.get("ticket_id"),
        "approval_status": r.get("approval_status") or cfg.APPROVAL_NOT_NEEDED,
    }


def row_count(conn: sqlite3.Connection, table: str) -> int:
    """验证写没写进去用 —— 别信『我以为写成功了』。"""
    if table not in ("audit", "cost", "tickets", "judgements"):
        raise ValueError(f"未知表名 {table!r}")
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def reset(conn: sqlite3.Connection) -> None:
    for table in ("audit", "cost", "tickets", "judgements"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
