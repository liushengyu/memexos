"""The fixed classification dimensions: stable ids in the catalog, labels in the vault's language on pages.

Models are asked in labels (they read the taxonomy in the vault's language); the catalog stores ids, so a vault
can switch language and old entries still mean the same thing. Parsing accepts either a label (any language) or an id.
"""

from __future__ import annotations

DIMENSIONS: dict[str, dict[str, dict[str, str]]] = {
    "content_type": {
        "tutorial": {"en": "tutorial", "zh": "教程实操"}, "explainer": {"en": "explainer", "zh": "原理讲解"},
        "course": {"en": "course", "zh": "课程讲座"}, "test": {"en": "test", "zh": "实测评测"},
        "case-study": {"en": "case study", "zh": "案例复盘"}, "interview": {"en": "interview", "zh": "访谈对话"},
        "opinion": {"en": "opinion", "zh": "观点评论"}, "news": {"en": "news", "zh": "资讯动态"},
        "showcase": {"en": "showcase", "zh": "作品展示"}, "tool-intro": {"en": "tool intro", "zh": "工具介绍"},
        "paper": {"en": "paper", "zh": "论文研究"}, "project-log": {"en": "project log", "zh": "项目记录"}},
    "level": {"intro": {"en": "intro", "zh": "入门"}, "intermediate": {"en": "intermediate", "zh": "进阶"},
              "expert": {"en": "expert", "zh": "专家"}},
    "evidence": {
        "first-hand": {"en": "first-hand test", "zh": "一手实测"}, "official": {"en": "official or original author", "zh": "官方或原作者"},
        "academic": {"en": "academic research", "zh": "学术研究"}, "third-party": {"en": "third-party report", "zh": "第三方转述"},
        "opinion": {"en": "personal opinion", "zh": "个人经验观点"}, "promotional": {"en": "promotional", "zh": "营销推广"}},
    "usage": {
        "learn-skill": {"en": "learn a skill", "zh": "学技能"}, "understand": {"en": "understand how it works", "zh": "查原理"},
        "facts": {"en": "look up facts", "zh": "查事实数据"}, "inspiration": {"en": "inspiration", "zh": "找灵感参考"},
        "track-news": {"en": "track news", "zh": "跟踪动态"}, "compare": {"en": "compare options", "zh": "选型参考"},
        "decision-review": {"en": "decision review", "zh": "决策复盘"}},
    "durability": {"long-term": {"en": "long-term", "zh": "长期"}, "within-year": {"en": "within a year", "zh": "一年内"},
                   "short-lived": {"en": "short-lived", "zh": "时效短"}},
}


def labels(dim: str, lang: str) -> list[str]:
    return [v.get(lang) or v["en"] for v in DIMENSIONS[dim].values()]


def label(dim: str, value, lang: str):
    """id (or list of ids) → label(s) in lang; unknown values pass through."""
    if isinstance(value, list):
        return [label(dim, v, lang) for v in value]
    d = DIMENSIONS[dim].get(to_id(dim, value) or "")
    return (d.get(lang) or d["en"]) if d else value


def to_id(dim: str, value):
    """label in any language, or id → id; unknown → None (lists map element-wise and drop unknowns)."""
    if isinstance(value, list):
        return [x for x in (to_id(dim, v) for v in value) if x]
    if value is None:
        return None
    v = str(value).strip()
    if v in DIMENSIONS[dim]:
        return v
    for k, names in DIMENSIONS[dim].items():
        if v in names.values() or v.lower() in (n.lower() for n in names.values()):
            return k
    return None
