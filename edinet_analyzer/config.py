"""設定管理モジュール

優先順位:
1. 環境変数 (EDINET_SUBSCRIPTION_KEY)
2. .env ファイル
3. config/config.json
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "config.json"
ENV_PATH = ROOT_DIR / ".env"

DEFAULTS = {
    "rate_limit": 1,
    "max_retries": 3,
    "timeout": 30,
    "cache_enabled": True,
}


def _load_dotenv() -> None:
    """簡易的な .env ファイル読み込み（python-dotenv不要）"""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            os.environ.setdefault(key, value)


def load_config() -> dict:
    """設定を読み込んで返す"""
    _load_dotenv()

    config = dict(DEFAULTS)

    # config.json から読み込み
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                file_config = json.load(f)
            config.update(file_config)
        except Exception as e:
            logger.warning("config.json の読み込みに失敗しました: %s", e)

    # 環境変数で上書き
    env_key = os.environ.get("EDINET_SUBSCRIPTION_KEY")
    if env_key:
        config["subscription_key"] = env_key

    if not config.get("subscription_key"):
        raise ValueError(
            "EDINET APIキーが設定されていません。\n"
            "以下のいずれかで設定してください:\n"
            "  1. 環境変数: export EDINET_SUBSCRIPTION_KEY=your_key\n"
            "  2. .env ファイル: EDINET_SUBSCRIPTION_KEY=your_key\n"
            "  3. config/config.json: {\"subscription_key\": \"your_key\"}"
        )

    return config
