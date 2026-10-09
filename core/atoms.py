# -*- coding: utf-8 -*-
"""事实原子抽取 —— 溯源校验的核心。

分三类（v1 两类 + v2 新增 C 类）：

    A 类「客观事实」数字 / 日期 / 金额 / 人数 / 机构名 / 学校名 / 职级
        → 必须能在原简历里找到出处，找不到就不放行。

    B 类「技术名词」工具名 / 框架名 / 协议名
        → 可以来自 JD（JD 本来就在要求它）。
          但**例外**：若紧挨着「熟练 / 精通 / 主导 / 负责 / 落地」这类能力断言，
          而简历里没有这个名词 → 判为编造。

    C 类「状态 / 性质断言」上线 / 生产 / 已发布 / 已交付 / 服务 N 用户
        → v2 新增，这是本项目相对同类项目多做的一层。
          原简历只写到「完成流程（技术验证档）」，改写稿不许写「已上线（生产落地档）」。
          判定是**纯枚举比大小**，不需要模型判断「像不像编的」。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config as cfg  # noqa: E402
from core.normalize import normalize  # noqa: E402

# ── A 类 ──────────────────────────────────────────────────────────────
A_PATTERNS: list[tuple[str, str]] = [
    (r"\d+(?:\.\d+)?\s*(?:%|％|万|亿|人|个|条|份|块|题|次|天|个月|年|元|美元|小时|周|周次|倍|张|台|套|篇|页|人次)", "number"),
    (r"\d+(?:\.\d+)?\s*[kK]", "number"),
    (r"20\d{2}\s*[.\-/年]\s*\d{1,2}\s*(?:月)?", "date"),
    (r"20\d{2}\s*[-—~至]\s*20\d{2}", "date_range"),
    (r"[\u4e00-\u9fa5]{2,10}(?:有限公司|科技有限公司|信息技术有限公司|集团|研究院|事务所)", "org"),
    (r"[\u4e00-\u9fa5]{2,10}(?:大学|学院|职业技术学院)", "school"),
    (r"(?:初级|中级|高级|资深|首席|主任)(?:开发工程师|工程师|算法工程师|架构师|研究员)", "job_title"),
    (r"(?:HCIP|HCIE|HCIA|PMP|CISP|CPA|CET)[-  A-Za-z0-9]*", "certificate"),
]

# 有些 A 类词太常见，出现在简历里也未必是真事实，白名单放行
A_WHITELIST_RE = re.compile(r"^(?:1|2|3|4|5|6|7|8|9|10)\s*年$")
# 1数字加单位 2数字加K 3日期 4日期区间 5机构名 6学校名 7职级 8证书
# ── B 类 ──────────────────────────────────────────────────────────────
B_ASSERT_RE = re.compile(r"(熟练|精通|掌握|主导|负责|落地|具备|熟悉|擅长|深耕)")

# 技术名词：优先用手工词表（准确），再补「大写开头英文词」的兜底规则
B_TECH_TERMS = (
    "python", "fastapi", "flask", "django", "pydantic", "uvicorn", "asyncio",
    "langgraph", "langchain", "llamaindex", "openai", "deepseek", "qwen", "通义千问",
    "rag", "bm25", "rrf", "chroma", "milvus", "faiss", "pinecone", "weaviate",
    "embedding", "transformers", "pytorch", "torch", "bert", "lora", "qlora",
    "seqeval", "spacy", "jieba", "numpy", "pandas", "sqlite", "postgres", "postgresql",
    "mysql", "redis", "mongodb", "kafka", "spark", "flink", "hadoop", "elasticsearch",
    "docker", "kubernetes", "k8s", "jenkins", "gitlab", "github", "git",
    "mcp", "function calling", "react", "vue", "spring", "golang", "java", "c++",
    "linux", "nginx", "grafana", "prometheus", "wandb", "streamlit", "gradio",
    "faiss", "vs code", "jupyter", "pytest", "requests", "httpx", "openai sdk",
)

# ── C 类：性质断言 → 交付阶段档位 ─────────────────────────────────────
C_STAGE_PATTERNS: dict[str, tuple[str, ...]] = {
    "生产落地": (
        r"已上线", r"上线运行", r"正式上线", r"生产环境", r"线上环境", r"上线部署",
        r"已发布", r"已交付上线", r"投入使用", r"已部署上线", r"线上稳定",
        r"服务\s*\d+\s*(?:万|千|百)?\s*用户", r"支撑\s*\d+\s*(?:万|千|百)?\s*(?:业务|客户|商家)",
        r"日均\s*\d+", r"线上系统", r"生产可用",
    ),
    "内部试点": (
        r"内部试点", r"小范围验证", r"灰度发布", r"灰度", r"试点运行",
        r"内部试用", r"\d+\s*人试用", r"内部验证",
    ),
    "原型/技术验证": (
        r"完成流程", r"跑通链路", r"跑通", r"完成评测", r"技术验证", r"原型",
        r"完成开发", r"验证性", r"完成链路", r"demo", r"演示版", r"端到端跑通",
    ),
    "计划/估算": (
        r"计划(?:中|开展)?", r"预计", r"拟(?:定|开)", r"规划中", r"待建设", r"方案设计阶段",
    ),
}
# 把说到什么程度分成四个档位 从强到弱


def _dedup(atoms: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out: list[dict] = []
    for a in atoms:
        key = (a["type"], a["atom"])
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out
# 去重

def extract_atoms(text: str) -> list[dict]:
    """从一段文本里抽出全部事实原子。

    返回：[{atom, type, stage?}]  —— type ∈ A / B / C；C 类额外带 stage（档位）
    """
    atoms: list[dict] = []
# 入参是文本 返回一串小字典
    # A 类
    for pattern, kind in A_PATTERNS:
        for m in re.finditer(pattern, text):
            raw = m.group(0).strip()
            if kind == "number" and A_WHITELIST_RE.match(raw):
                continue
            atoms.append({"atom": raw, "type": "A", "kind": kind})
# 拿八条正则轮流扫一遍 命中就记下来
    # B 类：词表命中
    lowered = text.lower()
    for term in B_TECH_TERMS:
        for m in re.finditer(re.escape(term), lowered):
            atoms.append({"atom": text[m.start():m.end()], "type": "B", "kind": "tech"})
    # B 类兜底：连续的大写开头英文词（2 个字母以上），排除句首普通词
    for m in re.finditer(r"\b[A-Z][A-Za-z0-9]{1,15}\b", text):
        word = m.group(0)
        if word.lower() in B_TECH_TERMS:
            continue
        if word in ("AI", "LLM", "RAG", "MCP", "API", "SQL", "JSON", "HTTP", "ID", "UI"):
            atoms.append({"atom": word, "type": "B", "kind": "tech"})

    # C 类：按档位从强到弱扫，同一个词只归最强档
    claimed: set[tuple[int, int]] = set()
    for stage in ("生产落地", "内部试点", "原型/技术验证", "计划/估算"):
        for pattern in C_STAGE_PATTERNS[stage]:
            for m in re.finditer(pattern, text, re.I):
                span = (m.start(), m.end())
                if any(s0 <= span[0] < s1 or s0 < span[1] <= s1 for s0, s1 in claimed):
                    continue
                claimed.add(span)
                atoms.append({
                    "atom": text[m.start():m.end()], "type": "C",
                    "kind": "claim", "stage": stage,
                })

    return _dedup(atoms)


def stage_rank(stage: str) -> int:
    return cfg.DELIVERY_STAGE_RANK.get(stage, -1)
# 把档位名字换成数字 用来比大小 给个没见过的档位就返回-1

def find_assertion_context(text: str, atom: str, window: int = 12) -> str:
    """取原子附近的一小段文本 —— 用来判断这个 B 类词是不是挨着能力断言。"""
    idx = text.find(atom)
    if idx == -1:
        return ""
    return text[max(0, idx - window): idx + len(atom) + window]
# 以这个技术为名词为中心 左右各剪12个字

def has_capability_assertion(text: str, atom: str) -> bool:
    """B 类例外判定：技术名词旁边有没有「熟练 / 精通 / 主导 / 负责」这类断言。"""
    ctx = find_assertion_context(text, atom)
    return bool(B_ASSERT_RE.search(ctx))
# 判断这个词是不是cnb

def match_in_text(atom: str, n_text: str, *, fuzzy: bool = True) -> bool:
    """原子 → 规范化 → 在（已规范化的）文本里找出处。

    调用方必须传**已经 normalize 过的** n_text，否则等于拿两把尺子量。

    ⚠️ 这个函数被两处共用，所以必须留在 core ——
       ① 溯源校验（units/provenance）判断改写稿有没有编造；
       ② 改写处方（units/tailor）判断「建议你写的这句话，你简历里到底有没有素材」。
       各写一份的话，迟早出现「溯源说没编造、处方说没素材」这种自相矛盾。
       synonyms 必须和 normalize(简历) 时用的完全一致，理由见函数注释里的老坑。
    """
    if not atom.strip():
        return False
    needle = normalize(atom, fuzzy=fuzzy, synonyms=True)
    if not needle:
        return False
    return needle in n_text
# 判断这个·原子在目标文本里有没有出处 1两边必须都规范化过 2这个函数在core里 只一份 3synonyms=True 必须和清洗简历时用的设置完全一致，不一致就会两边对不上。


if __name__ == "__main__":  # 手动单测
    demo = "负责企业知识库系统，已上线生产环境，服务 3000 用户；熟练使用 Kafka；完成 Qwen3 LoRA 微调流程。"
    for a in extract_atoms(demo):
        print(a)
# 自测