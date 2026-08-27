"""
RSS / Atom / Springer API / Copernicus Recent ソースの取得と投稿。

ファイル内の構成:
  1. 日付パース
  2. エントリ ID・フィルタ
  3. abstract 取得
  4. フィード取得（RSS/HTTP）
  5. Copernicus Recent アダプタ
  6. Springer Meta API アダプタ
  7. 投稿オーケストレーション（エントリ処理パイプライン・フィード取得レジストリ）
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from collections.abc import Callable
from datetime import timedelta
from email.utils import parsedate_to_datetime

import feedparser
from bs4 import BeautifulSoup
from slack_sdk import WebClient

from bot_config import DRY_RUN, TZ_TOKYO, config, springer_api_key
from classifier import classify_relevance
from identifiers import extract_doi_from_entry, normalize_doi
from metadata import enrich_metadata, fetch_json
from posted import PostedRecord, resolve_posted_key
from posting_flow import post_and_record_rss, record_posted_entry
from summarizer import maybe_summarize
from text_utils import clean_abstract, is_bibliographic_metadata, text_from_maybe_html


# ── 定数 ────────────────────────────────────────────

_COPERNICUS_DATE_RE = re.compile(r"^\d{1,2} [A-Z][a-z]{2} \d{4}$")
_COPERNICUS_DOI_RE = re.compile(r"10\.5194/[^\s,;<>]+", flags=re.IGNORECASE)


# ── 1. 日付パース ───────────────────────────────────

def get_entry_datetime(entry) -> dt.datetime | None:
    # 1) feedparserがパースしたstruct_timeを優先
    st = entry.get("updated_parsed") or entry.get("published_parsed")
    if st:
        return dt.datetime.fromtimestamp(
            calendar.timegm(st),
            tz=dt.timezone.utc,
        ).astimezone(TZ_TOKYO)

    # 2) 日付文字列をパース（RSS: pubDate, Atom: updated/published 等）
    for k in ("updated", "published", "pubDate", "dc_date"):
        v = entry.get(k)
        if not v:
            continue
        try:
            d = parsedate_to_datetime(v)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d.astimezone(TZ_TOKYO)
        except Exception:
            pass

    return None


def get_feed_datetime(feed) -> dt.datetime | None:
    """
    Get feed-level timestamp such as RSS channel lastBuildDate.
    Used for feeds whose items do not have per-entry dates, e.g. ScienceDirect RSS.
    """
    feed_meta = getattr(feed, "feed", {}) or {}

    st = (
        feed_meta.get("updated_parsed")
        or feed_meta.get("published_parsed")
        or feed_meta.get("lastbuilddate_parsed")
    )
    if st:
        return dt.datetime.fromtimestamp(
            calendar.timegm(st),
            tz=dt.timezone.utc,
        ).astimezone(TZ_TOKYO)

    for k in (
        "updated",
        "published",
        "lastbuilddate",
        "lastBuildDate",
        "pubDate",
    ):
        v = feed_meta.get(k)
        if not v:
            continue
        try:
            d = parsedate_to_datetime(v)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d.astimezone(TZ_TOKYO)
        except Exception:
            pass

    return None


def get_effective_entry_datetime(entry, feed, journal: dict) -> dt.datetime | None:
    """
    Per-entry date is preferred.
    If unavailable and journal.date_strategy == 'feed_last_build',
    use RSS channel-level lastBuildDate.
    """
    pub_dt = get_entry_datetime(entry)
    if pub_dt is not None:
        return pub_dt

    if journal.get("date_strategy") == "feed_last_build":
        return get_feed_datetime(feed)

    return None


# ── 2. エントリ ID・フィルタ ─────────────────────────

def ensure_urlish(s: str) -> str:
    s = (s or "").strip()
    p = urllib.parse.urlparse(s)

    if p.scheme:
        return s

    if "/" in s and "." in s.split("/")[0]:
        return "https://" + s

    return s


def build_entry_id_candidates(entry, raw: str) -> tuple[str, str | None]:
    doi = extract_doi_from_entry(entry)

    # extract_doi_from_entry() already returns the canonical form
    # like "doi:10.xxxx/...".  Do not add another "doi:" prefix.
    if doi:
        return doi, raw

    return raw, None


def get_title_text(entry) -> str:
    """
    Return a plain-text title without triggering BeautifulSoup filename warnings.
    """
    return text_from_maybe_html(entry.get("title", "") or "").strip()


def matches_include_keywords(title: str, abstract: str, journal: dict) -> bool:
    """
    If journal has include_keywords, pass only entries whose title or abstract
    contains at least one keyword. If include_keywords is absent or empty,
    do not filter.
    """
    include_keywords = journal.get("include_keywords") or []
    if not include_keywords:
        return True

    text = f"{title}\n{abstract}".lower()
    return any(str(kw).lower() in text for kw in include_keywords)


def matches_exclude_title_patterns(title: str, journal: dict) -> tuple[bool, str]:
    """
    Exclude non-research items by title pattern before metadata/classifier calls.
    """
    patterns = journal.get("exclude_title_patterns") or []
    if not patterns:
        return False, ""

    title_lc = title.lower()

    for pattern in patterns:
        pattern_lc = str(pattern).lower().strip()
        if not pattern_lc:
            continue

        if title_lc.startswith(pattern_lc):
            return True, pattern_lc

    return False, ""


def find_existing_posted_key(
    posted: dict[str, PostedRecord],
    *entry_ids: str,
) -> str:
    """
    Return canonical posted key for the first identifier already known.
    """
    for entry_id in entry_ids:
        existing_key = resolve_posted_key(posted, entry_id)
        if existing_key:
            return existing_key

    return ""


# ── 3. abstract 取得 ─────────────────────────────────

def pick_html_for_abstract(entry, abstract_tag: str) -> str:
    tag = (abstract_tag or "summary").lower()

    if tag == "content":
        # feedparser: entry.content is a list of dicts; safely pick first if exists
        c = entry.get("content") or []
        if c and isinstance(c, list) and "value" in c[0]:
            return c[0]["value"]
        return entry.get("summary", "")

    if tag == "description":
        return entry.get("description") or entry.get("summary", "")

    # default: summary
    return entry.get("summary", "")


def fetch_url_text(url: str, timeout: int = 8) -> str:
    """
    Fetch HTML text with a browser-like User-Agent.
    Returns empty string on failure.
    """
    if not url:
        return ""

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; paper-slack-bot/1.0; "
                    "+https://github.com/)"
                ),
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(charset, errors="replace")
    except Exception as e:
        if DRY_RUN:
            print(f"[DRY_RUN] html_fetch_failed url={url} error={e}")
        return ""


def extract_abstract_from_html(html: str) -> str:
    """
    Extract abstract-like text from publisher HTML.
    Tries common meta tags and ScienceDirect-like abstract sections.
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # 1) Common metadata fields
    meta_names = [
        "dc.description",
        "description",
        "citation_abstract",
        "og:description",
    ]
    for name in meta_names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find(
            "meta",
            attrs={"property": name},
        )
        if tag and tag.get("content"):
            text = clean_abstract(tag["content"])
            if len(text) >= 80:
                return text

    # 2) ScienceDirect often has an abstracts container
    candidates = []

    for selector in [
        "#abstracts",
        "section#abstracts",
        "div#abstracts",
        "section.Abstracts",
        "div.Abstracts",
        "div.abstract",
        "section.abstract",
    ]:
        for node in soup.select(selector):
            text = node.get_text(" ", strip=True)
            text = clean_abstract(text)
            if len(text) >= 80:
                candidates.append(text)

    if candidates:
        return max(candidates, key=len)

    # 3) Generic fallback: find heading 'Abstract' and read nearby text
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        htxt = heading.get_text(" ", strip=True).lower()
        if htxt == "abstract":
            parts = []
            for sib in heading.find_all_next(["p", "div"], limit=8):
                txt = sib.get_text(" ", strip=True)
                if txt:
                    parts.append(txt)
            text = clean_abstract(" ".join(parts))
            if len(text) >= 80:
                return text

    return ""


