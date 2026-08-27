from __future__ import annotations

import feedparser
from unittest.mock import patch

import rss_sources


def _feed(*entries: dict) -> feedparser.FeedParserDict:
    return feedparser.FeedParserDict(
        {"status": 200, "bozo": False, "entries": list(entries)}
    )


def test_agu_dedupes_by_doi_and_merges_all_provenance() -> None:
    feeds = iter(
        [
            _feed(
                {
                    "title": "A paper",
                    "doi": "10.1002/agu.123",
                    "summary": "short",
                    "prism_publicationname": "JGR Solid Earth",
                }
            ),
            _feed(
                {
                    "title": "A paper",
                    "link": "https://doi.org/10.1002/AGU.123",
                    "summary": "a much longer abstract from the second search",
                    "dc_source": "JGR Solid Earth",
                }
            ),
            _feed(
                {
                    "title": "A paper",
                    "prism_doi": "10.1002/agu.123",
                    "journal": "Journal of Geophysical Research: Solid Earth",
                }
            ),
        ]
    )
    journal = {
        "title": "AGU",
        "agu_search_terms": ["earthquake", "seismic"],
        "agu_taxonomies": [
            {"concept_id": 123, "name": "Seismology", "group": "Solid Earth"}
        ],
    }
    with patch.object(
        rss_sources,
        "parse_feed_with_headers",
        side_effect=lambda _: next(feeds),
    ):
        result = rss_sources.fetch_agu_taxonomy_feed(journal, 24)

    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry["agu_search_terms"] == ["earthquake", "seismic"]
    assert entry["agu_taxonomies"] == ["Seismology"]
    assert entry["agu_taxonomy_groups"] == ["Solid Earth"]
    assert entry["agu_journals"] == [
        "JGR Solid Earth",
        "Journal of Geophysical Research: Solid Earth",
    ]
    assert entry["summary"] == "a much longer abstract from the second search"
