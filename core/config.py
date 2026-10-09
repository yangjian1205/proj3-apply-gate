# -*- coding: utf-8 -*-
"""proj3-apply-gate 共用配置：路径、枚举、常量映射。

设计原则：**凡是「映射关系」都写死在这里，不许写进 prompt**。
比如五态→三分类、交付阶段档位，都是代码常量，不是模型自由发挥的地方。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# ── 目录 ─────────────────────────────────────────────────────────────
DATA = ROOT / "data"
JD_DIR = DATA / "jd"
PROFILE_DIR = DATA / "profile"
GENERATED_DIR = DATA / "generated"
OUTPUT_DIR = ROOT / "output"

# ── 真源（唯一事实来源，其余全部生成）─────────────────────────────────
RULES_PATH = DATA / "rules.json"
LEDGER_PATH = DATA / "ledger.json"
PROFILE_JSON = PROFILE_DIR / "candidate_profile.json"
# ⚠️ 评测 / 门禁专用档案（固定不变），生产路径用使用者自己的 PROFILE_JSON。
#
# 为什么要分开：门禁要回答的是「**代码有没有退化**」，不是「使用者的偏好变没变」。
# 两者混在一起会出事 —— 实测踩过：使用者把期望城市从「广州」改成「上海」后，
# 一道「base 上海」的硬门槛题就不再构成冲突（**这是正确行为**），
# 但评测集的 gold 是按「广州」标注的，于是门禁无故变红、漏放率 0% 跳到 5%。
# 那是档案变了，不是代码坏了。所以评测一律跑固定档案，与使用者的档案解耦。
EVAL_PROFILE_JSON = DATA / "eval_profile.json"
RESUME_PATH = PROFILE_DIR / "resume.md"
EXPERIENCE_BANK = PROFILE_DIR / "experience_bank.md"

# ── 模板（脱敏，入库；真实档案缺失时的回退）──────────────────────────
# 真实档案含手机号 / 邮箱 / 真实公司名，一律 .gitignore；仓库里只放这几个模板。
# 别人 clone 下来没填档案时回退到模板 —— 这样门禁/评测能跑通、看得到效果，
# 同时 doctor 会明确告诉你「你现在用的是示例档案，不是你的」。
# **绝不静默回退**：用了哪个记在 EXAMPLE_IN_USE 里，入口处会打印警告。
LEDGER_EXAMPLE = DATA / "ledger.example.json"
PROFILE_EXAMPLE_JSON = PROFILE_DIR / "candidate_profile.example.json"
RESUME_EXAMPLE = PROFILE_DIR / "resume.example.md"
EXPERIENCE_BANK_EXAMPLE = PROFILE_DIR / "experience_bank.example.md"

EXAMPLE_IN_USE: set[str] = set()

# ── 运行期数据 ────────────────────────────────────────────────────────
AUDIT_DB = DATA / "audit.db"
CHECKPOINT_DB = DATA / "checkpoint.db"

# ── 工作台 ────────────────────────────────────────────────────────────
WORKBENCH_TEMPLATE = ROOT / "workbench" / "template.html"
WORKBENCH_SNAPSHOT = DATA / "workbench_snapshot.json"
DASHBOARD_HTML = GENERATED_DIR / "dashboard.html"

# ── 档位 ──────────────────────────────────────────────────────────────
TIER_ABSOLUTE = "absolute"
TIER_THRESHOLD = "threshold"
TIER_SOFT = "soft"

# ── 对外三分类 ────────────────────────────────────────────────────────
APPLY = "apply"
REJECT = "reject"
BORDERLINE = "borderline"
VERDICTS = (APPLY, REJECT, BORDERLINE)
VERDICT_CN = {APPLY: "该投", REJECT: "不该投", BORDERLINE: "边界"}

# ── 对内五态 → 对外三分类（写死成常量，这是接口，不是提示词的工作）──
STAGE_TO_VERDICT: dict[str, str] = {
    "已匹配": APPLY,
    "表达缺口": BORDERLINE,
    "证据不足": BORDERLINE,
    "真实缺口": REJECT,
    "待确认": BORDERLINE,
}
STAGES = tuple(STAGE_TO_VERDICT)

# ── 交付阶段档位（C 类性质断言的判定依据：纯枚举比大小）──────────────
DELIVERY_STAGE_RANK: dict[str, int] = {
    "计划/估算": 0,
    "原型/技术验证": 1,
    "内部试点": 2,
    "生产落地": 3,
}
DELIVERY_STAGES = tuple(DELIVERY_STAGE_RANK)

# ── 审批状态 ──────────────────────────────────────────────────────────
APPROVAL_NOT_NEEDED = "not_needed"
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_STATES = (
    APPROVAL_NOT_NEEDED,
    APPROVAL_PENDING,
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
)

# 产物扫描的黑名单词：成稿里出现这些字样 → FAIL
FORBIDDEN_IN_FINAL = ("pending", "待补", "verify_failed", "【待补", "TODO", "占位")


class ConfigError(RuntimeError):
    """真源读不到 / 结构不对时抛这个。绝不降级成别的判断方式。"""


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise ConfigError(f"真源文件不存在：{path} —— 拒绝启动（不允许降级）")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法 JSON：{exc}") from exc


def load_rules() -> dict:
    """读硬门槛规则唯一真源。读不到直接抛错 —— 没有规则宁可不跑。"""
    data = _load_json(RULES_PATH)
    if not isinstance(data.get("rules"), list) or not data["rules"]:
        raise ConfigError(f"{RULES_PATH} 里没有 rules 数组")
    seen: set[str] = set()
    for rule in data["rules"]:
        rid = rule.get("rule_id")
        if not rid:
            raise ConfigError(f"{RULES_PATH} 有规则缺 rule_id：{rule}")
        if rid in seen:
            raise ConfigError(f"{RULES_PATH} 里 rule_id 重复：{rid}")
        seen.add(rid)
        if rule.get("tier") not in (TIER_ABSOLUTE, TIER_THRESHOLD, TIER_SOFT):
            raise ConfigError(f"{rid} 的 tier 非法：{rule.get('tier')!r}")
    return data


def _load_json_or_example(real: Path, example: Path, kind: str) -> dict:
    """真实档案优先；缺失时回退到脱敏模板，并记下「当前用的是示例」。

    ⚠️ 回退**必须留痕**（EXAMPLE_IN_USE），入口处会打印警告。
       别人 clone 下来直接跑：门禁和评测都能通（模板与模板简历自洽），
       但 doctor 会明说「你现在用的是示例档案」—— 绝不让人误以为在判自己的简历。
    """
    if real.exists():
        return _load_json(real)
    if example.exists():
        EXAMPLE_IN_USE.add(kind)
        return _load_json(example)
    raise ConfigError(
        f"缺少 {real.name}，也没有模板 {example.name}。\n"
        f"请把 {example.name} 复制成 {real.name} 再填你自己的信息。"
    )


def load_profile() -> dict:
    """读候选人档案（硬门槛判定的输入）。

    可用环境变量 `PROJ3_PROFILE` 指向别的档案文件 —— 评测与门禁靠它切到固定档案
    （`data/eval_profile.json`），保证「使用者改自己的档案」不会让门禁结果漂移。
    生产路径（`main.py judge` / 网页工作台）不设这个变量，用的就是使用者真实档案。
    """
    override = os.getenv("PROJ3_PROFILE")
    if override:
        return _load_json(Path(override))
    return _load_json_or_example(PROFILE_JSON, PROFILE_EXAMPLE_JSON, "candidate_profile")


def using_examples() -> list[str]:
    """当前进程里哪些档案用的是模板 —— 入口处据此提醒使用者。"""
    return sorted(EXAMPLE_IN_USE)


def use_eval_profile() -> None:
    """把当前进程切到评测固定档案。评测与 L1 门禁入口都要先调它。"""
    if not EVAL_PROFILE_JSON.exists():
        raise ConfigError(
            f"评测固定档案不存在：{EVAL_PROFILE_JSON}\n"
            "没有它，评测结果会跟着使用者的个人档案漂移。"
        )
    os.environ["PROJ3_PROFILE"] = str(EVAL_PROFILE_JSON)


def ledger_path() -> Path:
    """账本实际路径：真实账本优先，缺失时回退脱敏模板。

    需要它单独存在的原因：`validate_ledger.py` 与 `check_generated.py` 是按**路径**读文件的，
    它们不能只调 `load_ledger()` 拿 dict —— 还得知道「读的是哪个文件」（要写进产物、要报给使用者）。
    """
    if LEDGER_PATH.exists():
        return LEDGER_PATH
    if LEDGER_EXAMPLE.exists():
        EXAMPLE_IN_USE.add("ledger")
        return LEDGER_EXAMPLE
    raise ConfigError(
        f"缺少 {LEDGER_PATH.name}，也没有模板 {LEDGER_EXAMPLE.name}。\n"
        f"请把 {LEDGER_EXAMPLE.name} 复制成 {LEDGER_PATH.name} 再填你自己的经历。"
    )


def load_ledger() -> dict:
    """简历事实基线（C 类性质断言靠它拿档位）。缺失时回退脱敏模板。"""
    return _load_json(ledger_path())


def load_resume() -> str:
    """原简历纯文本 —— 溯源校验的唯一对照物。缺失时回退模板简历。"""
    if RESUME_PATH.exists():
        return RESUME_PATH.read_text(encoding="utf-8")
    if RESUME_EXAMPLE.exists():
        EXAMPLE_IN_USE.add("resume")
        return RESUME_EXAMPLE.read_text(encoding="utf-8")
    raise ConfigError(
        f"原简历不存在：{RESUME_PATH}\n"
        f"请把 {RESUME_EXAMPLE.name} 复制成 {RESUME_PATH.name}，再粘贴你自己的简历原文。"
    )


def load_experience_bank() -> str:
    """经历库（改写时的重组素材来源）。缺失时回退模板。"""
    if EXPERIENCE_BANK.exists():
        return EXPERIENCE_BANK.read_text(encoding="utf-8")
    if EXPERIENCE_BANK_EXAMPLE.exists():
        EXAMPLE_IN_USE.add("experience_bank")
        return EXPERIENCE_BANK_EXAMPLE.read_text(encoding="utf-8")
    return ""


def iter_jd_files() -> list[Path]:
    return sorted(JD_DIR.glob("jd-*.json"))


def load_jd(path: Path) -> dict:
    return _load_json(path)
