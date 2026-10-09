# -*- coding: utf-8 -*-
"""units/match-score · 匹配打分与五态分类

只输出**对内五态**，三分类由代码常量映射 —— 口径升级不改对外契约。

失败策略是本单元最要紧的设计：
    模型给不出合法 schema → 重试 1 次 → 仍失败则**降级成「待确认」交人工**。
    绝不猜一个分类。因为「猜错」和「说我不知道」在指标里代价完全不同：
    前者污染准确率，后者只是多一个人工决定的动作。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pydantic import BaseModel, Field  # noqa: E402

from core import config as cfg  # noqa: E402
from core.llm import CostLedger, structured_call  # noqa: E402

STAGE = Literal["已匹配", "表达缺口", "证据不足", "真实缺口", "待确认"]


class ScoreBreakdown(BaseModel):
    relevant: int = Field(description="相关度 0-10：经历与岗位方向的贴合程度")
    level: int = Field(description="职级匹配 0-10：要求职级与本人层级的差距，只影响打分不影响是否投")
    skill: int = Field(description="技能覆盖 0-10：JD 要求的技术栈在简历里的覆盖比例")


class MatchResult(BaseModel):
    stage_5: STAGE = Field(
        description=(
            "五态判定。已匹配=硬门槛全过且证据充分；"
            "表达缺口=简历职责描述里做过但没写进技能栏；"
            "证据不足=材料暗示可能具备但撑不起对外表述；"
            "真实缺口=事实明确不满足且能引用 JD 原句；"
            "待确认=JD 原文根本没提这项条件，信息不足"
        )
    )
    score: ScoreBreakdown
    reasons: list[str] = Field(description="判定理由，每条不超过 40 字，能引用 JD 原句的必须引用")
    evidence: list[str] = Field(description="支撑判定的简历侧证据，必须来自给定的简历材料，不许编")
    missing_info: list[str] = Field(description="判定所缺的关键信息，没有就给空数组")
    gap_anchor: str = Field(
        default="",
        description=(
            "若判为『表达缺口』，写清 JD 要求的能力出现在简历的哪一段职责描述里；"
            "若判为『待确认』，写清 JD 原文缺了哪项条件。其他情况留空"
        ),
    )


SYSTEM = """你是简历与岗位的匹配分析员。你的输出会被下方代码校验，不合规会重试，再不合规就作废。

五种状态的定义（只能从这五个里选一个）：
- 已匹配：硬门槛全过，软条件大部分命中，简历里有可展开的证据
- 表达缺口：简历正文（个人优势 / 工作经历 / 项目经历）里**完全找不到** JD 要求的那项能力的做法描述，但从经历库能看出本人做过
- 证据不足：材料暗示可能具备，但撑不起对外表述
- 真实缺口：事实明确不满足，且能引用 JD 原句作依据
- 待确认：JD 原文**根本没提**这项条件，信息不足

【硬性判定顺序 —— 按顺序检查，前一步成立就不往后走】

第 1 步 · 工作地点是否确定？
  如果 JD 出现「坐班 / 驻场 / 常驻 / 到岗 / 现场 / 线下协作」这类现场要求，
  却没有明确写出工作城市（或写成「多地可选」「以面试沟通为准」「视团队安排而定」「其他条件可谈」），
  → 判「待确认」，并在 missing_info 里逐条列出缺什么（例：未写明工作城市；未说明是否支持远程）。
  ⚠️ 不许用「JD 没写限制就等于没限制」推出 apply —— 那是在替招聘方做决定。
  ⚠️ 反过来：JD 完全没有出现现场要求时，不要因为「没写城市」就判待确认。

第 2 步 · 简历是不是真的没提这项能力？
  「表达缺口」只在一种情况下成立：JD 要求的能力，简历正文里**完全找不到对应的做法描述**。
  如果简历正文已经有对应描述（哪怕只是写在职责条目里、没进技能栈速览），就不算表达缺口，按已匹配处理。
  判「表达缺口」时必须写清三件事：JD 要的是哪一项 + 简历里缺哪句描述 + 建议补哪句话。

第 3 步 · 其余情况按 已匹配 / 真实缺口 / 证据不足 判。

