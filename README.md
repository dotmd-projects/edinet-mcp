# EDINET財務諸表分析 MCP Server

EDINET APIを利用して日本企業の財務諸表データを取得・分析するMCPサーバー。
Claude DesktopからEDINETの決算データに直接アクセスできます。

## セットアップ

### 1. 依存パッケージのインストール

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. EDINET APIキーの取得

本ツールの利用にはEDINET APIのサブスクリプションキーが必要です。以下の手順で無料で取得できます。

1. **アカウント作成**
   [EDINET API 利用登録ページ](https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1) にアクセスし、氏名・メールアドレス・電話番号を入力して新規登録します。

2. **多要素認証**
   メールで届く確認コードを入力後、SMS認証を行います。電話番号は国番号 `+81` を選択し、先頭の `0` を省略して入力します（例: `80-XXXX-XXXX`）。

3. **ブラウザのポップアップ許可**
   APIキーはポップアップウィンドウで表示されます。ブラウザが `https://api.edinet-fsa.go.jp` のポップアップをブロックしている場合は、事前に許可設定を行ってください（Microsoft Edgeの利用も推奨されています）。

4. **APIキーの取得**
   認証完了後、連絡先を登録するとサブスクリプションキーが画面に表示されます。

詳細な仕様は [EDINET API仕様書 (Version 2)](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0110.html) を参照してください。

### 3. Claude Desktopへの登録

`~/Library/Application Support/Claude/claude_desktop_config.json` に以下を追加します:

```json
{
  "mcpServers": {
    "edinet-analyzer": {
      "command": "/path/to/mcp_edinet_analyzer/.venv/bin/python",
      "args": ["/path/to/mcp_edinet_analyzer/server.py"],
      "env": {
        "EDINET_SUBSCRIPTION_KEY": "your_subscription_key_here"
      }
    }
  }
}
```

`command` と `args` のパスは実際のインストール先に合わせてください。
設定後、Claude Desktopを再起動すると利用可能になります。

## 提供ツール

| ツール | 説明 |
|-------|------|
| `search_company` | 企業名からEDINETコードを検索 |
| `list_financial_reports` | 指定期間の決算書類一覧を取得 |
| `get_financial_data` | 企業の財務データ (PL/BS/CF) を取得・億円単位で表示 |
| `compare_companies` | 複数企業の財務データを比較 |

### 使用例（Claude Desktopでの質問）

- 「トヨタ自動車のEDINETコードを調べて」
- 「トヨタ自動車の2025年6月の決算書類を一覧して」
- 「トヨタ自動車の2025年6月の財務データを取得して」
- 「トヨタと本田の財務データを比較して」

### 取得可能な財務項目

- **損益計算書 (PL)**: 売上高、売上原価、売上総利益、販管費、営業利益、経常利益、当期純利益 等
- **貸借対照表 (BS)**: 流動資産、固定資産、資産合計、負債合計、純資産合計 等
- **キャッシュフロー (CF)**: 営業CF、投資CF、財務CF 等

## プロジェクト構成

```
mcp_edinet_analyzer/
├── server.py               # MCPサーバー本体
├── edinet_analyzer/         # コアロジック
│   ├── analyzer.py          # EDINET API連携・XBRL解析
│   └── config.py            # 設定管理
├── config/
│   └── config.json          # 運用設定 (rate_limit, timeout等)
├── data/
│   └── master/              # EDINETコードマスタ (EdinetcodeDlInfo.csv)
├── logs/                    # ログファイル
├── .env.example             # .envのテンプレート
└── requirements.txt         # 依存パッケージ
```

## 設定

`config/config.json` で運用パラメータを変更できます:

```json
{
    "rate_limit": 1,
    "max_retries": 3,
    "timeout": 30,
    "cache_enabled": true
}
```
