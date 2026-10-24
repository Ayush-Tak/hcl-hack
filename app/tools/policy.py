"""Policy search: plain keyword match over data/policies/*.md, one chunk per '## ' section.

Hybrid BM25 + vector search (D-06) comes later; this is the version the demo needs today.
"""

import re
from pathlib import Path

from app.config import settings
from app.tools.registry import Ctx, schema, tool

STOPWORDS = {
    "the",
    "a",
    "an",
    "is",
    "for",
    "of",
    "to",
    "my",
    "what",
    "how",
    "do",
    "i",
    "in",
    "on",
    "and",
    "or",
    "it",
    "can",
    "me",
    "you",
    "your",
    "are",
    "does",
    "much",
    "there",
    "any",
}
MIN_MATCHES = 2


def _front_matter(text: str) -> tuple[dict, str]:
    """Metadata is the `key: value` lines before the first section, with or without --- fences."""
    start = 0 if text.startswith("## ") else text.find("\n## ")
    if start == -1:
        return {}, ""
    meta = {}
    for line in text[:start].splitlines():
        key, colon, value = line.partition(":")
        if colon and key.strip() and " " not in key.strip():
            meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, text[start:].lstrip("\n")


def load_sections(folder: Path) -> list[dict]:
    sections = []
    for path in sorted(folder.glob("*.md")):
        meta, body = _front_matter(path.read_text(encoding="utf-8"))
        for block in re.split(r"^## ", body, flags=re.MULTILINE)[1:]:
            title, _, text = block.partition("\n")
            sections.append(
                {
                    "doc": meta.get("doc", path.stem),
                    "section": title.strip(),
                    "version": meta.get("version", ""),
                    "effective_date": meta.get("effective_date", ""),
                    "text": text.strip(),
                }
            )
    return sections


def _stem(word: str) -> str:
    # Crude, but enough for "delayed" to match "delay" and "refunds" to match "refund".
    for suffix in ("ing", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _words(text: str) -> set[str]:
    return {
        _stem(w)
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if w not in STOPWORDS and len(w) > 2
    }


@tool(
    "search_policy",
    "Search the bank's policy documents (fees, cards, UPI, disputes, loans). Answer only from the "
    "returned text and cite it. If nothing is found, say the policy doesn't cover it.",
    schema({"query": {"type": "string"}}, ["query"]),
)
def search_policy(ctx: Ctx, args: dict) -> dict:
    query = _words(args["query"])
    # A section matches only if MIN_MATCHES different question words appear in it. Among matches,
    # title words count twice, so "compensation" beats a long section that just mentions UPI more.
    matches = [
        (len(query & _words(s["section"] + " " + s["text"])), s)
        for s in load_sections(settings.policies_dir)
    ]
    ranked = sorted(
        (
            (count + len(query & _words(s["section"])), s)
            for count, s in matches
            if count >= MIN_MATCHES
        ),
        key=lambda pair: -pair[0],
    )
    hits = [s for _, s in ranked][:3]
    if not hits:
        return {
            "not_found": "No policy section matches this question",
            "searched": args["query"],
        }
    return {"results": hits}
