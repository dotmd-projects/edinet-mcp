"""EDINET財務諸表分析 MCP Server

Claude Desktop等からEDINET APIの財務データに直接アクセスするためのMCPサーバー。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# edinet_analyzerパッケージをインポート可能にする
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

from edinet_analyzer.analyzer import EdinetFinancialAnalyzer, TARGET_DOC_TYPES

# ログ設定（stdoutはMCP通信で使うため、stderrのみに出力）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

mcp = FastMCP("edinet-mcp", instructions="EDINET APIを利用して日本企業の財務諸表データを取得・分析するツールです。")

# Analyzerのインスタンスを遅延初期化
_analyzer: EdinetFinancialAnalyzer | None = None

ONE_OKU = 100_000_000


def get_analyzer() -> EdinetFinancialAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = EdinetFinancialAnalyzer()
    return _analyzer


def _to_okuyen(value) -> str | None:
    """値を億円表記の文字列に変換する。欠損値はNone、数値以外はそのまま文字列化"""
    if value is None or str(value) in ("nan", "None", ""):
        return None
    try:
        return f"{float(value) / ONE_OKU:,.0f}億円"
    except (ValueError, TypeError):
        return str(value)


def _period_columns(df) -> list[str]:
    """転置済みDataFrameから期間列（企業名・項目以外）を取り出す"""
    return [c for c in df.columns if c not in ("企業名", "項目")]


@mcp.tool()
def search_company(company_name: str) -> str:
    """企業名からEDINETコードを検索します。

    Args:
        company_name: 検索する企業名（例: トヨタ自動車、ソニーグループ）
    """
    analyzer = get_analyzer()
    code = analyzer.get_edinet_code(company_name)
    if code:
        return f"企業名: {company_name}\nEDINETコード: {code}"
    return f"{company_name} のEDINETコードが見つかりませんでした。正式な企業名で再度お試しください。"


@mcp.tool()
def list_financial_reports(
    company_name: str,
    start_date: str,
    end_date: str,
) -> str:
    """指定期間内の企業の決算書類一覧を取得します。

    日付範囲が広いほど時間がかかるため、1〜2ヶ月以内の範囲を推奨します。
    3月決算企業の有価証券報告書は6月頃に提出されます。

    Args:
        company_name: 企業名（例: トヨタ自動車）
        start_date: 検索開始日（YYYY-MM-DD形式、例: 2025-06-01）
        end_date: 検索終了日（YYYY-MM-DD形式、例: 2025-06-30）
    """
    analyzer = get_analyzer()

    documents = analyzer.list_documents(company_name, start_date, end_date)
    if documents is None:
        return f"{company_name} のEDINETコードが見つかりませんでした。"
    if not documents:
        return f"{company_name} の書類が指定期間内に見つかりませんでした。"

    lines = [
        f"企業名: {company_name}",
        f"期間: {start_date} ~ {end_date}",
        f"検出書類数: {len(documents)}件",
        "",
    ]
    for doc in documents:
        doc_type = TARGET_DOC_TYPES.get(doc.get("docTypeCode"), doc.get("docTypeCode"))
        submit_date = EdinetFinancialAnalyzer._submit_date(doc) or "不明"
        doc_id = doc.get("docID", "不明")
        title = doc.get("docDescription", "")
        lines.append(f"- {submit_date} | {doc_type} | ID: {doc_id} | {title}")

    return "\n".join(lines)


@mcp.tool()
def get_financial_data(
    company_name: str,
    start_date: str,
    end_date: str,
) -> str:
    """企業の財務データ（PL/BS/CF）を取得・分析します。

    指定期間内の有価証券報告書・半期報告書等から財務諸表データを抽出し、
    期間ごとに整理して返します。
    日付範囲は1〜2ヶ月以内を推奨します（広いと時間がかかります）。
    3月決算企業の有価証券報告書は通常6月に提出されます。

    Args:
        company_name: 企業名（例: トヨタ自動車）
        start_date: 検索開始日（YYYY-MM-DD形式、例: 2025-06-01）
        end_date: 検索終了日（YYYY-MM-DD形式、例: 2025-06-30）
    """
    analyzer = get_analyzer()

    df = analyzer.analyze_companies(
        company_names=[company_name],
        start_date=start_date,
        end_date=end_date,
    )

    if df.empty:
        return f"{company_name} の財務データが見つかりませんでした。期間を広げて再度お試しください。"

    lines = [f"【{company_name} の財務データ】", ""]
    period_cols = _period_columns(df)

    for _, row in df.iterrows():
        item_name = row.get("項目", "")
        values = [
            f"{col}: {formatted}"
            for col in period_cols
            if (formatted := _to_okuyen(row.get(col))) is not None
        ]
        if values:
            lines.append(f"■ {item_name}")
            lines.extend(f"  {v}" for v in values)
            lines.append("")

    return "\n".join(lines)


@mcp.tool()
def compare_companies(
    company_names: list[str],
    start_date: str,
    end_date: str,
) -> str:
    """複数企業の財務データを比較します。

    日付範囲は1〜2ヶ月以内を推奨します（広いと時間がかかります）。
    3月決算企業の有価証券報告書は通常6月に提出されます。

    Args:
        company_names: 企業名のリスト（例: ["トヨタ自動車", "本田技研工業"]）
        start_date: 検索開始日（YYYY-MM-DD形式）
        end_date: 検索終了日（YYYY-MM-DD形式）
    """
    analyzer = get_analyzer()

    df = analyzer.analyze_companies(
        company_names=company_names,
        start_date=start_date,
        end_date=end_date,
    )

    if df.empty:
        return "財務データが見つかりませんでした。"

    lines = [f"【企業比較】{', '.join(company_names)}", f"期間: {start_date} ~ {end_date}", ""]
    period_cols = _period_columns(df)

    for company in company_names:
        company_df = df[df["企業名"] == company]
        if company_df.empty:
            lines.append(f"▶ {company}: データなし")
            lines.append("")
            continue

        lines.append(f"▶ {company}")
        for _, row in company_df.iterrows():
            item_name = row.get("項目", "")
            values = [
                formatted
                for col in period_cols
                if (formatted := _to_okuyen(row.get(col))) is not None
            ]
            if values:
                lines.append(f"  {item_name}: {' → '.join(values)}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="EDINET財務諸表分析 MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="トランスポート方式 (default: stdio)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="SSEホスト (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="SSEポート (default: 8000)")
    args = parser.parse_args()

    if args.transport == "sse":
        # FastMCP.run() はhost/port引数を受け取らないため、settings経由で指定する
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        mcp.run()
