"""Tokenizing for search, for languages with and without spaces between words.

Chinese and Japanese have no spaces, so a BM25 index over "words" finds almost
nothing for a whole question. The classic fix is overlapping character
bigrams: 检索重排 → 检索 索重 重排. Latin-script text is split into lowercase
words. Queries are tokenized the same way, so a keyword written by the asking
agent matches no matter how the page phrases the rest of the sentence.
"""

from __future__ import annotations

import functools
import re

from memexos import i18n

CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿가-힯]+")
WORD = re.compile(r"[^\W_]+(?:[.'+#-][^\W_]+)*", re.U)
SPLIT = re.compile(r"[\s，。？！、；：,.?!;:（）()「」『』“”\"'《》<>【】\[\]/\\|]+")


def cjk_bigrams(run: str) -> list[str]:
    if len(run) == 1:
        return [run]
    return [run[i:i + 2] for i in range(len(run) - 1)]


def tokens(text: str) -> list[str]:
    """Index/query tokens: CJK bigrams + lowercase words."""
    out: list[str] = []
    pos = 0
    for m in CJK.finditer(text):
        out += [w.lower() for w in WORD.findall(text[pos:m.start()])]
        out += cjk_bigrams(m.group(0))
        pos = m.end()
    out += [w.lower() for w in WORD.findall(text[pos:])]
    return out


def index_text(text: str) -> str:
    return " ".join(tokens(text))


@functools.cache
def filler_words() -> frozenset[str]:
    """Question and filler words of every language pack, lowercased (they carry no topic)."""
    out: set[str] = set()
    for lg in i18n.available():
        f = i18n.t("search.fillers", lg)
        out |= {w.lower() for w in f} if isinstance(f, list) else set()
    return frozenset(out)


def content_tokens(text: str) -> list[str]:
    """tokens() without filler words: in a small knowledge base "who" or "the" can be rare enough to outrank the topic."""
    fill = filler_words()
    return [t for t in tokens(text) if t not in fill]


def keywords(question: str, lang: str | None = None, limit: int = 6) -> list[str]:
    """Rough keywords from a question: drop question and filler words, keep fragments of 2+ characters.

    The asking agent usually knows better (`--kw`); this is the fallback.
    """
    q = question.strip()
    fillers: list[str] = []
    for lg in ([lang] if lang else list(i18n.available())):   # questions often mix languages
        f = i18n.t("search.fillers", lg)
        fillers += f if isinstance(f, list) else []
    if fillers:
        for f in sorted(set(fillers), key=len, reverse=True):
            if CJK.search(f):
                q = q.replace(f, " ")
            else:
                q = re.sub(r"(?i)\b" + re.escape(f) + r"\b", " ", q)
    # single CJK function characters between content words
    q = re.sub(r"(?<=[一-鿿])[吗呢吧啊呀了的得地和与跟把被给在是也都又还就会要能让]+(?=[一-鿿]|\s|$)", " ", q)
    out = []
    for p in (x for x in SPLIT.split(q) if x):
        if CJK.search(p):
            if len(p) >= 2:
                out.append(p[:8])
        elif len(p) >= 2:
            out.append(p)
    return list(dict.fromkeys(out))[:limit]


def snippet(text: str, terms: list[str], width: int = 160) -> str:
    """The passage, on one line, where the most different terms occur close together (case-insensitive); a term
    found once anywhere beats a passage with none."""
    low = text.lower()
    terms = [t.lower() for t in dict.fromkeys(terms) if t and t.strip()]
    hits = []                                   # (position, term index)
    for k, t in enumerate(terms):
        i = low.find(t)
        n = 0
        while i >= 0 and n < 50:
            hits.append((i, k))
            i = low.find(t, i + 1)
            n += 1
    best, best_score = 0, -1
    for pos, _ in hits:
        start = max(0, pos - width // 3)
        seen = {k for q, k in hits if start <= q < start + width - 10}
        score = len(seen) * 1000 - pos // 1000        # more distinct terms first, then earlier in the page
        if score > best_score:
            best, best_score = pos, score
    start = max(0, best - width // 3) if hits else 0
    s = text[start:start + width]
    s = re.sub(r"\s+", " ", s).strip()
    return ("…" if start > 0 else "") + s + ("…" if start + width < len(text) else "")


def matched(text: str, terms: list[str]) -> list[str]:
    """Which of the terms the text contains (case-insensitive), in the order given."""
    low = text.lower()
    return [t for t in dict.fromkeys(terms) if t and t.lower() in low]
