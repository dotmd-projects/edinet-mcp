"""config.py / __main__.py のユニットテスト"""

import pandas as pd
import pytest

from edinet_analyzer.__main__ import (
    _format_value,
    build_parser,
    detect_format,
    print_summary,
)
from edinet_analyzer.config import load_config


class TestDetectFormat:
    def test_explicit_format_wins(self):
        assert detect_format("out.csv", "json") == "json"

    def test_detects_from_extension(self):
        assert detect_format("out.csv", None) == "csv"
        assert detect_format("out.json", None) == "json"
        assert detect_format("out.xlsx", None) == "xlsx"

    def test_unknown_extension_falls_back_to_csv(self):
        assert detect_format("out.txt", None) == "csv"
        assert detect_format("", None) == "csv"


class TestBuildParser:
    def test_company_is_required(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_multiple_companies(self):
        parser = build_parser()
        args = parser.parse_args(["--company", "トヨタ自動車", "本田技研工業"])
        assert args.company == ["トヨタ自動車", "本田技研工業"]

    def test_date_options(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--company", "テスト", "--from", "2025-01-01", "--to", "2025-06-30"]
        )
        assert args.start_date == "2025-01-01"
        assert args.end_date == "2025-06-30"


class TestFormatValue:
    def test_formats_numbers_with_separator(self):
        assert _format_value(1000000.0) == "1,000,000"
        assert _format_value(0) == "0"

    def test_nan_and_none_become_empty(self):
        assert _format_value(None) == ""
        assert _format_value(float("nan")) == ""

    def test_strings_pass_through(self):
        assert _format_value("テキスト") == "テキスト"


class TestPrintSummary:
    def test_displays_transposed_dataframe(self, capsys):
        # analyze_companies() が返す転置済み形式（行=項目、列=期間）
        df = pd.DataFrame(
            {
                "企業名": ["テスト社", "テスト社"],
                "項目": ["売上高", "営業利益"],
                "2024-04-01～2025-03-31": [1000000.0, 200000.0],
            }
        )

        print_summary(df)

        out = capsys.readouterr().out
        assert "【テスト社】" in out
        assert "売上高" in out
        assert "1,000,000" in out
        assert "200,000" in out

    def test_handles_multiple_companies(self, capsys):
        df = pd.DataFrame(
            {
                "企業名": ["A社", "B社"],
                "項目": ["売上高", "売上高"],
                "2025-03-31": [100.0, 200.0],
            }
        )

        print_summary(df)

        out = capsys.readouterr().out
        assert "【A社】" in out
        assert "【B社】" in out


class TestLoadConfig:
    def test_env_var_provides_subscription_key(self, monkeypatch):
        monkeypatch.setenv("EDINET_SUBSCRIPTION_KEY", "key-from-env")
        config = load_config()
        assert config["subscription_key"] == "key-from-env"

    def test_defaults_are_present(self, monkeypatch):
        monkeypatch.setenv("EDINET_SUBSCRIPTION_KEY", "key")
        config = load_config()
        assert config["max_retries"] == 3
        assert config["timeout"] == 30
