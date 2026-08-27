from __future__ import annotations

from pathlib import Path

import rss_sources


def test_recent_html_is_converted_to_feed_entries():
    fixture = Path(__file__).parent / "fixtures" / "copernicus_recent.html"
    entries = rss_sources.parse_copernicus_recent_html(
        fixture.read_text(encoding="utf-8")
    )

    assert len(entries) == 2
    assert entries[0]["title"] == "Mantle convection in the deep Earth"
    assert entries[0]["doi"] == "doi:10.5194/acp-24-12345"
    assert entries[0]["link"] == "https://doi.org/10.5194/acp-24-12345"
