"""Configuration read from environment variables.

On Streamlit Cloud the values come from the app's Secrets (copied into env by
streamlit_app.py). On GitHub Actions they come from repository Secrets. Locally
they can come from a real environment or a .env exported beforehand.
"""
import os


def _get(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


class Settings:
    def __init__(self) -> None:
        self.database_url = _get(
            "DATABASE_URL",
            "postgresql+psycopg2://pairtrader:***PASSWORD_REMOVED***@localhost:5432/trading_signals",
        )
        self.extended_base_url = _get("EXTENDED_BASE_URL", "https://api.starknet.extended.exchange")
        self.telegram_bot_token = _get("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = _get("TELEGRAM_CHAT_ID", "")
        self.scan_interval_minutes = int(_get("SCAN_INTERVAL_MINUTES", "15"))

        # Pair trading defaults
        self.pair_lookback = int(_get("PAIR_LOOKBACK", "60"))
        self.pair_entry_zscore = float(_get("PAIR_ENTRY_ZSCORE", "2.0"))
        self.pair_exit_zscore = float(_get("PAIR_EXIT_ZSCORE", "0.5"))
        self.pair_stop_zscore = float(_get("PAIR_STOP_ZSCORE", "4.0"))
        self.pair_max_holding_days = int(_get("PAIR_MAX_HOLDING_DAYS", "30"))

        # MA50 retest defaults
        self.ma_period = int(_get("MA_PERIOD", "50"))
        self.retest_tolerance_pct = float(_get("RETEST_TOLERANCE_PCT", "0.4"))
        self.retest_window = int(_get("RETEST_WINDOW", "60"))
        self.risk_reward = float(_get("RISK_REWARD", "2.0"))


settings = Settings()
