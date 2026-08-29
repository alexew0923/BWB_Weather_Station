"""Shared metric formatting used across overview and live views."""

import streamlit as st


def summary_metrics(items):
    """Render a small responsive row of labeled summary values."""
    with st.container(horizontal=True, wrap=True, gap="small"):
        for label, value, help_text in items:
            st.metric(label, value, border=True, help=help_text)
