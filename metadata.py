from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request

from bot_config import DRY_RUN
from identifiers import normalize_doi
from text_utils import (
    openalex_abstract_from_inverted_index,
    strip_jats,
    text_from_maybe_html,
    title_similarity,
)


def fetch_json(url: str, timeout: int = 5) -> dict:
    """
    Fetch JSON from a public metadata API.
    Returns {} on failure.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "paper-slack-bot/1.0 (mailto:kh-yabu@eri.u-tokyo.ac.jp)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return json.loads(raw.decode(charset, errors="replace"))
    except Exception as e:
        if DRY_RUN:
            print(f"[DRY_RUN] metadata_fetch_failed url={url} error={e}")
        return {}


def enrich_from_crossref(
    title: str,
    journal_title: str = "",
    journal: dict | None = None,
    pub_dt: dt.datetime | None = None,
) -> dict:
    """
    Search Crossref by title and return DOI/abstract if a high-confidence match is found.

    For journal RSS feeds, narrow the query by ISSN/type/year when possible.
    Crossref is mostly used here as a DOI resolver; abstracts are often absent
    for Elsevier records.
    """
    journal = journal or {}

    filters = ["type:journal-article"]

    issn = journal.get("issn") or journal.get("crossref_issn")
    if issn:
        filters.append(f"issn:{issn}")

    if pub_dt is not None:
        y = pub_dt.year
        filters.append(f"from-pub-date:{y}-01-01")
        filters.append(f"until-pub-date:{y}-12-31")

    params = {
        "query.title": text_from_maybe_html(title),
        "rows": "3",
        "select": "DOI,title,container-title,abstract,URL,published-online,published-print,type",
        "filter": ",".join(filters),
    }

    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    data = fetch_json(url, timeout=int(journal.get("metadata_timeout", 5)))

    items = data.get("message", {}).get("items", [])

    best = None
    best_score = 0.0

    for item in items:
        item_titles = item.get("title") or []
        item_title = item_titles[0] if item_titles else ""

        score = title_similarity(title, item_title)

        containers = item.get("container-title") or []
        container_text = " ".join(containers).lower()
        if journal_title and journal_title.lower() in container_text:
            score += 0.03

        if score > best_score:
            best_score = score
            best = item

    if not best or best_score < float(journal.get("metadata_match_threshold", 0.92)):
        if DRY_RUN:
            print(
                f"[DRY_RUN] crossref_no_confident_match "
                f"score={best_score:.3f} title={title}"
            )
        return {}

    doi = normalize_doi(best.get("DOI", ""))
    abstract = strip_jats(best.get("abstract", ""))

    if DRY_RUN:
        print(
            f"[DRY_RUN] crossref_match "
            f"score={best_score:.3f} doi={doi} abstract_length={len(abstract)}"
        )

    return {
        "source": "crossref",
        "score": best_score,
        "doi": doi,
        "abstract": abstract,
    }


def enrich_from_openalex(title: str, journal_title: str = "") -> dict:
    """
    Search OpenAlex by title and return DOI/abstract if a high-confidence match is found.
    """
    params = {
        "search": text_from_maybe_html(title),
        "filter": "type:article",
        "per-page": "3",
        "select": "doi,title,display_name,abstract_inverted_index,primary_location,type,publication_year",
        "mailto": "kh-yabu@eri.u-tokyo.ac.jp",
    }

    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = fetch_json(url)

    items = data.get("results", [])
    if not isinstance(items, list):
        items = []

    best = None
    best_score = 0.0

    for item in items:
        item_title = item.get("title") or item.get("display_name") or ""
        score = title_similarity(title, item_title)

        source = item.get("primary_location", {}).get("source", {}) or {}
        source_name = (source.get("display_name") or "").lower()

        if journal_title and journal_title.lower() in source_name:
            score += 0.03

        if score > best_score:
            best_score = score
            best = item

    if not best or best_score < 0.92:
        if DRY_RUN:
            print(
                f"[DRY_RUN] openalex_no_confident_match "
                f"score={best_score:.3f} title={title}"
            )
        return {}

    doi = normalize_doi(best.get("doi", ""))
    abstract = openalex_abstract_from_inverted_index(
        best.get("abstract_inverted_index")
    )

    if DRY_RUN:
        print(
            f"[DRY_RUN] openalex_match "
            f"score={best_score:.3f} doi={doi} abstract_length={len(abstract)}"
        )

    return {
        "source": "openalex",
        "score": best_score,
        "doi": doi,
        "abstract": abstract,
    }


def enrich_metadata(
    title: str,
    journal: dict,
    pub_dt: dt.datetime | None = None,
) -> dict:
    """
    Try public metadata APIs in configured order.
    """
    sources = journal.get("metadata_fallback") or []
    journal_title = journal.get("full_title") or journal.get("title", "")

    for source in sources:
        if source == "openalex":
            result = enrich_from_openalex(title, journal_title)
        elif source == "crossref":
            result = enrich_from_crossref(
                title,
                journal_title,
                journal=journal,
                pub_dt=pub_dt,
            )
        else:
            result = {}

        if not result:
            continue

        doi = result.get("doi", "")
        abstract = result.get("abstract", "")

        # DOI alone is still useful for stable posted IDs.
        if doi or len(abstract) >= int(journal.get("min_abstract_length", 500)):
            return result

    return {}
