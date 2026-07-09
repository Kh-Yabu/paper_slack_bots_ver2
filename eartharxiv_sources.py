from __future__ import annotations

import datetime as dt
import logging
from datetime import timedelta

from sickle import Sickle
from slack_sdk import WebClient

from bot_config import TZ_TOKYO
from identifiers import normalize_entry_url
from posted import PostedRecord
from posting_flow import post_and_record
from summarizer import maybe_summarize


def fetch_and_post_eartharxiv(
    client: WebClient,
    cfg: dict,
    hours_back: int,
    posted: dict[str, PostedRecord],
) -> None:
    target_start = dt.datetime.now(tz=TZ_TOKYO) - timedelta(hours=hours_back)
    target_end = dt.datetime.now(tz=TZ_TOKYO)

    keywords_lc = [kw.lower() for kw in cfg.get("keywords", [])]
    channel = cfg.get("slack_channel_id", "")

    sickle = Sickle("https://eartharxiv.org/api/oai/")

    try:
        oai_from = target_start.astimezone(dt.timezone.utc).date().isoformat()
        oai_until = target_end.astimezone(dt.timezone.utc).date().isoformat()

        records = sickle.ListRecords(
            metadataPrefix="oai_dc",
            **{
                "from": oai_from,
                "until": oai_until,
            },
        )

        for record in records:
            metadata = record.metadata or {}

            title = " ".join(metadata.get("title", []))
            abstract_en = " ".join(metadata.get("description", []))
            identifiers = metadata.get("identifier", [])

            if not title:
                continue

            if not abstract_en:
                abstract_en = title

            link = None
            for x in identifiers:
                if "eartharxiv.org" in x:
                    link = x
                    break

            if not link:
                continue

            dates = metadata.get("date", [])
            if not dates:
                continue

            try:
                pub_dt = dt.datetime.fromisoformat(
                    dates[0].replace("Z", "+00:00"),
                ).astimezone(TZ_TOKYO)
            except Exception:
                continue

            if not (target_start <= pub_dt <= target_end):
                continue

            text_lc = f"{title}\n{abstract_en}".lower()

            if keywords_lc:
                if not any(kw in text_lc for kw in keywords_lc):
                    continue

            entry_id = normalize_entry_url(link)

            if entry_id in posted:
                continue

            summary = maybe_summarize(title, abstract_en)

            post_and_record(
                client,
                channel,
                title=title,
                link=link,
                summary=summary,
                abstract=abstract_en,
                posted=posted,
                entry_id=entry_id,
                pub_dt=pub_dt,
                journal="EarthArXiv",
                reason="posted",
            )

    except Exception as e:
        logging.error("EarthArXiv fetch failed: %s", e)
