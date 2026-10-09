#!/usr/bin/env python3
"""把一份工作台快照（JSON）内联进 workbench/template.html，产出单文件 HTML。

用法：
    # 用示例数据生成可双击的预览（默认）
    python scripts/build_workbench.py

    # 用真实快照生成正式看板
    python scripts/build_workbench.py --in data/workbench_snapshot.json --out data/generated/dashboard.html

为什么要"内联"这一步：
    最终 HTML 要满足「零依赖、双击就开」。直接 fetch 本地 data.json 会被浏览器的
    file:// 同源策略拦住，所以只能在构建期把数据塞进 <script id="data-json"> 里。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "workbench" / "template.html"

# 只替换 <script id="data-json" ...> 与它对应的 </script> 之间的内容
PATTERN = re.compile(
    r'(<script\s+id="data-json"[^>]*>)(.*?)(</script>)',
    re.S,
)

REQUIRED_TOP = ("generated_at", "stats", "records")
REQUIRED_RECORD = ("jd_id", "company", "title", "verdict", "verdict_5", "approval_status")


def die(msg: str) -> None:
    print(f"[build_workbench] 失败：{msg}", file=sys.stderr)
    raise SystemExit(1)


def load_snapshot(path: Path) -> dict:
    if not path.exists():
        die(f"找不到快照文件 {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        die(f"{path} 不是合法 JSON：{exc}")
    for key in REQUIRED_TOP:
        if key not in data:
            die(f"{path} 缺少顶层字段 {key!r}")
    if not isinstance(data["records"], list):
        die(f"{path} 的 records 必须是数组")
    for i, rec in enumerate(data["records"]):
        missing = [k for k in REQUIRED_RECORD if k not in rec]
        if missing:
            die(f"{path} records[{i}] 缺少字段：{missing}")
    return data


def build(snapshot: dict, template_path: Path, out_path: Path) -> None:
    if not template_path.exists():
        die(f"找不到模板 {template_path}")
    html = template_path.read_text(encoding="utf-8")
    if not PATTERN.search(html):
        die('模板里找不到 <script id="data-json"> 标记')

    # ensure_ascii=False 让中文原样写入；转义 </ 防止提前闭合 script 标签
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    out = PATTERN.sub(lambda m: m.group(1) + payload + m.group(3), html, count=1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out, encoding="utf-8")

    n = len(snapshot["records"])
    pending = sum(1 for r in snapshot["records"] if r.get("approval_status") == "pending")
    print(f"[build_workbench] 已生成 {out_path}")
    print(f"  快照时间：{snapshot.get('generated_at')}")
    print(f"  记录 {n} 条，其中待审批 {pending} 条")
    print(f"  文件大小：{out_path.stat().st_size:,} 字节")


def main() -> int:
    ap = argparse.ArgumentParser(description="把快照内联进工作台模板，生成单文件 HTML。")
    ap.add_argument("--in", dest="src", default=str(ROOT / "workbench" / "data.sample.json"),
                    help="快照 JSON 路径（默认 workbench/data.sample.json）")
    ap.add_argument("--out", dest="dst", default=str(ROOT / "workbench" / "preview.html"),
                    help="输出 HTML 路径（默认 workbench/preview.html）")
    ap.add_argument("--template", dest="tpl", default=str(TEMPLATE), help="模板路径")
    args = ap.parse_args()

    snapshot = load_snapshot(Path(args.src))
    build(snapshot, Path(args.tpl), Path(args.dst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
