#!/usr/bin/env bash
set -euo pipefail

: "${SPRINGER_API_KEY:?SPRINGER_API_KEY is not set. Run: export SPRINGER_API_KEY='...'}"

CONFIG_FILE="${CONFIG_FILE:-config.yaml}"

# 集計期間。まず31日でよいです。
DAYS="${DAYS:-31}"

# YYYY-MM-DD。空なら今日 JST。
END_DATE="${END_DATE:-}"

# API調査用の取得深さ。まずは 25 x 20 = 500 records/query。
PAGE_SIZE="${PAGE_SIZE:-25}"
MAX_PAGES="${MAX_PAGES:-20}"

# API呼び出し間隔。429が出るなら 0.5〜1.0 に上げる。
SLEEP_SEC="${SLEEP_SEC:-0.25}"

# 推奨max_pages計算用の安全係数。
# hours_back=72なら「過去3日の最大件数 × SAFETY」を収めるページ数を出す。
SAFETY="${SAFETY:-2.0}"

OUT_PREFIX="${OUT_PREFIX:-springer_volume}"

python - "$CONFIG_FILE" "$DAYS" "$END_DATE" "$PAGE_SIZE" "$MAX_PAGES" "$SLEEP_SEC" "$SAFETY" "$OUT_PREFIX" <<'PY'
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import re
import statistics
import sys
import urllib.error
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from zoneinfo import ZoneInfo

try:
    import yaml
except Exception as e:
    raise SystemExit(
        "PyYAML is required. Install or run inside your paper_slack_bots environment.\n"
        f"Original error: {e}"
    )

CONFIG_FILE = sys.argv[1]
DAYS = int(sys.argv[2])
END_DATE_ARG = sys.argv[3].strip()
PAGE_SIZE = int(sys.argv[4])
MAX_PAGES = int(sys.argv[5])
SLEEP_SEC = float(sys.argv[6])
SAFETY = float(sys.argv[7])
OUT_PREFIX = sys.argv[8]

SOURCE_REGEX = os.environ.get("SOURCE_REGEX", "").strip()

API_KEY = os.environ["SPRINGER_API_KEY"]
BASE_URL = "https://api.springernature.com/meta/v2/json"
TZ = ZoneInfo("Asia/Tokyo")

if END_DATE_ARG:
    END_DATE = dt.date.fromisoformat(END_DATE_ARG)
else:
    END_DATE = dt.datetime.now(TZ).date()

START_DATE = END_DATE - dt.timedelta(days=DAYS - 1)
DATES = [START_DATE + dt.timedelta(days=i) for i in range(DAYS)]

DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.I)


def norm_doi(raw: object) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    m = DOI_RE.search(s)
    if not m:
        return ""
    doi = m.group(0).strip().rstrip(").,;]}")
    return "doi:" + doi.lower()


def first_nonempty(*values: object) -> str:
    for v in values:
        if v is None:
            continue
        if isinstance(v, list):
            for x in v:
                y = first_nonempty(x)
                if y:
                    return y
            continue
        if isinstance(v, dict):
            for k in ("value", "href", "url"):
                y = first_nonempty(v.get(k))
                if y:
                    return y
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def parse_record_date_value(value: object) -> dt.date | None:
    s = first_nonempty(value)
    if not s:
        return None

    # 例: 2026-07-02, 2026-07-02T00:00:00Z
    s = s.replace("Z", "+00:00")

    try:
        if len(s) == 10:
            return dt.date.fromisoformat(s)
        d = dt.datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(TZ).date()
    except Exception:
        pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date()
        except Exception:
            pass

    return None


def record_date(record: dict) -> tuple[dt.date | None, str]:
    # bot側 rss_sources.py と同じ優先順
    for key in (
        "onlineDate",
        "publicationDate",
        "coverDate",
        "printDate",
        "acceptanceDate",
    ):
        d = parse_record_date_value(record.get(key))
        if d is not None:
            return d, key
    return None, ""


