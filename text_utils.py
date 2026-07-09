from __future__ import annotations

import html as html_lib
import json
import re
from difflib import SequenceMatcher

from bs4 import BeautifulSoup


def parse_json_object(text: str) -> dict:
    """
    Parse a JSON object from model output.
    Returns {} if parsing fails.
    """
    s = (text or "").strip()

    # Remove Markdown fences if the model accidentally adds them.
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)

    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def clean_abstract(text: str) -> str:
    """
    Remove unwanted leading markers like 'Abstract' or 'Summary' from abstract text.
    """
    return re.sub(
        r"^(Summary|Abstract)\s*",
        "",
        text.strip(),
        flags=re.IGNORECASE,
    )

_BIBLIOGRAPHIC_ABSTRACT_RE = re.compile(
    r"^\s*"
    r"(publication date\s*:|source\s*:|author\(s\)\s*:|authors?\s*:|volume\s*:)",
    flags=re.IGNORECASE,
)


def is_bibliographic_metadata(text: str) -> bool:
    """
    Return True if text looks like citation metadata rather than a real abstract.
    Typical ScienceDirect RSS summaries may contain only:
    'Publication date: ... Source: ... Author(s): ...'
    """
    s = clean_abstract(text or "")
    s = re.sub(r"\s+", " ", s).strip()

    if not s:
        return False

    s_lc = s.lower()

    has_biblio_fields = (
        "publication date:" in s_lc
        and "source:" in s_lc
        and ("author(s):" in s_lc or "authors:" in s_lc)
    )

    if has_biblio_fields:
        return True

    return bool(_BIBLIOGRAPHIC_ABSTRACT_RE.search(s))
    
def strip_jats(text: str) -> str:
    """
    Crossref abstracts are sometimes JATS/XML fragments.
    Convert them to plain text.
    """
    if not text:
        return ""

    soup = BeautifulSoup(text, "html.parser")
    return soup.get_text(" ", strip=True)


_HTML_TAG_RE = re.compile(r"<[A-Za-z][^>]*>")


def text_from_maybe_html(text: str) -> str:
    """
    Convert only actual HTML-like strings with tags.
    Avoid BeautifulSoup warning for plain titles.
    """
    s = text or ""
    if _HTML_TAG_RE.search(s):
        return BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    return html_lib.unescape(s)


def normalize_title_for_match(title: str) -> str:
    """
    Normalize title for fuzzy matching.
    """
    s = text_from_maybe_html(title)
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s:/.-]", "", s)
    return s.strip()


def title_similarity(a: str, b: str) -> float:
    aa = normalize_title_for_match(a)
    bb = normalize_title_for_match(b)

    if not aa or not bb:
        return 0.0

    return SequenceMatcher(None, aa, bb).ratio()


def openalex_abstract_from_inverted_index(inv: dict | None) -> str:
    """
    Reconstruct plaintext abstract from OpenAlex abstract_inverted_index.
    """
    if not inv:
        return ""

    positions: dict[int, str] = {}

    for word, pos_list in inv.items():
        if not isinstance(pos_list, list):
            continue
        for pos in pos_list:
            try:
                positions[int(pos)] = word
            except Exception:
                continue

    if not positions:
        return ""

    return " ".join(positions[i] for i in sorted(positions))
