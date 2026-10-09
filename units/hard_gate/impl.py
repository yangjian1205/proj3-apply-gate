# -*- coding: utf-8 -*-
"""units/hard-gate · 硬门槛闸（纯代码，不调模型）

能力边界（写死在这里，防止它越界）：
    我管：JD 明写的死条件与档案客观事实的冲突 —— 签证 / 户籍工作权 / 坐班城市 /
          学历门槛 / 必须持有的证书 / 应届专属 / 语言等级 / 年限阈值。
    我不管：职级高低、技能覆盖度、行业背景、简历写得好不好 —— 那些只打分，永不拦人。

错误策略：
    规则文件读不到 → 直接抛错。**绝不降级成模型判断** —— 一旦降级，漏放率 = 0% 的承诺就废了。

硬指标①（漏放率 = 0%）由我守：
    本单元不引入任何随机性，同样的输入永远给同样的输出，
    所以「硬门槛题必须 100% 命中」是一个可以每天重跑的门禁，不是一个承诺。

两条实测踩过的坑，已固化在代码里：
    1. 匹配跑在**原文**上，不跑在规范化文本上 —— 只有原文的索引才能切出「是 JD 子串」的引文。
    2. 排除词只在**匹配所在的那一句**内生效，且前面带否定词（不/无/未/非/没）时不算排除。
       否则「不提供工作签证支持」里的「提供工作签证支持」会把它自己排除掉。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core import config as cfg  # noqa: E402
from core.normalize import sentence_of, snippet  # noqa: E402

# ── 城市词表（用于 onsite_only 的冲突判定）────────────────────────────
CITY_KEYWORDS = (
    "北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安",
    "苏州", "天津", "重庆", "长沙", "厦门", "合肥", "郑州", "济南", "青岛",
    "无锡", "宁波", "东莞", "佛山", "珠海", "香港", "澳门", "台北",
    "新加坡", "迪拜", "东京", "首尔", "纽约", "旧金山", "西雅图", "伦敦",
    "柏林", "悉尼", "多伦多", "阿联酋",
)

_NEG_PREFIX = re.compile(r"[不无未非没]\s*$")

# 中文数字年限（原文匹配用；规范化函数里的转换不参与引文定位）
_CN_YEARS = r"[零一二两三四五六七八九十百]+"


class HardGateError(RuntimeError):
    """规则真源不可用时的硬失败。"""
# 定义专门的错误类型

# ── 匹配工具 ──────────────────────────────────────────────────────────
def _sentence_excluded(sentence: str, excludes: list[str]) -> bool:
    """在句子范围内找排除词；排除词前面紧跟否定词时不算排除。"""
    for ex in excludes or []:
        for m in re.finditer(ex, sentence, re.I):
            if not _NEG_PREFIX.search(sentence[:m.start()]):
                return True
    return False
# 判断这句里有没有排除词 逐个词排除 找到后看他前面有没有否定词 没有才算真的排除

def _match_pattern(text: str, patterns: list[str], excludes: list[str]) -> re.Match | None:
    """在原文上找第一个「所在句内没有排除词」的匹配。返回的 match 索引可用于切片原文。"""
    for pat in patterns or []:
        for m in re.finditer(pat, text, re.I):
            sentence = sentence_of(text, m.start(), m.end())
            if _sentence_excluded(sentence, excludes):
                continue
            return m
    return None


def _cities_in(text: str) -> list[str]:
    return [c for c in CITY_KEYWORDS if c in text]
# 列出JD里提出哪些城市 用列表推导式一遍过

# ── 判定器 ────────────────────────────────────────────────────────────
def _check_pattern_only(rule, text, profile):
    return _match_pattern(text, rule["patterns"], rule.get("exclude_patterns"))
# 文字匹配

def _check_city_conflict(rule, text, profile):
    m = _match_pattern(text, rule["patterns"], rule.get("exclude_patterns"))
    if not m:
        return None
    found = _cities_in(text)
    if not found:
        # JD 没说城市 → 信息不足，不触发（交给 borderline，不许猜）
        return None
    expected = set(profile.get("expected_cities") or [])
    if expected and all(c not in expected for c in found):
        return m
    return None
# 城市冲突

def _check_degree_conflict(rule, text, profile):
    m = _match_pattern(text, rule["patterns"], rule.get("exclude_patterns"))
    if not m:
        return None
    mine = int(profile.get("degree_level", 0) or 0)
    need = int(rule.get("required_level", 99))
    return m if need > mine else None
# 学历

def _check_certificate_conflict(rule, text, profile):
    return _match_pattern(text, rule["patterns"], rule.get("exclude_patterns"))
# 证书

def _check_fresh_grad_conflict(rule, text, profile):
    m = _match_pattern(text, rule["patterns"], rule.get("exclude_patterns"))
    if not m:
        return None
    return None if profile.get("is_fresh_graduate") else m
# 应届生

def _check_language_conflict(rule, text, profile):
    m = _match_pattern(text, rule["patterns"], rule.get("exclude_patterns"))
    if not m:
        return None
    return None if profile.get("language_certs") else m
# 语言证书

def _parse_years(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    from core.normalize import _cn_segment_to_int  # 局部导入，避免污染主链路

    return _cn_segment_to_int(raw)
# 把年限文字变成数字 写成函数内部局部导入

def _check_years_gap(rule, text, profile, thresholds=None):
    m = _match_pattern(text, rule["patterns"], rule.get("exclude_patterns"))
    if not m:
        return None
    raw = next((g for g in m.groups() if g), None)
    required = _parse_years(raw) if raw else None
    if required is None:
        return None
    mine = float(profile.get("years_experience", 0) or 0)
    gap = required - mine
    thresholds = thresholds or {}
    if rule.get("hint_only"):
        lo = thresholds.get("years_gap_hint_min", 1)
        hi = thresholds.get("years_gap_hint_max", 2)
        return m if lo <= gap <= hi else None
    return m if gap >= thresholds.get("years_gap_reject", 3) else None
# 年限判定器 如果这条规则是 hint_only（只提示）：差值落在 1—2 年之间就记为提示，不拦；差值 ≥ 3 年才拦。

def _check_never_reject(rule, text, profile):
    return None  # 第三档永远不产生拦截
# 永久不拦

CHECKERS = {
    "pattern_only": _check_pattern_only,
    "city_conflict": _check_city_conflict,
    "degree_conflict": _check_degree_conflict,
    "certificate_conflict": _check_certificate_conflict,
    "fresh_grad_conflict": _check_fresh_grad_conflict,
    "language_conflict": _check_language_conflict,
    "years_gap": _check_years_gap,
    "never_reject": _check_never_reject,
}
# 规则函数对照表

# ── 主入口 ────────────────────────────────────────────────────────────
def check_hard_rules(
    jd_text: str,
    *,
    rules: dict | None = None,
    profile: dict | None = None,
) -> dict:
    """跑三档规则，返回确定性结果。

    返回：
        flags        命中的拦截规则 id（绝对硬 + 阈值硬）
        rule_ids     同上（兼容 workbench/CONTRACT.md 的字段名）
        quotes       {rule_id: JD 原句引文} —— 引文保证是 jd_text 的**子串**
        reasons      [{type, rule_id, quote, tier, label, advice}]
        hints        只提示不拦的记录（年限差 1—2 年那类）
        soft_hits    第三档命中的词，交给 match-score 打分用，绝不拦人
        hard_verdict pass / reject
    """
    if not isinstance(jd_text, str) or not jd_text.strip():
        raise HardGateError("jd_text 为空 —— 拒绝给出判定（空输入不产生结论）")
    # 第一道 文本空直接报错
    rules = rules or cfg.load_rules()
    profile = profile or cfg.load_profile()
    thresholds = rules.get("thresholds", {})
    # 拿到 规则表 档案 阈值配置
    flags: list[str] = []
    quotes: dict[str, str] = {}
    reasons: list[dict] = []
    hints: list[dict] = []
    soft_hits: dict[str, list[str]] = {}
    # 五个收集容器：命中的规则 id、引文、详细理由、提示、软条件命中
    for rule in rules["rules"]:
        if rule["tier"] == cfg.TIER_SOFT:
            hits = [p for p in rule.get("patterns", []) if re.search(p, jd_text, re.I)]
            if hits:
                soft_hits[rule["rule_id"]] = hits
            continue
            # 软条件先单独处理：只记录命中了哪些词（给打分环节用），然后 continue 跳过——永不进拦截逻辑。
        checker = CHECKERS.get(rule.get("check", ""))
        if checker is None:
            raise HardGateError(f"规则 {rule['rule_id']} 的 check 类型未知：{rule.get('check')!r}")
            # 按名字取判定函数
        if rule.get("check") == "years_gap":
            m = _check_years_gap(rule, jd_text, profile, thresholds)
        else:
            m = checker(rule, jd_text, profile)
        # 跑判定 年限需要单独拿出来
        if not m:
            continue

        # 引文 = 包含匹配位置的整句，切自原文 → 一定是子串
        quote = sentence_of(jd_text, m.start(), m.end())
        if not quote or quote not in jd_text:
            quote = snippet(jd_text, m.group(0))
            # 产出引文
        entry = {
            "type": "hard" if rule["tier"] == cfg.TIER_ABSOLUTE else "threshold",
            "rule_id": rule["rule_id"],
            "quote": quote,
            "tier": rule["tier"],
            "label": rule.get("label", ""),
            "advice": rule.get("advice", ""),
        }
        # 拼一条完整记录
        if rule["tier"] == cfg.TIER_THRESHOLD and rule.get("hint_only"):
            entry["type"] = "hint"
            hints.append(entry)
        else:
            flags.append(rule["rule_id"])
            quotes[rule["rule_id"]] = quote
            reasons.append(entry)
        # 分流：标了 hint_only 的只进 hints（提醒，不拦）；其余的才算真命中，进 flags / quotes / reasons。
    return {
        "flags": flags,
        "rule_ids": list(flags),
        "quotes": quotes,
        "reasons": reasons,
        "hints": hints,
        "soft_hits": soft_hits,
        "hard_verdict": cfg.REJECT if flags else "pass",
        "rules_version": rules.get("rules_version"),
        "rules_count": len(rules["rules"]),
    }
# 返回结果字典。rule_ids 是 flags 的副本，只是为了兼容另一个界面的字段名。hard_verdict 就是最终答案：有命中 = reject，没命中 = pass。另外带上规则版本和条数，方便追溯。



if __name__ == "__main__":  # 手动单测用
    demo = "base 北京，需全职坐班，不支持远程办公；要求 5 年以上工作经验。"
    out = check_hard_rules(demo)
    for r in out["reasons"]:
        print(r["rule_id"], "|", r["quote"])
    print("verdict:", out["hard_verdict"], "| hints:", [h["rule_id"] for h in out["hints"]])
