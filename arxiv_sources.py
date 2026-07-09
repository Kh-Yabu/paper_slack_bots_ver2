from __future__ import annotations

import datetime as dt
import logging
from datetime import timedelta

import arxiv
from slack_sdk import WebClient

from bot_config import TZ_TOKYO, config
from identifiers import normalize_entry_url
from posted import PostedRecord
from posting_flow import post_and_record
from summarizer import maybe_summarize


def fetch_and_post_arxiv(
    client: WebClient,
    arxiv_cfg: dict,
    hours_back: int,
    posted: dict[str, PostedRecord],
) -> None:
    target_start = dt.datetime.now(tz=TZ_TOKYO) - timedelta(hours=hours_back)
    target_end = dt.datetime.now(tz=TZ_TOKYO)

    categories = arxiv_cfg.get("categories", [])
    keywords_lc = [kw.lower() for kw in arxiv_cfg.get("keywords", [])]
    channel = arxiv_cfg.get("slack_channel_id", "")

    client_arxiv = arxiv.Client(
        page_size=25,
        delay_seconds=5,
        num_retries=5,
    )

    query = " OR ".join(f"cat:{c}" for c in categories)
    search = arxiv.Search(
        query=query,
        max_results=100,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )

    try:
        for result in client_arxiv.results(search):
            pub_dt = result.published.replace(
                tzinfo=dt.timezone.utc,
            ).astimezone(TZ_TOKYO)

            if pub_dt > target_end:
                continue

            if pub_dt < target_start:
                break

            abstract_en = " ".join(result.summary.splitlines())

            if keywords_lc:
                if not any(kw in abstract_en.lower() for kw in keywords_lc):
                    continue

            entry_id = normalize_entry_url(result.entry_id)
            if entry_id in posted:
                continue

            summary = maybe_summarize(result.title, abstract_en)

            post_and_record(
                client,
                channel,
                title=result.title,
                link=result.entry_id,
                summary=summary,
                abstract=abstract_en,
                posted=posted,
                entry_id=entry_id,
                pub_dt=pub_dt,
                journal="arXiv",
                reason="posted",
            )

    except Exception as e:
        logging.error("arXiv fetch failed: %s", e)
        return
