# Configuration reference

The bot reads public settings from `config.yaml` and credentials from `secrets.yaml`. Keep `secrets.yaml` private.

## Minimal configuration

```yaml
hours_back: 48
keep_hours: 2000
timezone: Asia/Tokyo

summarization:
  language: Japanese
  instructions: Write for researchers in computational biology.

workspaces:
  - name: default
    arxiv:
      slack_channel_id: C01234567
      categories: [q-bio.BM]
      keywords: [protein, structure prediction]
    journals: []
```

## Global settings

| Key | Type | Default | Description |
|---|---|---|---|
| `hours_back` | integer | `48` | Only process papers published within this many hours. Make this longer than the interval between scheduled runs. |
| `keep_hours` | integer | `2000` | Retain duplicate-prevention records for this many hours. |
| `timezone` | string | `Asia/Tokyo` | IANA timezone used for date comparisons and saved state, for example `UTC` or `America/New_York`. |

## Summarization

```yaml
summarization:
  language: Japanese
  instructions: >
    Write for graduate students in materials science.
    Preserve alloy names and measured values.
```

| Key | Default | Description |
|---|---|---|
| `language` | `Japanese` | Output language passed to the summary model. |
| `instructions` | empty | Optional audience, field, terminology, or style guidance. |

The standard output is a translated title followed by four concise bullets covering background, objective or method, result, and significance.

## Workspaces

Every item under `workspaces` represents one Slack workspace.

| Key | Required | Description |
|---|---|---|
| `name` | yes | Must match a key under `slack_api_tokens` in `secrets.yaml`. |
| `arxiv` | no | arXiv source settings. |
| `eartharxiv` | no | EarthArXiv source settings. |
| `journals` | no | A list of RSS/API sources. An empty list is valid. |

## arXiv

```yaml
arxiv:
  slack_channel_id: C01234567
  categories:
    - cs.AI
    - cs.LG
  keywords:
    - diffusion model
    - reinforcement learning
```

| Key | Required | Description |
|---|---|---|
| `slack_channel_id` | yes | Destination Slack channel ID. |
| `categories` | yes | arXiv category IDs. |
| `keywords` | no | Case-insensitive Abstract substrings. An empty list accepts every paper in the categories. |

## EarthArXiv

```yaml
eartharxiv:
  slack_channel_id: C01234567
  keywords:
    - earthquake
    - geodesy
```

`keywords` is matched case-insensitively against the title and Abstract. An empty list accepts all new records.

## Journal sources: common fields

```yaml
journals:
  - title: Example Journal
    full_title: Journal of Example Science
    rss_url: https://journal.example.org/rss/latest.xml
    slack_channel_id: C01234567
    abstract_tag: summary
    include_keywords:
      - target topic
    exclude_title_patterns:
      - Editorial
      - Correction
```

| Key | Default | Description |
|---|---|---|
| `title` | required | Short source name used in logs and state records. |
| `full_title` | `title` | Full journal name used in metadata matching. |
| `source_type` | `rss` | `rss`, `springer_api`, `copernicus_recent`, or `agu_taxonomy`. |
| `rss_url` | none | RSS/Atom URL. Required by a standard RSS source. |
| `slack_channel_id` | required | Destination Slack channel ID. |
| `abstract_tag` | `summary` | Feed field containing the Abstract: commonly `summary`, `description`, or `content`. |
| `include_keywords` | empty | At least one case-insensitive keyword must appear in title or available feed text. Empty accepts all. |
| `exclude_title_patterns` | empty | Skip titles containing any configured string. |
| `date_strategy` | `entry` | Use `feed_last_build` only when entries have no per-item publication date. |
| `feed_timeout` | `30` | Feed request timeout in seconds. |

## Abstract and DOI enrichment

```yaml
metadata_fallback:
  - openalex
  - crossref
abstract_fallback:
  - html
min_abstract_length: 500
metadata_timeout: 5
html_timeout: 8
issn: "1234-5678"
```

| Key | Description |
|---|---|
| `metadata_fallback` | Metadata services used to recover a DOI or Abstract. `crossref` plus `openalex` first resolves the DOI, then performs the more reliable OpenAlex DOI lookup. |
| `abstract_fallback` | Add `html` to try extracting an Abstract from the article page. |
| `min_abstract_length` | Enrichment runs when the current Abstract is shorter than this value. |
| `issn` / `crossref_issn` | Narrows Crossref matching. |
| `metadata_match_threshold` | Minimum Crossref title similarity; default `0.92`. |
| `metadata_timeout` | Metadata request timeout in seconds. |
| `html_timeout` | Publisher HTML request timeout in seconds. |

Transient API errors do not mark the paper as processed, so a later scheduled run can retry it.

## Optional LLM relevance filter

```yaml
prefilter:
  provider: openai
  model: gpt-5.6-luna
  reasoning_effort: low
  target_scope: >
    Include papers about battery degradation and electrochemical interfaces.
    Exclude studies that only discuss grid economics.
  fallback_models:
    - gpt-5.6-terra
prefilter_uncertain: post
```

