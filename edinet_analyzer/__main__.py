"""EDINET財務諸表分析ツール CLI

使用例:
    python -m edinet_analyzer --company トヨタ自動車 --from 2025-01-01 --to 2025-12-31
    python -m edinet_analyzer --company トヨタ自動車 本田技研工業 -o results.csv
    python -m edinet_analyzer --company トヨタ自動車 --format json -o results.json
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from .analyzer import EdinetFinancialAnalyzer


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(
                "logs/edinet_analyzer.log", encoding="utf-8",
            ),
        ],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edinet_analyzer",
        description="EDINET財務諸表分析ツール — 企業の決算資料を抽出・分析",
    )
    parser.add_argument(
        "--company",
        nargs="+",
        required=True,
        help="分析対象の企業名（複数指定可）",
    )
    parser.add_argument(
        "--from",
        dest="start_date",
        default=(datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"),
        help="検索開始日 (YYYY-MM-DD)。デフォルト: 1年前",
    )
    parser.add_argument(
        "--to",
        dest="end_date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="検索終了日 (YYYY-MM-DD)。デフォルト: 今日",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="出力ファイルパス。拡張子に応じて形式を自動判定 (.csv / .json / .xlsx)",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json", "xlsx"],
        default=None,
        help="出力形式を明示的に指定（--output の拡張子より優先）",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="詳細ログを出力",
    )
    return parser


def detect_format(output_path: str, explicit_format: str | None) -> str:
    if explicit_format:
        return explicit_format
    if output_path:
        ext = output_path.rsplit(".", 1)[-1].lower()
        if ext in ("csv", "json", "xlsx"):
            return ext
    return "csv"


def save_results(df: pd.DataFrame, output_path: str, fmt: str) -> None:
    if fmt == "csv":
        df.to_csv(output_path, index=False, encoding="utf-8-sig")
    elif fmt == "json":
        df.to_json(output_path, orient="records", force_ascii=False, indent=2)
    elif fmt == "xlsx":
        df.to_excel(output_path, index=False, engine="openpyxl")

    print(f"結果を保存しました: {output_path} ({fmt}形式, {len(df)}件)")


def _format_value(value) -> str:
    """表示用に数値を桁区切りへ変換する"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, (int, float)):
        return f"{value:,.0f}"
    return str(value)


def print_summary(df: pd.DataFrame) -> None:
    """分析結果（行=財務項目、列=期間）を企業ごとに表示する"""
    for company in df["企業名"].unique():
        cdf = df[df["企業名"] == company].drop(columns=["企業名"])

        formatted = cdf.copy()
        for col in formatted.columns:
            if col == "項目":
                continue
            formatted[col] = formatted[col].map(_format_value)

        print(f"\n【{company}】({len(cdf)}項目)")
        print(formatted.to_string(index=False))

    print("\nヒント: -o results.csv を指定すると結果をファイルに保存できます")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # ログディレクトリ確保
    Path("logs").mkdir(exist_ok=True)

    setup_logging(args.verbose)

    # 日付のバリデーション
    try:
        start = datetime.strptime(args.start_date, "%Y-%m-%d")
        end = datetime.strptime(args.end_date, "%Y-%m-%d")
        if start > end:
            print("エラー: 開始日が終了日より後です", file=sys.stderr)
            return 1
    except ValueError:
        print("エラー: 日付の形式が不正です (YYYY-MM-DD)", file=sys.stderr)
        return 1

    # 分析実行
    try:
        analyzer = EdinetFinancialAnalyzer()
    except ValueError as e:
        print(f"設定エラー: {e}", file=sys.stderr)
        return 1

    print(f"企業: {', '.join(args.company)}")
    print(f"期間: {args.start_date} ~ {args.end_date}")
    print()

    df = analyzer.analyze_companies(
        company_names=args.company,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    if df.empty:
        print("データが見つかりませんでした。", file=sys.stderr)
        return 1

    # 出力
    if args.output:
        fmt = detect_format(args.output, args.format)
        save_results(df, args.output, fmt)
    else:
        print_summary(df)

    return 0


if __name__ == "__main__":
    sys.exit(main())
