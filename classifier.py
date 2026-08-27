from __future__ import annotations

import re

from bot_config import (
    DRY_RUN,
    DRY_RUN_CLASSIFY,
    client_github,
    client_oa,
    secrets,
)
from text_utils import parse_json_object


CLASSIFIER_MAX_OUTPUT_TOKENS = 300


def call_github_classifier(model: str, system_msg: dict, user_msg: dict) -> str:
    """Call GitHub Models through its OpenAI-compatible endpoint."""
    if client_github is None:
        raise RuntimeError("GitHub Models is not configured.")

    kwargs = {
        "model": model,
        "messages": [system_msg, user_msg],
    }
    if model.startswith("openai/gpt-5"):
        kwargs["max_completion_tokens"] = CLASSIFIER_MAX_OUTPUT_TOKENS
    else:
        kwargs["max_tokens"] = CLASSIFIER_MAX_OUTPUT_TOKENS

    response = client_github.chat.completions.create(**kwargs)
    return (response.choices[0].message.content or "").strip()


def call_openai_classifier(
    model: str,
    system_msg: dict,
    user_msg: dict,
    *,
    reasoning_effort: str = "low",
) -> str:
    """Call the OpenAI Responses API."""
    request = {
        "model": model,
        "input": [system_msg, user_msg],
        "text": {"verbosity": "low"},
        "max_output_tokens": CLASSIFIER_MAX_OUTPUT_TOKENS,
    }
    if model.startswith(("gpt-5", "o1", "o3", "o4")):
        request["reasoning"] = {"effort": reasoning_effort}

    response = client_oa.responses.create(**request)
    return (getattr(response, "output_text", None) or "").strip()


def _matches_hard_include(title: str, abstract_en: str, prefilter: dict) -> str:
    """Return a configured rule name when a deterministic include rule matches."""
    combined = f"{title}\n{abstract_en[:4000]}"

    for item in prefilter.get("hard_include_patterns") or []:
        if isinstance(item, str):
            pattern = item
            field = "all"
            rule = "hard_include"
        elif isinstance(item, dict):
            pattern = str(item.get("pattern") or "")
            field = str(item.get("field") or "all").lower()
            rule = str(item.get("rule") or "hard_include")
        else:
            continue

        if not pattern:
            continue
        if field == "title":
            haystack = title
        elif field == "abstract":
            haystack = abstract_en
        else:
            haystack = combined
        try:
            if re.search(pattern, haystack, flags=re.IGNORECASE):
                return rule
        except re.error as exc:
            print(f"[WARN] invalid hard_include pattern={pattern!r} error={exc}")

    return ""


def _model_candidates(provider: str, prefilter: dict) -> list[str]:
    if provider == "github":
        primary = (
            prefilter.get("model")
            or secrets.get("classifier_model")
            or "openai/gpt-4.1-nano"
        )
        fallbacks = (
            prefilter.get("fallback_models")
            or secrets.get("classifier_fallback_models")
            or []
        )
    else:
        primary = (
            prefilter.get("model")
            or secrets.get("openai_prefilter_model")
            or secrets.get("openai_summary_model")
            or secrets.get("openai_model")
            or "gpt-5.6-luna"
        )
        fallbacks = prefilter.get("fallback_models") or []

    if isinstance(fallbacks, str):
        fallbacks = [fallbacks]

    result: list[str] = []
    for candidate in [primary, *fallbacks]:
        candidate = str(candidate or "").strip()
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def classify_relevance(title: str, abstract_en: str, journal: dict) -> tuple[str, str]:
    """Return ``(decision, reason)`` for an optional journal prefilter."""
    prefilter = journal.get("prefilter") or {}
    if not prefilter:
        return "relevant", "no_prefilter"

    if DRY_RUN and not DRY_RUN_CLASSIFY:
        return "relevant", "dry_run_classification_skipped"

    hard_include_rule = _matches_hard_include(title, abstract_en, prefilter)
    if hard_include_rule:
        return "relevant", hard_include_rule

    provider = str(
        prefilter.get("provider")
        or secrets.get("classifier_provider")
        or "openai"
    ).strip().lower()
    if provider not in {"openai", "github"}:
        return "uncertain", f"unsupported_provider:{provider}"

    target_scope = str(
        prefilter.get("target_scope")
        or "Use the configured research group's interests and the paper content."
    ).strip()
    reasoning_effort = str(prefilter.get("reasoning_effort") or "low").strip()

    system_msg = {
        "role": "system",
        "content": (
            "You classify newly published research papers for a Slack feed. "
            "Follow the user-provided target scope. Return JSON only and do not "
            "summarize the paper."
        ),
    }
    user_msg = {
        "role": "user",
        "content": (
            "Decide whether this paper should be posted.\n\n"
            f"Target scope:\n{target_scope}\n\n"
            "Choose uncertain rather than irrelevant when the available title or "
            "abstract is insufficient. Return exactly one compact JSON object:\n"
            '{"decision":"relevant|irrelevant|uncertain",'
            '"reason":"one short reason grounded in the title or abstract"}\n\n'
            f"Title:\n{title}\n\n"
            f"Abstract or summary:\n{abstract_en[:4000]}"
        ),
    }

    errors: list[str] = []
    for model in _model_candidates(provider, prefilter):
        try:
            if provider == "github":
                text = call_github_classifier(model, system_msg, user_msg)
            else:
                text = call_openai_classifier(
                    model,
                    system_msg,
                    user_msg,
                    reasoning_effort=reasoning_effort,
                )

            result = parse_json_object(text)
            decision = str(result.get("decision") or "").strip().lower()
            reason = str(result.get("reason") or "").strip()
            if decision not in {"relevant", "irrelevant", "uncertain"}:
                raise ValueError(f"invalid decision: {decision!r}")
            if not reason:
                raise ValueError("empty reason")

            if DRY_RUN:
                print(
                    "[DRY_RUN] relevance_classification "
                    f"journal={journal.get('title', '')} provider={provider} "
                    f"model={model} decision={decision} reason={reason} title={title}"
                )
            return decision, reason
        except Exception as exc:
            errors.append(f"{model}:{type(exc).__name__}")
            print(
                "[WARN] relevance_classification_failed "
                f"journal={journal.get('title', '')} provider={provider} "
                f"model={model} error={type(exc).__name__}"
            )

    # Fail open. ``prefilter_uncertain: skip`` can opt into stricter behavior.
    return "uncertain", "classifier_failed:" + ",".join(errors)
