"""analyzer.py のユニットテスト（ネットワーク不要）"""

from datetime import datetime

import pandas as pd
import pytest

from edinet_analyzer.analyzer import (
    EdinetFinancialAnalyzer,
    TARGET_DOC_TYPES,
    _extract_xbrl_element,
)


@pytest.fixture()
def analyzer(monkeypatch):
    monkeypatch.setenv("EDINET_SUBSCRIPTION_KEY", "test-key")
    a = EdinetFinancialAnalyzer()
    a.rate_limit = 0  # テストでは待機しない
    return a


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"results": []}
        self.content = content

    def json(self):
        return self._payload


class TestNormalizeCompanyName:
    def test_removes_kabushiki_gaisha_variants(self):
        norm = EdinetFinancialAnalyzer._normalize_company_name
        assert norm("トヨタ自動車株式会社") == "トヨタ自動車"
        assert norm("（株）テスト") == "テスト"
        assert norm("(株)テスト") == "テスト"
        assert norm("㈱テスト") == "テスト"

    def test_removes_spaces_and_lowercases(self):
        norm = EdinetFinancialAnalyzer._normalize_company_name
        assert norm("ソニー　グループ") == "ソニーグループ"
        assert norm("ABC Corp") == "abccorp"

    def test_non_string_returns_empty(self):
        norm = EdinetFinancialAnalyzer._normalize_company_name
        assert norm(None) == ""
        assert norm(float("nan")) == ""


class TestSubmitDate:
    def test_extracts_date_part(self):
        doc = {"submitDateTime": "2025-06-25 15:00"}
        assert EdinetFinancialAnalyzer._submit_date(doc) == "2025-06-25"

    def test_empty_string_returns_empty(self):
        assert EdinetFinancialAnalyzer._submit_date({"submitDateTime": ""}) == ""

    def test_none_returns_empty(self):
        assert EdinetFinancialAnalyzer._submit_date({"submitDateTime": None}) == ""

    def test_missing_key_returns_empty(self):
        assert EdinetFinancialAnalyzer._submit_date({}) == ""


class TestDecodeXml:
    def test_decodes_utf8(self):
        assert EdinetFinancialAnalyzer._decode_xml("売上高".encode("utf-8")) == "売上高"

    def test_decodes_shift_jis(self):
        assert EdinetFinancialAnalyzer._decode_xml("売上高".encode("shift_jis")) == "売上高"


class TestExtractXbrlElement:
    class FakeElem:
        def __init__(self, text, context_ref):
            self.text = text
            self._ctx = context_ref

        def get(self, name):
            return self._ctx if name == "contextRef" else None

    CONTEXTS = {
        "CYDuration": {"type": "duration", "start": "2024-04-01", "end": "2025-03-31"},
        "CYInstant": {"type": "instant", "date": "2025-03-31"},
    }

    def test_duration_context(self):
        results = {}
        elem = self.FakeElem("1,000,000", "CYDuration")
        _extract_xbrl_element(elem, "NetSales", "NetSales", self.CONTEXTS, results)
        assert results == {"NetSales_2024-04-01_2025-03-31": 1000000.0}

    def test_instant_context(self):
        results = {}
        elem = self.FakeElem("2000000", "CYInstant")
        _extract_xbrl_element(elem, "Assets", "Assets", self.CONTEXTS, results)
        assert results == {"Assets_2025-03-31": 2000000.0}

    def test_unknown_context_is_skipped(self):
        results = {}
        elem = self.FakeElem("100", "UnknownContext")
        _extract_xbrl_element(elem, "NetSales", "NetSales", self.CONTEXTS, results)
        assert results == {}

    def test_non_numeric_text_is_skipped(self):
        results = {}
        elem = self.FakeElem("該当なし", "CYDuration")
        _extract_xbrl_element(elem, "NetSales", "NetSales", self.CONTEXTS, results)
        assert results == {}


class TestProcessFinancialData:
    XBRL_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl
    xmlns:xbrli="http://www.xbrl.org/2003/instance"
    xmlns:jpcrp_cor="http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2022-11-01/jpcrp_cor">
  <xbrli:context id="CurrentYearDuration">
    <xbrli:period>
      <xbrli:startDate>2024-04-01</xbrli:startDate>
      <xbrli:endDate>2025-03-31</xbrli:endDate>
    </xbrli:period>
  </xbrli:context>
  <xbrli:context id="CurrentYearInstant">
    <xbrli:period>
      <xbrli:instant>2025-03-31</xbrli:instant>
    </xbrli:period>
  </xbrli:context>
  <jpcrp_cor:NetSales contextRef="CurrentYearDuration">1000000</jpcrp_cor:NetSales>
  <jpcrp_cor:Assets contextRef="CurrentYearInstant">2000000</jpcrp_cor:Assets>
