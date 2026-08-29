# Weather Station Reliability Audit

A reproducible Python reliability analysis of the Better With Bees environmental monitoring system.

## Overview

The Better With Bees weather station is a field-deployed environmental monitoring system installed in a school pollinator garden. It collects real-world environmental telemetry including temperature, humidity, soil moisture, air pressure, rain/wetness measurements, and battery voltage.

After inheriting responsibility for the system, this audit was developed to establish a quantitative reliability baseline by answering:

- How reliably did telemetry reach the final dataset?
- When did system-level delivery failures occur?
- Which problems were sensor-specific rather than communication-related?
- Which conclusions can and cannot be supported by the available telemetry?

The goal is not to repair or redesign the system, but to objectively measure its current behavior before future hardware, firmware, or monitoring improvements.

---

# System Architecture

The Better With Bees monitoring pipeline consists of:

```text
Environmental Sensors
        |
        v
XIAO ESP32C3 Transmitter
        |
        | ESP-NOW wireless communication
        v
XIAO ESP32C3 Receiver
        |
        | I2C
        v
ESP32 Data Bridge
        |
        | School Wi-Fi
        v
Google Apps Script
        |
        v
Google Sheets HistoricalData
        |
        v
Python Reliability Audit
```

The audit analyzes historical telemetry only.

It does not modify:

- firmware,
- Apps Script,
- hardware,
- or the live data pipeline.

---

# Problem

The weather station is a deployed sensing system with no application-level delivery guarantees across the full pipeline.

The communication chain contains several possible failure points:

- ESP-NOW wireless communication
- receiver processing
- ESP32-to-ESP32 communication
- school Wi-Fi availability
- Apps Script ingestion
- Google Sheets storage

When telemetry is missing from the dataset, the existing system does not record which stage failed or whether a measurement was ever successfully transmitted.

After months of operation, there was no quantitative answer to:

> How much telemetry actually arrived, when did failures occur, and were failures caused by the system or individual sensors?

This audit establishes that baseline.

---

# Goal

Create one reproducible analysis artifact that:

- measures telemetry delivery reliability,
- identifies significant data gaps,
- separates system-level failures from sensor-level failures,
- preserves ambiguous behavior instead of silently correcting it.

Explicit non-goals:

- machine learning,
- forecasting,
- dashboards,
- live alerting,
- databases,
- firmware redesign.

---

# Running the Audit

From this directory:

```bash
.venv/bin/python reliability_audit.py data/HistoricalData.csv
```

An optional output directory can be provided:

```bash
.venv/bin/python reliability_audit.py data/HistoricalData.csv output_directory/
```

The audit generates CSV reports and diagnostic figures.

### Project structure

`reliability_audit.py` remains the command-line entry point. The implementation
is split by responsibility without changing the command or generated outputs:

| Module | Responsibility |
|---|---|
| `audit_config.py` | operating schedule, fixed assumptions, thresholds and shared constants |
| `data_validation.py` | CSV loading, schema checks and integrity handling |
| `outage_analysis.py` | inter-arrival gaps and outage classification |
| `reliability_metrics.py` | daily row, sensor and reliability calculations |
| `reporting.py` | console summaries and anomaly notes |
| `visualization.py` | PNG figure generation |
| `test_reliability_audit.py` | regression tests for the expected-count and timestamp logic |

### Running the tests

```bash
.venv/bin/python -m unittest test_reliability_audit -v
```

The tests cover the calculations whose failure would change a reliability
conclusion: regime-aware expected counts, the reconciliation identity, DST
handling, gap severity and day classification. Presentation code and figure
generation are deliberately untested -- a wrong plot is visible, a wrong
denominator is not.

---

# Key Results

Dataset analyzed:

| Metric | Result |
|---|---:|
| Dataset period | 2025-11-01 to 2026-07-30 |
| Telemetry records analyzed | 24,833 |
| Calendar days analyzed | 272 |
| Overall telemetry completeness | 35.6% |
| Days with no telemetry received | 120 |
| Longest telemetry blackout | 19 days |
| Missed transmissions | 45,989 |
| Duplicate/repeat rows occupying no new slot | 970 |