def record_key(record: dict) -> str:
    doi = norm_doi(
        first_nonempty(
            record.get("doi"),
            record.get("identifier"),
            record.get("electronicIdentifier"),
        )
    )
    if doi:
        return doi

    # DOIがない場合の保険。通常はほぼ使わない想定。
    link = ""
    for key in ("url", "fullTextUrl", "selfUrl", "pdfUrl", "abstractUrl"):
        v = record.get(key)
        link = first_nonempty(v)
        if link:
            break

    if link:
        return link.strip().lower()

    title = first_nonempty(record.get("title"))
    date, _ = record_date(record)
    return f"title:{title}|date:{date}" if title else ""


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def springer_queries_for_journal(j: dict) -> list[str]:
    queries = j.get("springer_queries") or []
    if isinstance(queries, str):
        queries = [queries]

    queries = [str(q).strip() for q in queries if str(q).strip()]
    if queries:
        return queries

    q = str(j.get("springer_query") or "").strip()
    if not q and j.get("issn"):
        q = f"issn:{j['issn']}"

    return [q] if q else []


def iter_springer_sources(config: dict):
    for ws in config.get("workspaces", []) or []:
        ws_name = ws.get("name", "")
        for j in ws.get("journals", []) or []:
            if j.get("source_type") != "springer_api":
                continue

            queries = springer_queries_for_journal(j)
            if not queries:
                continue

            title = j.get("title") or j.get("full_title") or "(untitled)"
            yield {
                "workspace": ws_name,
                "title": title,
                "queries": queries,
            }

def fetch_page(query: str, start: int, page_size: int) -> dict:
    params = {
        "api_key": API_KEY,
        "q": query,
        "s": str(start),
        "p": str(page_size),
    }
    url = BASE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "paper-slack-bot-volume-estimator/1.0",
        },
    )

    last_error = None
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                return json.loads(raw.decode(charset, errors="replace"))

        except urllib.error.HTTPError as e:
            last_error = e

            if e.code == 429:
                retry_after = e.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    wait = int(retry_after)
                else:
                    wait = min(300, 30 * attempt)

                print(
                    f"  [RATE_LIMIT] HTTP 429. wait={wait}s "
                    f"attempt={attempt}/5 start={start}",
                    flush=True,
                )
                time.sleep(wait)
                continue

            raise

    raise last_error


