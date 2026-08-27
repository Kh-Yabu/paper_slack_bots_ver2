# Paper Slack Bot

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[日本語](README.ja.md)

Paper Slack Bot collects newly published papers from arXiv, EarthArXiv, journal RSS/API sources, filters them, summarizes their abstracts with OpenAI, and posts them to Slack. GitHub Actions can run it on a schedule without keeping your computer online.

The bot is field-agnostic. Change the categories, keywords, relevance criteria, and summary language in `config.yaml` for any research area.

## Features

- arXiv category and keyword searches
- EarthArXiv keyword searches
- Standard RSS and Atom feeds
- Springer Nature Meta API, Copernicus recent listings, and AGU/Wiley search feeds
- Abstract recovery through OpenAlex, Crossref, and publisher HTML
- Fast keyword filtering plus an optional LLM relevance filter
- Configurable summaries through the OpenAI Responses API
- DOI/URL duplicate prevention
- Multiple Slack workspaces
- Dry runs that do not post to Slack

## How it works

```text
Fetch papers
  → check date, duplicates, and excluded titles
  → apply keyword filters
  → recover a missing abstract when possible
  → optionally classify relevance
  → summarize with OpenAI
  → post to Slack
  → record the item in posted_entries.txt
```

## What you need

- A GitHub account
- Permission to install an app in a Slack workspace
- An OpenAI API key
- Python 3.10+ and Git if you want to run locally

OpenAI API billing is separate from ChatGPT subscriptions.

## Quick start

### 1. Fork and clone

Click **Fork** on GitHub. To edit and test locally, clone your fork:

```bash
git clone https://github.com/YOUR_NAME/paper_slack_bots_ver2.git
cd paper_slack_bots_ver2
```

### 2. Create a Slack App

