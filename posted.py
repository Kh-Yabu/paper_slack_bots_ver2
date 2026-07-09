from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from bot_config import POSTED_FILE, TZ_TOKYO, config
from identifiers import normalize_posted_key


_ALIAS_REASON_PREFIX = "alias:"


@dataclass
class PostedRecord:
    dtval: dt.datetime
    status: str = "posted"
    journal: str = ""
    reason: str = ""


def normalize_record_key(entry_id: str) -> str:
    """
    Normalize a posted-entry key.

    Unlike normalize_posted_key(), this function treats blank strings as empty.
    This prevents accidental bogus keys such as '://'.
    """
    if not (entry_id or "").strip():
        return ""
    return normalize_posted_key(entry_id)


def alias_target_from_reason(reason: str) -> str:
    """
    Return canonical key encoded in an alias reason, if present.
    """
    reason = (reason or "").strip()
    if not reason.startswith(_ALIAS_REASON_PREFIX):
        return ""

    return normalize_record_key(reason[len(_ALIAS_REASON_PREFIX):])


def resolve_posted_key(
    posted: dict[str, PostedRecord],
    entry_id: str,
) -> str:
    """
    Resolve an entry_id to the canonical key stored in posted.

    If the key itself is an alias and the canonical target exists, return the
    canonical target. Otherwise return the normalized key itself.
    """
    key = normalize_record_key(entry_id)
    if not key or key not in posted:
        return ""

    alias_target = alias_target_from_reason(posted[key].reason)
    if alias_target and alias_target in posted:
        return alias_target

    return key


def load_posted_ids() -> dict[str, PostedRecord]:
    posted: dict[str, PostedRecord] = {}

    if not POSTED_FILE.exists():
        return posted

    for line in POSTED_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        parts = line.split("\t")
        if len(parts) < 2:
            continue

        entry_id, dt_str = parts[0], parts[1]
        status = parts[2] if len(parts) >= 3 else "posted"
        journal = parts[3] if len(parts) >= 4 else ""
        reason = parts[4] if len(parts) >= 5 else ""

        norm_id = normalize_record_key(entry_id)
        if not norm_id:
            continue

        try:
            posted[norm_id] = PostedRecord(
                dtval=dt.datetime.fromisoformat(dt_str),
                status=status,
                journal=journal,
                reason=reason,
            )
        except Exception:
            continue

    return posted


def save_posted_ids(posted: dict[str, PostedRecord]) -> None:
    with POSTED_FILE.open("w", encoding="utf-8") as f:
        for eid, rec in posted.items():
            f.write(
                f"{eid}\t{rec.dtval.isoformat()}\t"
                f"{rec.status}\t{rec.journal}\t{rec.reason}\n"
            )


def prune_old_posted_ids(
    posted: dict[str, PostedRecord],
    keep_hours: int,
) -> dict[str, PostedRecord]:
    threshold = dt.datetime.now(tz=TZ_TOKYO) - timedelta(hours=keep_hours + 1)
    return {eid: rec for eid, rec in posted.items() if rec.dtval >= threshold}


def mark_posted(
    posted: dict[str, PostedRecord],
    entry_id: str,
    pub_dt: dt.datetime,
    *,
    status: str = "posted",
    journal: str = "",
    reason: str = "",
) -> None:
    norm_id = normalize_record_key(entry_id)
    if not norm_id:
        return

    posted[norm_id] = PostedRecord(
        dtval=pub_dt,
        status=status,
        journal=journal,
        reason=reason,
    )


def mark_alias(
    posted: dict[str, PostedRecord],
    alias_id: str,
    canonical_id: str,
    pub_dt: dt.datetime,
    *,
    status: str,
    journal: str,
) -> None:
    norm_alias_id = normalize_record_key(alias_id)
    norm_canonical_id = normalize_record_key(canonical_id)

    if not norm_alias_id or not norm_canonical_id:
        return

    if norm_alias_id == norm_canonical_id:
        return

    posted[norm_alias_id] = PostedRecord(
        dtval=pub_dt,
        status=status,
        journal=journal,
        reason=f"{_ALIAS_REASON_PREFIX}{norm_canonical_id}",
    )


def unique_aliases(
    aliases: Iterable[str],
    *,
    canonical_id: str,
) -> list[str]:
    norm_canonical_id = normalize_record_key(canonical_id)
    seen = {norm_canonical_id}
    result: list[str] = []

    for alias in aliases:
        norm_alias = normalize_record_key(alias)
        if not norm_alias or norm_alias in seen:
            continue

        seen.add(norm_alias)
        result.append(norm_alias)

    return result


def mark_and_save(
    posted: dict[str, PostedRecord],
    entry_id: str,
    pub_dt: dt.datetime,
    *,
    status: str,
    journal: str,
    reason: str,
) -> None:
    mark_with_aliases_and_save(
        posted,
        entry_id,
        pub_dt,
        aliases=(),
        status=status,
        journal=journal,
        reason=reason,
    )


def mark_with_aliases_and_save(
    posted: dict[str, PostedRecord],
    entry_id: str,
    pub_dt: dt.datetime,
    *,
    aliases: Iterable[str] = (),
    status: str,
    journal: str,
    reason: str,
) -> None:
    canonical_id = normalize_record_key(entry_id)
    if not canonical_id:
        return

    mark_posted(
        posted,
        canonical_id,
        pub_dt,
        status=status,
        journal=journal,
        reason=reason,
    )

    for alias in unique_aliases(aliases, canonical_id=canonical_id):
        mark_alias(
            posted,
            alias,
            canonical_id,
            pub_dt,
            status=status,
            journal=journal,
        )

    save_posted_ids(
        prune_old_posted_ids(
            posted,
            int(config.get("keep_hours", 24 * 30)),
        )
    )
