# Adding paper sources

Start with one source and a test Slack channel. Run with `DRY_RUN=true` before enabling real posts.

## Standard RSS or Atom feed

```yaml
workspaces:
  - name: default
    journals:
      - title: Example Journal
        rss_url: https://journal.example.org/rss/latest.xml
        slack_channel_id: C01234567
        abstract_tag: summary
```

Common Abstract fields are:

| Feed style | Try first |
|---|---|
| RSS with `<description>` | `abstract_tag: description` |
| Atom or feedparser summary | `abstract_tag: summary` |
| Feed with encoded full content | `abstract_tag: content` |

If the log shows empty or bibliographic-only Abstracts, add fallback sources:

```yaml
metadata_fallback:
  - openalex
  - crossref
abstract_fallback:
  - html
min_abstract_length: 500
```

OpenAlex and Crossref are matched conservatively. Adding the journal's `full_title` and `issn` improves matching.

## Filter a broad feed

Apply inexpensive rules before an LLM filter:

```yaml
- title: Broad Journal
  full_title: Journal of Broad Science
  issn: "1234-5678"
  rss_url: https://journal.example.org/feed.xml
  slack_channel_id: C01234567
  include_keywords:
    - battery
    - electrochemical
  exclude_title_patterns:
    - Editorial
    - Correction
    - Book Review
  metadata_fallback:
    - openalex
    - crossref
  prefilter:
    provider: openai
    target_scope: >
      Include experimental or computational studies of battery degradation,
      interfaces, and ion transport. Exclude grid-economics-only papers.
  prefilter_uncertain: post
```

Tips for `target_scope`:

- Describe the intended readers.
- List both included and excluded topics.
- Explain boundary cases that keywords alone cannot distinguish.
- Prefer `prefilter_uncertain: post` until you have reviewed dry-run results.

## arXiv

```yaml
arxiv:
  slack_channel_id: C01234567
  categories:
    - cs.LG
    - stat.ML
  keywords:
    - diffusion
    - foundation model
```

Find category IDs in the [arXiv category taxonomy](https://arxiv.org/category_taxonomy). Keywords are optional and matched against the Abstract.

## EarthArXiv

```yaml
eartharxiv:
  slack_channel_id: C01234567
  keywords:
    - earthquake
    - fault
```

EarthArXiv records are retrieved through OAI-PMH. Keywords are matched against title and Abstract.

## Springer Nature Meta API

1. Obtain a Springer Nature API key.
2. Add `springer_api_key` to local `secrets.yaml`, or add `SPRINGER_API_KEY` as a GitHub Actions repository secret.
3. Add a source:

```yaml
- title: Springer Example
  source_type: springer_api
  springer_queries:
    - '(issn:1234-5678 AND ("battery" OR "electrochemical"))'
  springer_page_size: 20
  springer_max_pages: 5
  slack_channel_id: C01234567
  metadata_fallback:
    - openalex
    - crossref
```

Use `scripts/estimate_springer_volume.sh` to estimate result volume and choose `springer_max_pages`:

```bash
export SPRINGER_API_KEY="your-key"
CONFIG_FILE=config.yaml DAYS=31 bash scripts/estimate_springer_volume.sh
```

## Copernicus recent-preprint page

```yaml
- title: Copernicus Example
  source_type: copernicus_recent
  copernicus_recent_url: https://journal.copernicus.org/
  copernicus_recent_max_entries: 100
  slack_channel_id: C01234567
  include_keywords:
    - your topic
```

This adapter parses a Copernicus recent-listing HTML page. Publisher layout changes can require parser updates, so always verify it with a dry run.

## AGU / Wiley keyword and taxonomy feeds

```yaml
- title: AGU Example
  source_type: agu_taxonomy
  slack_channel_id: C01234567
  agu_search_terms:
    - earthquake
    - seismic
  agu_taxonomies:
    - name: Verified taxonomy label
      concept_id: 123456
      group: Solid Earth
      direct_accept: false
  exclude_title_patterns:
    - Correction
    - Editorial
  prefilter:
    provider: openai
    target_scope: >
      Describe which AGU papers are useful to the group.
  prefilter_uncertain: post
```

The adapter:

1. Fetches every configured broad search term.
2. Fetches every configured taxonomy concept.
3. Merges duplicate records by DOI, then normalized URL.
4. Keeps matched search and taxonomy labels as relevance context.
5. Classifies, summarizes, and posts each unique paper once.

Wiley search identifiers are not universal standards. Verify every `concept_id` against the current publisher search page. An invalid or obsolete ID may return an empty feed.

## Validate a new source

Run tests first:

```bash
python -m pytest
```

Fetch and filter without API calls or Slack posts:

```bash
DRY_RUN=true python main.py
```

Include OpenAI classification and summaries without posting:

```bash
DRY_RUN=true DRY_RUN_CLASSIFY=true DRY_RUN_SUMMARIZE=true python main.py
```

Redirect a real post test to a dedicated channel:

```bash
OVERRIDE_SLACK_CHANNEL_ID=C0TESTCHANNEL python main.py
```

To avoid touching normal duplicate state during local experiments:

```bash
POSTED_FILE=posted_entries_test.txt DRY_RUN=true python main.py
```
