from __future__ import annotations

import os
import time

from bot_config import DRY_RUN, DRY_RUN_SUMMARIZE, client_oa, config, secrets


SUMMARY_MODEL = (
    os.getenv("OPENAI_SUMMARY_MODEL", "").strip()
    or str(secrets.get("openai_summary_model") or "").strip()
    or str(secrets.get("openai_model") or "").strip()
    or "gpt-5.6-luna"
)
SUMMARY_REASONING_EFFORT = (
    os.getenv("OPENAI_SUMMARY_REASONING_EFFORT", "").strip()
    or str(secrets.get("openai_summary_reasoning_effort") or "").strip()
    or "low"
)


def _summary_preferences() -> tuple[str, str]:
    settings = config.get("summarization") or {}
    language = str(settings.get("language") or "Japanese").strip()
    instructions = str(settings.get("instructions") or "").strip()
    return language, instructions


def summarize(title: str, abstract_en: str) -> str:
    language, custom_instructions = _summary_preferences()
    system_msg = {
        "role": "system",
        "content": (
            "You summarize academic paper abstracts accurately and concisely. "
            "Use only information stated in the title and abstract. Do not add "
            "claims, implications, or terminology that the source does not support."
        ),
    }
    user_msg = {
        "role": "user",
        "content": (
            f"Title:\n{title}\n\n"
            f"Abstract:\n{abstract_en}\n\n"
            f"Write the output in {language} using Slack mrkdwn.\n"
            "1. Start with a natural translation of the title in bold.\n"
            "2. Add four concise bullet points covering background, objective or "
            "method, main result, and significance.\n"
            "3. Preserve established technical terms and numerical results.\n"
            "4. If the abstract is missing or incomplete, translate only the title."
            + (
                f"\n\nAdditional instructions:\n{custom_instructions}"
                if custom_instructions
                else ""
            )
        ),
    }

    for attempt in range(1, 4):
        try:
            request = {
                "model": SUMMARY_MODEL,
                "input": [system_msg, user_msg],
                "text": {"verbosity": "low"},
            }
            if SUMMARY_MODEL.startswith(("gpt-5", "o1", "o3", "o4")):
                request["reasoning"] = {"effort": SUMMARY_REASONING_EFFORT}

            response = client_oa.responses.create(**request)
            text = (getattr(response, "output_text", None) or "").strip()
            if not text:
                raise RuntimeError("OpenAI returned an empty summary")
            return text
        except Exception as exc:
            print(
                f"[WARN] summary attempt={attempt}/3 model={SUMMARY_MODEL} "
                f"error={type(exc).__name__}"
            )
            if attempt < 3:
                time.sleep(5)

    raise RuntimeError(f"OpenAI summary failed after retries: model={SUMMARY_MODEL}")


def maybe_summarize(title: str, abstract_en: str) -> str:
    if DRY_RUN and not DRY_RUN_SUMMARIZE:
        return (
            "[DRY_RUN] Summary generation skipped. "
            "Set DRY_RUN_SUMMARIZE=true to test OpenAI summarization."
        )
    return summarize(title, abstract_en)
