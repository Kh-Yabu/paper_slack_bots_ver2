from __future__ import annotations

from bot_config import (
    DRY_RUN,
    DRY_RUN_CLASSIFY,
    client_github,
    client_oa,
    secrets,
)
from text_utils import parse_json_object


def call_github_classifier(model: str, system_msg: dict, user_msg: dict) -> str:
    """
    Call GitHub Models via OpenAI-compatible chat completions API.
    """
    if client_github is None:
        raise RuntimeError("GitHub Models client is not configured.")

    rsp = client_github.chat.completions.create(
        model=model,
        messages=[system_msg, user_msg],
        max_tokens=200,
    )

    return (rsp.choices[0].message.content or "").strip()


def call_openai_classifier(model: str, system_msg: dict, user_msg: dict) -> str:
    """
    Call existing OpenAI Responses API.
    """
    rsp = client_oa.responses.create(
        model=model,
        input=[system_msg, user_msg],
        text={"verbosity": "low"},
    )

    return (getattr(rsp, "output_text", None) or "").strip()

def classify_relevance(title: str, abstract_en: str, journal: dict) -> tuple[str, str]:
    """
    Classify whether a paper is relevant to the bot scope.

    Returns:
        (decision, reason)
        decision is one of: relevant, irrelevant, uncertain
    """
    prefilter = journal.get("prefilter") or {}
    if not prefilter:
        return "relevant", "no_prefilter"

    if DRY_RUN and not DRY_RUN_CLASSIFY:
        return "relevant", "dry_run_classification_skipped"

    provider = (
        prefilter.get("provider")
        or secrets.get("classifier_provider")
        or "openai"
    ).strip().lower()

    if provider == "github":
        model = (
            prefilter.get("model")
            or secrets.get("classifier_model")
            or "openai/gpt-4.1-nano"
        )
    else:
        model = (
            prefilter.get("model")
            or secrets.get("openai_prefilter_model")
            or secrets.get("openai_model")
        )

    target_scope = prefilter.get(
        "target_scope",
        "solid Earth geoscience, especially seismology, volcanology, geodesy, "
        "tectonics, faults, subduction zones, crust, lithosphere, mantle, "
        "solid Earth structure, deformation, gravity, and seismic observations.",
    )

    system_msg = {
        "role": "system",
        "content": (
            "You classify new journal papers for a Japanese Slack bot used by "
            "solid Earth science researchers. "
            "Return JSON only. Do not summarize the paper."
        ),
    }

    user_msg = {
        "role": "user",
        "content": (
            "Classify whether this paper should be posted to a Slack bot for "
            "solid Earth geoscience researchers.\n\n"
            f"Target scope:\n{target_scope}\n\n"
            "Relevant examples:\n"
            "- seismology, earthquakes, faults, slow slip, tremor\n"
            "- volcanoes, magma systems, caldera processes\n"
            "- geodesy, deformation, gravity, crustal motion\n"
            "- tectonics, subduction, rifting, mountain building\n"
            "- crust, lithosphere, mantle structure or dynamics\n"
            "- seismic tomography, receiver functions, anisotropy, rheology\n\n"
            "Usually irrelevant examples:\n"
            "- pure paleoclimate or ocean chemistry\n"
            "- pure cosmochemistry or meteorites with no solid-Earth link\n"
            "- biology, ecology, atmospheric science\n"
            "- editorial board, erratum, corrigendum, book review\n"
            "- purely petrological/geochemical papers with no tectonic, volcanic, "
            "mantle-dynamic, or solid-Earth geophysical implication\n\n"
            "If it is plausibly relevant, choose uncertain rather than irrelevant.\n\n"
            "Return exactly this JSON shape:\n"
            '{"decision":"relevant|irrelevant|uncertain","reason":"short reason"}\n\n'
            f"Title:\n{title}\n\n"
            f"Abstract or summary:\n{abstract_en[:4000]}"
        ),
    }

    try:
        if provider == "github":
            text = call_github_classifier(model, system_msg, user_msg)
        else:
            text = call_openai_classifier(model, system_msg, user_msg)

        obj = parse_json_object(text)

        decision = str(obj.get("decision", "")).strip().lower()
        reason = str(obj.get("reason", "")).strip()

        if decision not in {"relevant", "irrelevant", "uncertain"}:
            decision = "uncertain"
            reason = reason or "invalid_classifier_output"

        if DRY_RUN:
            print(
                f"[DRY_RUN] relevance_classification "
                f"journal={journal.get('title', '')} "
                f"provider={provider} "
                f"model={model} "
                f"decision={decision} "
                f"reason={reason} "
                f"title={title}"
            )

        return decision, reason

    except Exception as e:
        if DRY_RUN:
            print(
                f"[DRY_RUN] relevance_classification_failed "
                f"journal={journal.get('title', '')} "
                f"provider={provider} "
                f"model={model} "
                f"error={e} "
                f"title={title}"
            )

        # Fail open: do not drop a paper only because classification failed.
        return "uncertain", "classifier_failed"
