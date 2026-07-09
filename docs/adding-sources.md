# Adding a New Source

This guide walks through adding a new journal or feed to your configuration.

## 1. Standard RSS feed

Most journals provide an RSS or Atom feed. Start with the simplest setup:

```yaml
- title: My Journal
  rss_url: https://journal.example.com/rss/advance-access.xml
  link_tag: link
  abstract_tag: summary
  slack_channel_id: C01234567
  exclude_title_patterns:
    - Editorial
    - Book review
```

### Finding the right RSS fields

Inspect the feed XML to determine field names:

```bash
curl -sL "https://journal.example.com/feed.rss" | head -100
```

Common mappings:

| Publisher | `link_tag` | `abstract_tag` |
|-----------|------------|----------------|
| Science / AAAS | `link` | `summary` |
| Wiley (AGU) | `link` | `content` |
| OUP | `link` | `summary` |
| ScienceDirect | `link` | `summary` |
| Copernicus OJS | `link` | `description` |

If the abstract is missing or truncated in RSS, add metadata fallback:

```yaml
metadata_fallback:
  - openalex
  - crossref
min_abstract_length: 500
```

## 2. Broad feed with keyword + LLM filter

For high-volume journals (Nature, Science, PNAS topic feeds), use a two-stage filter:

```yaml
- title: Nature
  full_title: Nature
  issn: "0028-0836"
  rss_url: https://www.nature.com/nature.rss
  slack_channel_id: C01234567
  include_keywords:
    - your-keyword
    - another-keyword
  exclude_title_patterns:
    - Editorial
    - News
    - Book Review
  metadata_fallback:
    - openalex
    - crossref
  prefilter:
    method: llm
    provider: github
    target_scope: >
      Describe precisely what papers in YOUR FIELD are relevant.
      List irrelevant categories explicitly.
  prefilter_uncertain: skip
```

**Tips for writing `target_scope`:**

- Be specific about what is relevant and irrelevant
- Mention edge cases (e.g. "climate papers are irrelevant unless they discuss crustal deformation")
- State how to handle uncertainty

## 3. Springer Nature Meta API

For journals without reliable RSS, or to query across ISSNs:

1. Register for a [Springer Nature Meta API](https://dev.springernature.com/) key
2. Add `springer_api_key` to `secrets.yaml`
3. Configure:

```yaml
- title: My Springer Journal
  source_type: springer_api
  springer_queries:
    - '(issn:1234-5678 AND ("keyword-one" OR "keyword-two"))'
  springer_page_size: 25
  springer_max_pages: 2
  slack_channel_id: C01234567
  metadata_fallback:
    - openalex
    - crossref
  prefilter:
    method: llm
    provider: github
    target_scope: >
      Papers relevant to [YOUR FIELD].
```

Use `scripts/estimate_springer_volume.sh` (requires `SPRINGER_API_KEY`) to estimate how many papers your query returns and tune `springer_max_pages`.

## 4. Copernicus recent preprints

For Copernicus journals where the standard RSS feed is unreliable:

```yaml
- title: EGUsphere
  source_type: copernicus_recent
  rss_url: https://egusphere.copernicus.org/
  copernicus_recent_url: https://egusphere.copernicus.org/
  copernicus_recent_max_entries: 100
  abstract_tag: summary
  slack_channel_id: C01234567
  include_keywords:
    - your-topic
  metadata_fallback:
    - openalex
    - crossref
```

## 5. arXiv category

Add or extend the `arxiv` section in your workspace:

```yaml
arxiv:
  slack_channel_id: C01234567
  categories:
    - cs.LG
    - stat.ML
  keywords:
    - transformer
    - diffusion
```

Browse categories at [arxiv.org/category_taxonomy](https://arxiv.org/category_taxonomy).

## 6. Test before going live

Always test new sources with dry run:

```bash
DRY_RUN=true DRY_RUN_CLASSIFY=true DRY_RUN_SUMMARIZE=true python main.py
```

Optionally redirect to a test channel:

```bash
DRY_RUN=false OVERRIDE_SLACK_CHANNEL_ID=C0TESTCHANNEL python main.py
```

Use a separate posted log during testing:

```bash
POSTED_FILE=posted_entries_test.txt DRY_RUN=true python main.py
```

## Example configurations

- **Starter template**: [`config.yaml`](../config.yaml)
- **Full solid-Earth geophysics setup**: [`examples/config.solid-earth.yaml`](../examples/config.solid-earth.yaml)
