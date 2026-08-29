"""Centralized import bridge for the repository's hyphenated analysis folders."""

import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
STATIONWATCH_DIR = REPOSITORY_ROOT / "analysis" / "stationwatch-live"
BATTERY_DIR = REPOSITORY_ROOT / "analysis" / "battery-energy-analysis"
RELIABILITY_DIR = REPOSITORY_ROOT / "analysis" / "reliability-audit"

for engine_dir in (STATIONWATCH_DIR, BATTERY_DIR, RELIABILITY_DIR):
    engine_path = str(engine_dir)
    if engine_path not in sys.path:
        sys.path.insert(0, engine_path)
