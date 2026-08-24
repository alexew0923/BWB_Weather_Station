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
| 2026-04-21 → 2026-07-30 | Night shutdown period | 204 |

The transition was identified from observed telemetry behavior rather than assumed.

Evidence:

- Night-hour telemetry existed before 2026-04-21.
- Night-hour telemetry disappeared afterward.
- Daily event counts matched the two expected operating regimes.

The audit verifies these regimes during execution so future schedule changes produce warnings instead of silently invalidating calculations.

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
- per-hop failure logging,
- battery-condition analysis,
- improved sensor diagnostics,
- school-calendar-aware reliability models.

Out of scope for this audit:

- machine learning,
- dashboards,
- firmware redesign,
- database systems.
