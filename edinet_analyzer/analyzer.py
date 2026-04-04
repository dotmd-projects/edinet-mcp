"""EDINET財務諸表分析のコアロジック"""

import io
import json
import logging
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, date as date_type
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests

from .config import ROOT_DIR, load_config

logger = logging.getLogger(__name__)

# 対象とする書類種別
TARGET_DOC_TYPES = {
    "120": "有価証券報告書",
    "140": "四半期報告書",
    "143": "四半期報告書",
    "144": "四半期報告書",
    "145": "四半期報告書",
}


class EdinetFinancialAnalyzer:
    def __init__(self):
        self.base_url = "https://disclosure.edinet-fsa.go.jp/api/v2"
        self.config = load_config()

        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": self.config["subscription_key"],
        }

        self.data_dir = ROOT_DIR / "data"
        self.master_dir = self.data_dir / "master"
        self.cache_dir = self.data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.docs_cache_dir = self.data_dir / "cache" / "documents"
        self.docs_cache_dir.mkdir(parents=True, exist_ok=True)

        self.rate_limit = self.config.get("rate_limit", 1)
        self.max_retries = self.config.get("max_retries", 3)
        self.timeout = self.config.get("timeout", 30)

        self.session = requests.Session()
        self.session.headers.update(self.headers)

        # 日付ごとの書類一覧キャッシュ（メモリ）
        self._documents_cache: Dict[str, dict] = {}
        # マスターCSVキャッシュ
        self._master_df: Optional[pd.DataFrame] = None

        self._initialize_financial_items()

    def _initialize_financial_items(self):
        """財務諸表の項目を初期化"""
        self.financial_items = {
            "PL": {
                "NetSales": "売上高",
                "CostOfSales": "売上原価",
                "GrossProfit": "売上総利益",
                "SellingGeneralAndAdministrativeExpenses": "販売費及び一般管理費合計",
                "OperatingIncome": "営業利益",
                "OrdinaryIncome": "経常利益",
                "ProfitLoss": "当期純利益",
                "SalariesAndAllowancesSGA": "販管費_人件費",
                "BonusesSGA": "販管費_賞与",
                "ProvisionForBonusesSGA": "販管費_賞与引当金繰入額",
                "RetirementBenefitExpensesSGA": "販管費_退職給付費用",
                "LegalWelfarePremiumsSGA": "販管費_法定福利費",
                "WelfarePremiumsSGA": "販管費_福利厚生費",
                "DepreciationSGA": "販管費_減価償却費",
                "RentExpensesSGA": "販管費_賃借料",
                "RentSGA": "販管費_地代家賃",
                "LeaseExpensesSGA": "販管費_リース料",
                "RepairExpensesSGA": "販管費_修繕費",
                "InsurancePremiumsSGA": "販管費_保険料",
                "UtilitiesPremiumsSGA": "販管費_水道光熱費",
                "TravelingExpensesSGA": "販管費_旅費交通費",
                "CommunicationExpensesSGA": "販管費_通信費",
                "AdvertisingExpensesSGA": "販管費_広告宣伝費",
                "SalesPromotionExpensesSGA": "販管費_販売促進費",
                "EntertainmentExpensesSGA": "販管費_交際費",
                "CommissionFeeSGA": "販管費_支払手数料",
                "ResearchAndDevelopmentExpensesSGA": "販管費_研究開発費",
                "OperatingRevenue": "営業収益",
                "OperatingExpenses": "営業費用",
                "NonOperatingIncome": "営業外収益",
                "NonOperatingExpenses": "営業外費用",
                "ExtraordinaryIncome": "特別利益",
                "ExtraordinaryLoss": "特別損失",
                "IncomeTaxes": "法人税等",
                "ProfitLossAttributableToOwnersOfParent": "親会社株主に帰属する当期純利益",
            },
            "BS": {
                "CurrentAssets": "流動資産合計",
                "NoncurrentAssets": "固定資産合計",
                "Assets": "資産合計",
                "CashAndDeposits": "現金及び預金",
                "NotesAndAccountsReceivableTrade": "受取手形及び売掛金",
                "Inventories": "棚卸資産",
                "ShortTermInvestmentSecurities": "有価証券",
                "PropertyPlantAndEquipment": "有形固定資産",
                "IntangibleAssets": "無形固定資産",
                "InvestmentsAndOtherAssets": "投資その他の資産",
                "CurrentLiabilities": "流動負債合計",
                "NoncurrentLiabilities": "固定負債合計",
                "Liabilities": "負債合計",
                "NotesAndAccountsPayableTrade": "支払手形及び買掛金",
                "ShortTermLoansPayable": "短期借入金",
                "CurrentPortionOfLongTermLoansPayable": "1年内返済予定の長期借入金",
                "LongTermLoansPayable": "長期借入金",
                "BondsPayable": "社債",
                "NetAssets": "純資産合計",
                "ShareCapital": "資本金",
                "CapitalSurplus": "資本剰余金",
                "RetainedEarnings": "利益剰余金",
                "TreasuryShares": "自己株式",
                "ValuationAndTranslationAdjustments": "評価・換算差額等",
                "NonControllingInterests": "非支配株主持分",
            },
            "CF": {
                "NetCashProvidedByUsedInOperatingActivities": "営業CF",
                "NetCashProvidedByUsedInInvestmentActivities": "投資CF",
                "NetCashProvidedByUsedInFinancingActivities": "財務CF",
                "CashAndCashEquivalents": "現金等期末残高",
                "DepreciationAndAmortizationOpeCF": "減価償却費",
                "InterestAndDividendsIncomeReceived": "受取利息及び受取配当金",
                "InterestExpensesPaid": "支払利息",
                "IncomeTaxesPaid": "法人税等の支払額",
                "PaymentsForPropertyPlantAndEquipment": "有形固定資産の取得による支出",
                "ProceedsFromSalesOfPropertyPlantAndEquipment": "有形固定資産の売却による収入",
                "PaymentsForInvestmentSecurities": "投資有価証券の取得による支出",
                "ProceedsFromSalesOfInvestmentSecurities": "投資有価証券の売却による収入",
                "ProceedsFromLongTermLoans": "長期借入れによる収入",
                "RepaymentsOfLongTermLoans": "長期借入金の返済による支出",
                "ProceedsFromIssuanceOfBonds": "社債の発行による収入",
                "RedemptionOfBonds": "社債の償還による支出",
                "CashDividendsPaid": "配当金の支払額",
            },
        }

        self.financial_items_reverse = {}
        for statement_type, items in self.financial_items.items():
            self.financial_items_reverse[statement_type] = {
                v: k for k, v in items.items()
            }

    # ------------------------------------------------------------------
    # EDINETコード検索
    # ------------------------------------------------------------------

    def _load_master_df(self) -> Optional[pd.DataFrame]:
        """マスターCSVを読み込みキャッシュする"""
        if self._master_df is not None:
            return self._master_df

        master_path = self.master_dir / "EdinetcodeDlInfo.csv"
        if not master_path.exists():
            logger.error("マスターファイルが見つかりません: %s", master_path)
            return None

        df = None
        for encoding in ("cp932", "shift_jis", "utf-8"):
            try:
                df = pd.read_csv(master_path, encoding=encoding, skiprows=1)
                if "ＥＤＩＮＥＴコード" in df.columns and "提出者名" in df.columns:
                    break
            except Exception:
                continue

        if df is None or df.empty:
            logger.error("マスターファイルの読み込みに失敗しました")
            return None

        df["_norm"] = df["提出者名"].apply(self._normalize_company_name)
        self._master_df = df
        logger.info("マスターCSV読み込み完了: %d件", len(df))
        return df

    def get_edinet_code(self, company_name: str) -> Optional[str]:
        """企業名からEDINETコードを取得"""
        df = self._load_master_df()
        if df is None:
            return None

        normalized = self._normalize_company_name(company_name)

        # 完全一致
        exact = df[df["_norm"] == normalized]
        if not exact.empty:
            match = exact.iloc[0]
            logger.info("企業を特定: %s (EDINETコード: %s)", match["提出者名"], match["ＥＤＩＮＥＴコード"])
            return match["ＥＤＩＮＥＴコード"]

        # 部分一致 + 類似度
        partial = df[df["_norm"].str.contains(normalized, na=False)]
        if not partial.empty:
            best_idx = max(
                range(len(partial)),
                key=lambda i: SequenceMatcher(
                    None, normalized, partial.iloc[i]["_norm"]
                ).ratio(),
            )
            match = partial.iloc[best_idx]
            logger.warning(
                "完全一致なし。類似企業を使用: %s (EDINETコード: %s)",
                match["提出者名"],
                match["ＥＤＩＮＥＴコード"],
            )
            return match["ＥＤＩＮＥＴコード"]

        logger.error("%s のEDINETコードが見つかりませんでした", company_name)
        return None

    @staticmethod
    def _normalize_company_name(name) -> str:
        if not isinstance(name, str):
            return ""
        for char in ("株式会社", "（株）", "(株)", "㈱", "　", " "):
            name = name.replace(char, "")
        return name.strip().lower()

    # ------------------------------------------------------------------
    # EDINET API
    # ------------------------------------------------------------------

    def _is_past_date(self, date_str: str) -> bool:
        """過去の日付かどうかを判定"""
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date() < date_type.today()
        except ValueError:
            return False

    def _load_docs_disk_cache(self, date_str: str) -> Optional[dict]:
        """ディスクキャッシュから書類一覧を読み込む"""
        cache_path = self.docs_cache_dir / f"{date_str}.json"
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def _save_docs_disk_cache(self, date_str: str, data: dict) -> None:
        """過去日付の書類一覧をディスクキャッシュに保存"""
        if not self._is_past_date(date_str):
            return
        cache_path = self.docs_cache_dir / f"{date_str}.json"
        try:
            cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.debug("ディスクキャッシュ書き込み失敗 (%s): %s", date_str, e)

    def get_documents_list(self, date: str) -> dict:
        """指定日の書類一覧を取得（メモリ+ディスクキャッシュ付き）"""
        # メモリキャッシュ
        if date in self._documents_cache:
            return self._documents_cache[date]

        # ディスクキャッシュ（過去日付のみ）
        disk_cached = self._load_docs_disk_cache(date)
        if disk_cached is not None:
            self._documents_cache[date] = disk_cached
            return disk_cached

        url = f"{self.base_url}/documents.json"
        params = {"date": date, "type": 2}

        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 200:
                    result = resp.json()
                    self._documents_cache[date] = result
                    self._save_docs_disk_cache(date, result)
                    return result
                if resp.status_code == 404:
                    empty = {"results": []}
                    self._documents_cache[date] = empty
                    self._save_docs_disk_cache(date, empty)
                    return empty
                if resp.status_code == 429:
                    time.sleep(self.rate_limit * 2)
                    continue
                logger.warning("API %d: %s", resp.status_code, date)
                empty = {"results": []}
                self._documents_cache[date] = empty
                return empty
            except Exception as e:
                if attempt == self.max_retries - 1:
                    logger.error("API呼び出し失敗 (%d回リトライ): %s", self.max_retries, e)
                    empty = {"results": []}
                    self._documents_cache[date] = empty
                    return empty
                time.sleep(self.rate_limit)

        return {"results": []}

    def download_xbrl(self, doc_id: str) -> Optional[bytes]:
        """書類IDからXBRLをダウンロード"""
        cache_path = self.cache_dir / f"{doc_id}.xbrl"
        if self.config.get("cache_enabled") and cache_path.exists():
            logger.debug("キャッシュを使用: %s", doc_id)
            return cache_path.read_bytes()

        url = f"{self.base_url}/documents/{doc_id}"
        params = {"type": 1, "Subscription-Key": self.config["subscription_key"]}

        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 200:
                    return self._extract_xbrl_from_zip(resp.content, doc_id, cache_path)
                if resp.status_code == 429:
                    logger.warning("レート制限。待機中...")
                    time.sleep(self.rate_limit * 2)
                    continue
                logger.error("ダウンロード失敗 %s: HTTP %d", doc_id, resp.status_code)
                return None
            except requests.exceptions.RequestException as e:
                if attempt == self.max_retries - 1:
                    logger.error("ダウンロードエラー %s: %s", doc_id, e)
                    return None
                time.sleep(self.rate_limit)

        return None

    def _extract_xbrl_from_zip(
        self, zip_bytes: bytes, doc_id: str, cache_path: Path
    ) -> Optional[bytes]:
        """ZIPからXBRLインスタンス文書を抽出

        優先順位:
        1. PublicDoc内の .xbrl ファイル（通常XBRLインスタンス文書、財務データ本体）
        2. PublicDoc内の .htm ファイル（InlineXBRL、フォールバック）
        """
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                public_files = [
                    f for f in zf.filelist if "PublicDoc" in f.filename
                ]
                # .xbrl インスタンス文書を優先
                xbrl_instance = [
                    f for f in public_files if f.filename.endswith(".xbrl")
                ]
                htm_files = [
                    f for f in public_files if f.filename.endswith(".htm")
                ]

                target = None
                if xbrl_instance:
                    target = xbrl_instance[0]
                elif htm_files:
                    target = htm_files[0]

                if not target:
                    logger.warning("ZIP内にXBRLファイルなし: %s", doc_id)
                    return None

                content = zf.read(target.filename)
                logger.debug("XBRL抽出: %s -> %s", doc_id, target.filename)

                if self.config.get("cache_enabled"):
                    cache_path.write_bytes(content)

                return content
        except zipfile.BadZipFile:
            logger.error("不正なZIPファイル: %s", doc_id)
            return None

    # ------------------------------------------------------------------
    # XBRL解析
    # ------------------------------------------------------------------

    def process_financial_data(self, xml_content: bytes) -> Optional[Dict]:
        """XMLから財務データを抽出

        InlineXBRL (.htm) と通常XBRL (.xbrl) の両方に対応。
        """
        if xml_content is None:
            return None

        xml_str = self._decode_xml(xml_content)
        if xml_str is None:
            return None

        # BOM除去
        xml_str = xml_str.lstrip("\ufeff")

        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError as e:
            logger.error("XML解析エラー: %s", e)
            return None

        # 名前空間の取得
        namespaces = dict(
            node for _, node in ET.iterparse(io.StringIO(xml_str), events=["start-ns"])
        )

        # ルート要素のタグで形式を判定
        is_inline = root.tag.endswith("}html") or root.tag == "html"

        ns = dict(namespaces)
        # 標準の名前空間を補完（存在しない場合のみ）
        ns.setdefault("ix", "http://www.xbrl.org/2008/inlineXBRL")
        ns.setdefault("xbrli", "http://www.xbrl.org/2003/instance")
        ns.setdefault("jpcrp_cor", "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2022-11-01/jpcrp_cor")
        ns.setdefault("jpigp_cor", "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2022-11-01/jpigp_cor")

        # context情報を収集
        contexts = self._parse_contexts(root, ns)

        financial_data = {}
        found_items = 0

        for statement_type, items in self.financial_items.items():
            for item_name in items:
                if is_inline:
                    values = self._find_inline_xbrl_values(root, ns, item_name, contexts)
                else:
                    values = self._find_xbrl_values(root, ns, namespaces, item_name, contexts)
                for key, value in values.items():
                    financial_data[f"{statement_type}_{key}"] = value
                    found_items += 1

        if found_items > 0:
            logger.info("財務データ抽出完了: %d項目", found_items)
            return financial_data

        logger.warning("財務データが取得できませんでした")
        return None

    @staticmethod
    def _decode_xml(content: bytes) -> Optional[str]:
        for enc in ("utf-8", "shift_jis", "cp932"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        logger.error("XMLのデコードに失敗しました")
        return None

    @staticmethod
    def _parse_contexts(root, ns: dict) -> dict:
        contexts = {}
        for ctx in root.findall(".//xbrli:context", ns):
            ctx_id = ctx.get("id")
            if not ctx_id:
                continue
            period = ctx.find(".//xbrli:period", ns)
            if period is None:
                continue
            instant = period.find(".//xbrli:instant", ns)
            if instant is not None:
                contexts[ctx_id] = {"type": "instant", "date": instant.text}
            else:
                start = period.find(".//xbrli:startDate", ns)
                end = period.find(".//xbrli:endDate", ns)
                if start is not None and end is not None:
                    contexts[ctx_id] = {
                        "type": "duration",
                        "start": start.text,
                        "end": end.text,
                    }
        return contexts

    @staticmethod
    def _find_inline_xbrl_values(root, ns: dict, item_name: str, contexts: dict) -> dict:
        """InlineXBRL (.htm) から ix:nonFraction 要素を検索"""
        results = {}

        elements = root.findall(f".//ix:nonFraction[@name='{item_name}']", ns)
        if not elements:
            for prefix in ("jpcrp_cor:", "jpigp_cor:"):
                elements = root.findall(
                    f".//ix:nonFraction[@name='{prefix}{item_name}']", ns
                )
                if elements:
                    break

        for elem in elements:
            ctx_ref = elem.get("contextRef")
            if not ctx_ref or ctx_ref not in contexts:
                continue
            try:
                text = (elem.text or "").strip().replace(",", "")
                value = float(text)
                ctx = contexts[ctx_ref]
                if ctx["type"] == "instant":
                    key = f"{item_name}_{ctx['date']}"
                else:
                    key = f"{item_name}_{ctx['start']}_{ctx['end']}"
                results[key] = value
            except (ValueError, TypeError):
                continue

        return results

    @staticmethod
    def _find_xbrl_values(root, ns: dict, namespaces: dict, item_name: str, contexts: dict) -> dict:
        """通常XBRL (.xbrl) からタグ名で要素を検索

        jpcrp_cor:NetSales のような直接タグ名のほか、
        企業固有名前空間のタグ名末尾一致も検索する。
        また、SummaryOfBusinessResults 系のタグにも対応する。
        """
        results = {}

        # 検索パターン: 完全一致 + SummaryOfBusinessResults サフィックス付き
        search_names = [item_name, f"{item_name}SummaryOfBusinessResults"]

        # 標準名前空間で検索
        for search_name in search_names:
            for ns_prefix in ("jpcrp_cor", "jpigp_cor"):
                ns_uri = ns.get(ns_prefix)
                if not ns_uri:
                    continue
                elements = root.findall(f".//{{{ns_uri}}}{search_name}")
                for elem in elements:
                    _extract_xbrl_element(elem, search_name, item_name, contexts, results)

        # 企業固有名前空間で検索（タグ名末尾一致）
        for prefix, uri in namespaces.items():
            if not prefix or prefix in ("jpcrp_cor", "jpigp_cor", "xbrli", "xlink", "link", "iso4217"):
                continue
            for search_name in search_names:
                elements = root.findall(f".//{{{uri}}}{search_name}")
                for elem in elements:
                    _extract_xbrl_element(elem, search_name, item_name, contexts, results)

        return results


    # ------------------------------------------------------------------
    # メイン分析
    # ------------------------------------------------------------------

    def analyze_companies(
        self,
        company_names: List[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """複数企業の財務データを分析"""
        company_data: Dict[str, list] = {name: [] for name in company_names}

        logger.info("=== 分析開始 ===")
        logger.info("期間: %s ~ %s", start_date, end_date)
        logger.info("対象企業: %s", ", ".join(company_names))

        # EDINETコードを事前に解決
        code_to_name: Dict[str, str] = {}
        for company_name in company_names:
            edinet_code = self.get_edinet_code(company_name)
            if not edinet_code:
                logger.error("%s のEDINETコードが取得できません。スキップします。", company_name)
                continue
            code_to_name[edinet_code] = company_name

        if not code_to_name:
            return pd.DataFrame()

        # 日付範囲を1回だけクエリし、全企業分の書類をまとめて収集
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        date_range = pd.date_range(start_dt, end_dt, freq="B")

        target_codes = set(code_to_name.keys())
        documents_by_code: Dict[str, list] = {code: [] for code in target_codes}
        documents_by_code = self._search_documents_multi(date_range, target_codes)

        # 企業ごとに書類を処理
        for edinet_code, company_name in code_to_name.items():
            all_documents = documents_by_code.get(edinet_code, [])
            logger.info("%s: 検出された書類数: %d", company_name, len(all_documents))

            for doc_idx, doc in enumerate(all_documents, 1):
                doc_type = doc.get("docTypeCode")
                doc_type_name = TARGET_DOC_TYPES.get(doc_type, doc_type)
                doc_id = doc.get("docID")

                logger.info(
                    "  書類 %d/%d: %s (ID: %s)",
                    doc_idx,
                    len(all_documents),
                    doc_type_name,
                    doc_id,
                )

                xbrl_content = self.download_xbrl(doc_id)
                if not xbrl_content:
                    logger.warning("  ダウンロード失敗")
                    continue

                fin_data = self.process_financial_data(xbrl_content)
                if fin_data:
                    fin_data.update(
                        {
                            "企業名": company_name,
                            "提出日": doc.get("submitDateTime", "").split()[0],
                            "書類種別": doc_type_name,
                            "EDINETコード": edinet_code,
                            "書類ID": doc_id,
                        }
                    )
                    company_data[company_name].append(fin_data)
                    logger.info("  取得成功: %s", fin_data["提出日"])
                else:
                    logger.warning("  解析失敗")

            logger.info("%s の処理完了", company_name)

        return self._create_result_dataframe(company_data)

    def _search_documents_multi(self, date_range, edinet_codes: set) -> Dict[str, list]:
        """日付範囲内の対象書類を複数企業分まとめて検索（APIコール1回/日）"""
        results: Dict[str, list] = {code: [] for code in edinet_codes}
        total_days = len(date_range)
        last_request_time = 0.0

        for idx, date in enumerate(date_range):
            date_str = date.strftime("%Y-%m-%d")
            if (idx + 1) % 50 == 0 or idx == 0:
                logger.info("  書類検索中... %d/%d 日", idx + 1, total_days)

            # キャッシュヒット時はsleep不要
            if date_str not in self._documents_cache:
                elapsed = time.time() - last_request_time
                if elapsed < self.rate_limit:
                    time.sleep(self.rate_limit - elapsed)

            try:
                last_request_time = time.time()
                docs = self.get_documents_list(date_str)
                if docs and docs.get("results"):
                    for doc in docs["results"]:
                        code = doc.get("edinetCode")
                        if code in edinet_codes and doc.get("docTypeCode") in TARGET_DOC_TYPES:
                            results[code].append(doc)
                            logger.info("  %s: 書類検出 (%s)", date_str, code)
            except Exception as e:
                logger.debug("書類検索エラー (%s): %s", date_str, e)

        return results

    def _search_documents(self, date_range, edinet_code: str) -> list:
        """日付範囲内の対象書類を検索（単一企業用）"""
        results = self._search_documents_multi(date_range, {edinet_code})
        return results.get(edinet_code, [])

    def _create_result_dataframe(self, company_data: Dict) -> pd.DataFrame:
        """財務データをDataFrameに変換

        期間ごとに1行に整形する。
        例: PL_NetSales_2023-04-01_2024-03-31 → 期間開始=2023-04-01, 売上高=value
        """
        import re

        # 項目名 → 日本語名の逆引きテーブルを構築
        item_to_japanese: Dict[str, str] = {}
        for statement_type, items in self.financial_items.items():
            for eng_name, jpn_name in items.items():
                item_to_japanese[f"{statement_type}_{eng_name}"] = jpn_name

        all_rows = []

        for company, data_list in company_data.items():
            for data in data_list:
                base_info = {
                    "企業名": data.get("企業名"),
                    "提出日": data.get("提出日"),
                    "書類種別": data.get("書類種別"),
                    "EDINETコード": data.get("EDINETコード"),
                    "書類ID": data.get("書類ID"),
                }

                # 期間ごとにデータをグルーピング
                # duration (PL/CF): PL_NetSales_2023-04-01_2024-03-31 → 期間終了=2024-03-31
                # instant (BS):     BS_Assets_2024-03-31              → 期間終了=2024-03-31
                # 同じ期間終了日のdurationとinstantを1行にマージする
                period_data: Dict[str, dict] = {}

                for key, value in data.items():
                    if not key.startswith(("PL_", "BS_", "CF_")) or value is None:
                        continue

                    # duration: ST_Item_YYYY-MM-DD_YYYY-MM-DD
                    m_dur = re.match(
                        r"^([A-Z]+_\w+?)_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})$",
                        key,
                    )
                    # instant: ST_Item_YYYY-MM-DD
                    m_inst = re.match(
                        r"^([A-Z]+_\w+?)_(\d{4}-\d{2}-\d{2})$",
                        key,
                    )

                    if m_dur:
                        item_key = m_dur.group(1)
                        period_start = m_dur.group(2)
                        period_end = m_dur.group(3)
                    elif m_inst:
                        item_key = m_inst.group(1)
                        period_start = ""
                        period_end = m_inst.group(2)
                    else:
                        continue

                    # 期間終了日をキーにしてマージ
                    if period_end not in period_data:
                        period_data[period_end] = {"期間終了": period_end}

                    # duration の期間開始を設定（上書きしない）
                    if period_start and "期間開始" not in period_data[period_end]:
                        period_data[period_end]["期間開始"] = period_start

                    jpn_name = item_to_japanese.get(item_key, item_key)
                    period_data[period_end][jpn_name] = value

                # 各期間を1行として追加
                for period_info in period_data.values():
                    period_info.setdefault("期間開始", "")
                    row = {**base_info, **period_info}
                    all_rows.append(row)

        if not all_rows:
            logger.warning("処理可能なデータが見つかりませんでした")
            return pd.DataFrame()

        df = pd.DataFrame(all_rows)

        # 日付列を変換
        df["提出日"] = pd.to_datetime(df["提出日"])
        df["期間終了"] = pd.to_datetime(df["期間終了"])
        df.loc[df["期間開始"] != "", "期間開始"] = pd.to_datetime(
            df.loc[df["期間開始"] != "", "期間開始"]
        )

        # 期間終了でソート
        df = df.sort_values(["企業名", "期間終了"])

        logger.info("=== 結果 ===")
        logger.info("取得件数: %d期間", len(df))
        for company in df["企業名"].unique():
            count = len(df[df["企業名"] == company])
            logger.info("  %s: %d期間", company, count)

        # 転置: 期間を列、項目を行にする
        return self._transpose_result(df)

    def _transpose_result(self, df: pd.DataFrame) -> pd.DataFrame:
        """期間を列、項目を行に転置する"""
        meta_cols = ["企業名", "提出日", "書類種別", "EDINETコード", "書類ID", "期間開始", "期間終了"]
        item_cols = [c for c in df.columns if c not in meta_cols]

        all_frames = []
        for company in df["企業名"].unique():
            cdf = df[df["企業名"] == company].sort_values("期間終了")

            # 列ヘッダー用のラベルを作成（例: "2024-04-01～2025-03-31"）
            period_labels = []
            for _, row in cdf.iterrows():
                start = row["期間開始"]
                end = row["期間終了"]
                if pd.notna(start) and start != "":
                    label = f"{pd.Timestamp(start).strftime('%Y-%m-%d')}～{pd.Timestamp(end).strftime('%Y-%m-%d')}"
                else:
                    label = pd.Timestamp(end).strftime("%Y-%m-%d")
                period_labels.append(label)

            # 項目×期間の転置DataFrame
            transposed = pd.DataFrame(index=item_cols, columns=period_labels)
            for label, (_, row) in zip(period_labels, cdf.iterrows()):
                for item in item_cols:
                    transposed.loc[item, label] = row.get(item)

            # NaNのみの行（全期間でデータなし）を除去
            transposed = transposed.dropna(how="all")

            # indexを「項目」列に変換し、企業名を先頭に挿入
            transposed = transposed.reset_index(names="項目")
            transposed.insert(0, "企業名", company)

            all_frames.append(transposed)

        return pd.concat(all_frames, ignore_index=True)


def _extract_xbrl_element(
    elem, search_name: str, item_name: str, contexts: dict, results: dict
):
    """XBRL要素から値を抽出してresultsに追加"""
    ctx_ref = elem.get("contextRef")
    if not ctx_ref or ctx_ref not in contexts:
        return
    try:
        text = (elem.text or "").strip().replace(",", "")
        if not text:
            return
        value = float(text)
        ctx = contexts[ctx_ref]
        if ctx["type"] == "instant":
            key = f"{item_name}_{ctx['date']}"
        else:
            key = f"{item_name}_{ctx['start']}_{ctx['end']}"
        results[key] = value
    except (ValueError, TypeError):
        pass