Every scheduled transmission is accounted for exactly:

```text
received 24,833  +  missed 45,989  -  surplus 970  =  expected 69,852
```

The audit prints this reconciliation on every run and warns if the residual is
not zero. See "Expected transmissions are counted once" below for why.

Major findings:

- The dominant reliability issue was absence of telemetry rather than gradual sensor degradation.
- The station operated under two different sampling regimes during deployment.
- Duplicate/repeated transmissions caused misleadingly high row counts on certain days.
- Sensor reliability and telemetry delivery reliability were separate failure modes.
- Soil moisture and air pressure exhibited independent sensor-level anomalies.

---

# Methodology

## Telemetry Delivery vs Sensor Completeness

The audit does not treat every missing value as the same failure.

Two separate metrics are calculated:

| Metric | Question | Denominator |
|---|---|---|
| Telemetry delivery completeness | Did a measurement event reach the dataset? | Expected scheduled events |
| Sensor completeness | When telemetry arrived, was the sensor value populated? | Received telemetry events |

Example:

A day where 10 telemetry events arrive and all contain temperature data has:

- low telemetry completeness,
- 100% temperature completeness.

This indicates a delivery problem rather than a temperature sensor failure.

---

# Establishing Expected Sampling Rates

The station did not operate under one constant schedule throughout deployment.

Two operating regimes were identified:

| Period | Operating schedule | Expected events/day |
|---|---|---:|
| 2025-11-01 → 2026-04-20 | Continuous operation | 288 |
| 2026-04-21 → 2026-07-30 | Night shutdown, 06:00–23:00 | 204 |

Two days are neither: daylight-saving transitions make 2025-11-02 a 25-hour day
(300 schedulable events) and 2026-03-08 a 23-hour day (276). They cancel, so the
272-day total is unchanged at 69,852.

The transition was identified from observed telemetry behavior rather than assumed.

Evidence:

- Night-hour telemetry existed before 2026-04-21.
- Night-hour telemetry disappeared afterward.
- Daily event counts matched the two expected operating regimes.

The audit verifies these regimes during execution so future schedule changes produce warnings instead of silently invalidating calculations.

---

# Expected Transmissions Are Counted Once

The operating schedule lives in one place, `OPERATING_REGIMES` in
`audit_config.py`, and every expected, missed and completeness figure is derived
from it through `scheduled_transmissions_between()`. Nothing else in the audit
computes an expected count.

This matters because the audit previously had two. The daily denominator was
regime-aware, but the missed-transmission count divided raw gap minutes by five,
as if the station were schedulable around the clock. After the nightly shutdown
began, a gap spanning the powered-down hours was charged for transmissions that
were never schedulable. The two figures disagreed by 3,811 transmissions (5.5%)
across the dataset, and by 29% on the single 2026-04-22 → 2026-05-08 outage
(4,645 claimed against 3,301 actually schedulable).

Rows are positioned on a *powered timeline*: each row carries the index of the
scheduled slot it occupies, skipping every minute the station had no power.
Losses are differences between those indices, so the per-interval terms
telescope and the reconciliation is exact rather than approximate.

---

# Duplicate and Repeated Telemetry

Raw CSV rows are not automatically assumed to represent unique measurements.

The dataset contains repeated telemetry records caused by apparent repeated transmissions or downstream duplication.

The audit preserves:

- raw row count,
- unique telemetry events,
- duplicate/repeated records.

Duplicate telemetry is treated as a data-integrity issue rather than additional system availability.

---

# Gap Detection

Timestamp intervals are analyzed to identify missing telemetry periods.

Thresholds are derived from the empirical inter-arrival distribution before classification.

The audit reports:

- gap start and end times,
- duration,
- estimated missed transmissions,
- severity category,
- surrounding telemetry counters.

The analysis does not claim the exact failed component.

A missing telemetry event could originate from:

- transmitter failure,
- ESP-NOW communication loss,
- receiver failure,
- Wi-Fi interruption,
- Apps Script ingestion failure.

