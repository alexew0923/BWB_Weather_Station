# Better With Bees station monitor

## Purpose

This is the unified local UI for Better With Bees operational monitoring and
historical battery research. It deliberately keeps presentation separate from
the reusable analysis engines.

## Architecture

```text
apps/station-monitor/
→ Streamlit routing, presentation, caching, charts, and page-specific errors

analysis/stationwatch-live/
→ live data loading, parsing, schedule logic, freshness classification, and diagnostics

analysis/battery-energy-analysis/
→ historical battery calculations, modeling, CLI, reports, and core tests
```

The dependency direction is app → analysis. Neither analysis engine imports
Streamlit, and the app does not duplicate status or battery calculations.

## Data sources

```text
/live
→ configured public Google Sheet CSV
→ current operational delivery state

/battery
→ analysis/reliability-audit/data/HistoricalData.csv by default
→ historical battery and reliability research
```

The overview uses the live source for station status, last telemetry, and the
latest battery field. It does not substitute historical data when live telemetry
is unavailable.

## Configuration

Set the public Sheet export URL using the existing StationWatch pattern:

```bash
export STATIONWATCH_SHEET_URL="https://docs.google.com/spreadsheets/d/<sheet-id>/export?format=csv&gid=0"
```

Alternatively, copy `analysis/stationwatch-live/.env.example` to
`analysis/stationwatch-live/.env`. Exported environment variables take
precedence. Do not commit local configuration or credentials.

The battery page uses project-relative defaults. Optional overrides are:

```bash
export BWB_HISTORICAL_CSV=/path/to/HistoricalData.csv
export BWB_RELIABILITY_OUTPUT_DIR=/path/to/reliability-audit-output
```

## Run

From the repository root:

```bash
python -m pip install -r apps/station-monitor/requirements.txt
streamlit run apps/station-monitor/app.py
```

Routes are explicit:

- `/` — overview
- `/live` — StationWatch live monitoring
- `/battery` — battery and energy analysis

Live monitoring is intentionally uncached and can auto-refresh every 45 seconds.
Historical analysis is cached in the UI layer using source-file fingerprints;
changing the CSV or reliability exports invalidates that cache.

## Limits

- `OFFLINE` means fresh telemetry has not reached Google Sheets; it does not
  identify a failed physical component.
- `MONITOR ERROR` means the source could not be observed and is not an outage.
- Observed voltage is not battery percentage, state of charge, health, or
  remaining runtime.
- The energy model is uncalibrated and uses only explicit caller inputs.
- Sensor-health analysis and deployment configuration are outside this app.
