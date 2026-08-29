"""Unified Streamlit UI for Better With Bees station monitoring."""

from pathlib import Path

import streamlit as st

from styles import apply_styles


APP_DIR = Path(__file__).resolve().parent

st.set_page_config(
    page_title="Better With Bees environmental monitoring",
    page_icon=":material/sensors:",
    layout="wide",
    initial_sidebar_state="collapsed",
)
apply_styles()

overview = st.Page(
    APP_DIR / "app_pages" / "overview.py",
    title="Overview",
    icon=":material/home:",
    default=True,
)
live = st.Page(
    APP_DIR / "app_pages" / "live.py",
    title="Live monitoring",
    icon=":material/sensors:",
    url_path="live",
)
battery = st.Page(
    APP_DIR / "app_pages" / "battery.py",
    title="Battery analysis",
    icon=":material/battery_charging_full:",
    url_path="battery",
)

st.navigation([overview, live, battery], position="top").run()
