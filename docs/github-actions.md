# GitHub Actions setup

The included workflow installs the bot, runs tests, fetches new papers, posts them to Slack, and commits `posted_entries.txt` so later runs do not post the same paper again.

## Enable Actions

After forking the repository, open the **Actions** tab. GitHub may ask you to enable workflows for the fork.

The workflow file is [`.github/workflows/post_papers.yml`](../.github/workflows/post_papers.yml).

## Add repository secrets

Open:

**Settings → Secrets and variables → Actions → New repository secret**

Required:

| Secret | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key used for summaries and the default relevance filter. |
| `SLACK_API_TOKEN` | Slack bot token for the workspace named `default`. |

Optional:

| Secret | Description |
|---|---|
| `SPRINGER_API_KEY` | Required only for `source_type: springer_api`. |
| `OPENALEX_API_KEY` | Optional OpenAlex API key. |

GitHub's official UI steps are in [Using secrets in GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

## First run

1. Open **Actions**.
2. Select **Post new papers to Slack**.
3. Select **Run workflow**.
4. Leave `dry_run` enabled.
5. Inspect the **Run bot** log.

Dry run does not call OpenAI, post to Slack, or save duplicate state. When the configuration looks correct, run manually with `dry_run` disabled.

## Schedule

The default schedule runs four times per day:

```yaml
schedule:
  - cron: "20 21,1,5,9 * * *"
```

GitHub cron values use UTC.

| UTC | Japan time (JST) |
|---|---|
| 21:20 | 06:20 next day |
| 01:20 | 10:20 |
| 05:20 | 14:20 |
| 09:20 | 18:20 |

Change the cron expression in the workflow if needed. Keep `hours_back` in `config.yaml` longer than the gap between runs so a delayed workflow does not miss papers.

## Generated `secrets.yaml`

The workflow creates a temporary `secrets.yaml` inside the runner. The file is not committed. To change summary models or add another Slack workspace, edit this block in the workflow:

```yaml
openai_api_key: "${{ secrets.OPENAI_API_KEY }}"
openai_summary_model: "gpt-5.6-luna"

slack_api_tokens:
  default: "${{ secrets.SLACK_API_TOKEN }}"
```

For another workspace:

1. Create a repository secret such as `SLACK_API_TOKEN_LAB_B`.
2. Add a matching token entry to the workflow:

```yaml
slack_api_tokens:
  default: "${{ secrets.SLACK_API_TOKEN }}"
  lab_b: "${{ secrets.SLACK_API_TOKEN_LAB_B }}"
```

3. Add `name: lab_b` under `workspaces` in `config.yaml`.

## Duplicate-prevention state

After a successful real run, the workflow commits only `posted_entries.txt`. The workflow has:

```yaml
permissions:
  contents: write
```

If your repository rules block direct pushes to the default branch, choose one of these approaches:

- Allow the GitHub Actions bot to update `posted_entries.txt`.
- Run the bot from a dedicated branch that permits state commits.
- Replace Git-based state with an external database or object store.

The last option requires code changes and is outside the starter setup.

## GitHub Models relevance filter

The workflow grants `models: read` and writes its built-in `${{ github.token }}` to the temporary secrets file. To use it for a journal, set:

```yaml
prefilter:
  provider: github
  model: openai/gpt-4.1-nano
  target_scope: Your relevance rules.
```

OpenAI remains the starter default because the same API key is already required for summaries.

## Common failures

### `Resource not accessible by integration` while saving state

Check workflow `contents: write` permission and repository branch rules.

### Secrets appear empty

Confirm the names match exactly. GitHub returns an empty string for a missing secret. Secrets are also not provided to workflows triggered from untrusted forks.

### The scheduled workflow does not run

Confirm Actions are enabled, the workflow exists on the default branch, and the repository has not had scheduled workflows disabled because of inactivity.

### A feed works locally but not on GitHub-hosted runners

Some publishers block or throttle cloud IP addresses. Use another source adapter, add metadata fallback, reduce request volume, or move `runs-on` to a properly maintained self-hosted runner.
