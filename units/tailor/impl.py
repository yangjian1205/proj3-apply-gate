# -*- coding: utf-8 -*-
"""units/tailor · 定向改写（用模型，但被代码兜底）

改写只做两件事：重排顺序、重述措辞。
红线写在 system prompt 里，**但真正兜底的是下一环的 provenance** ——
我写出去的每一句都会被拆成事实原子逐个回原简历核对，核不上就打回来重写。
所以这里不是「要求模型不要编」，是「编了过不了门」。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from pydantic import BaseModel, Field  # noqa: E402

from core import config as cfg  # noqa: E402
from core.atoms import match_in_text  # noqa: E402
from core.llm import CostLedger, structured_call, text_call  # noqa: E402
from core.normalize import normalize  # noqa: E402

VALID_MAPPING_STATUS = ("已覆盖", "可强化", "真实缺口")


class JdMappingItem(BaseModel):
    """一条「按 JD 的改写处方」。

    这是使用者真正要的东西：**别只告诉我该不该投，告诉我这份 JD 该怎么改简历**。
    所以每条都必须能回答三件事：JD 要什么 → 我简历里有什么 → 该怎么写。
    """

    jd_requirement: str = Field(description="JD 里的一条硬性要求或关键技能，一句话提炼，不超过 30 字")
    jd_quote: str = Field(description="支撑这条要求的 JD 原句，必须逐字照抄 JD 原文（会核对）")
    resume_evidence: str = Field(
        default="",
        description="你简历里对应的原句，逐字照抄，不要改写。简历里确实没有就填空字符串 —— 不许编（会核对）",
    )
    suggestion: str = Field(
        description="建议怎么处理这一条：已有素材的，指出换成什么说法、放到哪个位置；"
        "确实没有的，明说「别编，要么补项目、要么面试时诚实说明」"
    )
    status: str = Field(description="三选一：已覆盖 / 可强化 / 真实缺口")


class TailorOutput(BaseModel):
    resume_md: str = Field(description="针对该 JD 定制的简历正文（Markdown），只重排与重述")
    cover_letter_md: str = Field(description="招呼语 / 求职信（Markdown），不超过 300 字")
    changes: list[str] = Field(description="这次做了哪些重排与重述，逐条写，便于人工复核")
    jd_mapping: list[JdMappingItem] = Field(
        description="按 JD 逐条给出的改写处方，最多 12 条，按重要性排序。"
        "必须覆盖 JD 的所有硬性要求与主要技能项，不能只挑好写的说"
    )


SYSTEM = """你是简历定制改写员。你的稿子会被机器逐项核对，编造的内容一定会被抓出来并打回重写。

你只能做两件事：
1. **重排顺序** —— 把与这个 JD 最相关的项目/经历提到前面。
2. **重述措辞** —— 把简历里已有的事实换成更贴这个 JD 的说法。

绝对禁止（违反一定会被拦下）：
- 新增任何原简历里没有的数字、百分比、金额、年限、人数、日期、公司名、学校名、职级、证书名
- 新增任何原简历里没有的技术名词，特别是「熟练使用 X」「精通 X」「主导 X」这类能力断言
- 把性质升格：原简历写「完成流程 / 跑通链路」，你**不许**写成「已上线 / 生产环境 / 服务 N 用户」
- 编造原简历和经历库之外的任何经历

关于性质表述的档位上限（这条最容易被忽略）：
原简历的三个项目都处于「原型 / 技术验证」阶段 ——
允许写：完成流程、跑通链路、完成评测、端到端验证
禁止写：已上线、生产环境、线上运行、服务 N 用户、已交付客户

改写要具体、可追问，不要用「赋能」「闭环」这类空词。

关于 jd_mapping（逐条改写处方）—— 使用者拿它决定「这份简历要怎么改」，所以：
- **覆盖要全**：JD 里的硬性要求（学历 / 年限 / 技能 / 职责）逐条列，不许只挑你答得上来的写。
  最多 12 条，按「对拿面试影响大小」排序。