The current telemetry does not contain enough information to identify the failed hop.

---

# Daily Reliability Classification

Daily classifications are generated using:

1. telemetry completeness,
2. gap severity,
3. sensor-level completeness.

The audit distinguishes between:

- good operation,
- transmission loss,
- telemetry blackout,
- sensor-level issues,
- over-baseline event rates.

A telemetry blackout means zero records reached the final dataset. It does not prove which physical component failed.

---

# Detailed Findings

## Telemetry Reliability

Overall telemetry completeness:

**35.6%**

The reliability distribution was:

| Day classification | Days | Share |
|---|---:|---:|
| Telemetry blackout | 120 | 44.1% |
| Minor transmission loss | 50 | 18.4% |
| Partial transmission loss | 39 | 14.3% |
| Good day | 30 | 11.0% |
| Severe transmission loss | 26 | 9.6% |
| Sensor-level issue | 5 | 1.8% |
| Over-baseline | 2 | 0.7% |

Important observations:

- 120 of 272 days produced no telemetry.
- The longest telemetry blackout lasted 19 days.
- Two days exceeded expected event rates because of repeated/fast-cycling telemetry behavior:
  - 2025-12-17
  - 2025-12-18

---

## Sensor Completeness

Sensor completeness within received telemetry events:

| Sensor | Completeness |
|---|---:|
| Rain Value | 99.2% |
| Humidity | 97.9% |
| Temperature | 97.7% |
| Air Pressure | 91.0% |
| Battery Voltage | 63.8% |
| Soil Moisture | 55.3% |

Interpretation:

- Battery voltage was introduced partway through deployment and should not be interpreted using overall missing percentage.
- Soil moisture showed a major change in behavior during deployment.
- Air pressure experienced periods of missing and physically abnormal values.

---

# Diagnostic Visualizations

## Daily Telemetry Completeness

Shows the percentage of expected telemetry events received per day.