1. Open [Slack App Management](https://api.slack.com/apps) and select **Create New App**.
2. Choose **From scratch**, then select a name and workspace.
3. Open **OAuth & Permissions**.
4. Add `chat:write` under **Bot Token Scopes**.
5. Select **Install to Workspace** (or **Reinstall to Workspace** after a change).
6. Copy the **Bot User OAuth Token**, which begins with `xoxb-`.
7. Add the app to the channel where it should post.
8. Copy that channel's ID, such as `C01234567`, from the channel details.

When you invite the app to the destination channel, `chat:write` is normally the only required scope. See Slack's [message sending guide](https://docs.slack.dev/messaging/sending-and-scheduling-messages/) for the official setup.

### 3. Create an OpenAI API key

Create a key in the [OpenAI API dashboard](https://platform.openai.com/api-keys) and store it safely.

The starter configuration uses the Responses API with `gpt-5.6-luna` and low reasoning effort, which is suitable for a high-volume summary feed. You can change the model and effort in `secrets.yaml` or the GitHub Actions workflow. See OpenAI's [model guidance](https://developers.openai.com/api/docs/guides/latest-model).

### 4. Edit `config.yaml`

At minimum, replace the channel ID, arXiv categories, and keywords:

```yaml
summarization:
  language: English
  instructions: >
    Write for researchers in computational biology.

workspaces:
  - name: default
    arxiv:
      slack_channel_id: C01234567
      categories:
        - q-bio.BM
      keywords:
        - protein
        - structure prediction
    journals: []
```

An empty `keywords` list accepts every new paper in the selected categories. See [Adding paper sources](docs/adding-sources.md) for RSS/API examples and the [configuration reference](docs/configuration.md) for every option.

### 5. Add GitHub Actions secrets

In your fork, open:

**Settings → Secrets and variables → Actions → New repository secret**

Add these required secrets:

| Name | Value |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `SLACK_API_TOKEN` | The Slack `xoxb-...` token |

Optional secrets:

| Name | When needed |
|---|---|
| `SPRINGER_API_KEY` | When using `source_type: springer_api` |
| `OPENALEX_API_KEY` | To increase OpenAlex search capacity |

GitHub documents the UI in [Using secrets in GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets). Never put real tokens in `config.yaml` or the workflow file.

Open **Actions → Post new papers to Slack → Run workflow**. Keep `dry_run: true` for the first run. After checking the logs, run with `dry_run: false`. The schedule in the workflow then runs automatically.

See [GitHub Actions setup](docs/github-actions.md) for schedule and troubleshooting details.

## Run locally

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp secrets.example.yaml secrets.yaml
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item secrets.example.yaml secrets.yaml
```

Fill in `openai_api_key` and `slack_api_tokens.default` in `secrets.yaml`.

Check sources without calling OpenAI or posting:

```bash
DRY_RUN=true python main.py
```

Test classification and summarization without posting:

```bash
DRY_RUN=true DRY_RUN_CLASSIFY=true DRY_RUN_SUMMARIZE=true python main.py
```

Neither dry-run mode updates `posted_entries.txt`.

Post normally:

```bash
python main.py
```

## Important files

| File | Purpose | Safe to publish? |
|---|---|---|
| `config.yaml` | Sources, filters, channel IDs | Yes |
| `secrets.yaml` | API keys and Slack tokens | No |
| `posted_entries.txt` | Duplicate-prevention state | Yes |
| `.github/workflows/post_papers.yml` | Schedule and automation | Yes |

Deleting `posted_entries.txt` can cause recent papers to be posted again.

## Supported source types

| Source | Configuration | Use case |
|---|---|---|
| arXiv | `arxiv` section | Category and keyword searches |
| EarthArXiv | `eartharxiv` section | Earth-science preprints |
| RSS / Atom | `source_type: rss` or omitted | Standard journal feeds |
| Springer Nature Meta API | `source_type: springer_api` | ISSN or query-based retrieval |
| Copernicus recent | `source_type: copernicus_recent` | Copernicus HTML listings |
| AGU / Wiley search | `source_type: agu_taxonomy` | Merge keyword/taxonomy feeds and deduplicate by DOI |

## Optional LLM relevance filter

Add a `prefilter` only to broad feeds. Narrow feeds are faster and cheaper without it.

```yaml
prefilter:
  provider: openai
  target_scope: >
    Include papers about protein structure prediction and molecular simulation.
    Exclude purely clinical case reports without a computational method.
prefilter_uncertain: post
```

The classifier returns `relevant`, `irrelevant`, or `uncertain`. API failures fail open as `uncertain`; use `prefilter_uncertain: skip` only when strict filtering is more important than avoiding missed papers.

## Multiple Slack workspaces

Match each `workspaces[].name` in `config.yaml` with a key in `slack_api_tokens`:

```yaml
slack_api_tokens:
  lab_a: xoxb-...
  lab_b: xoxb-...
```

For GitHub Actions, create a repository secret for each token and extend the workflow's generated `secrets.yaml` block.

## Troubleshooting

- `not_in_channel`: invite the Slack App to the destination channel.
- `channel_not_found`: use the `C...` channel ID, not the channel name.
- `Slack token not found for workspace`: match the workspace name and token key.
- OpenAI authentication failure: check local `secrets.yaml` or the `OPENAI_API_KEY` repository secret.
- Duplicate posts: make sure `posted_entries.txt` was not deleted or rolled back.
- Missing RSS abstracts: check `abstract_tag`, then try `metadata_fallback: [openalex, crossref]` or `abstract_fallback: [html]`.

## Tests

```bash
python -m pytest
```

Main modules:

```text
main.py               Run all configured sources
arxiv_sources.py      arXiv
eartharxiv_sources.py EarthArXiv
rss_sources.py        RSS / Springer / Copernicus / AGU
metadata.py           OpenAlex and Crossref enrichment
classifier.py         Optional relevance classification
summarizer.py         OpenAI summarization
slack_post.py         Slack messages
posted.py             Duplicate-prevention state
bot_config.py         YAML and environment loading
```

## Security and cost

- Never commit `secrets.yaml`, API keys, or Slack tokens.
- Rotate credentials immediately if they may have leaked.
- Start with dry run and a dedicated test channel.
- API cost depends on paper volume, model choice, and whether LLM prefiltering is enabled.
- Use `include_keywords` before an LLM prefilter to reduce API calls.

## Credits

Based on [Butadiene/paper_slack_bots](https://github.com/Butadiene/paper_slack_bots). The current version is extended and maintained by Kh-Yabu.

## License

MIT License. See [LICENSE](LICENSE).
