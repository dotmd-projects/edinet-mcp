"""server.py (MCPサーバー) のユニットテスト"""

import pandas as pd
import pytest

pytest.importorskip("mcp")

from server import _period_columns, _to_okuyen, mcp


class TestToOkuyen:
    def test_converts_to_oku_unit(self):
        assert _to_okuyen(100_000_000) == "1億円"
        assert _to_okuyen(300_000_000.0) == "3億円"
        assert _to_okuyen("1000000000") == "10億円"

    def test_missing_values_return_none(self):
        assert _to_okuyen(None) is None
        assert _to_okuyen(float("nan")) is None
        assert _to_okuyen("") is None

    def test_non_numeric_passes_through(self):
        assert _to_okuyen("非数値") == "非数値"


class TestPeriodColumns:
    def test_excludes_meta_columns(self):
        df = pd.DataFrame(
            columns=["企業名", "項目", "2024-04-01～2025-03-31", "2025-03-31"]
        )
        assert _period_columns(df) == ["2024-04-01～2025-03-31", "2025-03-31"]


class TestMcpTools:
    def test_all_tools_are_registered(self):
        import asyncio

        tools = {t.name for t in asyncio.run(mcp.list_tools())}
        assert tools == {
            "search_company",
            "list_financial_reports",
            "get_financial_data",
            "compare_companies",
        }
