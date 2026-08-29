"""Shared visual tokens for the station monitor presentation layer."""

from pathlib import Path

import streamlit as st


APP_DIR = Path(__file__).resolve().parent

PALETTE = {
    "green": "#15803d",
    "amber": "#c2740a",
    "red": "#d43b3b",
    "gray": "#78827f",
    "accent": "#176b68",
}


def apply_styles():
    """Apply the one restrained style layer shared by all three pages."""
    variables = "\n".join(f"  --sw-{name}: {value};" for name, value in PALETTE.items())
    stationwatch_css = (APP_DIR / "assets" / "dashboard.css").read_text(
        encoding="utf-8"
    )
    st.html(
        f"""
        <style>
        :root {{
        {variables}
          --sw-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace;
        }}
        [data-testid="stMainBlockContainer"] {{
          max-width: 1480px;
          padding-top: 2rem;
          padding-bottom: 4rem;
        }}
        .bwb-kicker {{
          color: var(--sw-accent);
          font-size: 0.76rem;
          font-weight: 700;
          letter-spacing: 0.12em;
          text-transform: uppercase;
          margin-bottom: 0.35rem;
        }}
        .bwb-deck {{
          color: color-mix(in srgb, currentColor 72%, transparent);
          font-size: 1.03rem;
          max-width: 920px;
          line-height: 1.55;
          margin: 0.2rem 0 0.9rem 0;
        }}
        .bwb-note {{
          border-left: 3px solid #d18a3d;
          background: color-mix(in srgb, #d18a3d 9%, transparent);
          padding: 0.75rem 0.9rem;
          border-radius: 0 6px 6px 0;
          font-size: 0.91rem;
        }}
        .bwb-route-card {{
          min-height: 8rem;
        }}
        @media (max-width: 700px) {{
          [data-testid="stMainBlockContainer"] {{ padding-top: 1.15rem; }}
          .bwb-deck {{ font-size: 0.95rem; }}
        }}
        {stationwatch_css}
        </style>
        """
    )
