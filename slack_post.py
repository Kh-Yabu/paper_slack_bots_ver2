from __future__ import annotations

import time
import logging
import html as html_lib

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from bot_config import DRY_RUN, OVERRIDE_SLACK_CHANNEL_ID
from text_utils import clean_abstract, is_bibliographic_metadata, text_from_maybe_html

_LAST_POST_AT_BY_CHANNEL: dict[str, float] = {}
SLACK_POST_INTERVAL_SECONDS = 2

SLACK_SECTION_TEXT_LIMIT = 3000


def truncate_for_slack_block(text: str, max_len: int) -> str:
    text = text or ""
    if len(text) <= max_len:
        return text

    suffix = "\n…（Slackの文字数制限により省略）"
    keep = max(0, max_len - len(suffix))
    return text[:keep].rstrip() + suffix

def wait_for_slack_rate_limit(channel: str) -> None:
    now = time.monotonic()
    last = _LAST_POST_AT_BY_CHANNEL.get(channel, 0.0)
    wait = SLACK_POST_INTERVAL_SECONDS - (now - last)

    if wait > 0:
        time.sleep(wait)

    _LAST_POST_AT_BY_CHANNEL[channel] = time.monotonic()

def escape_slack_text(text: str) -> str:
    """
    Escape text for Slack mrkdwn outside link syntax.
    Slack requires &, <, > to be escaped when they are literal text.
    """
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def slack_link_label(text: str) -> str:
    """
    Sanitize a title used as the label part of Slack's <url|label> syntax.
    HTML tags such as <sub> break mrkdwn links unless removed first.
    """
    label = text_from_maybe_html(text)
    label = html_lib.unescape(label)
    label = " ".join(label.split())

    # The pipe character terminates Slack's <url|label> syntax.
    label = label.replace("|", "／")

    return escape_slack_text(label)


def post(
    client: WebClient,
    channel: str,
    *,
    title: str,
    link: str,
    summary: str,
    abstract: str,
) -> bool:
    try:
        clean_abs = clean_abstract(abstract)
        include_abstract = bool(clean_abs) and not is_bibliographic_metadata(clean_abs)
        display_title = slack_link_label(title)

        summary = truncate_for_slack_block(summary, SLACK_SECTION_TEXT_LIMIT)
        if OVERRIDE_SLACK_CHANNEL_ID:
            channel = OVERRIDE_SLACK_CHANNEL_ID
        if not channel:
            logging.error("Slack channel ID is missing")
            return False

        if DRY_RUN:
            print("[DRY_RUN] would_post_to_slack")
            print(f"[DRY_RUN] channel={channel}")
            print(f"[DRY_RUN] title={display_title}")
            print(f"[DRY_RUN] link={link}")
            print(f"[DRY_RUN] summary={summary[:500]}")
            print(f"[DRY_RUN] abstract_length={len(clean_abs)}")
            print(f"[DRY_RUN] include_abstract={include_abstract}")
            return True

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*<{link}|{display_title}>*",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": summary,
                },
            },
        ]

        if include_abstract:
            abstract_header = "*Abstract*\n"
            abstract_text = abstract_header + truncate_for_slack_block(
                clean_abs,
                SLACK_SECTION_TEXT_LIMIT - len(abstract_header),
            )

            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": abstract_text,
                    },
                }
            )

        wait_for_slack_rate_limit(channel)
        
        client.chat_postMessage(
            channel=channel,
            text=f"{display_title}\n{summary}",
            blocks=blocks,
        )
        return True

    except SlackApiError as e:
        logging.error("Slack posting failed: %s", e)
        return False
    except Exception as e:
        logging.error("Slack request failed: %s", e)
        return False