def percentile(values: list[int | float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = math.ceil((p / 100.0) * len(xs)) - 1
    idx = max(0, min(idx, len(xs) - 1))
    return float(xs[idx])


def rolling_sum(counts_by_date: dict[dt.date, int], window: int) -> list[int]:
    vals = []
    for end in DATES:
        start = end - dt.timedelta(days=window - 1)
        s = 0
        for i in range(window):
            d = start + dt.timedelta(days=i)
            if START_DATE <= d <= END_DATE:
                s += counts_by_date.get(d, 0)
        vals.append(s)
    return vals


def rec_pages(max_3day_per_query: int, page_size: int) -> int:
    needed = math.ceil(max_3day_per_query * SAFETY)
    return max(1, math.ceil(needed / page_size))


config = load_config(CONFIG_FILE)
sources = list(iter_springer_sources(config))
if SOURCE_REGEX:
    pat = re.compile(SOURCE_REGEX, re.I)
    sources = [s for s in sources if pat.search(s["title"])]

if not sources:
    raise SystemExit(f"No matching springer_api sources. SOURCE_REGEX={SOURCE_REGEX!r}")

print(f"Config: {CONFIG_FILE}")
print(f"Window: {START_DATE} .. {END_DATE} JST ({DAYS} days)")
print(f"Probe depth: PAGE_SIZE={PAGE_SIZE}, MAX_PAGES={MAX_PAGES}")
print(f"Sources: {len(sources)}")
print()

daily_rows = []
summary_rows = []
query_rows = []

for source in sources:
    title = source["title"]
    queries = source["queries"]

    print(f"=== {title} ===")
    print(f"queries={len(queries)}")

    # source全体では DOI dedupe する
    source_keys_by_date: dict[dt.date, set[str]] = defaultdict(set)

    # 推奨 max_pages は「最も混む query」に合わせるため、query別にも持つ
    query_counts_by_date_list: list[dict[dt.date, int]] = []

    source_records_fetched = 0
    source_requests = 0
    source_oldest = None
    source_newest = None
    source_total_api = 0
    coverage_flags = []

    for qi, query in enumerate(queries, start=1):
        q_keys_by_date: dict[dt.date, set[str]] = defaultdict(set)
        q_records_fetched = 0
        q_requests = 0
        q_oldest = None
        q_newest = None
        q_total_api = None

        for page in range(1, MAX_PAGES + 1):
            start_index = 1 + (page - 1) * PAGE_SIZE

            try:
                data = fetch_page(query, start_index, PAGE_SIZE)
            except Exception as e:
                print(f"  [WARN] query {qi} page {page} failed: {e}")
                break

            q_requests += 1
            source_requests += 1

            result = data.get("result") or []
            if result and isinstance(result, list):
                try:
                    q_total_api = int(result[0].get("total"))
                except Exception:
                    pass

            records = data.get("records") or []
            if not isinstance(records, list):
                records = []

            if not records:
                break

            q_records_fetched += len(records)
            source_records_fetched += len(records)

            for rec in records:
                if not isinstance(rec, dict):
                    continue

                d, date_field = record_date(rec)
                if d is None:
                    continue

                if q_oldest is None or d < q_oldest:
                    q_oldest = d
                if q_newest is None or d > q_newest:
                    q_newest = d
                if source_oldest is None or d < source_oldest:
                    source_oldest = d
                if source_newest is None or d > source_newest:
                    source_newest = d

                key = record_key(rec)
                if not key:
                    continue

                if START_DATE <= d <= END_DATE:
                    q_keys_by_date[d].add(key)
                    source_keys_by_date[d].add(key)

            if len(records) < PAGE_SIZE:
                break

            time.sleep(SLEEP_SEC)

        if q_total_api is not None:
            source_total_api += q_total_api

        q_counts_by_date = {d: len(keys) for d, keys in q_keys_by_date.items()}
        query_counts_by_date_list.append(q_counts_by_date)

        q_daily = [q_counts_by_date.get(d, 0) for d in DATES]
        q_roll3 = rolling_sum(q_counts_by_date, 3)
        q_unique = sum(q_daily)

        if q_oldest is None:
            coverage = "no_dates_seen"
        elif q_oldest > START_DATE:
            coverage = "maybe_not_deep_enough"
        else:
            coverage = "ok"

        if coverage != "ok":
            coverage_flags.append(f"q{qi}:{coverage}")

        query_rows.append(
            {
                "source": title,
                "query_index": qi,
                "query": query,
                "api_total": q_total_api if q_total_api is not None else "",
                "requests": q_requests,
                "records_fetched": q_records_fetched,
                "unique_in_window": q_unique,
                "mean_per_day": round(statistics.mean(q_daily), 3) if q_daily else 0,
                "p95_day": percentile(q_daily, 95),
                "max_day": max(q_daily) if q_daily else 0,
                "p95_3day": percentile(q_roll3, 95),
                "max_3day": max(q_roll3) if q_roll3 else 0,
                "oldest_seen": q_oldest.isoformat() if q_oldest else "",
                "newest_seen": q_newest.isoformat() if q_newest else "",
                "coverage": coverage,
            }
        )

        print(
            f"  q{qi}: fetched={q_records_fetched} "
            f"in_window={q_unique} "
            f"max3d={max(q_roll3) if q_roll3 else 0} "
            f"oldest={q_oldest} newest={q_newest} coverage={coverage}"
        )

    source_counts_by_date = {d: len(keys) for d, keys in source_keys_by_date.items()}

    for d in DATES:
        daily_rows.append(
            {
                "source": title,
                "date": d.isoformat(),
                "count": source_counts_by_date.get(d, 0),
            }
        )

    daily = [source_counts_by_date.get(d, 0) for d in DATES]
    roll3 = rolling_sum(source_counts_by_date, 3)

    per_query_max_3day = 0
    for q_counts in query_counts_by_date_list:
        q_roll3 = rolling_sum(q_counts, 3)
        if q_roll3:
            per_query_max_3day = max(per_query_max_3day, max(q_roll3))

    recommended_pages_p20 = rec_pages(per_query_max_3day, 20)
    recommended_pages_p50 = rec_pages(per_query_max_3day, 50)
    recommended_pages_p100 = rec_pages(per_query_max_3day, 100)

    coverage_summary = "ok" if not coverage_flags else ";".join(coverage_flags)

    row = {
        "source": title,
        "queries": len(queries),
        "requests": source_requests,
        "records_fetched": source_records_fetched,
        "rough_api_total_sum_across_queries": source_total_api,
        "unique_in_window": sum(daily),
        "mean_per_day": round(statistics.mean(daily), 3) if daily else 0,
        "p50_day": percentile(daily, 50),
        "p90_day": percentile(daily, 90),
        "p95_day": percentile(daily, 95),
        "max_day": max(daily) if daily else 0,
        "mean_3day": round(statistics.mean(roll3), 3) if roll3 else 0,
        "p90_3day": percentile(roll3, 90),
        "p95_3day": percentile(roll3, 95),
        "max_3day": max(roll3) if roll3 else 0,
        "per_query_max_3day": per_query_max_3day,
        "recommended_max_pages_if_page_size_20": recommended_pages_p20,
        "recommended_max_pages_if_page_size_50": recommended_pages_p50,
        "recommended_max_pages_if_page_size_100": recommended_pages_p100,
        "oldest_seen": source_oldest.isoformat() if source_oldest else "",
        "newest_seen": source_newest.isoformat() if source_newest else "",
        "coverage": coverage_summary,
    }
    summary_rows.append(row)

    print(
        f"SUMMARY {title}: "
        f"unique_31d={row['unique_in_window']} "
        f"mean/day={row['mean_per_day']} "
        f"p95/day={row['p95_day']} "
        f"max/day={row['max_day']} "
        f"max3d={row['max_3day']} "
        f"per_query_max3d={row['per_query_max_3day']} "
        f"rec pages: p20={recommended_pages_p20}, p50={recommended_pages_p50}, p100={recommended_pages_p100} "
        f"coverage={coverage_summary}"
    )
    print()

daily_csv = f"{OUT_PREFIX}_daily.csv"
summary_csv = f"{OUT_PREFIX}_summary.csv"
query_csv = f"{OUT_PREFIX}_query_summary.csv"

with open(daily_csv, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["source", "date", "count"])
    w.writeheader()
    w.writerows(daily_rows)

with open(summary_csv, "w", newline="", encoding="utf-8") as f:
    fieldnames = list(summary_rows[0].keys()) if summary_rows else []
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(summary_rows)

with open(query_csv, "w", newline="", encoding="utf-8") as f:
    fieldnames = list(query_rows[0].keys()) if query_rows else []
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(query_rows)

print("Wrote:")
print(f"  {daily_csv}")
print(f"  {summary_csv}")
print(f"  {query_csv}")
print()
print("Read this first:")
print(f"  column recommended_max_pages_if_page_size_20 / 50 / 100 in {summary_csv}")
print()
print("If coverage contains maybe_not_deep_enough, rerun with larger MAX_PAGES, e.g.:")
print("  PAGE_SIZE=25 MAX_PAGES=20 sh estimate_springer_volume.sh")
PY