- **素材必须真实**：`resume_evidence` 只能逐字照抄简历原文。简历里没有这条素材 →
  留空字符串，`status` 写「真实缺口」。**留空是允许的、加分的；编一句扣分。
  下方代码会拿你的 `resume_evidence` 回简历逐个核对，核不上就整条降级为「真实缺口」。**
- **suggestion 要能直接照做**：写清「换成什么说法 / 放到简历哪个位置 / 哪句话该删」。
  缺口的就直说「这条你没有，别编 —— 要么补一个真做过的项目，要么面试时如实说明」。
- **status 只能是三值之一**：
  已覆盖 = 简历有素材且说法已经够贴；可强化 = 简历有素材但说法不贴这个 JD；
  真实缺口 = 简历里确实没有。**不许为了好看把缺口写成已覆盖。**"""


def _missed_block(missed: list[str], rewrite_round: int) -> str:
    if not missed:
        return ""
    lines = [
        "",
        f"⚠️ 这是第 {rewrite_round} 轮重写。上一轮的稿子有以下内容在原简历里找不到出处，必须删掉或换成真实说法：",
    ]
    lines += [f"  - {m}" for m in missed]
    return "\n".join(lines)


def tailor(
    jd: dict,
    match_result: dict,
    resume_text: str,
    *,
    experience_bank: str = "",
    routing: str = "",
    missed: list[str] | None = None,
    rewrite_round: int = 0,
    ledger: CostLedger | None = None,
    trace_id: str = "",
    llm=None,
) -> dict:
    """产出定制稿 + 招呼语。不合 schema 重试 1 次；仍失败直接报错，不产出半成品。"""
    missed = missed or []
    ceiling = cfg.load_ledger().get("draft_stage_ceiling", {}).get("value", "原型/技术验证")

    prompt = f"""请针对下面这个岗位，把候选人的简历做定向改写。

【岗位】{jd.get('company')} · {jd.get('title')}（{jd.get('city') or '城市未标注'}）

【JD 原文】
{jd['text']}

【匹配判定（供你决定重排重点）】
五态：{match_result.get('stage_5')}
分项分：{match_result.get('score')}（总分 {match_result.get('score_total')}）
判定理由：{match_result.get('reasons')}
已具备的证据：{match_result.get('evidence')}
说明：{match_result.get('gap_anchor') or '—'}

【简历路由（改写重点）】
{routing[:1200]}

【原简历全文（唯一事实来源，一个字都不许改它本身）】
{resume_text}

【经历库卡片（重组的素材来源）】
{experience_bank[:3000]}

【性质档位上限】{ceiling}
{_missed_block(missed, rewrite_round)}

