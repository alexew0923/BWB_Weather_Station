# stationwatch-live

StationWatch Live provides a lightweight operational view of whether Better With
Bees telemetry is currently reaching Google Sheets. It answers one question:

> Is fresh telemetry currently reaching Google Sheets?

It is an operational monitoring interface, not a historical analytics platform.
Long-run reliability analysis belongs to the separate reliability-audit project.

## Current capabilities

- retrieves the public Google Sheets telemetry CSV
- identifies the latest valid reading
- handles Halifax-local timezone-aware timestamps (`ZoneInfo("America/Halifax")`)
- computes telemetry age
- classifies telemetry as HEALTHY / DELAYED / OFFLINE
- terminal CLI
- Streamlit live dashboard
- recent telemetry context (latest readings and their arrival gaps)
- clear monitor-error handling, kept separate from an OFFLINE station status

## Project layout

```
stationwatch-live/
├── station_health.py        shared health logic (no printing, no UI)
├── station_watch.py         terminal CLI
├── app.py                   Streamlit dashboard
├── dashboard.css            dashboard styling, loaded by app.py
├── .streamlit/config.toml   theme: colours, fonts, radii
├── test_station_watch.py    tests, using controlled inputs only
├── .env.example             template for local configuration
├── requirements.txt
├── README.md
└── .gitignore
```

`station_health.py` is the single source of truth for the thresholds and the
health calculation. Both interfaces render the same `HealthReport`; neither
computes its own timestamps or thresholds.

## Configuration

The telemetry URL points at a specific Google Sheet, so it is supplied by the
environment and is never committed. Everything else — the `America/Halifax`
timezone, the expected sampling interval, and the health thresholds — is
ordinary configuration that lives in source control.

Copy the template and fill in the real CSV export URL of the telemetry Sheet:

```bash
cp .env.example .env
```

`.env` is git-ignored. An exported environment variable takes precedence over
the file, so a scheduler or CI job can supply it instead:

```bash
export STATIONWATCH_SHEET_URL="https://docs.google.com/spreadsheets/d/<sheet-id>/export?format=csv&gid=0"
```

If the variable is not set, both interfaces report a monitor error naming the
missing setting. They never report the station as OFFLINE because of it.

## Running the CLI

```bash
python station_watch.py
```

Example output:

```
Better With Bees — StationWatch Live
────────────────────────────────────
Status:          HEALTHY
Last telemetry:  2026-08-29 00:12 ADT
Age:             4m 18s
Checked:         2026-08-29 00:16 ADT

Fresh telemetry is reaching Google Sheets.
```

The CLI exits `0` on a successful check and `1` on a monitor error.

## Running the dashboard

Install the one dependency, then start Streamlit:

```bash
pip install -r requirements.txt
```

```bash
streamlit run app.py
```

The dashboard leads with the current state, then telemetry age, the latest
timestamp and last check, the configured thresholds, recent arrival intervals,
and recent readings. Its theme lives in `.streamlit/config.toml` and its layout
styling in `dashboard.css`; the semantic status colours are defined once in
`PALETTE` in `app.py` and shared by the stylesheet and the chart. Those colours
hold their contrast against both the light and the dark background, so the page
reads correctly in either theme without having to detect which is active.

Refreshing is deliberately unhurried, because the station itself samples every
few minutes:

- **Refresh now** re-reads the Sheet immediately.
- **Auto-refresh** re-reads it every 45 seconds while enabled.

Results are never cached, so any refresh shows current Google Sheets data.

## Statuses

| Status | Meaning |
| --- | --- |
| HEALTHY | newest telemetry is 10 minutes old or less |
| DELAYED | newest telemetry is more than 10 and less than 30 minutes old |
| OFFLINE | newest telemetry is 30 minutes old or more |
| MONITOR ERROR | StationWatch has no usable observation of the source, so no status applies |

A missing `STATIONWATCH_SHEET_URL`, an unreachable Sheet, and an unreadable CSV
are all monitor errors, distinguished by the reason shown on screen.

Thresholds live in `Thresholds` in `station_health.py`. The expected sampling
interval (~5 min) is displayed for context and is not used in classification.

### MONITOR ERROR is not OFFLINE

These are different states and are never conflated:

- **MONITOR ERROR** — the local connection is down, Google is unreachable, the
  request fails, or the CSV cannot be understood. StationWatch cannot observe the
  data source, so the telemetry status is unknown.
- **OFFLINE** — StationWatch reached Google Sheets successfully, and the newest
  telemetry there is stale.

A monitoring failure is never reported as an OFFLINE station.

## Observation boundary

StationWatch currently monitors the Google Sheets endpoint. An OFFLINE state
means fresh telemetry is no longer reaching Sheets; it does not yet determine
which upstream component failed.

Potential upstream failure domains include the sensor/device operation, the
transmitter, the ESP-NOW link, the receiver, Wi-Fi, the Apps Script upload, and
Google Sheets ingestion. StationWatch can detect that delivery stopped; it cannot
yet identify where. It never claims the station, transmitter, or receiver is
broken.

## Running the tests

```bash
python -m unittest -v
```

The tests use controlled inputs and never contact Google Sheets, so they cover
HEALTHY, DELAYED, OFFLINE, and MONITOR ERROR without touching production
telemetry.

## Dependencies

`streamlit` only, for the dashboard. The chart uses Altair, which Streamlit
already installs. Everything else — CSV parsing, HTTP, timezones, and reading
`.env` — uses the Python standard library, so the CLI runs with no third-party
packages installed.

## Future work

Not implemented, and deliberately out of scope for this version:

- transmitter heartbeat
- packet sequence numbers
- receiver health
- ESP-NOW diagnostics
- Wi-Fi and upload diagnostics
- battery health
- sensor health
- component-level failure isolation
- notifications/alerts, added later as a separate optional layer once an official
  Better With Bees project email account is available

This version has no notification layer of any kind. Its only configuration is the
telemetry URL described above; no credentials are needed to read a publicly
viewable Sheet.
