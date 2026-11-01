"""Run inside twitter-cli's own Python: fetch an X long-form article, keeping the blocks twitter-cli drops.

Called as: <twitter-cli's python> x_article.py <tweet id>; prints the same JSON as `twitter article <id> --json`.

Why: twitter-cli's Markdown conversion of article content skips every "atomic" block, and in X articles code
blocks, dividers and images are atomic blocks (their content lives in the entity map; code is of type MARKDOWN).
So code samples came out empty. This swaps its converter in this process only (the installed package is not
modified) and falls back to the original result if the structure is not what it expects. An atomic block's
entityRanges key refers to the entity map entry with the same key, not to a list index.
"""
import sys

try:
    import twitter_cli.parser as P

    _orig = P._parse_article

    def _parse_article(tweet_data):
        out = _orig(tweet_data)
        try:
            ar = P._deep_get(tweet_data, "article", "article_results", "result") or {}
            cs = ar.get("content_state") or {}
            blocks, em = cs.get("blocks") or [], cs.get("entityMap") or []
            if not any(b.get("type") == "atomic" for b in blocks):
                return out
            ents = em if isinstance(em, list) else [{"key": k, "value": v} for k, v in em.items()]
            by_key = {str(e.get("key")): e.get("value") or {} for e in ents}
            parts, n = [], 0
            for b in blocks:
                t, text = b.get("type", "unstyled"), b.get("text", "")
                if t == "atomic":
                    rng = (b.get("entityRanges") or [{}])[0].get("key")
                    v = by_key.get(str(rng), {})
                    kind, data = v.get("type"), v.get("data") or {}
                    if kind == "MARKDOWN" and data.get("markdown"):
                        parts.append(data["markdown"].strip())
                    elif kind == "DIVIDER":
                        parts.append("---")
                    elif kind == "MEDIA":
                        parts.append("[image]")
                    elif kind == "TWEET":
                        parts.append(f"[quoted tweet {data.get('tweetId', '')}]".strip())
                    continue
                if not text:
                    continue
                n = n + 1 if t == "ordered-list-item" else 0
                prefix = {"header-one": "# ", "header-two": "## ", "header-three": "### ", "blockquote": "> ",
                          "unordered-list-item": "- "}.get(t, f"{n}. " if t == "ordered-list-item" else "")
                parts.append(f"```\n{text}\n```" if t == "code-block" else prefix + text)
            if parts:
                out["article_text"] = "\n\n".join(parts)
        except Exception:  # noqa: BLE001 — structure changed: keep the original result
            pass
        return out

    P._parse_article = _parse_article
except Exception:  # noqa: BLE001
    pass

from twitter_cli.cli import cli  # noqa: E402

sys.exit(cli(["article", sys.argv[1], "--json"]))
