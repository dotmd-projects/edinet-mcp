# EDINET財務諸表分析ツール

企業名を指定するだけで、[EDINET](https://disclosure2.edinet-fsa.go.jp/)（金融庁の開示システム）から有価証券報告書・半期報告書を取得し、財務三表（PL/BS/CF）のデータを抽出するツールです。

**2つの使い方**ができます:

| 使い方 | こんな人向け | 実行方法 |
|-------|------------|---------|
| 🖥️ **CLI** | 財務データをCSV/Excelで分析したい | `python -m edinet_analyzer --company トヨタ自動車` |
| 🤖 **MCPサーバー** | Claude Desktop / Claude Codeから自然言語で聞きたい | 「トヨタの直近の売上と利益を調べて」 |

## できること

- 企業名からのEDINETコード自動検索（「トヨタ自動車」のような通称でもOK）
- 有価証券報告書・半期報告書・四半期報告書（2024年廃止前の過去分）の検索
- XBRLから約80項目の財務データを抽出
  - **損益計算書 (PL)**: 売上高、営業利益、経常利益、当期純利益、販管費の内訳 等
  - **貸借対照表 (BS)**: 資産・負債・純資産の主要項目、借入金、利益剰余金 等
  - **キャッシュフロー (CF)**: 営業CF、投資CF、財務CF、設備投資、配当 等
- 複数企業・複数期間の比較（行=財務項目、列=期間の表形式）
- CSV / JSON / Excel への出力
- APIレスポンスの自動キャッシュ（**同じ検索の2回目以降は数秒で完了**）

## クイックスタート

### 1. インストール

Python 3.9以上（MCPサーバーを使う場合は3.10以上）が必要です。

```bash
git clone https://github.com/dotmd-projects/edinet-mcp.git
cd edinet-mcp

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

パッケージとしてインストールする場合（`edinet-analyzer` コマンドが使えるようになります）:

```bash
pip install -e .          # CLIのみ
pip install -e ".[mcp]"   # MCPサーバーも使う場合
```

### 2. EDINET APIキーの取得（無料）

<details>
<summary>取得手順を開く</summary>

1. **アカウント作成**
   [EDINET API 利用登録ページ](https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1) にアクセスし、氏名・メールアドレス・電話番号を入力して新規登録します。

2. **多要素認証**
   メールで届く確認コードを入力後、SMS認証を行います。電話番号は国番号 `+81` を選択し、先頭の `0` を省略して入力します（例: `80-XXXX-XXXX`）。

3. **ブラウザのポップアップ許可**
   APIキーはポップアップウィンドウで表示されます。ブラウザが `https://api.edinet-fsa.go.jp` のポップアップをブロックしている場合は、事前に許可設定を行ってください（Microsoft Edgeの利用も推奨されています）。

4. **APIキーの取得**
   認証完了後、連絡先を登録するとサブスクリプションキーが画面に表示されます。

詳細な仕様は [EDINET API仕様書 (Version 2)](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html) を参照してください。

</details>

取得したキーを `.env` に設定します:

```bash
cp .env.example .env
# .env を編集: EDINET_SUBSCRIPTION_KEY=取得したキー
```

### 3. 実行

```bash
python -m edinet_analyzer --company トヨタ自動車 --from 2025-06-01 --to 2025-06-30
```

## CLIの使い方

```bash
# 基本（結果をターミナルに表形式で表示）
python -m edinet_analyzer --company トヨタ自動車 --from 2025-06-01 --to 2025-06-30

# CSVに保存
python -m edinet_analyzer --company トヨタ自動車 --from 2025-06-01 --to 2025-06-30 -o output/toyota.csv

# 複数企業をまとめて取得
python -m edinet_analyzer --company トヨタ自動車 本田技研工業 -o output/results.csv

# JSON / Excel出力（拡張子から自動判定）
python -m edinet_analyzer --company トヨタ自動車 -o output/results.json
python -m edinet_analyzer --company トヨタ自動車 -o output/results.xlsx

# 詳細ログ
python -m edinet_analyzer --company トヨタ自動車 -v
```

### オプション一覧

| オプション | 説明 | デフォルト |
|-----------|------|-----------|
| `--company` | 分析対象の企業名（スペース区切りで複数指定可） | 必須 |
| `--from` | 検索開始日 (YYYY-MM-DD) | 1年前 |
| `--to` | 検索終了日 (YYYY-MM-DD) | 今日 |
| `-o, --output` | 出力ファイルパス (.csv / .json / .xlsx) | 標準出力 |
| `--format` | 出力形式を明示指定 | 拡張子から自動判定 |
| `-v, --verbose` | 詳細ログを出力 | off |

### 出力イメージ

行が財務項目、列が期間の表形式です。1つの有価証券報告書に含まれる過去期間のデータもまとめて取得されます。

```
企業名,項目,2023-04-01～2024-03-31,2024-04-01～2025-03-31
トヨタ自動車,売上高,17575593000000,18277671000000
トヨタ自動車,営業利益,3094495000000,2966857000000
...
```

## MCPサーバーの使い方

Claude DesktopやClaude Codeから、自然言語でEDINETの財務データにアクセスできます。

### Claude Desktopへの登録

`~/Library/Application Support/Claude/claude_desktop_config.json` に以下を追加し、Claude Desktopを再起動します:

```json
{
  "mcpServers": {
    "edinet-mcp": {
      "command": "/path/to/edinet_analyzer/.venv/bin/python",
      "args": ["/path/to/edinet_analyzer/server.py"],
      "env": {
        "EDINET_SUBSCRIPTION_KEY": "your_subscription_key_here"
      }
    }
  }
}
```

`/path/to/edinet_analyzer` は実際のインストール先に置き換えてください。

### 提供ツール

| ツール | 説明 | 質問例 |
|-------|------|--------|
| `search_company` | 企業名からEDINETコードを検索 | 「トヨタ自動車のEDINETコードを調べて」 |
| `list_financial_reports` | 指定期間の決算書類一覧を取得 | 「トヨタの2025年6月の決算書類を一覧して」 |
| `get_financial_data` | 財務データ (PL/BS/CF) を取得・億円単位で表示 | 「トヨタの直近の売上と利益を教えて」 |
| `compare_companies` | 複数企業の財務データを比較 | 「トヨタと本田の財務を比較して」 |

### リモートMCP（SSE）として起動する場合

```bash
python server.py --transport sse --port 8000
```

クライアント側の設定:

```json
{
  "mcpServers": {
    "edinet-mcp": { "url": "https://your-server-url/sse" }
  }
}
```

Claude Code の場合:

```bash
claude mcp add edinet-mcp --transport sse https://your-server-url/sse
```

## キャッシュの仕組み

EDINET APIへのアクセスは1秒1リクエストに制限しているため、初回検索は期間の営業日数分の時間がかかります（1ヶ月分で約20秒、1年分で約4分）。日付範囲は1〜2ヶ月以内を推奨します。なお、3月決算企業の有価証券報告書は通常6月に提出されます。

一方、取得したデータは `data/cache/` に自動保存されるため、**2回目以降の同じ検索はAPIアクセスなしで数秒で完了**します:

- `data/cache/documents/` — 日付ごとの書類一覧（過去日のみキャッシュ）
- `data/cache/*.xbrl` — ダウンロード済みのXBRL本体

キャッシュは `config/config.json` の `"cache_enabled": false` で無効化できます。

## プロジェクト構成

```
edinet_analyzer/
├── server.py               # MCPサーバーエントリーポイント (FastMCP, stdio/SSE)
├── edinet_analyzer/        # メインパッケージ
│   ├── __main__.py         # CLIエントリーポイント
│   ├── analyzer.py         # EDINET API連携・XBRL解析（コアロジック）
│   └── config.py           # 設定管理
├── config/
│   └── config.json         # 運用設定 (rate_limit, timeout等)
├── data/
│   ├── master/             # EDINETコードマスタ (EdinetcodeDlInfo.csv)
│   └── cache/              # APIレスポンスのキャッシュ
├── test/                   # ユニットテスト (pytest)
├── output/                 # CLI分析結果の出力先
├── logs/                   # ログファイル
├── .env.example            # APIキー設定のテンプレート
├── pyproject.toml          # パッケージ定義
└── requirements.txt        # 依存パッケージ
```

## 設定

APIキーの読み込み優先順位:

1. 環境変数 `EDINET_SUBSCRIPTION_KEY`
2. `.env` ファイル

`config/config.json` で運用パラメータを変更できます:

```json
{
    "rate_limit": 1,
    "max_retries": 3,
    "timeout": 30,
    "cache_enabled": true
}
```

| キー | 説明 |
|-----|------|
| `rate_limit` | APIリクエストの最小間隔（秒） |
| `max_retries` | 通信エラー時のリトライ回数 |
| `timeout` | リクエストのタイムアウト（秒） |
| `cache_enabled` | ディスクキャッシュの有効/無効 |

## テスト

```bash
pip install pytest
python -m pytest test/
```

ネットワーク不要のユニットテスト48件が実行されます。

## ライセンス

[MIT License](LICENSE.md)
