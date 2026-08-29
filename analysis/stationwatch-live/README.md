# stationwatch-live

StationWatch Live provides a lightweight operational view of whether Better With
Bees telemetry is currently reaching Google Sheets. It answers one question:

> Is fresh telemetry currently reaching Google Sheets?

It is an operational monitoring interface, not a historical analytics platform.
Long-run reliability analysis belongs to the separate reliability-audit project.

## Current capabilities

- retrieves the public Google Sheets telemetry CSV
- identifies the latest valid reading
- handles Halifax-local timestamps, including daylight-saving transitions
- computes telemetry age on a UTC timeline
- classifies telemetry as HEALTHY / AWAITING TELEMETRY / DELAYED / OFFLINE /
  SCHEDULED INACTIVE
- knows the site's nightly power schedule, so expected silence is not a fault
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
environment rather than hard-coded. Everything else — the `America/Halifax`
timezone, the operating schedule, the expected sampling interval, and the health
thresholds — is ordinary configuration that lives in source control.

**This is configuration hygiene, not secrecy.** The monitored Sheet is
intentionally publicly readable: it is the same telemetry published on
betterwithbees.ca, its CSV export answers unauthenticated requests, and the
spreadsheet ID is already committed in `scripts/apps_script/doGet.js`. Keeping
the URL in `.env` means a deployment can be pointed somewhere else without
editing source, and it keeps a deployment detail out of the diff — it does not
protect anything. Do not treat `.env` here as a secret store, and do not assume
the Sheet is access-controlled.

Copy the template and fill in the real CSV export URL of the telemetry Sheet:

```bash
cp .env.example .env
```

`.env` is git-ignored. An exported environment variable takes precedence over
the file, so a scheduler or CI job can supply it instead:

```bash
export STATIONWATCH_SHEET_URL="https://docs.google.com/spreadsheets/d/<sheet-id>/export?format=csv&gid=0"
```

If the variable is not set — or is set to something that is not a usable URL —
both interfaces report a monitor error naming the setting. They never report the
station as OFFLINE because of it, and never show a traceback.

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
timestamp, last check and the expected operating window, the configured
thresholds, recent arrival intervals,
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
| AWAITING TELEMETRY | the operating window has reopened and the first reading has not arrived yet |
| DELAYED | newest telemetry is more than 10 and less than 30 minutes old |
| OFFLINE | newest telemetry is 30 minutes old or more |
| SCHEDULED INACTIVE | the site is outside its powered window, so no telemetry is due |
| MONITOR ERROR | StationWatch has no usable observation of the source, so no status applies |

A missing `STATIONWATCH_SHEET_URL`, an unreachable Sheet, and an unreadable CSV
are all monitor errors, distinguished by the reason shown on screen.

Thresholds live in `Thresholds` in `station_health.py`. The expected sampling
interval (~5 min) is displayed for context and is not used in classification.

### The operating schedule

The site loses building power overnight, so silence between 23:00 and 06:00 is
expected and is not a fault. Without this, StationWatch reported OFFLINE for
seven hours every night — roughly 29% of all wall-clock time — and an alert that
is wrong a third of the day is an alert nobody reads.

The schedule lives in `OPERATING_WINDOWS` in `station_health.py`:

| From | Powered window |
| --- | --- |
| (start of deployment) | continuous, no scheduled shutdown |
| 2026-04-21 | 06:00–23:00 America/Halifax |

It is date-aware on purpose. Overnight silence *before* 2026-04-21 was a real
outage and is still reported as OFFLINE; SCHEDULED INACTIVE is never applied
outside the regime where the shutdown actually exists.

When the window reopens, the newest reading is still hours old through no fault
of the station. A 15-minute startup grace (`Thresholds.startup_grace_minutes`)
covers boot, association and the first sampling cycle; after it expires the
ordinary 10/30-minute limits take over, so a station that fails to come back is
still reported.

During that grace the status is **AWAITING TELEMETRY**, not HEALTHY. The grace
suppresses a premature fault, but it is not evidence that anything arrived, and
reporting HEALTHY over hours-old data would describe a station that died
overnight as working every morning. Once a genuinely fresh reading lands the
status becomes HEALTHY on its own.

The same two facts are encoded in the reliability audit's
`analysis/reliability-audit/audit_config.py`. They are duplicated rather than
shared because StationWatch depends on nothing outside the standard library; if
the schedule changes, both must be updated.

**Limitation — the schedule is a configured assumption, not a measurement.**
The hours come from the project README and from the reliability audit's
empirically-pinned changeover date, not from any machine-readable configuration
provided by the school. StationWatch does **not** verify them against recent
telemetry the way the audit's `verify_baseline_regimes()` checks its own 288/204
baseline. If the powered window is ever narrowed, StationWatch will report
OFFLINE during the new dark period until `OPERATING_WINDOWS` is updated by hand.

The opposite error is covered: telemetry that genuinely arrives during a
supposedly inactive window is reported on its own merits rather than suppressed,
so a window that is too *wide* corrects itself on screen. Automatic schedule
inference is deliberately not implemented.

### SCHEDULED INACTIVE is not a clean bill of health

It says only that no telemetry is due. With site power off, a working station
and a failed one produce exactly the same silence, so the state deliberately
asserts nothing about the hardware. The dashboard hides the freshness limits
while it is active rather than showing numbers that are not being applied.

### Daylight saving time

The Sheet stores local wall-clock text with no UTC offset. On the annual
Atlantic fall-back the same written time occurs twice, and on the spring-forward
it never occurs at all. Attaching the timezone blindly picks the first
interpretation, which can make a reading look up to an hour older than it is —
a false OFFLINE once a year, and the reverse in spring.

StationWatch resolves the ambiguity against the current clock: of the two
possible instants, it takes the later one that is not in the future. Timestamps
are then carried as UTC internally and converted back to Halifax only for
display, because subtracting two aware datetimes that share one `tzinfo` object
makes Python compare wall clocks and silently reintroduces the same error.

An ambiguous newest reading is flagged on both the CLI and the dashboard rather
than presented as certain.

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

The tests use controlled inputs and never contact Google Sheets. They cover
HEALTHY, AWAITING TELEMETRY, DELAYED, OFFLINE, SCHEDULED INACTIVE and MONITOR
ERROR; the window boundaries and the startup grace; the regime date, so the shutdown is not
excused retroactively; and both daylight-saving transitions, including the case
where the old localisation would have produced a false OFFLINE.

## Dependencies

`streamlit` (1.62 or newer — the dashboard uses `st.html`, `st.space`,
`st.skeleton` and `st.container(horizontal=...)`) and `altair`, which the chart
imports directly. Everything else — CSV parsing, HTTP, timezones, and reading
`.env` — uses the Python standard library, so the CLI runs with no third-party
packages installed.

## Future work

Not implemented, and deliberately out of scope for this version:

- transmitter heartbeat
- packet sequence numbers
- distinguishing a scheduled shutdown from a site power failure that happens to
  coincide with it
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
