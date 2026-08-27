# Paper Slack Bot

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[English](README.md)

arXiv、EarthArXiv、学術誌の RSS/API から新着論文を集め、必要な論文だけを選び、OpenAI で要約して Slack に投稿するボットです。GitHub Actions を使えば、自分のパソコンを起動したままにせず無料枠内で定期実行できます。

研究分野は固定されていません。`config.yaml` のカテゴリ、キーワード、判定基準、要約言語を変更すれば、自然科学・工学・医学・情報科学などに利用できます。

## できること

- arXiv のカテゴリとキーワードによる新着検索
- EarthArXiv のキーワード検索
- 一般的な RSS / Atom フィードの取得
- Springer Nature Meta API、Copernicus 最新一覧、AGU/Wiley 検索フィードへの対応
- RSS に Abstract がない場合の OpenAlex / Crossref / 論文ページからの補完
- キーワードと任意の LLM 判定による絞り込み
- OpenAI Responses API による指定言語での要約
- DOI と URL を使った重複投稿の防止
- 複数 Slack ワークスペースへの投稿
- Slack に投稿しないドライラン

## 動作の流れ

```text
論文を取得
  ↓
期間・重複・除外タイトルを確認
  ↓
キーワードで絞り込み
  ↓
不足している Abstract を補完
  ↓
必要なら LLM で関連度を判定
  ↓
OpenAI で要約 → Slack へ投稿
  ↓
posted_entries.txt に記録
```

## 最初に必要なもの

- GitHub アカウント
- Slack ワークスペースで App を追加できる権限
- OpenAI API キー
- ローカルで試す場合は Python 3.10 以上と Git

OpenAI API と ChatGPT の有料プランは別サービスです。ChatGPT Plus などを契約していても、API の利用設定と料金は別途必要です。

## 5ステップで始める

### 1. このリポジトリを自分の GitHub にコピーする

GitHub 右上の **Fork** を押すのが最も簡単です。ローカルでも編集したい場合は、Fork したリポジトリを clone します。

```bash
git clone https://github.com/YOUR_NAME/paper_slack_bots_ver2.git
cd paper_slack_bots_ver2
```

### 2. Slack App を作る

