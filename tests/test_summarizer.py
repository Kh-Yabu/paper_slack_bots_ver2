from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import summarizer


def test_summary_language_and_instructions_come_from_config(monkeypatch):
    client = MagicMock()
    client.responses.create.return_value = SimpleNamespace(output_text="要約")
    monkeypatch.setattr(summarizer, "client_oa", client)
    monkeypatch.setattr(
        summarizer,
        "config",
        {
            "summarization": {
                "language": "Japanese",
                "instructions": "Use terminology common in materials science.",
            }
        },
    )

    assert summarizer.summarize("A title", "An abstract") == "要約"

    request = client.responses.create.call_args.kwargs
    prompt = request["input"][1]["content"]
    assert "Write the output in Japanese" in prompt
    assert "materials science" in prompt
    assert request["reasoning"] == {"effort": summarizer.SUMMARY_REASONING_EFFORT}
