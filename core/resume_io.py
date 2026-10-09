# -*- coding: utf-8 -*-
"""core/resume_io · 简历上传落地（解析 → 备份 → 写入 → 状态）

为什么单独一个模块，不塞进 server.py：
上传涉及「解析失败怎么办 / 抽出来是空白怎么办 / 原文件备份在哪」这些**会出错的分支**，
放在 HTTP 处理函数里既难测也容易漏。这里只做纯逻辑，server 那层只管收文件、调这里、回 JSON。

三条设计约束：
1. **必须先备份**。使用者的 resume.md 是溯源唯一对照物，覆盖不备份 = 数据丢失。
2. **拒绝残稿**。抽出字数太少（扫描件 / 空白 PDF）直接报错，不许把空文件写进去 ——
   写进去之后所有溯源都会「找不到出处」，而且没人知道是简历的问题。
3. **头部要标来源**。写明从哪个文件、什么时间、用什么方式抽的，
   以后追溯「这份简历怎么来的」不用问人。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from core import config as cfg

# 支持的扩展名。Word 不在内 —— 与其装依赖不如让用户另存为 PDF，反而更稳。
ALLOWED_SUFFIXES = (".pdf", ".txt", ".md", ".text", ".markdown")
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MIN_TEXT_CHARS = 200

BACKUP_DIR = cfg.PROFILE_DIR / "backups"


class ResumeIOError(RuntimeError):
    """上传失败 —— 带上「使用者该怎么做」的话，别只给技术报错。"""


# ── 解析 ──────────────────────────────────────────────────────────────
def extract_text(filename: str, blob: bytes) -> tuple[str, str]:
    """把上传的字节抽成纯文本。返回 (文本, 抽取方式说明)。"""
    suffix = Path(filename or "").suffix.lower()

    if suffix == ".pdf":
        try:
            import pymupdf  # 新包名。老写法 `import fitz` 会打弃用警告
        except ImportError as exc:  # pragma: no cover
            raise ResumeIOError("缺少 pymupdf，无法解析 PDF：pip install pymupdf") from exc
        try:
            doc = pymupdf.open(stream=blob, filetype="pdf")
        except Exception as exc:  # noqa: BLE001
            raise ResumeIOError(f"这个 PDF 打不开：{exc}") from exc
        try:
            n_pages = doc.page_count
            chunks: list[str] = []
            for page in doc:
                # ⚠️ 用 blocks，不要用默认的逐行抽取。
                #    PDF 里「性 别：」和「男」是两个独立文本对象，「求职意向：…应用」和「开发工程师」
                #    也是 —— 逐行抽出来就是 197 行、37% 是 12 字以下的碎行（实测过）。
                #    blocks 按段落分组，块内换行只是**视觉换行**，合并掉才还原成一句完整的话。
                for block in page.get_text("blocks"):
                    raw = str(block[4]) if len(block) > 4 else ""
                    if raw.strip():
                        chunks.append(join_block(raw))
        finally:
            doc.close()
        return tidy("\n".join(chunks)), f"PDF（pymupdf 按段落抽取 {n_pages} 页）"

    if suffix in ALLOWED_SUFFIXES:
        for enc, label in (("utf-8-sig", "UTF-8"), ("gbk", "GBK")):
            try:
                return tidy(blob.decode(enc)), f"纯文本（{label} 编码）"
            except UnicodeDecodeError:
                continue
        raise ResumeIOError("文本编码识别不了（既不是 UTF-8 也不是 GBK）。"
                            "用记事本另存为 UTF-8 再传一次。")

    raise ResumeIOError(
        f"不支持的格式 {suffix or '（没有扩展名）'}。"
        f"支持 {'、'.join(ALLOWED_SUFFIXES)}。"
        "Word 文档请在 Word 里「文件 → 另存为 → PDF」再上传。"
    )


def _is_wide(ch: str) -> bool:
    """中日韩字符或全角标点 —— 这类字符之间不该塞空格。"""
    return ("\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u30ff"
            or "\uff00" <= ch <= "\uffef")


def join_block(raw: str) -> str:
    """把一个 PDF 文本块里的**视觉换行**合并成完整句子。

    规则：两边都是中日韩 / 全角字符 → 直接拼；否则补一个空格。
      「应用」+「开发工程师」→ 直接拼（中文之间本来没空格）
      「性 别：」+「男」      → 直接拼（全角冒号收尾）
      「Chat」+「工具调用」    → 补空格（中英之间要有）
    """
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines:
        return ""
    out = lines[0]
    for ln in lines[1:]:
        if out and _is_wide(out[-1]) and _is_wide(ln[0]):
            out += ln
        else:
            out += " " + ln
    return out


def tidy(text: str) -> str:
    """清理抽取结果的排版噪声（PDF 抽出来的东西行尾空格、三连空行特别多）。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def check_text(text: str) -> None:
    """抽出内容太少就拒收 —— 写进去之后所有溯源都会失败，而且没人知道是简历的问题。"""
    if len(text) < MIN_TEXT_CHARS:
        raise ResumeIOError(
            f"只抽出 {len(text)} 个字，太少，像是扫描件或空白 PDF。\n"
            "怎么办：① 如果是图片型 PDF，换一份文字版；"
            "② 或用 Word 打开后「另存为 PDF」；③ 也可以直接传 .txt。"
        )