1. [Slack App 管理画面](https://api.slack.com/apps)で **Create New App** を選びます。
2. **From scratch** を選び、名前と投稿先ワークスペースを指定します。
3. 左側の **OAuth & Permissions** を開きます。
4. **Bot Token Scopes** に `chat:write` を追加します。
5. **Install to Workspace**（再設定時は **Reinstall to Workspace**）を押します。
6. 表示された **Bot User OAuth Token**（`xoxb-` で始まる文字列）を安全な場所へコピーします。
7. 投稿先 Slack チャンネルを開き、チャンネルのメンバー追加画面から作成した App を追加します。
8. チャンネル詳細の下部に表示されるチャンネル ID（例: `C01234567`）をコピーします。

App をチャンネルへ追加して使う場合、通常必要な権限は `chat:write` だけです。Slack の公式手順は [Sending and scheduling messages](https://docs.slack.dev/messaging/sending-and-scheduling-messages/) を参照してください。

### 3. OpenAI API キーを用意する

[OpenAI API のダッシュボード](https://platform.openai.com/api-keys)で API キーを作成します。キーは作成直後に一度しか表示されない場合があるため、安全な場所へ保存してください。

このリポジトリは OpenAI の Responses API を利用します。初期設定は多数の論文を処理しやすい `gpt-5.6-luna`、reasoning effort は `low` です。モデルや effort は `secrets.yaml` または GitHub Actions の workflow で変更できます。モデル設定の考え方は [OpenAI の Model guidance](https://developers.openai.com/api/docs/guides/latest-model) を参照してください。

### 4. `config.yaml` を編集する

最低限、次を自分用に置き換えます。

```yaml
summarization:
  language: Japanese
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

- `slack_channel_id`: 手順2で調べた Slack チャンネル ID
- `categories`: 監視する [arXiv カテゴリ](https://arxiv.org/category_taxonomy)
- `keywords`: Abstract にいずれかが含まれる論文だけを対象にします。空のリストならカテゴリ内の全論文が対象です。
- `summarization.language`: 要約する言語
- `summarization.instructions`: 分野、読者、用語などの追加指示

RSS や API の追加例は [論文ソースの追加方法](docs/adding-sources.md)、全設定項目は [設定リファレンス](docs/configuration.md) にあります。

### 5. GitHub Actions の Secrets を登録する

Fork したリポジトリで次の画面を開きます。

**Settings → Secrets and variables → Actions → New repository secret**

次の2つを登録します。

| Name | Value |
|---|---|
| `OPENAI_API_KEY` | 手順3で作成した OpenAI API キー |
| `SLACK_API_TOKEN` | 手順2で取得した `xoxb-...` トークン |

利用する機能に応じて次も追加できます。

| Name | 必要になる場合 |
|---|---|
| `SPRINGER_API_KEY` | `source_type: springer_api` を使う場合 |
| `OPENALEX_API_KEY` | OpenAlex の検索上限を増やしたい場合 |

GitHub の公式手順は [Using secrets in GitHub Actions](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets) を参照してください。値を `config.yaml` や workflow ファイルへ直接書かないでください。

登録後、**Actions → Post new papers to Slack → Run workflow** を開きます。最初は `dry_run: true` のまま実行してください。ログを確認して問題がなければ `dry_run: false` で実行できます。その後は workflow 内の schedule に従って自動実行されます。

詳しくは [GitHub Actions の設定](docs/github-actions.md) を参照してください。

## ローカルで試す

### インストール

macOS / Linux:

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

`secrets.yaml` の `openai_api_key` と `slack_api_tokens.default` を編集します。このファイルは `.gitignore` に含まれており、GitHub へ送られません。

### 安全に確認する

OpenAI を呼ばず、Slack にも投稿しない確認:

```bash
DRY_RUN=true python main.py
```

OpenAI の分類と要約まで確認するが、Slack には投稿しない確認:

```bash
DRY_RUN=true DRY_RUN_CLASSIFY=true DRY_RUN_SUMMARIZE=true python main.py
```

どちらのドライランも `posted_entries.txt` を更新しません。

実際に投稿する:

```bash
python main.py
```

Windows PowerShell では環境変数を次のように設定します。

```powershell
$env:DRY_RUN = "true"
python main.py
```

## 設定ファイルの役割

| ファイル | 内容 | GitHub に公開してよいか |
|---|---|---|
| `config.yaml` | 論文ソース、キーワード、チャンネル ID | 可 |
| `secrets.yaml` | API キー、Slack トークン | 不可 |
| `posted_entries.txt` | 重複投稿を防ぐ処理済み記録 | 可（workflow が自動更新） |
| `.github/workflows/post_papers.yml` | 定期実行の時刻と手順 | 可 |

`posted_entries.txt` には秘密情報を保存しません。削除すると、保存期間内の論文が再投稿される可能性があります。

## 対応する論文ソース

| 種類 | 設定 | 主な用途 |
|---|---|---|
| arXiv | `arxiv` セクション | カテゴリとキーワード検索 |
| EarthArXiv | `eartharxiv` セクション | 地球科学プレプリント |
| RSS / Atom | `source_type: rss`（省略可） | 一般的な学術誌フィード |
| Springer Nature Meta API | `source_type: springer_api` | ISSN やクエリによる取得 |
| Copernicus 最新一覧 | `source_type: copernicus_recent` | Copernicus の HTML 一覧 |
| AGU / Wiley 検索 | `source_type: agu_taxonomy` | キーワード・taxonomy feed の統合と DOI 重複排除 |

## LLM による関連度判定

RSS に分野外の論文が多い場合だけ `prefilter` を追加します。狭い RSS では省略する方が速く、API 費用もかかりません。

```yaml
prefilter:
  provider: openai
  target_scope: >
    Include papers about protein structure prediction and molecular simulation.
    Exclude purely clinical case reports without a computational method.
prefilter_uncertain: post
```

判定は `relevant`、`irrelevant`、`uncertain` の3種類です。API エラー時は論文を誤って捨てないよう `uncertain` として扱います。厳格に絞る場合だけ `prefilter_uncertain: skip` を使用してください。

## 複数の Slack ワークスペース

`config.yaml` の `workspaces[].name` と `secrets.yaml` の `slack_api_tokens` のキーを一致させます。

```yaml
# config.yaml
workspaces:
  - name: lab_a
    # ...
  - name: lab_b
    # ...
```

```yaml
# secrets.yaml
slack_api_tokens:
  lab_a: xoxb-...
  lab_b: xoxb-...
```

GitHub Actions で複数ワークスペースを使う場合は、ワークスペースごとの repository secret を作り、workflow が生成する `secrets.yaml` に対応する行を追加します。

## よくある問題

### Slack に `not_in_channel` と表示される

Slack App を投稿先チャンネルのメンバーに追加してください。

### Slack に `channel_not_found` と表示される

`config.yaml` の `slack_channel_id` がチャンネル名ではなく `C...` 形式の ID になっているか確認してください。非公開チャンネルでは App の招待も必要です。

### `Slack token not found for workspace` と表示される

`workspaces[].name` と `slack_api_tokens` のキーが一致していません。

### OpenAI の認証エラーになる

ローカルでは `secrets.yaml`、GitHub Actions では repository secret `OPENAI_API_KEY` を確認してください。API キーの前後に不要な空白を入れないでください。

### 同じ論文が再投稿される

`posted_entries.txt` が削除・巻き戻しされていないか確認してください。GitHub Actions の workflow には、このファイルを自動 commit するため `contents: write` 権限が必要です。

### RSS から Abstract を取得できない

出版社によって RSS の項目名が異なります。`abstract_tag` を確認し、`metadata_fallback: [openalex, crossref]` または `abstract_fallback: [html]` を試してください。

## 開発とテスト

```bash
python -m pytest
```

主なファイル:

```text
main.py               全ソースを順に実行
arxiv_sources.py      arXiv
eartharxiv_sources.py EarthArXiv
rss_sources.py        RSS / Springer / Copernicus / AGU
metadata.py           OpenAlex / Crossref による補完
classifier.py         任意の関連度判定
summarizer.py         OpenAI による要約
slack_post.py         Slack メッセージ作成・投稿
posted.py             重複防止記録
bot_config.py         YAML と環境変数の読み込み
```

## セキュリティと費用

- `secrets.yaml`、API キー、Slack token を commit しないでください。
- token が漏れた可能性があれば、Slack と OpenAI で直ちに再発行してください。
- 最初は必ず dry run とテスト用 Slack チャンネルを使ってください。
- API 利用料は、取得した論文数、LLM prefilter の有無、使用モデルによって変わります。
- 広いフィードでは `include_keywords` を先に設定すると、LLM 呼び出しを減らせます。

## 謝辞

このプロジェクトは [Butadiene/paper_slack_bots](https://github.com/Butadiene/paper_slack_bots) をもとにしています。現在のバージョンは Kh-Yabu が機能追加・保守しています。

## ライセンス

MIT License です。詳細は [LICENSE](LICENSE) を参照してください。
