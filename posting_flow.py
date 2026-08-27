from __future__ import annotations

from collections.abc import Iterable
import datetime as dt

from slack_sdk import WebClient

from bot_config import DRY_RUN
from posted import PostedRecord, mark_with_aliases_and_save
from slack_post import post


def record_posted_entry(
    posted: dict[str, PostedRecord],
    entry_id: str,
    pub_dt: dt.datetime,
    *,
    journal: str,
    reason: str,
    aliases: Iterable[str] = (),
    status: str = "posted",
) -> None:
    mark_with_aliases_and_save(
        posted,
        entry_id,
        pub_dt,
        aliases=aliases,
        status=status,
        journal=journal,
        reason=reason,
    )


def post_and_record(
    client: WebClient,
    channel: str,
    *,
    title: str,
    link: str,
    summary: str,
    abstract: str,
    posted: dict[str, PostedRecord],
    entry_id: str,
    pub_dt: dt.datetime,
    journal: str,
    reason: str,
    aliases: Iterable[str] = (),
    status: str = "posted",
) -> bool:
    posted_ok = post(
        client,
        channel,
        title=title,
        link=link,
        summary=summary,
        abstract=abstract,
    )

    if not posted_ok:
        return False
    if DRY_RUN:
        return True

    record_posted_entry(
        posted,
        entry_id,
        pub_dt,
        aliases=aliases,
        status=status,
        journal=journal,
        reason=reason,
    )
    return True


def post_and_record_rss(
    client: WebClient,
    channel: str,
    *,
    title: str,
    link: str,
    summary: str,
    abstract: str,
    posted: dict[str, PostedRecord],
    entry_id: str,
    pub_dt: dt.datetime,
    journal: str,
    reason: str,
    aliases: Iterable[str] = (),
    status: str = "posted",
) -> bool:
    return post_and_record(
        client,
        channel,
        title=title,
        link=link,
        summary=summary,
        abstract=abstract,
        posted=posted,
        entry_id=entry_id,
        pub_dt=pub_dt,
        journal=journal,
        reason=reason,
        aliases=aliases,
        status=status,
    )