</xbrli:xbrl>
"""

    def test_extracts_pl_and_bs_items(self, analyzer):
        data = analyzer.process_financial_data(self.XBRL_SAMPLE.encode("utf-8"))
        assert data is not None
        assert data["PL_NetSales_2024-04-01_2025-03-31"] == 1000000.0
        assert data["BS_Assets_2025-03-31"] == 2000000.0

    def test_handles_bom(self, analyzer):
        data = analyzer.process_financial_data(
            ("\ufeff" + self.XBRL_SAMPLE).encode("utf-8")
        )
        assert data is not None
        assert data["PL_NetSales_2024-04-01_2025-03-31"] == 1000000.0

    def test_returns_none_for_no_financial_items(self, analyzer):
        empty = '<?xml version="1.0"?><root/>'
        assert analyzer.process_financial_data(empty.encode("utf-8")) is None

    def test_returns_none_for_invalid_xml(self, analyzer):
        assert analyzer.process_financial_data(b"not xml at all <<<") is None

    def test_returns_none_for_none_input(self, analyzer):
        assert analyzer.process_financial_data(None) is None


class TestTargetDocTypes:
    def test_includes_annual_and_quarterly_reports(self):
        assert TARGET_DOC_TYPES["120"] == "有価証券報告書"
        assert "140" in TARGET_DOC_TYPES

    def test_includes_hanki_report(self):
        # 2024年の制度改正で四半期報告書は半期報告書に移行した
        assert TARGET_DOC_TYPES["160"] == "半期報告書"


class TestDocumentsListCache:
    PAYLOAD = {"results": [{"docID": "S100TEST", "edinetCode": "E00001"}]}

    def _patch_session(self, analyzer, monkeypatch, calls):
        def fake_get(url, params=None, timeout=None):
            calls.append(url)
            return FakeResponse(200, self.PAYLOAD)

        monkeypatch.setattr(analyzer.session, "get", fake_get)

    def test_past_date_is_cached_on_disk(self, analyzer, monkeypatch, tmp_path):
        analyzer.doc_cache_dir = tmp_path
        calls = []
        self._patch_session(analyzer, monkeypatch, calls)

        first = analyzer.get_documents_list("2024-01-04")
        second = analyzer.get_documents_list("2024-01-04")

        assert first == self.PAYLOAD
        assert second == self.PAYLOAD
        assert len(calls) == 1, "2回目はキャッシュから返るべき"
        assert (tmp_path / "2024-01-04.json").exists()

    def test_memory_cache_survives_disk_deletion(self, analyzer, monkeypatch, tmp_path):
        analyzer.doc_cache_dir = tmp_path
        calls = []
        self._patch_session(analyzer, monkeypatch, calls)

        analyzer.get_documents_list("2024-01-04")
        (tmp_path / "2024-01-04.json").unlink()
        second = analyzer.get_documents_list("2024-01-04")

        assert second == self.PAYLOAD
        assert len(calls) == 1, "ディスクが消えてもメモリキャッシュで返るべき"

    def test_today_is_not_cached(self, analyzer, monkeypatch, tmp_path):
        analyzer.doc_cache_dir = tmp_path
        calls = []
        self._patch_session(analyzer, monkeypatch, calls)

        today = datetime.now().strftime("%Y-%m-%d")
        analyzer.get_documents_list(today)
        analyzer.get_documents_list(today)

        assert len(calls) == 2, "当日分は提出が増えるためキャッシュしない"
        assert not (tmp_path / f"{today}.json").exists()

    def test_404_returns_empty_results(self, analyzer, monkeypatch, tmp_path):
        analyzer.doc_cache_dir = tmp_path
        monkeypatch.setattr(
            analyzer.session,
            "get",
            lambda url, params=None, timeout=None: FakeResponse(404),
        )
        assert analyzer.get_documents_list("2024-01-04") == {"results": []}


class TestSearchDocuments:
    RESULTS = {
        "results": [
            {"edinetCode": "E00001", "docTypeCode": "120", "docID": "A"},
            {"edinetCode": "E00001", "docTypeCode": "999", "docID": "B"},
            {"edinetCode": "E00002", "docTypeCode": "160", "docID": "C"},
            {"edinetCode": "E00003", "docTypeCode": "120", "docID": "D"},
        ]
    }

    def test_multi_search_filters_by_code_and_doc_type(self, analyzer, monkeypatch):
        monkeypatch.setattr(analyzer, "get_documents_list", lambda date: self.RESULTS)
        date_range = analyzer._business_days("2025-06-02", "2025-06-02")

        docs = analyzer._search_documents_multi(date_range, {"E00001", "E00002"})

        assert [d["docID"] for d in docs["E00001"]] == ["A"]
        assert [d["docID"] for d in docs["E00002"]] == ["C"]
        assert "E00003" not in docs

    def test_single_search_wraps_multi(self, analyzer, monkeypatch):
        monkeypatch.setattr(analyzer, "get_documents_list", lambda date: self.RESULTS)
        date_range = analyzer._business_days("2025-06-02", "2025-06-02")

        docs = analyzer._search_documents(date_range, "E00001")

        assert [d["docID"] for d in docs] == ["A"]


class TestListDocuments:
    def test_returns_none_when_code_not_found(self, analyzer, monkeypatch):
        monkeypatch.setattr(analyzer, "get_edinet_code", lambda name: None)
        assert analyzer.list_documents("未知の企業", "2025-06-01", "2025-06-30") is None

    def test_returns_documents_for_known_company(self, analyzer, monkeypatch):
        monkeypatch.setattr(analyzer, "get_edinet_code", lambda name: "E00001")
        monkeypatch.setattr(
            analyzer,
            "_search_documents",
            lambda date_range, code: [{"docID": "A", "edinetCode": code}],
        )
        docs = analyzer.list_documents("テスト社", "2025-06-01", "2025-06-30")
        assert docs == [{"docID": "A", "edinetCode": "E00001"}]


class TestMasterCache:
    def test_master_csv_is_read_only_once(self, analyzer, monkeypatch, tmp_path):
        csv_path = tmp_path / "EdinetcodeDlInfo.csv"
        csv_path.write_text(
            "メタ情報行\nＥＤＩＮＥＴコード,提出者名\nE00001,トヨタ自動車株式会社\n",
            encoding="cp932",
        )
        analyzer.master_dir = tmp_path

        read_count = 0
        orig_read_csv = pd.read_csv

        def counting_read_csv(*args, **kwargs):
            nonlocal read_count
            read_count += 1
            return orig_read_csv(*args, **kwargs)

        monkeypatch.setattr(pd, "read_csv", counting_read_csv)

        assert analyzer.get_edinet_code("トヨタ自動車") == "E00001"
        assert analyzer.get_edinet_code("トヨタ自動車") == "E00001"
        assert read_count == 1, "マスタCSVは初回のみ読み込むべき"


class TestCreateResultDataframe:
    def test_transposes_periods_to_columns(self, analyzer):
        company_data = {
            "テスト社": [
                {
                    "企業名": "テスト社",
                    "提出日": "2025-06-25",
                    "書類種別": "有価証券報告書",
                    "EDINETコード": "E00001",
                    "書類ID": "S100TEST",
                    "PL_NetSales_2024-04-01_2025-03-31": 1000000.0,
                    "BS_Assets_2025-03-31": 2000000.0,
                }
            ]
        }
        df = analyzer._create_result_dataframe(company_data)

        assert list(df["企業名"].unique()) == ["テスト社"]
        assert "項目" in df.columns
        assert "2024-04-01～2025-03-31" in df.columns

        sales = df[df["項目"] == "売上高"].iloc[0]
        assert sales["2024-04-01～2025-03-31"] == 1000000.0
        assets = df[df["項目"] == "資産合計"].iloc[0]
        assert assets["2024-04-01～2025-03-31"] == 2000000.0

    def test_empty_data_returns_empty_dataframe(self, analyzer):
        df = analyzer._create_result_dataframe({"テスト社": []})
        assert df.empty

    def test_invalid_submit_date_is_coerced_not_raised(self, analyzer):
        company_data = {
            "テスト社": [
                {
                    "企業名": "テスト社",
                    "提出日": "日付ではない値",
                    "書類種別": "有価証券報告書",
                    "EDINETコード": "E00001",
                    "書類ID": "S100TEST",
                    "PL_NetSales_2024-04-01_2025-03-31": 100.0,
                }
            ]
        }
        df = analyzer._create_result_dataframe(company_data)  # 例外にならないこと
        assert not df.empty

    def test_rows_with_invalid_period_end_are_dropped(self, analyzer):
        company_data = {
            "テスト社": [
                {
                    "企業名": "テスト社",
                    "提出日": "2025-06-25",
                    "書類種別": "有価証券報告書",
                    "EDINETコード": "E00001",
                    "書類ID": "S100TEST",
                    "BS_Assets_2024-99-99": 100.0,  # 存在しない日付
                }
            ]
        }
        df = analyzer._create_result_dataframe(company_data)
        assert df.empty
