from __future__ import annotations

import re
import urllib.parse

_ARXIV_ABS_RE = re.compile(r"^/abs/([^/?#]+)$")  # 例: /abs/2512.13064v2
_DOI_RE = re.compile(
    r"\b10\.\d{4,9}/[^\s\"<>]+",
    flags=re.IGNORECASE,
)


def normalize_doi(raw: str) -> str:
    """
    Normalize DOI-like strings to 'doi:10.xxxx/xxxxx'.
    Returns empty string if DOI is not found.
    """
    s = (raw or "").strip()
    if not s:
        return ""

    # Common DOI URL / prefix forms
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^doi:\s*", "", s, flags=re.IGNORECASE)

    m = _DOI_RE.search(s)
    if not m:
        return ""

    doi = m.group(0).strip().rstrip(").,;]}")
    return f"doi:{doi.lower()}"


def normalize_entry_url(url: str) -> str:
    """
    Remove query parameters and normalize some known patterns to prevent duplicates.
    """
    s = (url or "").strip()
    parsed = urllib.parse.urlparse(s)

    scheme = parsed.scheme
    netloc = parsed.netloc
    path = parsed.path

    # arXiv: treat different versions as the same paper
    # /abs/xxxx.yyyyyvN -> /abs/xxxx.yyyyy
    if netloc.lower().endswith("arxiv.org"):
        m = _ARXIV_ABS_RE.match(path)
        if m:
            arxiv_id = re.sub(r"v\d+$", "", m.group(1))
            path = f"/abs/{arxiv_id}"

    return f"{scheme}://{netloc}{path}".lower()


def normalize_posted_key(entry_id: str) -> str:
    """
    Normalize key read from posted_entries.
    DOI keys are preserved; URL keys are normalized as URLs.
    """
    entry_id = (entry_id or "").strip()
    if entry_id.lower().startswith("doi:"):
        return entry_id.lower()
    return normalize_entry_url(entry_id)


def extract_doi_from_entry(entry) -> str:
    """
    Try to extract DOI from feedparser entry.
    Supports common RSS/Atom fields and link objects.
    """
    candidates: list[str] = []

    for key in (
        "doi",
        "prism_doi",
        "dc_identifier",
        "dc_relation",
        "id",
        "guid",
        "link",
    ):
        value = entry.get(key)
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, list):
            candidates.extend(str(v) for v in value)

    for link_obj in entry.get("links", []) or []:
        if isinstance(link_obj, dict):
            candidates.append(str(link_obj.get("href", "")))
            candidates.append(str(link_obj.get("title", "")))

    # Some feeds only expose DOI inside summary/content.
    for key in ("summary", "description"):
        value = entry.get(key)
        if isinstance(value, str):
            candidates.append(value)

    content = entry.get("content") or []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                candidates.append(str(item.get("value", "")))

    for candidate in candidates:
        doi = normalize_doi(candidate)
        if doi:
            return doi

    return ""


def make_entry_id(raw_url: str, *, entry=None, doi: str = "") -> str:
    """
    Prefer DOI as stable entry_id. Fall back to normalized URL.
    """
    norm_doi = normalize_doi(doi)
    if norm_doi:
        return norm_doi

    if entry is not None:
        norm_doi = extract_doi_from_entry(entry)
        if norm_doi:
            return norm_doi

    return normalize_entry_url(raw_url)