| Key | Default | Description |
|---|---|---|
| `provider` | value in `secrets.yaml`, then `openai` | `openai` or `github`. |
| `model` | provider default | Per-source model override. |
| `reasoning_effort` | `low` | OpenAI reasoning effort. |
| `target_scope` | generic | Plain-language relevant and irrelevant criteria. Be explicit. |
| `fallback_models` | empty | Models tried in order if the primary model fails. |
| `prefilter_uncertain` | `post` | Use `skip` for strict filtering. |

The classifier returns `relevant`, `irrelevant`, or `uncertain`. If every model call fails, the decision is `uncertain` so that an API problem does not silently discard a paper.

### Deterministic hard includes

Use these sparingly when some keywords must always pass without an LLM call.

```yaml
prefilter:
  provider: openai
  target_scope: Papers about public research datasets.
  hard_include_patterns:
    - field: title       # title | abstract | all
      pattern: "open (data|dataset)"
      rule: open_dataset
```

Patterns are case-insensitive regular expressions.

## Springer Nature Meta API

Requires `springer_api_key` in `secrets.yaml` or the `SPRINGER_API_KEY` environment variable.

```yaml
- title: Springer Example
  source_type: springer_api
  springer_queries:
    - '(issn:1234-5678 AND ("battery" OR "electrochemical"))'
  springer_page_size: 20
  springer_max_pages: 5
  slack_channel_id: C01234567
```

| Key | Default | Description |
|---|---|---|
| `springer_queries` | none | One or more Meta API query strings. |
| `springer_query` | none | Single-query alternative. |
| `springer_page_size` | `20` | Results per page. |
| `springer_max_pages` | `5` | Maximum pages fetched per query. |
| `springer_timeout` | `10` | Request timeout in seconds. |
| `springer_date_filter_field` | `onlinedate` | API date field: `onlinedate`, `date`, or `year`. |
| `springer_date_filter_buffer_hours` | `24` | Extra time around the requested date window. |

If no query is given and `issn` is present, the bot uses `issn:<value>`.

## Copernicus recent listing

```yaml
- title: Copernicus Example
  source_type: copernicus_recent
  copernicus_recent_url: https://example.copernicus.org/
  copernicus_recent_max_entries: 100
  slack_channel_id: C01234567
```

This source parses the journal's recent-preprint HTML page instead of a normal feed.

## AGU / Wiley keyword and taxonomy search

```yaml
- title: AGU Example
  source_type: agu_taxonomy
  slack_channel_id: C01234567
  agu_search_terms:
    - earthquake
    - seismic
  agu_taxonomies:
    - name: Seismology
      concept_id: 123456
      group: Solid Earth
      direct_accept: false
  prefilter:
    provider: openai
    target_scope: Papers relevant to the group's AGU interests.
```

The bot fetches each broad keyword and taxonomy feed, merges repeated records by DOI or normalized URL, and carries the matched terms into the relevance prompt. `concept_id` values are publisher identifiers and must be verified against the current Wiley/AGU search site. Set `direct_accept: true` only for a taxonomy that should bypass LLM classification.

## `secrets.yaml`

Start by copying `secrets.example.yaml`.

```yaml
openai_api_key: "..."
openai_summary_model: gpt-5.6-luna
openai_summary_reasoning_effort: low
openai_prefilter_model: gpt-5.6-luna

slack_api_tokens:
  default: xoxb-...
```

Optional keys:

| Key | Description |
|---|---|
| `classifier_provider` | Default `openai` or `github`. |
| `classifier_model` | Default GitHub Models model name. |
| `classifier_fallback_models` | GitHub Models fallback list. |
| `github_token` | Required when using GitHub Models locally. GitHub Actions supplies its built-in token. |
| `springer_api_key` | Required for Springer API sources. |
| `slack_api_token` | Backward-compatible single-workspace token; `slack_api_tokens` is clearer. |

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `CONFIG_FILE` | `config.yaml` | Alternate config path. |
| `SECRETS_FILE` | `secrets.yaml` | Alternate secrets path. |
| `POSTED_FILE` | `posted_entries.txt` | Alternate duplicate state path. |
| `DRY_RUN` | `false` | Print Slack messages instead of posting and do not save state. |
| `DRY_RUN_SUMMARIZE` | `false` | Allow summary API calls during dry run. |
| `DRY_RUN_CLASSIFY` | `false` | Allow relevance API calls during dry run. |
| `OVERRIDE_SLACK_CHANNEL_ID` | empty | Redirect every real post to one test channel. |
| `OPENAI_SUMMARY_MODEL` | secret/default | Override summary model. |
| `OPENAI_SUMMARY_REASONING_EFFORT` | `low` | Override summary reasoning effort. |
| `GITHUB_TOKEN` | empty | Fallback GitHub Models token. |
| `SPRINGER_API_KEY` | empty | Fallback Springer API key. |
| `OPENALEX_API_KEY` | empty | Optional OpenAlex key. |
| `OPENALEX_MAILTO` | empty | Optional contact email for OpenAlex polite-pool usage. |
| `BOT_TIMEZONE` | value in `config.yaml` | Override the configured IANA timezone. |

## `posted_entries.txt`

The bot records processed papers to prevent duplicate posts. Each tab-separated line contains:

```text
<entry_id>  <date/time>  <status>  <source>  <reason>
```

DOI and URL aliases may point to the same canonical record. Records older than `keep_hours` are pruned automatically.
