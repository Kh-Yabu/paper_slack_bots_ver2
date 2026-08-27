from __future__ import annotations

import datetime as dt

import posting_flow
import rss_sources


def _call_post_and_record():
    return posting_flow.post_and_record(
        object(),
        "C123",
        title="Paper",
        link="https://example.com/paper",
        summary="Summary",
        abstract="Abstract",
        posted={},
        entry_id="doi:10.1234/test",
        pub_dt=dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
        journal="Test Journal",
        reason="posted:test",
    )


def test_failed_slack_post_is_not_recorded(monkeypatch):
    recorded = []
    monkeypatch.setattr(posting_flow, "post", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        posting_flow,
        "record_posted_entry",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    assert _call_post_and_record() is False
    assert recorded == []


def test_successful_slack_post_is_recorded(monkeypatch):
    recorded = []
    monkeypatch.setattr(posting_flow, "post", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        posting_flow,
        "record_posted_entry",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    assert _call_post_and_record() is True
    assert len(recorded) == 1


def test_dry_run_does_not_record_state(monkeypatch):
    recorded = []
    monkeypatch.setattr(posting_flow, "DRY_RUN", True)
    monkeypatch.setattr(posting_flow, "post", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        posting_flow,
        "record_posted_entry",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    assert _call_post_and_record() is True
    assert recorded == []


def test_rss_dry_run_does_not_record_filtered_state(monkeypatch):
    recorded = []
    monkeypatch.setattr(rss_sources, "DRY_RUN", True)
    monkeypatch.setattr(
        rss_sources,
        "record_posted_entry",
        lambda *args, **kwargs: recorded.append((args, kwargs)),
    )

    rss_sources.record_rss_status(
        {},
        "doi:10.1234/test",
        dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc),
        [],
        {"title": "Test Journal"},
        status="skipped",
        reason="keyword",
    )

    assert recorded == []