# ── 写入（必先备份）────────────────────────────────────────────────────
def save_resume(text: str, *, filename: str, method: str) -> dict:
    """备份现有简历 → 写入新简历。返回本次落地信息。"""
    cfg.PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    backup: Path | None = None
    if cfg.RESUME_PATH.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"resume-{time.strftime('%Y%m%d-%H%M%S')}.md"
        backup.write_bytes(cfg.RESUME_PATH.read_bytes())

    header = (
        "# 本人 · 简历原文（网页上传）\n\n"
        f"> 来源：`{filename}`（{method}，{time.strftime('%Y-%m-%d %H:%M')} 上传）\n"
        "> **本文件不可变** —— 它是 `provenance` 做事实溯源的唯一对照物，"
        "改写稿里每个事实都必须能在这里找到出处。\n"
        f"{f'> 上一版已备份到 `{backup.relative_to(cfg.ROOT).as_posix()}`' if backup else ''}\n"
    )
    cfg.RESUME_PATH.write_text(header + "\n" + text, encoding="utf-8")

    return {
        "chars": len(text),
        "lines": len([ln for ln in text.splitlines() if ln.strip()]),
        "method": method,
        "backup": backup.name if backup else None,
        "saved_to": str(cfg.RESUME_PATH.relative_to(cfg.ROOT).as_posix()),
    }


# ── 状态 ──────────────────────────────────────────────────────────────
def current() -> dict:
    """当前工作台用的是哪份简历。`is_example=True` 表示还是仓库自带的模板。"""
    if cfg.RESUME_PATH.exists():
        path, is_example = cfg.RESUME_PATH, False
    elif cfg.RESUME_EXAMPLE.exists():
        path, is_example = cfg.RESUME_EXAMPLE, True
    else:
        return {"exists": False, "is_example": False, "chars": 0, "lines": 0,
                "updated_at": "", "preview": "", "backups": []}

    text = path.read_text(encoding="utf-8")
    return {
        "exists": True,
        "is_example": is_example,
        "filename": path.name,
        "chars": len(text),
        "lines": len([ln for ln in text.splitlines() if ln.strip()]),
        "updated_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
        # 给全文。早先只给前 500 字，使用者在页面上点「看一眼」发现看不全，以为内容丢了一段。
        "preview": text,
        "backups": list_backups(),
    }


def list_backups() -> list[dict]:
    """旧版备份清单（新的在前），带大小与时间，供页面显示与恢复。"""
    if not BACKUP_DIR.exists():
        return []
    out = []
    for p in sorted(BACKUP_DIR.glob("resume-*.md"), reverse=True):
        st = p.stat()
        out.append({
            "name": p.name,
            "bytes": st.st_size,
            "updated_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
        })
    return out


def restore_backup(filename: str) -> dict:
    """把某一版备份恢复成当前简历。

    备份存在的意义就是**能恢复** —— 只备不还原等于没备。
    恢复动作本身也要先备份当前版本，否则「恢复错了」就再也回不去。
    """
    name = Path(filename).name  # 只取文件名，防 ../ 目录穿越
    src = BACKUP_DIR / name
    if not src.exists():
        raise ResumeIOError(f"找不到备份 {name}。现有备份：{[b['name'] for b in list_backups()] or '（无）'}")

    cfg.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if cfg.RESUME_PATH.exists():
        keep = BACKUP_DIR / f"resume-{time.strftime('%Y%m%d-%H%M%S')}.md"
        keep.write_bytes(cfg.RESUME_PATH.read_bytes())

    cfg.RESUME_PATH.write_bytes(src.read_bytes())
    return {"restored": name, "current": current()}
