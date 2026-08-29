# Better With Bees station monitor

## Purpose

This is the unified local UI for Better With Bees operational monitoring,
historical outage forensics, and battery research. It deliberately keeps
presentation separate from the reusable analysis engines.

## Architecture

```text
apps/station-monitor/
→ Streamlit routing, presentation, caching, charts, and page-specific errors

analysis/stationwatch-live/
→ live data loading, parsing, schedule logic, freshness classification, and diagnostics

analysis/reliability-audit/
→ historical validation, schedule-aware outage detection, incident analysis, evidence classification, and reports

analysis/battery-energy-analysis/
→ historical battery calculations, modeling, CLI, reports, and core tests
```

The dependency direction is app → analysis. No analysis engine imports
Streamlit, and the app does not duplicate status, incident, or battery
calculations.

## Data sources

```text
/live
→ configured public Google Sheet CSV
→ current operational delivery state

/incidents
→ the same configured historical Google Sheets CSV export as `/battery`
→ detected significant outages and evidence-bounded incident reports

/battery
→ configurable Google Sheets CSV export fetched by the app service
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

The deployed incident and battery pages require `HISTORICAL_DATA_URL`. Set it
to either a Google Sheets edit URL or a CSV export URL:

```bash
export HISTORICAL_DATA_URL="https://docs.google.com/spreadsheets/d/<sheet-id>/edit?gid=<gid>#gid=<gid>"
```

The app validates and normalizes Google Sheets links to
`/export?format=csv&gid=...`, fetches them in the app/service layer, and caches
the resulting historical computations for ten minutes. Network, permission,
HTML-response, empty-response, and analysis-validation failures remain
page-specific and do not break the other routes.

If neither `BWB_HISTORICAL_CSV` nor `HISTORICAL_DATA_URL` is configured, the
incident and battery pages show configuration messages and remain safely
isolated from the other routes.

If `BWB_HISTORICAL_CSV` is explicitly set, it takes precedence and both
historical pages use that local file instead. This remains an optional override
for local development and isolated UI tests. The Incident Explorer and battery
CLIs, plus their core tests, continue to accept file paths directly:

```bash
export BWB_HISTORICAL_CSV=/path/to/HistoricalData.csv
export BWB_RELIABILITY_OUTPUT_DIR=/path/to/reliability-audit-output
```

Both pages use the same local-first source resolver. The visible source label is
`Local historical data` or `Remote historical data`; local filesystem paths and
the configured remote URL are not displayed in the dashboard.

## Run

From the repository root:

```bash
python -m pip install -r apps/station-monitor/requirements.txt
streamlit run apps/station-monitor/app.py
```

Routes are explicit:

- `/` — overview
- `/live` — StationWatch live monitoring
- `/incidents` — significant-outage forensics and recovery analysis
- `/battery` — battery and energy analysis

Live monitoring is intentionally uncached and can auto-refresh every 45 seconds.
Remote historical analysis is cached in the UI layer for ten minutes. A local
override uses source-file fingerprints, so changing the CSV invalidates the
incident cache; battery reliability-export fingerprints retain their existing
cache invalidation behavior.

## Limits

- `OFFLINE` means fresh telemetry has not reached Google Sheets; it does not
  identify a failed physical component.
- `MONITOR ERROR` means the source could not be observed and is not an outage.
- A detected historical delivery gap does not identify which physical or cloud
  component failed; Incident Explorer preserves Observed, Suggestive, and Not
  determinable evidence classes.
- Expected incident transmissions follow the reliability audit's operating
  schedule and exclude intentionally inactive periods.
- Observed voltage is not battery percentage, state of charge, health, or
  remaining runtime.
- The energy model is uncalibrated and uses only explicit caller inputs.
- Sensor-health analysis and deployment configuration are outside this app.
