from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
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


def fetch_json(url: str, timeout: int = 5, source: str = "metadata") -> dict:
    """
    Fetch JSON from a public metadata API.

    Permanent failures return an empty mapping. Transient failures return a
    small marker so the caller can retry the paper on a later run.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "paper-slack-bot/1.0 "
                    "(+https://github.com/Kh-Yabu/paper_slack_bots_ver2)"
                ),
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return json.loads(raw.decode(charset, errors="replace"))
    except Exception as e:
        parsed = urllib.parse.urlsplit(url)
        status = getattr(e, "code", "")
        print(
            f"[WARN] metadata_fetch_failed source={source} "
            f"host={parsed.netloc} path={parsed.path} "
            f"status={status or 'unknown'} error={type(e).__name__}"
        )
        if DRY_RUN:
            print(f"[DRY_RUN] metadata_fetch_error_detail error={e}")
        if status:
            transient = status == 429 or 500 <= int(status) < 600
        else:
            transient = isinstance(
                e,
                (urllib.error.URLError, TimeoutError, OSError),
            )
        if transient:
            return {
                "_metadata_transient_error": {
                    "source": source,
                    "status": status or "unknown",
                    "error": type(e).__name__,
                }
            }
        return {}


def _openalex_params(params: dict[str, str]) -> dict[str, str]:
    """Add optional OpenAlex credentials without requiring them for DOI lookups."""
    result = dict(params)
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    mailto = os.getenv("OPENALEX_MAILTO", "").strip()
    if api_key:
        result["api_key"] = api_key
    if mailto:
        result["mailto"] = mailto
    return result


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

    # feed_last_build is the RSS refresh time, not the paper publication date.
    # Using it as a Crossref year filter hides older papers still present in
    # ScienceDirect feeds.
    if pub_dt is not None and journal.get("date_strategy") != "feed_last_build":
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
    data = fetch_json(
        url,
        timeout=int(journal.get("metadata_timeout", 5)),
        source="crossref_title",
    )
    if data.get("_metadata_transient_error"):
        return data

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


def enrich_from_openalex_doi(doi: str) -> dict:
    """Retrieve an OpenAlex work by DOI using the free singleton endpoint."""
    normalized_doi = normalize_doi(doi)
    if not normalized_doi:
        return {}

    doi_value = normalized_doi.removeprefix("doi:")
    work_id = urllib.parse.quote(f"https://doi.org/{doi_value}", safe=":/")
    params = _openalex_params(
        {
            "select": (
                "doi,title,display_name,abstract_inverted_index,"
                "primary_location,type,publication_year"
            )
        }
    )
    url = (
        f"https://api.openalex.org/works/{work_id}?"
        + urllib.parse.urlencode(params)
    )
    data = fetch_json(url, source="openalex_doi")
    if data.get("_metadata_transient_error"):
        return data
    if not data or data.get("error"):
        return {}

    returned_doi = normalize_doi(data.get("doi", ""))
    if returned_doi and returned_doi != normalized_doi:
        return {}

    abstract = openalex_abstract_from_inverted_index(
        data.get("abstract_inverted_index")
    )
    if DRY_RUN:
        print(
            f"[DRY_RUN] openalex_doi_match "
            f"doi={normalized_doi} abstract_length={len(abstract)}"
        )

    return {
        "source": "openalex",
        "score": 1.0,
        "doi": returned_doi or normalized_doi,
        "abstract": abstract,
    }


def enrich_from_openalex(title: str, journal_title: str = "") -> dict:
    """
    Search OpenAlex by title and return DOI/abstract if a high-confidence match is found.
    """
    params = _openalex_params({
        "search": text_from_maybe_html(title),
        "filter": "type:article",
        "per-page": "3",
        "select": "doi,title,display_name,abstract_inverted_index,primary_location,type,publication_year",
    })

    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    data = fetch_json(url, source="openalex_title")
    if data.get("_metadata_transient_error"):
        return data

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

    # Crossref is a reliable DOI resolver for publisher feeds even when it has
    # no abstract. Once a DOI is known, OpenAlex's singleton DOI lookup is free
    # and avoids consuming the much smaller full-text search allowance.
    crossref_result: dict = {}
    transient_error: dict = {}
    if "crossref" in sources and "openalex" in sources:
        crossref_result = enrich_from_crossref(
            title,
            journal_title,
            journal=journal,
            pub_dt=pub_dt,
        )
        if crossref_result.get("_metadata_transient_error"):
            transient_error = crossref_result
            crossref_result = {}
        crossref_doi = crossref_result.get("doi", "")
        if crossref_doi:
            openalex_result = enrich_from_openalex_doi(crossref_doi)
            if openalex_result.get("_metadata_transient_error"):
                return openalex_result
            candidates = [crossref_result]
            if openalex_result:
                candidates.append(openalex_result)
            return max(
                candidates,
                key=lambda result: len(result.get("abstract", "")),
            )

    for source in sources:
        if source == "crossref" and crossref_result:
            result = crossref_result
        elif source == "openalex":
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

        if result.get("_metadata_transient_error"):
            transient_error = result
            continue

        if not result:
            continue

        doi = result.get("doi", "")
        abstract = result.get("abstract", "")

        # DOI alone is still useful for stable posted IDs.
        if doi or len(abstract) >= int(journal.get("min_abstract_length", 500)):
            return result

    return transient_error
