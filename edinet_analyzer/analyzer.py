"""EDINET財務諸表分析のコアロジック"""

from __future__ import annotations

import io
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd
import requests

from .config import ROOT_DIR, load_config

logger = logging.getLogger(__name__)

# 対象とする書類種別
# 注: 四半期報告書は2024年の制度改正で廃止され、半期報告書(160)に移行した。
#     過去データ取得のため四半期報告書のコードも残している。
TARGET_DOC_TYPES = {
    "120": "有価証券報告書",
    "140": "四半期報告書",
    "143": "四半期報告書",
    "144": "四半期報告書",
    "145": "四半期報告書",
    "160": "半期報告書",
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
        self.doc_cache_dir = self.cache_dir / "documents"
        self.doc_cache_dir.mkdir(parents=True, exist_ok=True)

        self.rate_limit = self.config.get("rate_limit", 1)
        self.max_retries = self.config.get("max_retries", 3)
        self.timeout = self.config.get("timeout", 30)

        self.session = requests.Session()
        self.session.headers.update(self.headers)

        self._master_df: pd.DataFrame | None = None
        self._last_request_ts = 0.0
        # 日付ごとの書類一覧キャッシュ（メモリ。過去日のみ保持）
        self._documents_cache: dict[str, dict] = {}

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
            # CFのタグ名は日本基準タクソノミ(jppfs_cor)の実際の要素名に合わせる。
            # 明細項目には活動区分のサフィックス(OpeCF/InvCF/FinCF)が付く。
            "CF": {
                "NetCashProvidedByUsedInOperatingActivities": "営業CF",
                "NetCashProvidedByUsedInInvestmentActivities": "投資CF",
                "NetCashProvidedByUsedInFinancingActivities": "財務CF",
                "CashAndCashEquivalents": "現金等期末残高",
                "DepreciationAndAmortizationOpeCF": "減価償却費",
                "InterestAndDividendsIncomeReceivedOpeCFInvCF": "受取利息及び受取配当金の受取額",
                "InterestExpensesPaidOpeCFFinCF": "利息の支払額",
                "IncomeTaxesPaidOpeCF": "法人税等の支払額",
                "PurchaseOfPropertyPlantAndEquipmentInvCF": "有形固定資産の取得による支出",
                "ProceedsFromSalesOfPropertyPlantAndEquipmentInvCF": "有形固定資産の売却による収入",
                "PurchaseOfIntangibleAssetsInvCF": "無形固定資産の取得による支出",
                "PurchaseOfInvestmentSecuritiesInvCF": "投資有価証券の取得による支出",
                "ProceedsFromSalesOfInvestmentSecuritiesInvCF": "投資有価証券の売却による収入",
                "ProceedsFromLongTermLoansPayableFinCF": "長期借入れによる収入",
                "RepaymentOfLongTermLoansPayableFinCF": "長期借入金の返済による支出",
                "ProceedsFromIssuanceOfBondsFinCF": "社債の発行による収入",
                "RedemptionOfBondsFinCF": "社債の償還による支出",
                "CashDividendsPaidFinCF": "配当金の支払額",
            },
        }

    # ------------------------------------------------------------------
    # EDINETコード検索
    # ------------------------------------------------------------------

    def _load_master(self) -> pd.DataFrame | None:
        """EDINETコードマスタを読み込む（初回のみ。以降はメモリキャッシュ）"""
        if self._master_df is not None:
            return self._master_df

        master_path = self.master_dir / "EdinetcodeDlInfo.csv"
        if not master_path.exists():
            logger.error("マスターファイルが見つかりません: %s", master_path)
            return None

        for encoding in ("cp932", "shift_jis", "utf-8"):
            try:
                df = pd.read_csv(master_path, encoding=encoding, skiprows=1)
            except (UnicodeDecodeError, pd.errors.ParserError, ValueError):
                continue
            if "ＥＤＩＮＥＴコード" in df.columns and "提出者名" in df.columns:
                df["_norm"] = df["提出者名"].apply(self._normalize_company_name)
                # 英字名・ヨミでも検索できるよう正規化列を用意する
                en_col = "提出者名（英字）"
                kana_col = "提出者名（ヨミ）"
                df["_norm_en"] = (
                    df[en_col].apply(self._normalize_en)
                    if en_col in df.columns else ""
                )
                df["_norm_kana"] = (
                    df[kana_col].apply(self._normalize_company_name)
                    if kana_col in df.columns else ""
                )
                self._master_df = df
                return df

        logger.error("マスターファイルの読み込みに失敗しました")
        return None

    def get_edinet_code(self, company_name: str) -> str | None:
        """企業名からEDINETコードを取得"""
        df = self._load_master()
        if df is None or df.empty:
            return None

        normalized = self._normalize_company_name(company_name)   # 日本語名・ヨミ用
        # 英字照合は（法人格を除いた）入力に日本語が残らない場合のみ行う。
        # 日本語主体の入力から末尾のわずかな英字を拾う誤マッチを防ぐ。2文字以上を要求。
        # 例: "freee株式会社" → 法人格除去後 "freee"(日本語なし) → 英字照合を行う
        #     "存在しない企業XYZ" → 日本語が残る → 英字照合しない
        normalized_en = "" if self._has_japanese(normalized) else self._normalize_en(company_name)
        if len(normalized_en) < 2:
            normalized_en = ""
        has_en = "_norm_en" in df.columns
        has_kana = "_norm_kana" in df.columns

        # 完全一致（日本語名 → 英字名の順で優先）
        exact = df[df["_norm"] == normalized]
        if exact.empty and has_en and normalized_en:
            exact = df[df["_norm_en"] == normalized_en]
        if not exact.empty:
            match = exact.iloc[0]
            logger.info("企業を特定: %s (EDINETコード: %s)", match["提出者名"], match["ＥＤＩＮＥＴコード"])
            return match["ＥＤＩＮＥＴコード"]

        # 部分一致（日本語名・英字名・ヨミを横断）+ 類似度で最良を選ぶ
        mask = df["_norm"].str.contains(normalized, na=False, regex=False)
        if has_en and normalized_en:
            mask = mask | df["_norm_en"].str.contains(normalized_en, na=False, regex=False)
        if has_kana:
            mask = mask | df["_norm_kana"].str.contains(normalized, na=False, regex=False)
        partial = df[mask]

        if not partial.empty:
            def score(i: int) -> float:
                row = partial.iloc[i]
                # (照合クエリ, マスタ側の正規化文字列) の組で類似度を計算し最大を採用
                pairs = [(normalized, row["_norm"])]
                if has_en:
                    pairs.append((normalized_en, row["_norm_en"]))
                if has_kana:
                    pairs.append((normalized, row["_norm_kana"]))
                best = 0.0
                for query, field in pairs:
                    if query and isinstance(field, str) and field:
                        best = max(best, SequenceMatcher(None, query, field).ratio())
                return best

            best_idx = max(range(len(partial)), key=score)
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

    # 英字社名の末尾に付く法人格・接尾辞（照合前に除去する）
    _EN_SUFFIXES = (
        "kabushikikaisha", "incorporated", "corporation", "holdings",
        "company", "coltd", "limited", "corp", "inc", "ltd", "llc",
        "plc", "kk",
    )

    @staticmethod
    def _has_japanese(name) -> bool:
        """ひらがな・カタカナ・漢字を含むか判定する"""
        if not isinstance(name, str):
            return False
        return bool(re.search(r"[぀-ヿ㐀-鿿豈-﫿｡-ﾟ]", name))

    @classmethod
    def _normalize_en(cls, name) -> str:
        """英字社名を照合用に正規化する（英数字のみ + 末尾の法人格を除去）

        例: "freee K.K." → "freee", "Sony Group Corporation" → "sonygroup"
        """
        if not isinstance(name, str):
            return ""
        s = re.sub(r"[^a-z0-9]", "", name.lower())
        # 末尾の法人格接尾辞を繰り返し除去（"Co., Ltd." のような複合に対応）
        changed = True
        while changed:
            changed = False
            for suf in cls._EN_SUFFIXES:
                if len(s) > len(suf) and s.endswith(suf):
                    s = s[: -len(suf)]
                    changed = True
        return s

    # ------------------------------------------------------------------
    # EDINET API
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        """前回のAPIリクエストからrate_limit秒空ける（キャッシュヒット時は呼ばれない）"""
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request_ts = time.monotonic()

    def _request(self, url: str, params: dict) -> requests.Response | None:
        """リトライ・レート制限(429)処理付きのGETリクエスト

        通信エラーはmax_retries回まで再試行。429はリトライ回数を消費せず、
        指数バックオフで最大max_retries回まで待機する。
        """
        errors = 0
        rate_limited = 0
        while True:
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                errors += 1
                if errors >= self.max_retries:
                    logger.error("API呼び出し失敗 (%d回リトライ): %s", self.max_retries, e)
                    return None
                time.sleep(self.rate_limit)
                continue

            if resp.status_code == 429:
                rate_limited += 1
                if rate_limited >= self.max_retries:
                    logger.error("レート制限が解消されませんでした: %s", url)
                    return None
                logger.warning("レート制限。待機中...")
                time.sleep(self.rate_limit * (2 ** rate_limited))
                continue

            return resp

    def get_documents_list(self, date: str) -> dict:
        """指定日の書類一覧を取得

        過去日の一覧は変化しないため、cache_enabled時はメモリとディスクにキャッシュする
        （当日分は提出が増えるためキャッシュしない）。
        """
        cache_path = self.doc_cache_dir / f"{date}.json"
        is_past = date < datetime.now().strftime("%Y-%m-%d")
        use_cache = self.config.get("cache_enabled") and is_past

        if use_cache:
            if date in self._documents_cache:
                return self._documents_cache[date]
            if cache_path.exists():
                try:
                    result = json.loads(cache_path.read_text(encoding="utf-8"))
                    self._documents_cache[date] = result
                    return result
                except (OSError, json.JSONDecodeError) as e:
                    logger.debug("書類一覧キャッシュの読み込みに失敗 (%s): %s", date, e)

        url = f"{self.base_url}/documents.json"
        params = {"date": date, "type": 2}

        resp = self._request(url, params)
        if resp is None:
            return {"results": []}
        if resp.status_code == 404:
            return {"results": []}
        if resp.status_code != 200:
            logger.warning("API %d: %s", resp.status_code, date)
            return {"results": []}

        try:
            result = resp.json()
        except ValueError as e:
            logger.error("書類一覧のJSON解析に失敗 (%s): %s", date, e)
            return {"results": []}

        if use_cache:
            self._documents_cache[date] = result
            try:
                cache_path.write_text(
                    json.dumps(result, ensure_ascii=False), encoding="utf-8"
                )
            except OSError as e:
                logger.debug("書類一覧キャッシュの保存に失敗 (%s): %s", date, e)

        return result

    def download_xbrl(self, doc_id: str) -> bytes | None:
        """書類IDからXBRLをダウンロード"""
        cache_path = self.cache_dir / f"{doc_id}.xbrl"
        if self.config.get("cache_enabled") and cache_path.exists():
            logger.debug("キャッシュを使用: %s", doc_id)
            return cache_path.read_bytes()

        url = f"{self.base_url}/documents/{doc_id}"
        # 認証はセッションヘッダーのOcp-Apim-Subscription-Keyで行う
        # （クエリパラメータに載せるとURLログにAPIキーが露出するため）
        params = {"type": 1}

        resp = self._request(url, params)
        if resp is None:
            return None
        if resp.status_code != 200:
            logger.error("ダウンロード失敗 %s: HTTP %d", doc_id, resp.status_code)
            return None

        return self._extract_xbrl_from_zip(resp.content, doc_id, cache_path)

    def _extract_xbrl_from_zip(
        self, zip_bytes: bytes, doc_id: str, cache_path: Path
    ) -> bytes | None:
        """ZIPからXBRLインスタンス文書を抽出

        優先順位:
        1. PublicDoc内の .xbrl ファイル（通常XBRLインスタンス文書、財務データ本体）
        2. PublicDoc内の .htm ファイル（InlineXBRL、フォールバック）
           - ファイル名に「0105」（経理の状況）を含むものを優先し、
             なければ最大サイズのファイルを選ぶ（表紙等の空振りを避ける）
        """
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                public_files = [
                    f for f in zf.filelist if "PublicDoc" in f.filename
                ]
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
                    keiri = [f for f in htm_files if "0105" in Path(f.filename).name]
                    if keiri:
                        target = keiri[0]
                    else:
                        target = max(htm_files, key=lambda f: f.file_size)

                if not target:
                    logger.warning("ZIP内にXBRLファイルなし: %s", doc_id)
                    return None

                content = zf.read(target.filename)
                logger.debug("XBRL抽出: %s -> %s", doc_id, target.filename)

                if self.config.get("cache_enabled"):
                    try:
                        cache_path.write_bytes(content)
                    except OSError as e:
                        logger.debug("XBRLキャッシュの保存に失敗 (%s): %s", doc_id, e)

                return content
        except zipfile.BadZipFile:
            logger.error("不正なZIPファイル: %s", doc_id)
            return None

    # ------------------------------------------------------------------
    # XBRL解析
    # ------------------------------------------------------------------

    def process_financial_data(self, xml_content: bytes) -> dict | None:
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

        # 1パスでルート要素と名前空間を同時に取得する
        root = None
        namespaces: dict[str, str] = {}
        try:
            for event, obj in ET.iterparse(
                io.StringIO(xml_str), events=("start", "start-ns")
            ):
                if event == "start-ns":
                    prefix, uri = obj
                    namespaces.setdefault(prefix, uri)
                elif root is None:
                    root = obj
        except ET.ParseError as e:
            logger.error("XML解析エラー: %s", e)
            return None

        if root is None:
            logger.error("XMLにルート要素がありません")
            return None

        # ルート要素のタグで形式を判定
        is_inline = root.tag.endswith("}html") or root.tag == "html"

        ns = dict(namespaces)
        # 標準の名前空間を補完（存在しない場合のみ）
        ns.setdefault("ix", "http://www.xbrl.org/2008/inlineXBRL")
        ns.setdefault("xbrli", "http://www.xbrl.org/2003/instance")
        ns.setdefault("jpcrp_cor", "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2022-11-01/jpcrp_cor")
        ns.setdefault("jpigp_cor", "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2022-11-01/jpigp_cor")
        # jppfs_cor は日本基準の財務諸表本表タクソノミ。CF明細(PL/BS本体)の要素が属する
        ns.setdefault("jppfs_cor", "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2022-11-01/jppfs_cor")

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
    def _decode_xml(content: bytes) -> str | None:
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
            for prefix in ("jpcrp_cor:", "jpigp_cor:", "jppfs_cor:"):
                elements = root.findall(
                    f".//ix:nonFraction[@name='{prefix}{item_name}']", ns
                )
                if elements:
                    break

        for elem in elements:
            _extract_xbrl_element(elem, item_name, item_name, contexts, results)

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
            for ns_prefix in ("jpcrp_cor", "jpigp_cor", "jppfs_cor"):
                ns_uri = ns.get(ns_prefix)
                if not ns_uri:
                    continue
                elements = root.findall(f".//{{{ns_uri}}}{search_name}")
                for elem in elements:
                    _extract_xbrl_element(elem, search_name, item_name, contexts, results)

        # 企業固有名前空間で検索（タグ名末尾一致）
        for prefix, uri in namespaces.items():
            if not prefix or prefix in ("jpcrp_cor", "jpigp_cor", "jppfs_cor", "xbrli", "xlink", "link", "iso4217"):
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
        company_names: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """複数企業の財務データを分析"""
        company_data: dict[str, list] = {name: [] for name in company_names}

        logger.info("=== 分析開始 ===")
        logger.info("期間: %s ~ %s", start_date, end_date)
        logger.info("対象企業: %s", ", ".join(company_names))

        # 企業名 → EDINETコードを先に解決する
        code_to_company: dict[str, str] = {}
        for company_name in company_names:
            edinet_code = self.get_edinet_code(company_name)
            if edinet_code:
                code_to_company[edinet_code] = company_name
            else:
                logger.error("%s のEDINETコードが取得できません。スキップします。", company_name)

        if not code_to_company:
            return self._create_result_dataframe(company_data)

        # 日付範囲を1回だけ走査して全企業分の書類を収集する
        date_range = self._business_days(start_date, end_date)
        docs_by_code = self._search_documents_multi(date_range, set(code_to_company))

        for edinet_code, company_name in code_to_company.items():
            all_documents = docs_by_code.get(edinet_code, [])
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
                            "提出日": self._submit_date(doc),
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

    def list_documents(
        self, company_name: str, start_date: str, end_date: str
    ) -> list[dict] | None:
        """企業名と期間から対象書類の一覧を取得する

        EDINETコードが見つからない場合はNoneを、
        書類が見つからない場合は空リストを返す。
        """
        edinet_code = self.get_edinet_code(company_name)
        if not edinet_code:
            return None
        date_range = self._business_days(start_date, end_date)
        return self._search_documents(date_range, edinet_code)

    @staticmethod
    def _business_days(start_date: str, end_date: str) -> pd.DatetimeIndex:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        return pd.date_range(start_dt, end_dt, freq="B")

    @staticmethod
    def _submit_date(doc: dict) -> str:
        """書類のsubmitDateTimeから日付部分を安全に取り出す"""
        parts = (doc.get("submitDateTime") or "").split()
        return parts[0] if parts else ""

    def _search_documents_multi(
        self, date_range: pd.DatetimeIndex, edinet_codes: set[str]
    ) -> dict[str, list]:
        """日付範囲内の対象書類を複数企業分まとめて検索する"""
        docs_by_code: dict[str, list] = {code: [] for code in edinet_codes}
        total_days = len(date_range)

        for idx, date in enumerate(date_range):
            date_str = date.strftime("%Y-%m-%d")
            if (idx + 1) % 50 == 0 or idx == 0:
                logger.info("  書類検索中... %d/%d 日", idx + 1, total_days)

            docs = self.get_documents_list(date_str)
            for doc in docs.get("results", []):
                code = doc.get("edinetCode")
                if code in edinet_codes and doc.get("docTypeCode") in TARGET_DOC_TYPES:
                    docs_by_code[code].append(doc)
                    logger.info(
                        "  %s: 書類を検出 (%s, ID: %s)",
                        date_str,
                        code,
                        doc.get("docID"),
                    )

        return docs_by_code

    def _search_documents(
        self, date_range: pd.DatetimeIndex, edinet_code: str
    ) -> list:
        """日付範囲内の対象書類を検索（単一企業）"""
        return self._search_documents_multi(date_range, {edinet_code})[edinet_code]

    def _create_result_dataframe(self, company_data: dict) -> pd.DataFrame:
        """財務データをDataFrameに変換

        期間ごとに1行に整形する。
        例: PL_NetSales_2023-04-01_2024-03-31 → 期間開始=2023-04-01, 売上高=value
        """
        # 項目名 → 日本語名の逆引きテーブルを構築
        item_to_japanese: dict[str, str] = {}
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
                period_data: dict[str, dict] = {}

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

        # 日付列を変換（不正な値・空文字はNaT扱い。部分代入はpandas 3のstr dtypeでエラーになるため列全体を変換する）
        df["提出日"] = pd.to_datetime(df["提出日"], errors="coerce")
        df["期間終了"] = pd.to_datetime(df["期間終了"], errors="coerce")
        df["期間開始"] = pd.to_datetime(df["期間開始"].replace("", None), errors="coerce")

        # 期間終了が不正な行は後続の転置処理で扱えないため除外する
        invalid = df["期間終了"].isna()
        if invalid.any():
            logger.warning("期間終了が不正な%d行を除外しました", int(invalid.sum()))
            df = df[~invalid]
        if df.empty:
            return pd.DataFrame()

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

            # 期間ラベル → 列データの辞書を構築（例: "2024-04-01～2025-03-31"）
            col_data: dict[str, list] = {}
            for _, row in cdf.iterrows():
                start = row["期間開始"]
                end = row["期間終了"]
                if pd.notna(start) and start != "":
                    label = f"{pd.Timestamp(start).strftime('%Y-%m-%d')}～{pd.Timestamp(end).strftime('%Y-%m-%d')}"
                else:
                    label = pd.Timestamp(end).strftime("%Y-%m-%d")
                col_data[label] = [row.get(item) for item in item_cols]

            transposed = pd.DataFrame(col_data, index=item_cols)

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
