# GitHub Actions Setup

The included workflow runs the bot on a schedule and commits updated `posted_entries.txt` to prevent duplicate posts across runs.

## Workflow file

See [`.github/workflows/post_papers.yml`](../.github/workflows/post_papers.yml).

## Schedule

The default cron runs four times daily (UTC):

```yaml
schedule:
  - cron: '20 21,1,5,9 * * *'
```

| UTC | JST (approx.) |
|-----|---------------|
| 21:20 | 06:20 next day |
| 01:20 | 10:20 |
| 05:20 | 14:20 |
| 09:20 | 18:20 |

Adjust the cron expression for your timezone and desired frequency.

## Required secrets

Configure these in **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `OPENAI_API_KEY` | OpenAI API key for summarization |
| `SLACK_API_TOKEN` | Slack bot token for the default workspace |
| `SLACK_API_TOKEN_OTHER` | (optional) Token for additional workspaces |
| `SPRINGER_API_KEY` | (optional) Springer Nature Meta API key |

The workflow generates `secrets.yaml` at runtime from these secrets.

## Runner

The template workflow uses `ubuntu-latest`. For production you may prefer:

- **Self-hosted runner** — useful when feeds block cloud IP ranges
- **Scheduled cron on your own server** — run `python main.py` via cron instead

To switch to a self-hosted runner, change:

```yaml
runs-on: self-hosted
```

## Posted-entry persistence

After each run, the workflow commits `posted_entries.txt` if it changed:

```
chore: update posted_entries.txt [skip ci]
```

This prevents re-posting the same papers on the next scheduled run. Make sure the workflow has `contents: write` permission and can push to the branch it runs on.

## Dry-run flags in CI

The workflow sets environment variables to control behavior:

| Variable | Typical production value | Description |
|----------|------------------------|-------------|
| `DRY_RUN` | `false` | Actually post to Slack |
| `DRY_RUN_SUMMARIZE` | `true` | Call OpenAI for summarization |
| `DRY_RUN_CLASSIFY` | `true` | Call LLM for prefilter |

For initial testing, set `DRY_RUN=true` to inspect logs without posting.

## Manual trigger

The workflow supports `workflow_dispatch` — run it manually from the Actions tab to test changes.

## Branch setup

By default the workflow pushes to the same branch it runs on. If you use a dedicated deployment branch (e.g. `main` for public, `production` for your lab), update the push target in the commit step accordingly.
