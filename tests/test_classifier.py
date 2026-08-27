from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import classifier


def test_no_prefilter_accepts_without_api_call(monkeypatch):
    monkeypatch.setattr(
        classifier,
        "call_openai_classifier",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("classifier API should not be called")
        ),
    )

    assert classifier.classify_relevance("Title", "Abstract", {}) == (
        "relevant",
        "no_prefilter",
    )


def test_configured_hard_include_bypasses_model(monkeypatch):
    monkeypatch.setattr(
        classifier,
        "call_openai_classifier",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("classifier API should not be called")
        ),
    )
    journal = {
        "prefilter": {
            "hard_include_patterns": [
                {"field": "title", "pattern": "open dataset", "rule": "dataset"}
            ]
        }
    }

    assert classifier.classify_relevance(
        "An open dataset for research",
        "Abstract",
        journal,
    ) == ("relevant", "dataset")


def test_target_scope_is_used_without_field_specific_defaults(monkeypatch):
    captured = {}

    def fake_call(model, system_msg, user_msg, **kwargs):
        captured.update(
            model=model,
            system=system_msg["content"],
            user=user_msg["content"],
        )
        return '{"decision":"relevant","reason":"scope match"}'

    monkeypatch.setattr(classifier, "call_openai_classifier", fake_call)

    decision, reason = classifier.classify_relevance(
        "A protein-folding method",
        "We predict protein structures.",
        {
            "prefilter": {
                "provider": "openai",
                "model": "gpt-5.6-luna",
                "target_scope": "Computational biology and protein science.",
            }
        },
    )

    assert (decision, reason) == ("relevant", "scope match")
    assert "Computational biology" in captured["user"]
    assert "solid Earth" not in captured["system"] + captured["user"]


def test_openai_classifier_uses_responses_api_controls(monkeypatch):
    client = MagicMock()
    client.responses.create.return_value = SimpleNamespace(
        output_text='{"decision":"relevant","reason":"match"}'
    )
    monkeypatch.setattr(classifier, "client_oa", client)

    classifier.call_openai_classifier(
        "gpt-5.6-luna",
        {"role": "system", "content": "classify"},
        {"role": "user", "content": "paper"},
    )

    kwargs = client.responses.create.call_args.kwargs
    assert kwargs["reasoning"] == {"effort": "low"}
    assert kwargs["text"] == {"verbosity": "low"}
    assert kwargs["max_output_tokens"] == classifier.CLASSIFIER_MAX_OUTPUT_TOKENS