![Daily completeness](https://github.com/user-attachments/assets/c149b49b-4707-4c8d-9f7d-f6a22c1139dd)

## Gap Distribution

Shows the observed timestamp-gap distribution used to derive outage thresholds.

![Gap distribution](https://github.com/user-attachments/assets/e1b48ce2-1521-4c26-aa6b-d758600815e1)

## Daily Largest Telemetry Gap

Shows the largest telemetry interruption observed each day.

![Largest daily gap](https://github.com/user-attachments/assets/463e2a02-f703-4ae8-b871-fbae561af13e)

---

# Outputs

| File | Contents |
|---|---|
| `outage_intervals.csv` | Detected telemetry gaps, durations, severity, and surrounding counters |
| `sensor_completeness.csv` | Daily sensor-level completeness |
| `daily_reliability.csv` | Daily reliability metrics and classifications |
| Diagnostic figures | Reliability and outage visualizations |

---

# Development Environment

A virtual environment is used for reproducibility.

Create:

```bash
python3 -m venv .venv
```

Install dependencies:

```bash
.venv/bin/pip install -r requirements.txt
```

Dependencies:

- pandas
- numpy
- matplotlib

Additional development files:

- `pyrightconfig.json` configures VS Code/Pylance type checking.
- `requirements.lock.txt` records the exact package versions used during development.

Ignored files:

- `.venv/`
- generated CSV outputs
- generated PNG outputs

---

# Investigated Limitations and Anomalies

## Corrupted Telemetry Frame

One corrupted record contained impossible counter and sensor values.

Handling:

- The received row is preserved as evidence.
- Invalid fields are excluded from analysis.
- The cause is not determined.

---

## Zero Values vs Missing Values

Historical data handling does not always allow distinction between:

- a true zero reading,
- a missing value,
- an invalid sensor output.

The audit therefore does not globally convert zeros to missing values.

---

## Soil Moisture Behavior Change

Soil moisture showed a major multi-month change:

- missing values increased substantially,
- later readings occupied a different range.

The cause cannot be determined from telemetry alone.

Possible explanations include:

- hardware changes,
- probe changes,
- wiring changes,
- environmental changes.

---

## Air Pressure Anomalies

Air pressure experienced:

- periods of missing data,
- abnormal readings,
- changes in value distribution.

The cause remains unresolved.

---

## Timestamp Limitations

Timestamps represent receipt time rather than physical measurement time.

Therefore:

- Wi-Fi latency,
- Apps Script delays,
- transmission delays

are included in observed timing behavior.

### Daylight saving time

The sheet stores Apps Script receipt time as local wall-clock text with no UTC
offset, so the raw column is not a monotonic timeline. The dataset contains a
real Atlantic fall-back: `2025-11-02 01:58:38` is followed by `01:03:33`, with
`Count` still incrementing across it.

Timestamps are therefore localized to `America/Halifax` before any duration is
computed. Ambiguous fall-back timestamps are resolved from **file order** rather
than guessed: rows reach the sheet in arrival order, so within the repeated hour
everything before the backward step belongs to the first pass and everything
after to the second. Non-existent spring-forward timestamps are shifted forward
out of the skipped hour and reported.

Left unhandled, this produced:

- one backward "timestamp jump" reported as a receipt-clock anomaly (now 0);
- twelve spurious sub-minute gaps on 2025-11-02, created purely by sorting the
  repeated hour into itself (sub-nominal repeats: 758 → 747);
- a 25-hour day measured against a 24-hour denominator.

**Limitation:** for a single isolated row inside a repeated hour there is no
information in the source that can resolve it. The audit can only do so because
it has file order. A future export that loses row order would lose this.

---

## Ingestion-Behavior Changes (not modelled)

The audit models the *sampling* schedule. It does not model changes to the
*ingestion* code, which also alter what a cell means. `git log` on
`scripts/apps_script/doGet.js` shows two:

| Date | Change | Effect on the data |
|---|---|---|
| 2025-12-16 (`93a2aa5`) | zero-blanking added for columns 2–6; the `count != lastValue` de-duplication guard removed | a literal `0` from those sensors becomes an empty cell; repeat transmissions are no longer suppressed at ingest |
| 2026-04-05 (`7e8687d`) | the `i > 1` exemption removed | a genuine `0.00 °C` reading also becomes an empty cell |

Consequences that are visible in the data but **deliberately not corrected**:

- "Blank" does not mean the same thing across the deployment. Before 2025-12-16
  it meant `NaN` only; afterwards it also means "read exactly zero"; from
  2026-04-05 that includes Temperature, which is a core sensor. Soil-moisture
  literal zeros stop entirely after December, and the last Temperature zero is
  2026-04-08.
- All 551 December sub-minute repeat rows fall on or after 2025-12-16, and none
  before it. The two "Over-baseline (fast-cycling)" days are 2025-12-17 and
  2025-12-18, immediately after the de-duplication guard was removed. The audit
  still classifies them on their row counts, but that classification cannot
  separate "the node started repeat-transmitting" from "ingest stopped
  suppressing repeats it had always received".

These are not folded into the calculations because doing so would require
knowing what was actually deployed and when. `GOOGLE_SCRIPT_ID` in the firmware
is a placeholder and there is no `clasp` manifest or deployment record in the
repository, so the committed Apps Script cannot be shown to be the code that
produced any given row. Correcting for an unverified deployment date would
replace a known uncertainty with an invented one.

---

## Failure Attribution Limitations

The audit cannot determine whether missing telemetry was caused by:

- transmitter,
- ESP-NOW communication,
- receiver,
- Wi-Fi,
- Apps Script.

Additional device-side logging would be required.

---

# Future Work

Potential future improvements:

- live reliability monitoring,
- maintenance alerts,
- device-side sequence numbers,
- an archive-stage audit (`archiveOldSensorData` sits between the sheet and this
  dataset and is not currently observable; a day lost there is indistinguishable
  from a day the station never transmitted),
- per-hop failure logging,
- battery-condition analysis,
- improved sensor diagnostics,
- school-calendar-aware reliability models.

Out of scope for this audit:

- machine learning,
- dashboards,
- firmware redesign,
- database systems.
