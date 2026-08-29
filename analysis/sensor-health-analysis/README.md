# Historical Sensor-Health Analysis

This project asks a narrower question than the reliability audit:

> When telemetry successfully reached the historical dataset, how trustworthy
> were the individual sensor measurements?

It is a reproducible, deterministic CLI analysis. It does not run a dashboard,
modify the live system, repair data, or identify a failed hardware component.

## Relationship to the sibling analyses

| Project | Question |
|---|---|
| `../reliability-audit/` | Did scheduled telemetry reach the historical dataset? |
| `../battery-energy-analysis/` | How did the observed power system behave? |
| this project | When a row arrived, were its sensor fields populated and plausible? |
| `../stationwatch-live/` | What is the station's current operational status? |

The CLI narrowly reuses the reliability audit's validated CSV loader and
`America/Halifax` DST localization. It does not copy the outage detector or the
battery model.

## Run

From this directory, using the analysis-level environment:

```bash
../.venv/bin/python sensor_analysis.py ../reliability-audit/data/HistoricalData.csv
```

An alternate output directory is optional:

```bash
../.venv/bin/python sensor_analysis.py /path/to/HistoricalData.csv /path/to/output
```

The default `output/` directory is gitignored. Dependencies are only pandas,
NumPy, and Matplotlib:

```bash
python -m pip install -r requirements.txt
```

Run the focused synthetic suite with:

```bash
../.venv/bin/python -m unittest -v test_sensor_analysis.py
```

## Outputs

- `sensor_summary.csv`: concrete per-sensor completeness and event counts;
- `sensor_daily_metrics.csv`: daily completeness within received rows;
- `sensor_regime_metrics.csv`: completeness, median, IQR, variance, and range
  across documented interpretation regimes;
- `anomaly_events.csv`: event-based missing, bound, flatline, change, and
  cross-sensor evidence;
- `pressure_anomaly_clusters.csv`: pressure-specific Observed / Suggestive /
  Not determinable statements and recovery readings;
- `analysis_summary.json`: machine-readable assumptions, rules, counts, and
  future instrumentation candidates;
- four PNG diagnostics for completeness, event timing, pressure, and empirical
  rates of change.

No historical CSV is copied into this project.

## What is measured

### Sensor-specific completeness

The denominator is received rows, never expected transmission slots. A missing
12:10 telemetry row is delivery evidence and belongs to the reliability audit.
A received 12:10 row with only pressure blank is sensor-field evidence and
belongs here. Missing runs break across telemetry gaps so station outages cannot
be turned into long sensor failures.

### Values outside transparent bounds

The broad impossible bounds are inherited from the reliability audit and are
reported without deleting or imputing readings:

| Field | Impossible outside | Additional suspicious band | Meaning |
|---|---:|---:|---|
| Temperature | -50 to 60 °C | -35 to 45 °C | broad ambient bounds |
| Humidity | 0 to 100 %RH | none | physical percentage definition |
| Air Pressure | 800 to 1100 hPa | 870 to 1085 hPa | broad near-surface pressure bounds |
| Soil Moisture | 0 to 4095 | none | raw 12-bit ADC only |
| Rain Value | 0 to 4095 | none | raw 12-bit wetness ADC only |
| Battery Voltage | 0 to 6000 mV | none | context only; battery project is authoritative |

“Suspicious” is not “impossible,” and neither label assigns a component cause.

### Flatlines

Flatlines require both a minimum sample count and real elapsed duration. The
configured tolerances reflect stored resolution: 0.02 °C, 0.05 %RH, 0.05 hPa,
and exact equality for raw ADC channels. Temperature, humidity, and pressure
require 12 samples spanning at least 55 minutes. Soil requires six documented
opportunities spanning 150 minutes. Rain/wetness requires 36 samples and a full
day. Gaps over each sensor's continuity window end the run.

A 100% RH plateau or 4095 wetness-ADC plateau can be natural saturation/dry
conditions. Those are retained as minor context and are not classified as a
sensor failure by themselves.

### Rates of change

Every rate uses actual UTC elapsed minutes between localized timestamps. The
calculation neither assumes a five-minute interval nor bridges long outages.
Sub-minute repeat transmissions are excluded. The event table separates the
empirical 99.9th-percentile tail from the one domain where a broad physical rule
is defensible here: pressure changes over 5 hPa/min. Statistical outliers are
not automatically hardware faults.

### Interpretable regime shifts

The regime table uses repository-documented boundaries for ingestion zero
blanking (2025-12-16 and 2026-04-05), the empirically established schedule
change (2026-04-21), and the repository's v2 code era (2026-06-19). Git commit
dates do **not** prove deployment dates. That limitation appears in every
regime row rather than being silently treated as a known change point.

## Soil-moisture semantics

Older committed transmitter code sampled soil every sixth boot (approximately
30 minutes) and emitted zero on other transmissions. Committed ingestion code
later converted non-temperature zeros to blank cells. Current transmitter code
samples soil every transmission. The data supports a periodic-opportunity
pattern during parts of March–May and an every-row pattern by late June, but it
does not establish every deployment date or whether all early zeros are true
measurements versus sentinels.

Accordingly, the output includes both raw field completeness and an
opportunity-adjusted completeness. Only missing documented/estimated soil
opportunities become missing-run events. This is an interpretation aid with
explicit uncertainty, not a manufactured clean history.

## Rain/wetness semantics

The repository says this analog sensor does not measure rainfall amount and
functions similarly to the soil probe. This project therefore calls it a raw
rain/wetness ADC value. It does not report millimetres of rain or precipitation
quantity, and it does not treat long dry saturation as failure by itself.

## Pressure analysis

Pressure receives a dedicated cluster table and plot. The analysis explicitly
captures received rows with missing pressure, impossible values including the
repeated approximately 4.04 hPa readings, temporal concentration, and the first
subsequent plausible recovery reading. Regime metrics expose changes in median,
IQR, variance, missingness, and range.

Each cluster distinguishes:

- **Observed:** what fields and values are directly in received telemetry;
- **Suggestive:** what the pattern supports without assigning root cause;
- **Not determinable:** whether the origin was the pressure device, wiring,
  I2C, firmware, electrical instability, parsing/serialization, or ingestion.

## Cross-sensor signatures

The event table describes co-missing environmental fields on received rows.
Temperature plus humidity is labelled a sensor-module-level *pattern* because
the repository maps both to the SHT40; it is not asserted to be an SHT40
hardware failure. Broader combinations remain multi-sensor or telemetry-wide
anomalies. The analysis produces no probabilities or composite health score.

## Severity rules

- `minor`: isolated missing field, single-sensor signature, or a saturation
  flatline that has a plausible environmental interpretation;
- `significant`: multi-row missing run, suspicious reading, statistical rate
  outlier, ordinary flatline, or multi-sensor signature;
- `critical`: impossible reading/change, telemetry-wide populated-row anomaly,
  or a flatline lasting at least 24 hours.

Severity is event triage, not a health score and not a causal diagnosis.

## Limitations

Historical timestamps are Apps Script receipt times, not sensor-read times.
Zero and missing semantics changed in committed ingestion code and actual
deployment timing is unverified. Telemetry cannot generally distinguish:

- physical sensor failure;
- wiring or I2C communication;
- firmware or serialization behavior;
- electrical instability or reset behavior;
- radio/receiver effects;
- Apps Script or Sheets ingestion behavior.

The generated “Future instrumentation candidates” section lists only
instrumentation justified by observed ambiguity: per-sensor read status, I2C
error state, packet sequence, firmware version, reset/brownout state, and an
explicit soil-sample-valid flag. It does not implement any of them.
