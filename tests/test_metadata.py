from __future__ import annotations

import datetime as dt
import urllib.error

from feedparser import FeedParserDict

import metadata
import rss_sources


def test_openalex_doi_lookup_reconstructs_abstract(monkeypatch):
    captured = {}

    def fake_fetch_json(url, timeout=5, source="metadata"):
        captured.update(url=url, timeout=timeout, source=source)
        return {
            "doi": "https://doi.org/10.1016/j.example.2026.1",
            "title": "Example",
            "abstract_inverted_index": {
                "A": [0],
                "useful": [1],
                "abstract": [2],
            },
        }

    monkeypatch.setattr(metadata, "fetch_json", fake_fetch_json)

    result = metadata.enrich_from_openalex_doi("doi:10.1016/j.example.2026.1")

    assert result["doi"] == "doi:10.1016/j.example.2026.1"
    assert result["abstract"] == "A useful abstract"
    assert captured["source"] == "openalex_doi"
    assert "/works/https://doi.org/10.1016/j.example.2026.1?" in captured["url"]


def test_crossref_doi_is_resolved_before_openalex_title_search(monkeypatch):
    calls = []

    monkeypatch.setattr(
        metadata,
        "enrich_from_crossref",
        lambda *args, **kwargs: calls.append("crossref")
        or {
            "source": "crossref",
            "doi": "doi:10.1016/j.example.2026.1",
            "abstract": "",
        },
    )
    monkeypatch.setattr(
        metadata,
        "enrich_from_openalex_doi",
        lambda doi: calls.append(("openalex_doi", doi))
        or {
            "source": "openalex",
            "doi": doi,
            "abstract": "Recovered abstract " * 30,
        },
    )
    monkeypatch.setattr(
        metadata,
        "enrich_from_openalex",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("title search should not run after DOI resolution")
        ),
    )

    result = metadata.enrich_metadata(
        "Example title",
        {
            "title": "Example Journal",
            "metadata_fallback": ["openalex", "crossref"],
        },
    )

    assert calls == [
        "crossref",
        ("openalex_doi", "doi:10.1016/j.example.2026.1"),
    ]
    assert result["source"] == "openalex"


def test_fetch_json_reports_transient_error_without_logging_query(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            args[0].full_url,
            429,
            "Too Many Requests",
            {},
            None,
        )

    monkeypatch.setattr(metadata.urllib.request, "urlopen", fail)

    result = metadata.fetch_json(
        "https://api.openalex.org/works?search=private+title",
        source="openalex_title",
    )

    output = capsys.readouterr().out
    assert result["_metadata_transient_error"]["status"] == 429
    assert "source=openalex_title" in output
    assert "private" not in output


def test_rss_entry_is_retried_after_transient_metadata_failure(monkeypatch):
    monkeypatch.setattr(
        rss_sources,
        "enrich_metadata",
        lambda *args, **kwargs: {
            "_metadata_transient_error": {
                "source": "openalex_doi",
                "status": 429,
                "error": "HTTPError",
            }
        },
    )
    context = rss_sources._RssEntryContext(
        entry=FeedParserDict(title="Example title"),
        feed=FeedParserDict(),
        journal={
            "title": "Example Journal",
            "metadata_fallback": ["openalex", "crossref"],
            "min_abstract_length": 500,
        },
        source_type="rss",
        target_start=dt.datetime(2026, 8, 20),
        target_end=dt.datetime(2026, 8, 22),
        pub_dt=dt.datetime(2026, 8, 21),
        entry_id="doi:10.1016/j.example.2026.3",
        rss_text="Publication date: 2026 Source: Example Author(s): A",
    )
    stats = rss_sources._JournalRunStats()
    posted = {}

    assert rss_sources._step_resolve_abstract(context, posted, stats) is True
    assert stats.metadata_deferred == 1
    assert posted == {}