请输出四样东西：改写后的简历正文、招呼语、你做了哪些重排与重述、
以及按 JD 逐条的改写处方（jd_mapping —— 使用者主要看这个来决定简历怎么改，请写全）。"""

    result, usage, err = structured_call(
        [("system", SYSTEM), ("human", prompt)],
        TailorOutput, node="tailor", ledger=ledger, llm=llm, retries=1,
    )

    if result is None:
        raise TailorError(
            f"改写稿生成失败（第 {rewrite_round} 轮）：{err}\n"
            "按设计不产出半成品 —— 宁可不出稿，不许出残稿。"
        )
    if not (result.resume_md or "").strip():
        raise TailorError(f"改写稿正文为空（第 {rewrite_round} 轮）—— 拒绝产出残稿")

    return {
        "resume_md": result.resume_md,
        "cover_letter_md": result.cover_letter_md,
        "changes": result.changes,
        # 逐条处方必须过代码核对才交给使用者 —— 见 verify_mapping 的注释
        "jd_mapping": verify_mapping(result.jd_mapping or [], jd.get("text", ""), resume_text),
        "rewrite_round": rewrite_round,
        "usage": usage.as_dict() if usage else None,
        "trace_id": trace_id,
    }


def _split_clauses(text: str) -> list[str]:
    """按中英文标点切分句 —— 容忍「抄了两句、其中一句被轻度改写」。"""
    parts = re.split(r"[，。；、,.;\n|（）()【】\[\]：:]+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 4]


def verify_mapping(items: list, jd_text: str, resume_text: str) -> list[dict]:
    """代码核对每条处方 —— 模型说的话，回原文验一遍才算数。

    两道核对：
      ① `jd_quote` 必须真出自 JD —— 防止它凭空编一条要求让你去满足；
      ② `resume_evidence` 必须真出自简历 —— **这是「教人改简历」最容易出事的地方**：
         模型很乐意替你「润色」出一条你没做过的经历，写上去面试一问就穿。

    ②核不上时的处理是**整条降级为「真实缺口」并抹掉那段素材**，不许展示给使用者。
    宁可少告诉你一条「可以这样写」，也不能给你一个不存在的素材。
    """
    n_jd = normalize(jd_text)
    n_resume = normalize(resume_text)
    out: list[dict] = []

    for it in items[:12]:
        status = it.status if it.status in VALID_MAPPING_STATUS else "真实缺口"
        quote = (it.jd_quote or "").strip()
        ev = (it.resume_evidence or "").strip()
        note = ""

        quote_verified = bool(quote) and match_in_text(quote, n_jd)

        evidence_verified = False
        if ev:
            evidence_verified = match_in_text(ev, n_resume) or any(
                match_in_text(c, n_resume) for c in _split_clauses(ev)
            )

        if ev and not evidence_verified:
            # 声称的素材在简历里找不到 → 当它不存在，并且把这段编出来的话删掉
            status, ev = "真实缺口", ""
            note = "模型给的简历素材在简历里找不到，已按「真实缺口」处理，素材未展示"
        elif not ev and status != "真实缺口":
            # 没有素材却说「已覆盖 / 可强化」= 自相矛盾
            status = "真实缺口"
            note = "未给出简历素材，已按「真实缺口」处理"
        elif ev and status == "真实缺口":
            # 素材明明有出处，就不可能「确实没有」→ 至少是可强化
            status = "可强化"
            note = "素材在简历里有出处，已从「真实缺口」上调为「可强化」"

        if not quote_verified:
            note = (note + "；" if note else "") + "JD 引文未与原文核对上"

        out.append({
            "jd_requirement": (it.jd_requirement or "").strip(),
            "jd_quote": quote,
            "resume_evidence": ev,
            "suggestion": (it.suggestion or "").strip(),
            "status": status,
            "quote_verified": quote_verified,
            "evidence_verified": evidence_verified,
            "note": note,
        })

    return out


class TailorError(RuntimeError):
    """改写失败 —— 硬失败，不降级产出。"""


def interview_prep(jd: dict, match_result: dict, *, ledger: CostLedger | None = None, llm=None) -> str:
    """可选的面试准备（M9，最容易被砍的模块，默认不在主链路上）。"""
    prompt = (
        f"岗位：{jd.get('company')} · {jd.get('title')}\n\nJD 原文：\n{jd['text']}\n\n"
        f"我的匹配判定：{match_result.get('stage_5')}，理由 {match_result.get('reasons')}\n\n"
        "请给出：① 大概率会被追问的 5 个问题（要基于 JD 与我的经历的交集，不要泛泛而谈）"
        "② 每个问题的回答要点（只许用我简历里真实有的东西）"
        "③ 我可能被问倒的地方，以及诚实的应对方式。"
    )
    text, _ = text_call(
        [("system", "你是面试辅导教练，只使用用户提供的真实材料，不编造经历。"), ("human", prompt)],
        node="interview_prep", ledger=ledger, llm=llm,
    )
    return text
