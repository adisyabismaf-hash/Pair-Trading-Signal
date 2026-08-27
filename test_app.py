"""Headless smoke test of the Streamlit app across all 4 pages."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("DATABASE_URL", os.environ.get("DATABASE_URL", ""))
os.environ.setdefault("TELEGRAM_BOT_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
os.environ.setdefault("TELEGRAM_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", ""))
os.environ.setdefault("EXTENDED_BASE_URL", "https://api.starknet.extended.exchange")

from streamlit.testing.v1 import AppTest

PAGES = ["📊 Dashboard", "👁️ Watchlist", "⚡ Sinyal", "📈 Performa"]


def run_page(label: str):
    at = AppTest.from_file("streamlit_app.py", default_timeout=90)
    at.run()
    if at.exception:
        raise AssertionError(f"[{label}] exception on initial run: {[e.value for e in at.exception]}")
    # switch nav
    at.sidebar.radio[0].set_value(label).run()
    if at.exception:
        raise AssertionError(f"[{label}] exception after nav: {[e.value for e in at.exception]}")
    name = label.split(" ", 1)[1]
    print(f"PASS {name:12s} - metrics={len(at.metric)} tables={len(at.dataframe)} markdown={len(at.markdown)}")


if __name__ == "__main__":
    for p in PAGES:
        run_page(p)
    print("\nALL PAGES OK")