# ── 4. フィード取得（RSS/HTTP） ─────────────────────

def _is_agu_wiley_feed_url(url: str) -> bool:
    return (
        "agupubs.onlinelibrary.wiley.com" in (url or "")
        and "/action/showFeed" in (url or "")
    )


def fetch_feed_bytes_with_curl(
    url: str,
    timeout: int = 30,
) -> tuple[bytes, int | None, str, str]:
    """
    Fallback for Wiley/AGU RSS feeds.

    Use a curl request that is intentionally close to the manual browser-UA
    probe.  Wiley/Cloudflare can return 403 to Python urllib and sometimes to
    bare curl, so this fallback uses browser-like headers, compression, HTTP/1.1,
    retries, and a small cookie warm-up request before fetching the RSS URL.
    """
    marker = b"\n__PAPER_SLACK_CURL_META__"
    fmt = "\n__PAPER_SLACK_CURL_META__%{http_code}\t%{content_type}\t%{url_effective}"

    common_headers = [
        "-A",
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "-H",
        (
            "Accept: application/rss+xml, application/rdf+xml, "
            "application/atom+xml, application/xml;q=0.9, "
            "text/xml;q=0.8, */*;q=0.5"
        ),
        "-H",
        "Accept-Language: en-US,en;q=0.9,ja;q=0.8",
        "-H",
        "Cache-Control: no-cache",
        "-H",
        "Pragma: no-cache",
        "-H",
        "Referer: https://agupubs.onlinelibrary.wiley.com/",
    ]

    try:
        with tempfile.NamedTemporaryFile(prefix="paper-slack-agu-cookies-") as cookie_fp:
            cookie_file = cookie_fp.name

            # Warm up cookie jar.  Ignore failure; some runners are blocked even
            # here, but successful warm-up can make the subsequent RSS request
            # behave like the manual browser-like curl probe.
            subprocess.run(
                [
                    "curl",
                    "-L",
                    "-sS",
                    "--compressed",
                    "--http1.1",
                    "--connect-timeout",
                    "10",
                    "--max-time",
                    "15",
                    "-c",
                    cookie_file,
                    "-b",
                    cookie_file,
                    *common_headers,
                    "https://agupubs.onlinelibrary.wiley.com/",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=20,
            )

            proc = subprocess.run(
                [
                    "curl",
                    "-L",
                    "-sS",
                    "--compressed",
                    "--http1.1",
                    "--retry",
                    "2",
                    "--retry-delay",
                    "2",
                    "--connect-timeout",
                    "10",
                    "--max-time",
                    str(timeout),
                    "-c",
                    cookie_file,
                    "-b",
                    cookie_file,
                    *common_headers,
                    "-w",
                    fmt,
                    url,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=timeout + 10,
            )
    except Exception as e:
        if DRY_RUN:
            print(f"[DRY_RUN] feed_fetch_curl_exception url={url} error={e}")
        return b"", None, "", url

    out = proc.stdout or b""
    idx = out.rfind(marker)
    if idx < 0:
        if DRY_RUN:
            err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            print(
                f"[DRY_RUN] feed_fetch_curl_no_meta url={url} "
                f"returncode={proc.returncode} stderr={err[:300]}"
            )
        return out, None, "", url

    raw = out[:idx]
    meta = out[idx + len(marker):].decode("utf-8", errors="replace")
    parts = meta.split("\t", 2)

    status = None
    content_type = ""
    final_url = url
    if len(parts) >= 1:
        try:
            status = int(parts[0].strip())
        except Exception:
            status = None
    if len(parts) >= 2:
        content_type = parts[1].strip()
    if len(parts) >= 3:
        final_url = parts[2].strip() or url

    if DRY_RUN:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raw_head = raw[:120].decode("utf-8", errors="replace").replace("\n", " ")
        print(
            f"[DRY_RUN] feed_fetch_curl url={url} final_url={final_url} "
            f"status={status} content_type={content_type} "
            f"bytes={len(raw)} returncode={proc.returncode} "
            f"head={raw_head}"
            + (f" stderr={err[:300]}" if err else "")
        )

    return raw, status, content_type, final_url

def fetch_feed_bytes(
    url: str,
    timeout: int = 30,
) -> tuple[bytes, int | None, str, str]:
    """
    Fetch an RSS/Atom/RDF feed with browser-like headers.

    Some publisher feeds, especially Wiley/AGU search feeds, return 403 to
    Python/feedparser's default URL loader while curl/browser-like requests get
    valid XML.  Therefore we fetch bytes ourselves and pass the bytes to
    feedparser instead of giving feedparser the URL directly.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "application/rss+xml, application/rdf+xml, application/atom+xml, "
                "application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"
            ),
            "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
        },
    )

    raw = b""
    status: int | None = None
    content_type = ""
    final_url = url

    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            status = getattr(res, "status", None)
            content_type = res.headers.get("Content-Type", "")
            final_url = res.geturl()
            raw = res.read()
    except urllib.error.HTTPError as e:
        status = e.code
        content_type = e.headers.get("Content-Type", "") if e.headers else ""
        final_url = getattr(e, "url", url)
        try:
            raw = e.read() or b""
        except Exception:
            raw = b""
        if DRY_RUN:
            print(
                f"[DRY_RUN] feed_fetch_http_error url={url} final_url={final_url} "
                f"status={status} content_type={content_type}"
            )
    except urllib.error.URLError as e:
        if DRY_RUN:
            print(f"[DRY_RUN] feed_fetch_url_error url={url} error={e}")
        return b"", None, "", url
    except TimeoutError as e:
        # A socket read timeout may escape urllib as a bare TimeoutError.
        # Treat it as an empty feed so one slow publisher does not abort the
        # entire multi-journal run.
        if DRY_RUN:
            print(
                f"[DRY_RUN] feed_fetch_timeout "
                f"url={url} timeout={timeout} error={e}"
            )
        return b"", None, "", url

    # Wiley/AGU can still return 403 to Python urllib even when curl works
    # from the same GitHub Actions runner.  Keep RSS as the primary source and
    # fall back to curl only for AGU feed URLs.
    if status == 403 and _is_agu_wiley_feed_url(url):
        curl_raw, curl_status, curl_content_type, curl_final_url = fetch_feed_bytes_with_curl(
            url,
            timeout=timeout,
        )
        if curl_status == 200 and curl_raw:
            raw = curl_raw
            status = curl_status
            content_type = curl_content_type
            final_url = curl_final_url

    if DRY_RUN and raw and (
        raw.lstrip().lower().startswith(b"<!doctype html")
        or b"<html" in raw[:500].lower()
    ):
        head = raw[:500].decode("utf-8", errors="replace").replace("\n", " ")
        print(
            f"[DRY_RUN] feed_fetch_html url={url} final_url={final_url} "
            f"status={status} content_type={content_type} head={head}"
        )

    return raw, status, content_type, final_url


def parse_feed_with_headers(journal: dict) -> feedparser.FeedParserDict:
    """Fetch a normal RSS/Atom/RDF feed through fetch_feed_bytes()."""
    url = journal.get("rss_url", "")
    raw, status, content_type, final_url = fetch_feed_bytes(
        url,
        timeout=int(journal.get("feed_timeout", 30)),
    )

    feed = feedparser.parse(raw) if raw else feedparser.parse(b"")
    feed["status"] = status
    feed["content_type"] = content_type
    feed["href"] = final_url or url
    return feed


def build_agu_taxonomy_feed_url(
    concept_id: int | str,
    *,
    mi: str = "6hngq57",
    sort_by: str = "Earliest",
) -> str:
    """
    Build a Wiley/AGU RSS URL for one taxonomy ConceptID.

    Keep the search restricted to the taxonomy itself.  In particular, do not
    add AllField=seismology/volcanology, because that would turn the query into
    an intersection with a text search rather than a pure taxonomy feed.
    """
    concept_id_text = str(concept_id).strip()
    if not concept_id_text.isdigit():
        raise ValueError(f"invalid AGU ConceptID: {concept_id!r}")

    sort_by_text = str(sort_by or "Earliest").strip()
    if sort_by_text not in {"Latest", "Earliest"}:
        raise ValueError(
            f"invalid AGU sort order: {sort_by!r}; "
            "expected 'Latest' or 'Earliest'"
        )

    encoded_query = (
        f"%2526ConceptID%253D{concept_id_text}"
        "%2526content%253DarticlesChapters"
        f"%2526sortBy%253D{sort_by_text}"
        "%2526target%253Ddefault"
    )
    return (
        "https://agupubs.onlinelibrary.wiley.com/action/showFeed"
        f"?ui=0&mi={urllib.parse.quote(str(mi), safe='')}"
        "&type=search&feed=rss"
        f"&query={encoded_query}"
    )


def build_agu_keyword_feed_url(
    search_term: str,
    *,
    mi: str = "6hngq57",
    sort_by: str = "Earliest",
) -> str:
    """
    Build a Wiley/AGU RSS URL for one broad AllField search term.

    Empirical checks for this endpoint showed that Earliest returns the recent
    side of the result set, so this source intentionally keeps Earliest fixed.
    """
    term = str(search_term or "").strip()
    if not term:
        raise ValueError("AGU search term must not be empty")

    sort_by_text = str(sort_by or "Earliest").strip()
    if sort_by_text != "Earliest":
        raise ValueError(
            f"invalid AGU broad-search sort order: {sort_by!r}; "
            "expected 'Earliest'"
        )

    encoded_term = urllib.parse.quote(term, safe="")
    encoded_query = (
        f"%2526AllField%253D{encoded_term}"
        "%2526content%253DarticlesChapters"
        "%2526sortBy%253DEarliest"
        "%2526target%253Ddefault"
    )
    return (
        "https://agupubs.onlinelibrary.wiley.com/action/showFeed"
        f"?ui=0&mi={urllib.parse.quote(str(mi), safe='')}"
        "&type=search&feed=rss"
        f"&query={encoded_query}"
    )


def _iter_agu_search_terms(journal: dict) -> list[str]:
    """Normalize and de-duplicate broad AGU AllField search terms."""
    raw_terms = journal.get("agu_search_terms") or []
    if isinstance(raw_terms, str):
        raw_terms = [raw_terms]

    terms: list[str] = []
    seen: set[str] = set()
    for raw_term in raw_terms:
        term = str(raw_term or "").strip()
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        terms.append(term)

    return terms


def _iter_agu_taxonomy_specs(journal: dict) -> list[dict]:
    """
    Normalize configured AGU taxonomy entries.

    Expected configuration:
      agu_taxonomies:
        - name: Body Waves
          index_term: "7203"
          concept_id: 103783

    Entries with a missing/null concept_id are ignored so a generated mapping
    can be reviewed incrementally before every term has been resolved.
    """
    normalized: list[dict] = []

    for raw_spec in journal.get("agu_taxonomies") or []:
        if isinstance(raw_spec, dict):
            concept_id = raw_spec.get("concept_id")
            name = str(raw_spec.get("name") or "").strip()
            index_term = str(raw_spec.get("index_term") or "").strip()
            group = str(raw_spec.get("group") or "").strip()
            direct_accept = bool(raw_spec.get("direct_accept", False))
        else:
            concept_id = raw_spec
            name = ""
            index_term = ""
            group = ""
            direct_accept = False

        if concept_id in (None, ""):
            continue

        concept_id_text = str(concept_id).strip()
        if not concept_id_text.isdigit():
            raise ValueError(
                f"invalid AGU taxonomy concept_id={concept_id!r} "
                f"journal={journal.get('title', '')!r}"
            )

        normalized.append(
            {
                "concept_id": int(concept_id_text),
                "name": name or f"ConceptID {concept_id_text}",
                "index_term": index_term,
                "group": group,
                "direct_accept": direct_accept,
            }
        )

    return normalized


def _agu_entry_merge_key(entry: feedparser.FeedParserDict) -> str:
    doi = extract_doi_from_entry(entry)
    if doi:
        return doi

    raw = ensure_urlish(entry.get("link") or entry.get("id") or "")
    if not raw:
        return ""

    parsed = urllib.parse.urlsplit(raw)
    # Search-feed tracking parameters differ by taxonomy.  Strip them so the
    # same article is merged even when no DOI could be extracted.
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            "",
        )
    )


def _add_agu_search_provenance(
    entry: feedparser.FeedParserDict,
    search_term: str,
) -> None:
    terms = list(entry.get("agu_search_terms") or [])
    if search_term not in terms:
        terms.append(search_term)
    entry["agu_search_terms"] = terms


def _agu_entry_journal_names(entry: feedparser.FeedParserDict) -> list[str]:
    """Return publisher journal names carried by an AGU search result."""
    names: list[str] = []
    for key in (
        "prism_publicationname",
        "publicationname",
        "journal",
        "dc_source",
    ):
        value = entry.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in names:
            names.append(value.strip())

    source = entry.get("source")
    if isinstance(source, dict):
        value = source.get("title")
        if isinstance(value, str) and value.strip() and value.strip() not in names:
            names.append(value.strip())
    return names


def _merge_agu_entry(
    target: feedparser.FeedParserDict,
    incoming: feedparser.FeedParserDict,
) -> None:
    """Merge bibliographic data from another occurrence of the same DOI."""
    journals = list(target.get("agu_journals") or [])
    for name in _agu_entry_journal_names(target) + _agu_entry_journal_names(incoming):
        if name not in journals:
            journals.append(name)
    target["agu_journals"] = journals

    # A search result can be sparse in one feed and complete in another. Keep
    # the richest text while filling any fields absent from the first result.
    for key, value in incoming.items():
        if not target.get(key) and value:
            target[key] = value
    for key in ("summary", "description", "content"):
        current = target.get(key)
        if len(str(value := incoming.get(key) or "")) > len(str(current or "")):
            target[key] = value


def _add_agu_taxonomy_provenance(
    entry: feedparser.FeedParserDict,
    spec: dict,
) -> None:
    labels = list(entry.get("agu_taxonomies") or [])
    label = spec["name"]
    if label not in labels:
        labels.append(label)
    entry["agu_taxonomies"] = labels

    index_terms = list(entry.get("agu_index_terms") or [])
    if spec.get("index_term") and spec["index_term"] not in index_terms:
        index_terms.append(spec["index_term"])
    entry["agu_index_terms"] = index_terms

    concept_ids = list(entry.get("agu_concept_ids") or [])
    concept_id = str(spec["concept_id"])
    if concept_id not in concept_ids:
        concept_ids.append(concept_id)
    entry["agu_concept_ids"] = concept_ids

    groups = list(entry.get("agu_taxonomy_groups") or [])
    group = str(spec.get("group") or "").strip()
    if group and group not in groups:
        groups.append(group)
    entry["agu_taxonomy_groups"] = groups

    if spec.get("direct_accept"):
        entry["agu_direct_accept"] = True



def fetch_agu_taxonomy_feed(
    journal: dict,
    _hours_back: int,
) -> feedparser.FeedParserDict:
    """
    Fetch broad AGU keyword RSS feeds and taxonomy RSS feeds, then merge them.

    The broad AllField feeds are the primary discovery route. Configured
    ConceptID taxonomy feeds provide additional provenance and optional
    direct-accept information. All entries are merged by DOI (or normalized URL
    fallback) before date checks, duplicate checks, metadata enrichment,
    classification, summarization, or Slack posting.
    """
    specs = _iter_agu_taxonomy_specs(journal)
    search_terms = _iter_agu_search_terms(journal)
    mi = str(journal.get("agu_search_mi") or "6hngq57")
    sort_by = "Earliest"

    merged_entries: list[feedparser.FeedParserDict] = []
    by_key: dict[str, feedparser.FeedParserDict] = {}
    search_fetches: list[dict] = []
    taxonomy_fetches: list[dict] = []

    if DRY_RUN:
        print(
            "[DRY_RUN] agu_search_config "
            f"journal={journal.get('title', '')} "
            f"terms={search_terms} "
            f"count={len(search_terms)} "
            f"sort_by={sort_by}"
        )
        print(
            "[DRY_RUN] agu_taxonomy_config "
            f"journal={journal.get('title', '')} "
            f"configured={len(journal.get('agu_taxonomies') or [])} "
            f"normalized={len(specs)} "
            f"sort_by={sort_by}"
        )

    # Primary discovery route: broad AllField searches.
    for search_term in search_terms:
        rss_url = build_agu_keyword_feed_url(
            search_term,
            mi=mi,
            sort_by=sort_by,
        )
        child_journal = dict(journal)
        child_journal["rss_url"] = rss_url

        child_feed = parse_feed_with_headers(child_journal)
        search_fetches.append(
            {
                "term": search_term,
                "status": child_feed.get("status"),
                "entries": len(child_feed.entries),
                "href": child_feed.get("href") or rss_url,
                "bozo": bool(child_feed.get("bozo")),
            }
        )

        if DRY_RUN:
            print(
                "[DRY_RUN] agu_search_feed "
                f"journal={journal.get('title', '')} "
                f"term={search_term} "
                f"status={child_feed.get('status')} "
                f"entries={len(child_feed.entries)}"
            )

        for raw_entry in child_feed.entries:
            key = _agu_entry_merge_key(raw_entry)
            if key and key in by_key:
                merged = by_key[key]
                _merge_agu_entry(merged, raw_entry)
                _add_agu_search_provenance(merged, search_term)
                continue

            entry = feedparser.FeedParserDict(dict(raw_entry))
            _merge_agu_entry(entry, raw_entry)
            _add_agu_search_provenance(entry, search_term)
            merged_entries.append(entry)
            if key:
                by_key[key] = entry

    # Secondary route: ConceptID taxonomy feeds.
    for spec in specs:
        rss_url = build_agu_taxonomy_feed_url(
            spec["concept_id"],
            mi=mi,
            sort_by=sort_by,
        )
        child_journal = dict(journal)
        child_journal["rss_url"] = rss_url

        child_feed = parse_feed_with_headers(child_journal)
        taxonomy_fetches.append(
            {
                "name": spec["name"],
                "index_term": spec.get("index_term", ""),
                "concept_id": spec["concept_id"],
                "status": child_feed.get("status"),
                "entries": len(child_feed.entries),
                "href": child_feed.get("href") or rss_url,
                "bozo": bool(child_feed.get("bozo")),
            }
        )

        if DRY_RUN:
            print(
                "[DRY_RUN] agu_taxonomy_feed "
                f"journal={journal.get('title', '')} "
                f"taxonomy={spec['name']} "
                f"group={spec.get('group', '')} "
                f"direct_accept={spec.get('direct_accept', False)} "
                f"index_term={spec.get('index_term', '')} "
                f"concept_id={spec['concept_id']} "
                f"status={child_feed.get('status')} "
                f"entries={len(child_feed.entries)}"
            )

        for raw_entry in child_feed.entries:
            key = _agu_entry_merge_key(raw_entry)
            if key and key in by_key:
                merged = by_key[key]
                _merge_agu_entry(merged, raw_entry)
                _add_agu_taxonomy_provenance(merged, spec)
                continue

            entry = feedparser.FeedParserDict(dict(raw_entry))
            _merge_agu_entry(entry, raw_entry)
            _add_agu_taxonomy_provenance(entry, spec)
            merged_entries.append(entry)
            if key:
                by_key[key] = entry

    all_fetches = search_fetches + taxonomy_fetches
    statuses = [
        item["status"]
        for item in all_fetches
        if item["status"] is not None
    ]

    aggregate_status: int | None
    if not statuses:
        aggregate_status = None
    elif all(status == 200 for status in statuses):
        aggregate_status = 200
    else:
        aggregate_status = next(
            (status for status in statuses if status != 200),
            statuses[0],
        )

    return feedparser.FeedParserDict(
        {
            "entries": merged_entries,
            "feed": {
                "title": journal.get("full_title") or journal.get("title", ""),
            },
            "href": "agu-search-taxonomy://merged",
            "status": aggregate_status,
            "bozo": any(item["bozo"] for item in all_fetches),
            "agu_search_fetches": search_fetches,
            "agu_search_feed_count": len(search_terms),
            "agu_taxonomy_fetches": taxonomy_fetches,
            "agu_taxonomy_feed_count": len(specs),
            "agu_taxonomy_unique_entries": len(merged_entries),
            "agu_merged_unique_entries": len(merged_entries),
        }
    )

def debug_fetch_feed_head(url: str, journal_title: str, timeout: int = 10) -> None:
    if not DRY_RUN:
        return

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; paper-slack-bot/1.0; "
                    "+https://github.com/)"
                ),
                "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(500)
            charset = resp.headers.get_content_charset() or "utf-8"
            head = raw.decode(charset, errors="replace").replace("\n", " ")[:500]

            print(
                f"[DRY_RUN] feed_raw_head journal={journal_title} "
                f"status={resp.status} "
                f"content_type={resp.headers.get('Content-Type')} "
                f"final_url={resp.geturl()} "
                f"head={head}"
            )
    except Exception as e:
        print(
            f"[DRY_RUN] feed_raw_head_failed journal={journal_title} "
            f"url={url} error={e}"
        )


# ── 5. Copernicus Recent アダプタ ───────────────────

def _parse_copernicus_recent_date(value: str) -> dt.datetime | None:
    try:
        d = dt.datetime.strptime(value.strip(), "%d %b %Y")
    except ValueError:
        return None
    return d.replace(tzinfo=dt.timezone.utc).astimezone(TZ_TOKYO)


def _copernicus_lines_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    lines = []
    for line in soup.get_text("\n", strip=True).splitlines():
        s = " ".join(line.split())
        if s:
            lines.append(s)
    return lines


def _parse_copernicus_recent_block(
    date_line: str,
    block_lines: list[str],
) -> feedparser.FeedParserDict | None:
    pub_dt = _parse_copernicus_recent_date(date_line)
    if pub_dt is None:
        return None

    skip_exact = {
        "hide",
        "short summary",
        "all papers",
        "final revised papers only",
        "preprints only",
        "filters",
        "highlight paper",
        "| highlight paper",
    }

    title = ""
    for line in block_lines:
        line_lc = line.lower().strip()
        if (
            not line_lc
            or line_lc in skip_exact
            or line_lc.startswith("discussion:")
            or line_lc.startswith("preprint under review")
            or line_lc.startswith("egusphere,")
            or line_lc.startswith("solid earth,")
            or line_lc.startswith("https://doi.org/")
        ):
            continue
        title = line
        break

    block_text = "\n".join(block_lines)
    doi_match = _COPERNICUS_DOI_RE.search(block_text)
    doi = normalize_doi(doi_match.group(0)) if doi_match else ""

    if not title or not doi:
        return None

    summary_parts: list[str] = []
    summary_starts = [
        i for i, line in enumerate(block_lines)
        if line.strip().lower() == "short summary"
    ]
    if summary_starts:
        for line in block_lines[summary_starts[-1] + 1:]:
            line_lc = line.lower().strip()
            if line_lc == "hide" or _COPERNICUS_DATE_RE.match(line):
                break
            if line_lc in skip_exact or line_lc.startswith("discussion:"):
                continue
            summary_parts.append(line)

    summary = " ".join(summary_parts).strip() or title
    link = f"https://doi.org/{doi.removeprefix('doi:')}"

    return feedparser.FeedParserDict({
        "title": title,
        "link": link,
        "id": link,
        "doi": doi,
        "summary": summary,
        "published": date_line,
        "published_parsed": pub_dt.astimezone(dt.timezone.utc).utctimetuple(),
        "updated": date_line,
        "updated_parsed": pub_dt.astimezone(dt.timezone.utc).utctimetuple(),
    })


def parse_copernicus_recent_html(
    html: str,
    *,
    max_entries: int = 100,
) -> list[feedparser.FeedParserDict]:
    """Parse Copernicus Recent pages into feedparser-like entries."""
    lines = _copernicus_lines_from_html(html)
    entries: list[feedparser.FeedParserDict] = []

    date_indexes = [
        i for i, line in enumerate(lines)
        if _COPERNICUS_DATE_RE.match(line)
    ]

    for pos, start in enumerate(date_indexes):
        end = date_indexes[pos + 1] if pos + 1 < len(date_indexes) else len(lines)
        entry = _parse_copernicus_recent_block(lines[start], lines[start + 1:end])
        if entry is not None:
            entries.append(entry)
            if len(entries) >= max_entries:
                break

    return entries


def fetch_copernicus_recent_feed(journal: dict) -> feedparser.FeedParserDict:
    """Fetch Copernicus Recent HTML and expose it as an RSS-like feed."""
    url = journal.get("copernicus_recent_url") or journal.get("rss_url", "")
    html = fetch_url_text(url, timeout=int(journal.get("html_timeout", 15)))
    entries = parse_copernicus_recent_html(
        html,
        max_entries=int(journal.get("copernicus_recent_max_entries", 100)),
    )

    feed = feedparser.FeedParserDict({
        "entries": entries,
        "feed": {"title": journal.get("full_title") or journal.get("title", "")},
        "href": url,
        "status": 200 if html else None,
        "bozo": False,
    })

    if DRY_RUN:
        print(
            f"[DRY_RUN] copernicus_recent_parse "
            f"journal={journal.get('title', '')} entries={len(entries)} url={url}"
        )

    return feed


# ── 6. Springer Meta API アダプタ ─────────────────────

def _parse_springer_api_datetime(value: object) -> dt.datetime | None:
    if not value:
        return None

    if isinstance(value, dt.datetime):
        d = value
    else:
        s = str(value).strip()
        if not s:
            return None

        s = s.replace("Z", "+00:00")
        try:
            d = dt.datetime.fromisoformat(s)
        except ValueError:
            try:
                d = dt.datetime.strptime(s, "%Y-%m-%d")
            except ValueError:
                return None

    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)

    return d.astimezone(TZ_TOKYO)


def _springer_api_record_datetime(record: dict) -> dt.datetime | None:
    for key in (
        "onlineDate",
        "publicationDate",
        "coverDate",
        "printDate",
        "acceptanceDate",
    ):
        pub_dt = _parse_springer_api_datetime(record.get(key))
        if pub_dt is not None:
            return pub_dt

    return None


def _first_non_empty(values: list[str]) -> str:
    for value in values:
        if value:
            return value
    return ""


def _springer_api_record_link(record: dict, doi: str) -> str:
    """
    Return a stable article link for Springer Nature API records.

    The Meta API often exposes links like:
      http://link.springer.com/openurl/fulltext?id=doi:10....
    If query strings are later stripped during URL normalization, those all
    collapse to http://link.springer.com/openurl/fulltext and create false
    aliases across unrelated papers.  Prefer DOI URLs whenever a DOI is known
    or can be extracted from any returned URL.
    """
    if doi:
        return f"https://doi.org/{doi.removeprefix('doi:')}"

    url_candidates: list[str] = []

    for key in ("url", "fullTextUrl", "selfUrl", "pdfUrl", "abstractUrl"):
        value = record.get(key)
        if isinstance(value, str):
            url_candidates.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    url_candidates.append(
                        _first_non_empty(
                            [
                                str(item.get("value", "")),
                                str(item.get("href", "")),
                                str(item.get("url", "")),
                            ]
                        )
                    )
                else:
                    url_candidates.append(str(item))

    # Some Springer openurl links contain the DOI only in the query string.
    # Convert those to a DOI URL before later URL normalization removes the query.
    for url in url_candidates:
        url_doi = normalize_doi(url)
        if url_doi:
            return f"https://doi.org/{url_doi.removeprefix('doi:')}"

    for url in url_candidates:
        url = (url or "").strip()
        if not url:
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc.lower().endswith("link.springer.com") and parsed.path == "/openurl/fulltext":
            continue
        return url

    return ""


def _springer_api_record_to_entry(record: dict) -> feedparser.FeedParserDict:
    title = text_from_maybe_html(str(record.get("title", ""))).strip()
    doi = normalize_doi(
        _first_non_empty(
            [
                str(record.get("doi", "")),
                str(record.get("identifier", "")),
                str(record.get("electronicIdentifier", "")),
            ]
        )
    )
    link = _springer_api_record_link(record, doi)
    abstract = text_from_maybe_html(
        _first_non_empty(
            [
                str(record.get("abstract", "")),
                str(record.get("description", "")),
            ]
        )
    ).strip()
    pub_dt = _springer_api_record_datetime(record)

    entry = feedparser.FeedParserDict()
    entry["title"] = title
    entry["link"] = link
    entry["id"] = doi or link or str(record.get("identifier", ""))
    entry["summary"] = abstract
    entry["description"] = abstract
    entry["doi"] = doi
    entry["prism_doi"] = doi.removeprefix("doi:") if doi else ""
    entry["links"] = [{"href": link}] if link else []
    if abstract:
        entry["content"] = [{"type": "text/html", "value": abstract}]
    else:
        entry["content"] = []

    if pub_dt is not None:
        pub_utc = pub_dt.astimezone(dt.timezone.utc)
        entry["published"] = pub_utc.isoformat()
        entry["published_parsed"] = pub_utc.utctimetuple()
        entry["updated"] = pub_utc.isoformat()
        entry["updated_parsed"] = pub_utc.utctimetuple()

    return entry


def _springer_api_queries(journal: dict) -> list[str]:
    queries = journal.get("springer_queries") or []
    if isinstance(queries, str):
        queries = [queries]

    cleaned = [str(query).strip() for query in queries if str(query).strip()]
    if cleaned:
        return cleaned

    query = str(journal.get("springer_query") or "").strip()
    if not query and journal.get("issn"):
        query = f"issn:{journal['issn']}"

    return [query] if query else []


def _env_or_journal_bool(journal: dict, key: str, default: bool) -> bool:
    value = journal.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _springer_api_date_filter_bounds(
    journal: dict,
    hours_back: int,
) -> tuple[dt.date, dt.date]:
    """
    Return inclusive date bounds for Springer API query constraints.

    Springer API date constraints are day-granular.  Add a default buffer so
    records near the edge of the Slack posting window are still fetched; the
    later pub_dt check in fetch_and_post_rss() keeps the actual Slack window
    strict.
    """
    buffer_hours = int(journal.get("springer_date_filter_buffer_hours", 24))
    now = dt.datetime.now(tz=TZ_TOKYO)
    start = now - timedelta(hours=hours_back + buffer_hours)
    return start.date(), now.date()


def _springer_api_date_filter_keys(journal: dict) -> tuple[str, str]:
    field = str(journal.get("springer_date_filter_field", "onlinedate")).strip().lower()
    if field in {"date", "publication", "publicationdate", "pubdate"}:
        return "datefrom", "dateto"
    return "onlinedatefrom", "onlinedateto"


def _springer_api_query_has_date_filter(query: str) -> bool:
    return bool(
        re.search(r"\b(?:online)?datefrom\s*:", query, flags=re.IGNORECASE)
        or re.search(r"\b(?:online)?dateto\s*:", query, flags=re.IGNORECASE)
    )


def _springer_api_add_date_filter(
    query: str,
    journal: dict,
    hours_back: int,
) -> str:
    """
    Add Springer Meta API day-range constraints to q.

    The Meta API result order is not reliable enough for new-paper discovery.
    Therefore, narrow every Springer query to the posting window plus a buffer
    before paging through results.  Queries that already include datefrom/
    dateto/onlinedatefrom/onlinedateto are left unchanged, allowing manual
    overrides for experiments.
    """
    query = (query or "").strip()
    if not query:
        return query

    if not _env_or_journal_bool(journal, "springer_date_filter", True):
        return query

    if _springer_api_query_has_date_filter(query):
        return query

    from_key, to_key = _springer_api_date_filter_keys(journal)
    start_date, end_date = _springer_api_date_filter_bounds(journal, hours_back)

    return (
        f"({query}) "
        f"{from_key}:{start_date.isoformat()} "
        f"{to_key}:{end_date.isoformat()}"
    )


def _build_springer_api_url(
    journal: dict,
    page: int,
    *,
    query: str = "",
    include_api_key: bool = True,
) -> str:
    query = (query or "").strip()
    if not query:
        queries = _springer_api_queries(journal)
        query = queries[0] if queries else ""

    page_size = int(journal.get("springer_page_size", 20))
    start = 1 + (max(page, 1) - 1) * page_size

    params = {
        "q": query,
        "p": str(page_size),
        "s": str(start),
    }

    if include_api_key:
        params["api_key"] = springer_api_key

    return "https://api.springernature.com/meta/v2/json?" + urllib.parse.urlencode(
        params
    )


def fetch_springer_api_feed(
    journal: dict,
    hours_back: int | None = None,
) -> feedparser.FeedParserDict:
    feed = feedparser.FeedParserDict()
    feed["entries"] = []
    feed["feed"] = feedparser.FeedParserDict(
        {
            "title": journal.get("full_title") or journal.get("title", ""),
        }
    )

    if not springer_api_key:
        if DRY_RUN:
            print(
                f"[DRY_RUN] springer_api_missing_key journal={journal.get('title', '')}"
            )
        return feed

    if hours_back is None:
        hours_back = int(config.get("hours_back", 48))

    page_size = int(journal.get("springer_page_size", 20))
    max_pages = int(journal.get("springer_max_pages", 5))
    queries = _springer_api_queries(journal)
    all_entries: list[feedparser.FeedParserDict] = []
    seen_keys: set[str] = set()

    filtered_queries = [
        _springer_api_add_date_filter(query, journal, hours_back)
        for query in queries
    ]

    for query_index, query in enumerate(filtered_queries, start=1):
        for page in range(1, max_pages + 1):
            url = _build_springer_api_url(journal, page, query=query)
            data = fetch_json(url, timeout=int(journal.get("springer_timeout", 10)))

            if DRY_RUN and page == 1:
                total = (data.get("result") or [{}])[0].get("total", "")
                print(
                    f"[DRY_RUN] springer_api_query "
                    f"journal={journal.get('title', '')} "
                    f"query_index={query_index} "
                    f"api_total={total} "
                    f"page_size={page_size} "
                    f"max_pages={max_pages} "
                    f"query={query}"
                )

            raw_records = data.get("records") or []
            if not isinstance(raw_records, list) or not raw_records:
                if DRY_RUN:
                    message = data.get("message") or data.get("status") or ""
                    print(
                        f"[DRY_RUN] springer_api_empty journal={journal.get('title', '')} "
                        f"query_index={query_index} page={page} message={message}"
                    )
                break

            for record in raw_records:
                if not isinstance(record, dict):
                    continue

                entry = _springer_api_record_to_entry(record)
                dedupe_key = entry.get("doi") or entry.get("id") or entry.get("link")
                if dedupe_key and dedupe_key in seen_keys:
                    continue
                if dedupe_key:
                    seen_keys.add(dedupe_key)

                all_entries.append(entry)

            if len(raw_records) < page_size:
                break

    feed["entries"] = all_entries
    if queries:
        href_query = _springer_api_add_date_filter(queries[0], journal, hours_back)
        feed["href"] = _build_springer_api_url(
            journal,
            1,
            query=href_query,
            include_api_key=False,
        )
    else:
        feed["href"] = "https://api.springernature.com/meta/v2/json"
    feed["status"] = 200
    if DRY_RUN and len(queries) > 1:
        print(
            f"[DRY_RUN] springer_api_queries journal={journal.get('title', '')} "
            f"queries={len(queries)} unique_entries={len(all_entries)}"
        )
    return feed


# ── 7. 投稿オーケストレーション ───────────────────────

def record_rss_status(
    posted: dict[str, PostedRecord],
    entry_id: str,
    pub_dt: dt.datetime,
    aliases: list[str],
    journal: dict,
    *,
    status: str,
    reason: str,
) -> None:
    """
    Save RSS entry status together with DOI/URL aliases.
    """
    if DRY_RUN:
        return

    record_posted_entry(
        posted,
        entry_id,
        pub_dt,
        journal=journal.get("title", ""),
        reason=reason,
        aliases=aliases,
        status=status,
    )


@dataclass
class _JournalRunStats:
    seen: int = 0
    no_date: int = 0
    out_of_window: int = 0
    no_url: int = 0
    duplicate: int = 0
    skipped_keyword: int = 0
    candidate: int = 0
    posted: int = 0
    source_rss: int = 0
    source_html: int = 0
    source_springer_api: int = 0
    source_openalex: int = 0
    source_crossref: int = 0
    source_metadata: int = 0
    metadata_deferred: int = 0
    skipped_prefilter: int = 0
    skipped_title_pattern: int = 0
    prefilter_relevant: int = 0
    prefilter_uncertain: int = 0
    prefilter_irrelevant: int = 0
    extra_source_counts: dict[str, int] = field(default_factory=dict)

    def increment_source(self, abstract_source: str) -> None:
        attr = f"source_{abstract_source}"
        if hasattr(self, attr):
            setattr(self, attr, getattr(self, attr) + 1)
        else:
            self.extra_source_counts[attr] = self.extra_source_counts.get(attr, 0) + 1

    def should_print_summary(self, source_type: str) -> bool:
        return DRY_RUN and (
            source_type in {"springer_api", "agu_taxonomy"}
            or self.candidate > 0
            or self.skipped_keyword > 0
            or self.skipped_prefilter > 0
            or self.skipped_title_pattern > 0
        )

    def print_summary(self, journal_title: str) -> None:
        print(
            "[DRY_RUN] journal_summary "
            f"journal={journal_title} "
            f"seen={self.seen} "
            f"candidate={self.candidate} "
            f"posted={self.posted} "
            f"skipped_keyword={self.skipped_keyword} "
            f"duplicate={self.duplicate} "
            f"no_date={self.no_date} "
            f"out_of_window={self.out_of_window} "
            f"no_url={self.no_url} "
            f"source_rss={self.source_rss} "
            f"source_html={self.source_html} "
            f"source_springer_api={self.source_springer_api} "
            f"source_openalex={self.source_openalex} "
            f"source_crossref={self.source_crossref} "
            f"source_metadata={self.source_metadata} "
            f"metadata_deferred={self.metadata_deferred} "
            f"skipped_prefilter={self.skipped_prefilter} "
            f"skipped_title_pattern={self.skipped_title_pattern} "
            f"prefilter_relevant={self.prefilter_relevant} "
            f"prefilter_uncertain={self.prefilter_uncertain} "
            f"prefilter_irrelevant={self.prefilter_irrelevant} "
        )


@dataclass
class _RssEntryContext:
    entry: feedparser.FeedParserDict
    feed: feedparser.FeedParserDict
    journal: dict
    source_type: str
    target_start: dt.datetime
    target_end: dt.datetime
    pub_dt: dt.datetime | None = None
    title_text: str = ""
    entry_id: str = ""
    url_entry_id: str | None = None
    aliases: list[str] = field(default_factory=list)
    link_url: str = ""
    rss_text: str = ""
    abstract_en: str = ""
    abstract_source: str = "rss"


FeedFetcher = Callable[[dict, int], feedparser.FeedParserDict]


def _fetch_rss_feed(journal: dict, _hours_back: int) -> feedparser.FeedParserDict:
    return parse_feed_with_headers(journal)


def _fetch_copernicus_feed(
    journal: dict,
    _hours_back: int,
) -> feedparser.FeedParserDict:
    return fetch_copernicus_recent_feed(journal)


_FEED_FETCHERS: dict[str, FeedFetcher] = {
    "rss": _fetch_rss_feed,
    "springer_api": fetch_springer_api_feed,
    "copernicus_recent": _fetch_copernicus_feed,
    "agu_taxonomy": fetch_agu_taxonomy_feed,
}
_DEFAULT_FEED_FETCHER = _fetch_rss_feed


def _resolve_feed_fetcher(source_type: str) -> FeedFetcher:
    return _FEED_FETCHERS.get(source_type, _DEFAULT_FEED_FETCHER)


def _fetch_feed_for_journal(
    journal: dict,
    hours_back: int,
) -> tuple[str, feedparser.FeedParserDict]:
    source_type = journal.get("source_type", "rss")
    fetcher = _resolve_feed_fetcher(source_type)
    return source_type, fetcher(journal, hours_back)


def _debug_log_empty_feed(
    journal: dict,
    feed: feedparser.FeedParserDict,
    source_type: str,
) -> None:
    if not DRY_RUN or len(feed.entries) != 0:
        return

    rss_url = journal.get("rss_url", "")
    if not (
        source_type == "springer_api"
        or source_type == "copernicus_recent"
        or source_type == "agu_taxonomy"
        or "springer.com" in rss_url
        or "nature.com" in rss_url
        or "agupubs.onlinelibrary.wiley.com" in rss_url
        or feed.get("status") not in (None, 200)
    ):
        return

    if source_type == "springer_api":
        print(
            f"[DRY_RUN] springer_api_no_entries journal={journal.get('title', '')}"
        )
    elif source_type == "agu_taxonomy":
        print(
            "[DRY_RUN] agu_taxonomy_no_entries "
            f"journal={journal.get('title', '')} "
            f"search_feeds={feed.get('agu_search_feed_count', 0)} "
            f"taxonomy_feeds={feed.get('agu_taxonomy_feed_count', 0)} "
            f"search_fetches={feed.get('agu_search_fetches', [])} "
            f"taxonomy_fetches={feed.get('agu_taxonomy_fetches', [])}"
        )
    else:
        debug_fetch_feed_head(rss_url, journal.get("title", ""))


def _debug_log_feed_status(
    journal: dict,
    feed: feedparser.FeedParserDict,
) -> None:
    if not DRY_RUN:
        return

    print(
        f"[DRY_RUN] feed_status journal={journal.get('title')} "
        f"status={feed.get('status')} "
        f"bozo={feed.get('bozo')} "
        f"bozo_exception={repr(feed.get('bozo_exception', ''))} "
        f"entries={len(feed.entries)} "
        f"href={feed.get('href')} "
        f"feed_title={getattr(feed, 'feed', {}).get('title', '')}"
    )

    if journal.get("source_type") == "agu_taxonomy":
        entry_dates = [
            value
            for value in (get_entry_datetime(entry) for entry in feed.entries)
            if value is not None
        ]
        oldest = min(entry_dates).isoformat() if entry_dates else ""
        newest = max(entry_dates).isoformat() if entry_dates else ""
        print(
            "[DRY_RUN] agu_taxonomy_merge "
            f"journal={journal.get('title', '')} "
            f"search_feeds={feed.get('agu_search_feed_count', 0)} "
            f"taxonomy_feeds={feed.get('agu_taxonomy_feed_count', 0)} "
            f"unique_entries={feed.get('agu_merged_unique_entries', 0)} "
            f"search_raw_entries={sum(item.get('entries', 0) for item in feed.get('agu_search_fetches', []))} "
            f"taxonomy_raw_entries={sum(item.get('entries', 0) for item in feed.get('agu_taxonomy_fetches', []))} "
            f"dated_entries={len(entry_dates)} "
            f"oldest={oldest} "
            f"newest={newest}"
        )


def _step_validate_window(ctx: _RssEntryContext, stats: _JournalRunStats) -> bool:
    """Return True when this entry should not be processed further."""
    ctx.pub_dt = get_effective_entry_datetime(ctx.entry, ctx.feed, ctx.journal)
    if ctx.pub_dt is None:
        stats.no_date += 1
        if DRY_RUN:
            print(
                f"[DRY_RUN] skip_no_date "
                f"journal={ctx.journal.get('title', '')} "
                f"title={ctx.entry.get('title', '')}"
            )
        return True

    ctx.title_text = get_title_text(ctx.entry)
    if not (ctx.target_start <= ctx.pub_dt <= ctx.target_end):
        stats.out_of_window += 1
        return True

    return False


def _step_resolve_identity(
    ctx: _RssEntryContext,
    posted: dict[str, PostedRecord],
    stats: _JournalRunStats,
) -> bool:
    raw = ctx.entry.get("link") or ctx.entry.get("id") or ""
    raw = ensure_urlish(raw)
    if not raw:
        stats.no_url += 1
        return True

    ctx.entry_id, ctx.url_entry_id = build_entry_id_candidates(ctx.entry, raw)
    if not ctx.entry_id:
        stats.duplicate += 1
        return True

    existing_key = find_existing_posted_key(
        posted,
        ctx.entry_id,
        ctx.url_entry_id,
    )
    if existing_key:
        existing = posted[existing_key]
        stats.duplicate += 1

        # If one identifier is already known, save the other as an alias.
        record_rss_status(
            posted,
            existing_key,
            existing.dtval,
            [ctx.entry_id, ctx.url_entry_id],
            ctx.journal,
            status=existing.status,
            reason=existing.reason or "duplicate",
        )
        return True

    ctx.aliases = []
    if ctx.url_entry_id and ctx.url_entry_id != ctx.entry_id:
        ctx.aliases.append(ctx.url_entry_id)

    ctx.link_url = raw
    return False


def _step_filter_title(
    ctx: _RssEntryContext,
    posted: dict[str, PostedRecord],
    stats: _JournalRunStats,
) -> bool:
    is_excluded, exclude_reason = matches_exclude_title_patterns(
        ctx.title_text,
        ctx.journal,
    )
    if not is_excluded:
        return False

    if DRY_RUN:
        print(
            f"[DRY_RUN] skip_title_pattern "
            f"journal={ctx.journal.get('title', '')} "
            f"entry_id={ctx.entry_id} "
            f"reason={exclude_reason} "
            f"title={ctx.title_text}"
        )

    stats.skipped_title_pattern += 1
    record_rss_status(
        posted,
        ctx.entry_id,
        ctx.pub_dt,
        ctx.aliases,
        ctx.journal,
        status="skipped",
        reason=f"title_pattern:{exclude_reason}",
    )
    return True


def _step_filter_keyword(
    ctx: _RssEntryContext,
    posted: dict[str, PostedRecord],
    stats: _JournalRunStats,
) -> bool:
    html = pick_html_for_abstract(
        ctx.entry,
        ctx.journal.get("abstract_tag", "summary"),
    )
    ctx.rss_text = text_from_maybe_html(html).strip()

    # First-stage keyword filtering before expensive HTML/metadata/classifier calls.
    # For ScienceDirect, rss_text may be only bibliographic metadata, but title_text is still useful.
    if matches_include_keywords(ctx.title_text, ctx.rss_text, ctx.journal):
        return False

    if DRY_RUN:
        print(
            f"[DRY_RUN] skip_keyword "
            f"journal={ctx.journal.get('title', '')} "
            f"entry_id={ctx.entry_id} "
            f"title={ctx.title_text}"
        )

    stats.skipped_keyword += 1
    record_rss_status(
        posted,
        ctx.entry_id,
        ctx.pub_dt,
        ctx.aliases,
        ctx.journal,
        status="skipped",
        reason="keyword",
    )
    return True


def _step_resolve_abstract(
    ctx: _RssEntryContext,
    posted: dict[str, PostedRecord],
    stats: _JournalRunStats,
) -> bool:
    ctx.abstract_en = ctx.rss_text
    ctx.abstract_source = "springer_api" if ctx.source_type == "springer_api" else "rss"

    if is_bibliographic_metadata(ctx.abstract_en):
        ctx.abstract_en = ""
        ctx.abstract_source = "rss_metadata"

    min_abstract_length = int(ctx.journal.get("min_abstract_length", 500))

    if (
        "html" in (ctx.journal.get("abstract_fallback") or [])
        and len(ctx.abstract_en) < min_abstract_length
    ):
        page_html = fetch_url_text(
            ctx.link_url,
            timeout=int(ctx.journal.get("html_timeout", 8)),
        )
        html_abs = extract_abstract_from_html(page_html)
        if html_abs:
            ctx.abstract_en = html_abs
            ctx.abstract_source = "html"
        else:
            ctx.abstract_source = "rss_short"

    if (
        ctx.journal.get("metadata_fallback")
        and len(ctx.abstract_en) < min_abstract_length
    ):
        meta = enrich_metadata(
            ctx.entry.get("title", ""),
            ctx.journal,
            pub_dt=ctx.pub_dt,
        )

        transient_error = meta.get("_metadata_transient_error")
        if transient_error:
            stats.metadata_deferred += 1
            print(
                "[WARN] metadata_deferred "
                f"journal={ctx.journal.get('title', '')} "
                f"source={transient_error.get('source', 'unknown')} "
                f"status={transient_error.get('status', 'unknown')} "
                f"entry_id={ctx.entry_id}"
            )
            # Do not mark the item as processed. A later scheduled run can retry.
            return True

        meta_doi = meta.get("doi", "")
        meta_abs = meta.get("abstract", "")
        if is_bibliographic_metadata(meta_abs):
            meta_abs = ""

        if meta_doi:
            previous_entry_id = ctx.entry_id
            doi_entry_id = normalize_doi(meta_doi)
            if doi_entry_id:
                # If DOI key has already been seen, save URL/previous keys
                # as aliases and skip even if URL key was new.
                existing_key = find_existing_posted_key(posted, doi_entry_id)
                if existing_key:
                    existing = posted[existing_key]
                    stats.duplicate += 1

                    record_rss_status(
                        posted,
                        existing_key,
                        existing.dtval,
                        [previous_entry_id, ctx.url_entry_id, doi_entry_id],
                        ctx.journal,
                        status=existing.status,
                        reason=existing.reason or "duplicate",
                    )
                    return True

                else:
                    ctx.entry_id = doi_entry_id
                    ctx.aliases = [
                        candidate
                        for candidate in (previous_entry_id, ctx.url_entry_id)
                        if candidate and candidate != ctx.entry_id
                    ]

        if meta_abs and len(meta_abs) >= max(len(ctx.abstract_en), 300):
            ctx.abstract_en = meta_abs
            ctx.abstract_source = meta.get("source", "metadata")

    return False


def _step_classify_and_post(
    client: WebClient,
    ctx: _RssEntryContext,
    posted: dict[str, PostedRecord],
    stats: _JournalRunStats,
) -> None:
    if ctx.source_type == "agu_taxonomy" and ctx.entry.get("agu_direct_accept"):
        decision, relevance_reason = "relevant", "agu_taxonomy_direct_accept"
    else:
        classify_journal = ctx.journal
        if ctx.source_type == "agu_taxonomy" and ctx.journal.get("prefilter"):
            classify_journal = dict(ctx.journal)
            prefilter = dict(classify_journal.get("prefilter") or {})
            base_scope = str(prefilter.get("target_scope") or "").strip()
            labels = ", ".join(ctx.entry.get("agu_taxonomies") or [])
            index_terms = ", ".join(ctx.entry.get("agu_index_terms") or [])
            groups = ", ".join(ctx.entry.get("agu_taxonomy_groups") or [])
            search_terms = ", ".join(ctx.entry.get("agu_search_terms") or [])
            journals = ", ".join(ctx.entry.get("agu_journals") or [])
            context = (
                "This entry was retrieved through AGU broad keyword search "
                "and/or AGU taxonomy metadata. "
                f"Matched broad search terms: {search_terms or 'none'}. "
                f"Publishing journals: {journals or 'unknown'}. "
                f"Groups: {groups or 'unknown'}. "
                f"Index Terms: {index_terms or 'unknown'}. "
                f"Taxonomy labels: {labels or 'unknown'}."
            )
            prefilter["target_scope"] = (
                f"{base_scope}\n\n{context}" if base_scope else context
            )
            classify_journal["prefilter"] = prefilter

        decision, relevance_reason = classify_relevance(
            ctx.entry.get("title", ""),
            ctx.abstract_en,
            classify_journal,
        )
    if decision == "relevant":
        stats.prefilter_relevant += 1
    elif decision == "uncertain":
        stats.prefilter_uncertain += 1
    elif decision == "irrelevant":
        stats.prefilter_irrelevant += 1

    if decision == "irrelevant" or (
        decision == "uncertain"
        and ctx.journal.get("prefilter_uncertain") == "skip"
    ):
        stats.skipped_prefilter += 1
        record_rss_status(
            posted,
            ctx.entry_id,
            ctx.pub_dt,
            ctx.aliases,
            ctx.journal,
            status="skipped",
            reason=f"prefilter:{relevance_reason[:80]}",
        )
        return

    stats.candidate += 1
    stats.increment_source(ctx.abstract_source)

    if DRY_RUN:
        print(f"[DRY_RUN] candidate journal={ctx.journal.get('title', '')}")
        print(f"[DRY_RUN] entry_id={ctx.entry_id}")
        print(f"[DRY_RUN] pub_dt={ctx.pub_dt.isoformat()}")
        print(
            f"[DRY_RUN] date_strategy="
            f"{ctx.journal.get('date_strategy', 'entry')}"
        )
        print(f"[DRY_RUN] title={ctx.entry.get('title', '')}")
        print(f"[DRY_RUN] abstract_source={ctx.abstract_source}")
        print(f"[DRY_RUN] abstract_length={len(ctx.abstract_en)}")

    summary = maybe_summarize(ctx.entry.title, ctx.abstract_en)
    if post_and_record_rss(
        client,
        ctx.journal["slack_channel_id"],
        title=ctx.entry.title,
        link=ctx.link_url,
        summary=summary,
        abstract=ctx.abstract_en,
        posted=posted,
        entry_id=ctx.entry_id,
        pub_dt=ctx.pub_dt,
        journal=ctx.journal.get("title", ""),
        reason=f"posted:{ctx.abstract_source}",
        aliases=ctx.aliases,
    ):
        stats.posted += 1


def _process_rss_entry(
    client: WebClient,
    ctx: _RssEntryContext,
    posted: dict[str, PostedRecord],
    stats: _JournalRunStats,
) -> None:
    stats.seen += 1

    if _step_validate_window(ctx, stats):
        return
    if _step_resolve_identity(ctx, posted, stats):
        return
    if _step_filter_title(ctx, posted, stats):
        return
    if _step_filter_keyword(ctx, posted, stats):
        return
    if _step_resolve_abstract(ctx, posted, stats):
        return

    _step_classify_and_post(client, ctx, posted, stats)


def _process_journal_entries(
    client: WebClient,
    journal: dict,
    feed: feedparser.FeedParserDict,
    source_type: str,
    posted: dict[str, PostedRecord],
    target_start: dt.datetime,
    target_end: dt.datetime,
) -> None:
    stats = _JournalRunStats()

    for entry in feed.entries:
        ctx = _RssEntryContext(
            entry=entry,
            feed=feed,
            journal=journal,
            source_type=source_type,
            target_start=target_start,
            target_end=target_end,
        )
        _process_rss_entry(client, ctx, posted, stats)

    if stats.should_print_summary(source_type):
        stats.print_summary(journal.get("title", ""))


def fetch_and_post_rss(
    client: WebClient,
    journals: list[dict],
    hours_back: int,
    posted: dict[str, PostedRecord],
) -> None:
    target_start = dt.datetime.now(tz=TZ_TOKYO) - timedelta(hours=hours_back)
    target_end = dt.datetime.now(tz=TZ_TOKYO)

    for journal in journals:
        source_type, feed = _fetch_feed_for_journal(journal, hours_back)
        _debug_log_empty_feed(journal, feed, source_type)
        _debug_log_feed_status(journal, feed)
        _process_journal_entries(
            client,
            journal,
            feed,
            source_type,
            posted,
            target_start,
            target_end,
        )
