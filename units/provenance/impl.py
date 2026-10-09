# -*- coding: utf-8 -*-
"""units/provenance · 溯源校验（纯代码，不调模型）

硬指标②（事实可溯源率 = 100%）由本单元守。它是全项目第二个「敢写 = 100%」的地方，
理由是同一个：**判定结果唯一，不引入随机性**。

三层判定：
    A 类 数字 / 日期 / 机构名 / 职级 → 必须回原简历找到出处
    B 类 技术名词 → 允许来自 JD，但挨着能力断言且简历没有的算编造
    C 类 性质断言 → 与原简历的档位上限比大小，升格就拦（v2 新增，最有价值的一层）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core import config as cfg  # noqa: E402
from core.atoms import extract_atoms, has_capability_assertion, match_in_text, stage_rank  # noqa: E402
from core.normalize import normalize, sentence_of  # noqa: E402


class ProvenanceError(RuntimeError):
    """只读母版被指向、或真源不可用时的硬失败。"""


def read_stage_ceiling(ledger: dict | None = None) -> tuple[str, str]:
    """改写稿允许的最强性质档位。来自 ledger 顶层的人工标注，不来自模型。"""
    ledger = ledger or cfg.load_ledger()
    ceiling = ledger.get("draft_stage_ceiling") or {}
    value = ceiling.get("value")
    if value not in cfg.DELIVERY_STAGES:
        raise ProvenanceError(
            f"ledger.json 的 draft_stage_ceiling.value 非法：{value!r}，"
            f"必须是 {list(cfg.DELIVERY_STAGES)} 之一 —— 拒绝启动（不许降级）"
        )
    return value, ceiling.get("reason", "")


def guard_readonly_base(draft_path, resume_path=None) -> None:
    """只读母版 guard：改写稿不许写回原简历路径。"""
    if draft_path is None:
        return
    base = Path(resume_path or cfg.RESUME_PATH).resolve()
    target = Path(draft_path).resolve()
    if target == base:
        raise ProvenanceError(
            f"改写稿输出路径指向只读母版：{target}\n"
            "原简历是溯源校验的唯一对照物，改写稿必须写新文件 —— 拒绝写入。"
        )
# 防止改写稿覆盖到原简历上 把「原简历路径」和「改写稿输出路径」都转成绝对路径（.resolve()），一样就报错。

def _match_in_resume(atom: str, n_resume: str, *, fuzzy: bool = True) -> bool:
    """原子 → 规范化 → 在原简历的规范化文本里找。

    实现已收敛到 `core.atoms.match_in_text` —— 改写处方（tailor）也要用同一份判定，
    留在两处必然出现「溯源说没编造、处方说没素材」。这里只保留薄封装。
    """
    return match_in_text(atom, n_resume, fuzzy=fuzzy)


def verify(
    draft_text: str,
    resume_text: str,
    *,
    jd_text: str = "",
    ledger: dict | None = None,
    draft_path: str | Path | None = None,
    resume_path: str | Path | None = None,
) -> dict:
    """跑一次溯源校验。

    返回：
        passed            是否放行
        atoms             全部抽出的原子 [{atom, type, found, where, note}]
        evidence_map      能溯源的原子 → 出处
        missed            未命中的原子文案清单
        stage_violations  C 类升格的详细记录
        ceiling           本次使用的档位上限
    """
    guard_readonly_base(draft_path, resume_path)

    if not isinstance(draft_text, str) or not draft_text.strip():
        raise ProvenanceError("改写稿为空 —— 拒绝给出校验结论")

    ceiling, ceiling_reason = read_stage_ceiling(ledger)
    ceiling_rank = stage_rank(ceiling)

    n_resume = normalize(resume_text)
    atoms = extract_atoms(draft_text)
    # 三道前置关卡 1查路径 不许写回原简历 2改写稿空就报错 3读出档位上限转成数字 4把原简历清洗一遍 5从改写稿里抽出所有原子


    evidence_map: list[dict] = []
    missed: list[str] = []
    stage_violations: list[dict] = []
    checked: list[dict] = []
    # 四个收集容器 能找到出处的、找不到出处的（决定成败）、C 类升格明细、全部核对记录。
    for a in atoms:
        atom, kind, atype = a["atom"], a["type"], a["type"]
        found = _match_in_resume(atom, n_resume)
        record = {"atom": atom, "type": kind, "found": found, "where": "", "note": ""}
        # 每个原子做一次通用查找 record 是这条核对的完整记录，四个栏位：原子、类型、找没找到、出处/说明。
        if kind == "C":
            stage = a.get("stage", "")
            rank = stage_rank(stage)
            record["stage"] = stage
            if rank > ceiling_rank:
                record["found"] = False
                record["note"] = (
                    f"性质升格：原简历档位上限为『{ceiling}』，改写稿出现『{atom}』（{stage}档）"
                )
                sent = sentence_of(draft_text, draft_text.find(atom), draft_text.find(atom) + len(atom))
                stage_violations.append({
                    "atom": atom, "draft_stage": stage,
                    "ceiling": ceiling, "sentence": sent,
                    "reason": record["note"],
                })
                missed.append(f"{atom}（性质升格：{stage} > {ceiling}）")
            else:
                record["found"] = True
                record["where"] = f"档位 {stage} ≤ 上限 {ceiling}"
                evidence_map.append({"atom": atom, "type": kind, "found": True,
                                     "where": record["where"], "stage": stage})
            checked.append(record)
            continue
        # C类只看这个档位有没有越线
        if kind == "B":
            if found:
                record["where"] = "原简历技能/经历描述"
                evidence_map.append({"atom": atom, "type": kind, "found": True,
                                     "where": record["where"]})
            elif has_capability_assertion(draft_text, atom):
                record["note"] = f"能力断言（熟练/精通/主导…）旁边的技术名词『{atom}』在原简历里不存在"
                missed.append(f"{atom}（能力断言 + 简历无此技术 = 编造）")
            else:
                # 普通技术名词：允许来自 JD
                record["found"] = True
                record["where"] = "JD 要求的技术名词（非能力断言语境）"
                evidence_map.append({"atom": atom, "type": kind, "found": True,
                                     "where": record["where"]})
            checked.append(record)
            continue
        # B类 1简历有的就记过 2简历里没有，但旁边挂着「熟练/精通/主导」这类断言 → 这是拿别人的技术当自己的本事吹 → 记一条 missed（不放行）。3简历里没有，也不是在吹 → 允许，记「JD 要求的技术名词」。因为改写稿本来就应该照着 JD 的关键词写，技术名词出现在 JD 里是合理的。
        # A 类
        if found:
            record["where"] = "原简历"
            evidence_map.append({"atom": atom, "type": kind, "found": True, "where": record["where"]})
        else:
            record["note"] = f"A 类事实原子『{atom}』在原简历里找不到出处"
            missed.append(f"{atom}（A 类事实，原简历无出处）")
        checked.append(record)
    # A 类最简单也最硬：数字、日期、公司名、职级，找到就过，找不到就记 missed。没有例外、没有豁免——这就是「每个数字/年限/公司名都必须能在原简历找到出处」那条硬要求。
    passed = not missed
    return {
        "passed": passed,
        "atoms": checked,
        "atom_count": len(checked),
        "evidence_map": evidence_map,
        "missed": missed,
        "stage_violations": stage_violations,
        "ceiling": ceiling,
        "ceiling_reason": ceiling_reason,
        "by_type": {
            "A": sum(1 for c in checked if c["type"] == "A"),
            "B": sum(1 for c in checked if c["type"] == "B"),
            "C": sum(1 for c in checked if c["type"] == "C"),
        },
    }
# 汇总返回 passed 0才算过

def to_evidence_markdown(result: dict, jd: dict) -> str:
    """生成 output/<jd_id>/evidence.md 的内容。"""
    lines = [
        f"# 溯源报告 · {jd.get('company')} / {jd.get('title')}",
        "",
        f"- JD：`{jd.get('jd_id')}`（抓取于 {jd.get('fetched_at')}）",
        f"- 改写稿性质档位上限：**{result['ceiling']}**",
        f"- 抽到事实原子：**{result['atom_count']}** 个"
        f"（A 类 {result['by_type']['A']} / B 类 {result['by_type']['B']} / C 类 {result['by_type']['C']}）",
        f"- 校验结果：**{'✅ 全部找到出处' if result['passed'] else '❌ 有未命中项'}**",
        "",
        "## 每个原子的出处",
        "",
        "| 原子 | 类型 | 出处 |",
        "|---|---|---|",
    ]
    # 生成报告文件
    for item in result["atoms"]:
        as_type = {"A": "A 事实", "B": "B 技术", "C": "C 性质"}[item["type"]]
        where = item.get("where") or ("—— " + item.get("note", "未命中"))
        lines.append(f"| `{item['atom']}` | {as_type} | {where} |")
    # 每个原子一行
    if result["stage_violations"]:
        lines += ["", "## ⚠️ 性质升格（这一节就是本项目和同类项目的差别）", ""]
        for v in result["stage_violations"]:
            lines += [
                f"- 改写稿出现 **『{v['atom']}』**（{v['draft_stage']}档），"
                f"但原简历档位上限为 **{v['ceiling']}**",
                f"  - 出现位置：「{v['sentence']}」",
            ]
    # 有升格才写这一节
    if result["missed"]:
        lines += ["", "## 未命中清单（已回退给改写员）", ""]
        lines += [f"- {m}" for m in result["missed"]]

    lines += ["", "---", "", f"> 档位上限的设定理由：{result.get('ceiling_reason', '—')}"]
    return "\n".join(lines) + "\n"
# 收尾 有未命中就单独列一节

if __name__ == "__main__":  # 手动单测
    resume = "负责企业知识库建设，完成 Qwen3 LoRA 微调流程；使用 Python 与 FastAPI。"
    bad = "负责企业知识库建设，已上线生产环境，服务 3000 用户；熟练使用 Kafka。"
    out = verify(bad, resume)
    print("passed:", out["passed"])
    print("missed:", out["missed"])
    print("violations:", [v["atom"] for v in out["stage_violations"]])
