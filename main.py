from __future__ import annotations

import logging
import time
from collections.abc import Callable

from slack_sdk import WebClient

from arxiv_sources import fetch_and_post_arxiv
from bot_config import config, secrets
from eartharxiv_sources import fetch_and_post_eartharxiv
from posted import load_posted_ids
from rss_sources import fetch_and_post_rss


SourceRunner = Callable[[WebClient, dict, int, dict], None]


def _get_slack_token(workspace_name: str) -> str:
    tokens = secrets.get("slack_api_tokens") or {}
    if not tokens and secrets.get("slack_api_token"):
        tokens = {"default": secrets.get("slack_api_token")}

    return tokens.get(workspace_name, "")


def _run_timed_source(
    label: str,
    runner: SourceRunner,
    client: WebClient,
    source_cfg: dict,
    hours_back: int,
    posted: dict,
) -> None:
    t = time.monotonic()
    runner(client, source_cfg, hours_back, posted)
    print(f"[TIMER] {label}={time.monotonic() - t:.1f}s")


def _iter_workspace_sources(ws: dict) -> list[tuple[str, SourceRunner, dict]]:
    sources: list[tuple[str, SourceRunner, dict]] = []

    if ws.get("journals"):
        sources.append(("journals", fetch_and_post_rss, ws["journals"]))

    if ws.get("arxiv"):
        sources.append(("arxiv", fetch_and_post_arxiv, ws["arxiv"]))

    if ws.get("eartharxiv"):
        sources.append(("eartharxiv", fetch_and_post_eartharxiv, ws["eartharxiv"]))

    return sources


def main() -> None:
    t_all = time.monotonic()

    hours_back = int(config.get("hours_back", 48))
    posted = load_posted_ids()
    print(f"[TIMER] loaded_posted={len(posted)}")

    for ws in config.get("workspaces", []):
        name = ws.get("name")

        token = _get_slack_token(name)
        if not token:
            logging.error("Slack token not found for workspace: %s", name)
            continue

        client = WebClient(token=token)

        for label, runner, source_cfg in _iter_workspace_sources(ws):
            _run_timed_source(label, runner, client, source_cfg, hours_back, posted)

    print(f"[TIMER] total={time.monotonic() - t_all:.1f}s")


if __name__ == "__main__":
    main()