【明令不许做的四件事】
1. 不许输出「该投 / 不该投 / 边界」三分类 —— 那只由代码做映射，你只给五态。
2. 不许猜 JD 里没写的信息。缺信息就判「待确认」并写进 missing_info。
3. 不许编造简历里没有的经历。evidence 只能引用给你的材料。
4. 不许越界判 reject：出差频次、加班强度、职级措辞、公司规模、行业背景这些
   **不在硬门槛清单内**的条件，一律只影响 level / relevant 分项打分，不许据此判「真实缺口」。
   判「真实缺口」只有两种合法来源：① 硬门槛闸已经命中规则（必须转述，不许推翻）；
   ② JD 明写的事实与简历明确冲突，且你能引用 JD 原句。

职级措辞（资深 / 专家）只影响 level 分项打分，**不影响是否值得投**。"""


def _profile_block(profile: dict) -> str:
    return (
        f"候选人档案：期望城市 {profile.get('expected_cities')}，"
        f"接受远程={profile.get('accept_remote')}，接受异地={profile.get('accept_relocate')}，"
        f"最高学历 {profile.get('highest_degree')}（level {profile.get('degree_level')}），"
        f"是否应届={profile.get('is_fresh_graduate')}，"
        f"实际工作年限 {profile.get('years_experience')} 年，"
        f"已持有证书 {profile.get('certificates')}，语言等级证书 {profile.get('language_certs')}，"
        f"目标岗位 {profile.get('target_roles')}"
    )


def match(
    jd: dict,
    profile: dict,
    resume_text: str,
    *,
    hard_result: dict | None = None,
    experience_bank: str = "",
    ledger: CostLedger | None = None,
    trace_id: str = "",
    llm=None,
) -> dict:
    """跑一次匹配判定。返回带 verdict（三分类）与 verdict_5（五态）的字典。"""
    hard_result = hard_result or {}
    hints = hard_result.get("hints", [])
    soft_hits = hard_result.get("soft_hits", {})

    prompt = f"""请判定下面这个岗位与候选人是否匹配。

{_profile_block(profile)}

【硬门槛闸的结论（纯代码，必须转述，不许推翻）】
命中拦截规则：{hard_result.get('flags') or '无'}
只提示不拦的：{[h['rule_id'] for h in hints] or '无'}
第三档命中的词（只影响打分，不许因此判 reject）：{soft_hits or '无'}

【JD 原文】
{jd['text']}

【候选人简历（唯一事实来源，不许在此之外编造任何经历）】
{resume_text[:6000]}

【经历库卡片（改写时可用的素材，判定时的参考）】
{experience_bank[:2000]}

请给出五态判定、三个分项分、理由与证据。"""

    result, usage, err = structured_call(
        [("system", SYSTEM), ("human", prompt)],
        MatchResult, node="match_score", ledger=ledger, llm=llm, retries=1,
    )

    degraded = False
    if result is None:
        # 降级：判「待确认」交人工，绝不猜
        result = MatchResult(
            stage_5="待确认",
            score=ScoreBreakdown(relevant=0, level=0, skill=0),
            reasons=[f"模型未能给出合 schema 的结果，按设计降级为待确认交人工：{err}"],
            evidence=[],
            missing_info=["模型结构化输出失败，需要人工判定"],
            gap_anchor="",
        )
        degraded = True

    score = result.score
    clamped = []
    for field in ("relevant", "level", "skill"):
        value = getattr(score, field)
        if not 0 <= value <= 10:
            clamped.append(f"{field}={value} 超出 0—10，已夹到边界")
            setattr(score, field, max(0, min(10, value)))

    stage = result.stage_5
    if stage not in cfg.STAGE_TO_VERDICT:  # 双保险，schema 已限 Literal
        stage = "待确认"
        degraded = True

    # 硬门槛命中时，三分类必须以代码结论为准 —— 模型无权推翻
    if hard_result.get("hard_verdict") == cfg.REJECT:
        verdict = cfg.REJECT
        if stage not in ("真实缺口",):
            stage = "真实缺口"
    else:
        verdict = cfg.STAGE_TO_VERDICT[stage]

    total = score.relevant + score.level + score.skill
    return {
        "stage_5": stage,
        "verdict": verdict,
        "score": score.model_dump(),
        "score_total": total,
        "reasons": result.reasons,
        "evidence": result.evidence,
        "missing_info": result.missing_info,
        "gap_anchor": result.gap_anchor,
        "degraded": degraded,
        "clamped": clamped,
        "usage": usage.as_dict() if usage else None,
        "trace_id": trace_id,
    }
