# Configuration Reference

This document describes all options in `config.yaml` and `secrets.yaml`.

## Global settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `hours_back` | int | `48` | How many hours back to fetch entries from each source |
| `keep_hours` | int | `2000` | How long to retain records in `posted_entries.txt` before pruning |

## Workspace

Each item under `workspaces` represents one Slack workspace.

| Key | Type | Description |
|-----|------|-------------|
| `name` | string | Workspace identifier; must match a key in `slack_api_tokens` |

A workspace may define any combination of `arxiv`, `eartharxiv`, and `journals`.

## arXiv

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `slack_channel_id` | string | yes | Slack channel to post to |
| `categories` | list | yes | arXiv categories (e.g. `physics.geo-ph`, `cs.AI`) |
| `keywords` | list | no | Case-insensitive abstract keyword filter |

## EarthArXiv

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `slack_channel_id` | string | yes | Slack channel to post to |
| `keywords` | list | no | Case-insensitive keyword filter on title + abstract |

## Journal entries (common fields)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `title` | string | — | Short label used in logs and posted-entry records |
| `full_title` | string | — | Full journal name for metadata lookup |
| `source_type` | string | `rss` | `rss`, `springer_api`, or `copernicus_recent` |
| `rss_url` | string | — | Feed URL (required for `rss` and `copernicus_recent`) |
| `slack_channel_id` | string | — | Slack channel to post to |
| `link_tag` | string | `link` | RSS field for paper URL |
| `abstract_tag` | string | `summary` | RSS field for abstract text |
| `include_keywords` | list | — | First-stage filter: keyword must appear in title or abstract |
| `exclude_title_patterns` | list | — | Skip entries whose title contains any of these strings |
| `metadata_fallback` | list | — | Sources to fetch missing abstracts: `openalex`, `crossref`, `html` |
| `abstract_fallback` | list | — | Additional abstract sources (journal-specific) |
| `min_abstract_length` | int | — | Skip entries with shorter abstracts after enrichment |
| `date_strategy` | string | `entry` | How to determine publication date: `entry` or `feed_last_build` |
| `issn` / `eissn` | string | — | Helps Crossref / OpenAlex matching |
| `prefilter` | object | — | LLM relevance filter (see below) |
| `prefilter_uncertain` | string | `post` | Action for uncertain papers: `post` or `skip` |

## LLM prefilter

Used for broad journal feeds where keyword filtering alone is insufficient.

```yaml
prefilter:
  method: llm
  provider: github          # github | openai
  model: openai/gpt-4.1-nano  # optional override
  target_scope: >
    Describe your target research field in plain language.
    Be explicit about what is relevant, irrelevant, and uncertain.
```

The classifier returns one of:

- `relevant` — paper is posted
- `irrelevant` — paper is skipped
- `uncertain` — handled according to `prefilter_uncertain`

## Springer Nature API (`source_type: springer_api`)

Requires `springer_api_key` in `secrets.yaml`.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `springer_queries` | list | — | Springer Meta API query strings |
| `springer_page_size` | int | `25` | Records per API page |
| `springer_max_pages` | int | `1` | Maximum pages to fetch per query |

Example query:

```yaml
springer_queries:
  - '(issn:0028-0836 AND ("earthquake" OR "seismic"))'
```

## Copernicus recent listing (`source_type: copernicus_recent`)

Scrapes the recent-preprint HTML page instead of a standard RSS feed.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `copernicus_recent_url` | string | — | Journal root URL |
| `copernicus_recent_max_entries` | int | `100` | Maximum entries to scrape |

## secrets.yaml

| Key | Required | Description |
|-----|----------|-------------|
| `openai_api_key` | yes | OpenAI API key for summarization |
| `openai_model` | yes | Model for summarization (e.g. `gpt-4o-mini`) |
| `slack_api_token` | yes* | Bot token for a single workspace |
| `slack_api_tokens` | yes* | Map of workspace name → bot token |
| `classifier_provider` | no | Default LLM provider for prefilter: `github` or `openai` |
| `classifier_model` | no | Default model for GitHub Models prefilter |
| `github_token` | if using GitHub Models | GitHub personal access token or `${{ github.token }}` in Actions |
| `springer_api_key` | if using Springer API | Springer Nature Meta API key |

\* Provide either `slack_api_token` or `slack_api_tokens`.

## Environment variables

These override file paths and runtime behavior. Useful for local testing and CI.

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIG_FILE` | `config.yaml` | Path to config file |
| `SECRETS_FILE` | `secrets.yaml` | Path to secrets file |
| `POSTED_FILE` | `posted_entries.txt` | Path to posted-entry log |
| `DRY_RUN` | `false` | Skip Slack posting; print what would be posted |
| `DRY_RUN_SUMMARIZE` | `false` | When `DRY_RUN=true`, still call OpenAI for summarization |
| `DRY_RUN_CLASSIFY` | `false` | When `DRY_RUN=true`, still call LLM for prefilter |
| `OVERRIDE_SLACK_CHANNEL_ID` | — | Redirect all posts to this channel (testing) |
| `GITHUB_TOKEN` | — | Fallback for `github_token` in secrets |
| `SPRINGER_API_KEY` | — | Fallback for `springer_api_key` in secrets |

### Recommended dry-run command

```bash
DRY_RUN=true DRY_RUN_CLASSIFY=true DRY_RUN_SUMMARIZE=true python main.py
```

## posted_entries.txt

The bot records every processed entry to avoid duplicate posts. Each line is tab-separated:

```
<entry_id>	<posted_at_jst>	<status>	<journal>	<reason>
```

- `entry_id`: normalized URL or DOI
- `status`: `posted`, `skipped`, etc.
- `reason`: why the entry was posted or skipped (e.g. prefilter decision)

Old records are pruned automatically based on `keep_hours`.
