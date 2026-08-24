# Weather Station Reliability Audit

A reproducible Python audit of data delivery and sensor health for the Better with
Bees weather station, run against a `HistoricalData` CSV export.

```bash
.venv/bin/python reliability_audit.py data/HistoricalData.csv
```

Run from this directory. CSV exports live in `data/`. The output directory is an
optional second argument and defaults to `audit_output/`.

# Images
<img width="1950" height="780" alt="plot_daily_completeness" src="https://github.com/user-attachments/assets/c149b49b-4707-4c8d-9f7d-f6a22c1139dd" />
<img width="1950" height="715" alt="plot_gap_distribution" src="https://github.com/user-attachments/assets/e1b48ce2-1521-4c26-aa6b-d758600815e1" />
<img width="1950" height="780" alt="plot_daily_largest_gap" src="https://github.com/user-attachments/assets/463e2a02-f703-4ae8-b871-fbae561af13e" />


### Environment

A virtual environment lives in `.venv/` here. To recreate it:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

`requirements.txt` holds the direct dependencies (pandas, numpy, matplotlib);
`requirements.lock.txt` pins the exact versions this audit was last run against.
`pyrightconfig.json` points VS Code / Pylance at `.venv` so imports resolve.
`.venv/`, `data/` and the generated CSV/PNG outputs are all gitignored.

Nothing is written back to the sheet and no firmware or Apps Script code is
touched.

---

## Problem

The station is an inherited system with no delivery guarantees anywhere in the
chain:

```
XIAO ESP32C3 transmitter --ESP-NOW--> XIAO ESP32C3 receiver --I2C--> second ESP32
   --school Wi-Fi--> Apps Script (doGet.js) --> Google Sheets --> HistoricalData
```

ESP-NOW is fire-and-forget, the Wi-Fi is a school network, and `doGet.js` writes
whatever arrives without validation. When a reading is missing from the sheet,
nothing in the pipeline records *which* hop dropped it — or whether anything was
sent at all. Nine months of data had accumulated with no measured answer to "how
much of it is actually there, and what is wrong with the rest?"

An earlier in-sheet attempt (`scripts/apps_script/deprecatedReliabilityAudit.js`,
kept for reference but no longer in use) measured this against a fixed 288 expected rows per day. That was
correct until 2026-04-20 and wrong for every day after it, because the station's
duty cycle changed — which made its later completeness figures wrong too.

## Goal

One finished, re-runnable artifact that measures delivery reliability honestly
and flags — rather than silently repairs — everything ambiguous in the data.

Explicit non-goals: no ML, no forecasting, no dashboard, no live alerting, no
database, no firmware changes.

## Method

### The central rule: two different kinds of completeness

The audit never conflates these, and computes, reports and plots them separately:

| | Question | Denominator |
|---|---|---|
| **Row / system completeness** | Did a transmission arrive at all? | scheduled transmissions/day (288, then 204 — see below) |
| **Sensor completeness** | Within rows that *did* arrive, was this field populated? | rows actually received that day |

A day where the station sent 10 rows and all 10 carried a temperature is **100%
temperature completeness and 5% row completeness** — two distinct failures. A
dead soil probe is not a station outage, and an outage is not a sensor fault.

### The baseline is not a constant

The station's duty cycle **changed partway through the deployment**, so a single
denominator would be wrong for most of the dataset:

| Period | Operation | Expected/day |
|---|---|---|
| 2025-11-01 → 2026-04-20 (171 days) | ran 24 h | `24 × 60 / 5` = **288** |
| 2026-04-21 → 2026-07-30 (101 days) | building power cut ~23:00–06:00, 17 powered hours | `17 × 12` = **204** |

The changeover date is pinned from the data, not assumed:

- **2026-04-20 is the last day with night-hour traffic** (71 rows in hours
  23,00–05). From 2026-04-21 onward there are *exactly zero* night-hour rows for
  the remaining 100 days, apart from two 23:00:0x rows sitting on the cut-off.
- Night-vs-day rows per hour-of-day slot run at a ratio of ~1.0 every month from
  Nov to Apr (0.89–1.14), then collapse to 0.00–0.01 from May onward.
- Peak daily row counts track the two ceilings exactly: 293 / 573 / 291 / 286 /
  289 in Nov–Apr, then 205 / 199 / 200 in May / Jun / Jul.
- The ~426-minute overnight gaps begin 2026-04-20/21 and recur nightly after
  (13 in May, 19 in Jun, 30 in Jul).

`verify_baseline_regimes()` re-derives this on **every run** and prints it, so a
future schedule change surfaces as a warning instead of quietly skewing results:

```
regime                  expected   night/h    day/h   ratio  peak day
before 2026-04-21          288/day     570.1    544.6    1.05       573
from 2026-04-21            204/day       0.3    681.3    0.00       205
```

`deprecatedReliabilityAudit.js` used 288 for the whole period. That was
right until 2026-04-20 and wrong afterwards. Using 204 throughout — the obvious
correction — would have been wrong the other way, for 171 of 272 days.

### Gap severity thresholds

Derived from the empirical inter-arrival distribution, which the script prints
*before* applying any thresholds. Observed percentiles (minutes):

```
p50  4.97     p95   9.97     p99    39.72
p75  4.98     p97  10.03     p99.5 119.21
p90  5.02     p98  15.00     p99.7 424.26     max 27328 (19.0 d)
```

The distribution is strongly quantised at multiples of the 5-minute cycle: a lost
transmission does not shift the schedule, it leaves a hole. Thresholds are set at
cycle multiples and at natural breaks in the tail, not at round clock numbers:

| Threshold | Value | Why |
|---|---|---|
| sub-nominal | < 1 min | 758 gaps; 556 carry an unchanged `Count` — repeat transmissions from a fast-cycling node, not extra coverage |
| nominal ceiling | 7.5 min (1.5 cycles) | p90 is 5.02, so anything beyond this is a genuine hole |
| minor / moderate | 30 min (6 cycles) | just past p99 (39.7 min = 8 cycles) |
| moderate / major | 120 min (24 cycles) | p99.5 is 119.21 min — the empirical 0.5% tail boundary |
| major / critical | 480 min (8 h) | the sharpest discontinuity in the tail: 104 gaps exceed 6 h but only 37 exceed 8 h. That cliff *is* the overnight shutdown; 8 h is the first duration the building being dark cannot explain |

Gaps matching the overnight shape (evening → next morning, ≤ 10 h) are classified
`scheduled overnight shutdown` and excluded from outage totals, so routine
downtime never inflates the failure count.

### Daily classification

Structure kept from `deprecatedReliabilityAudit.js` so the two are
comparable, with three corrections:

1. denominator 288 → **288 before 2026-04-21, 204 after**
2. a new **Over-baseline** class — the old script capped completeness at 100%,
   which hid the days that exceeded the physical maximum. A 10% tolerance keeps
   midnight-boundary jitter (289 rows against a 288 baseline) out of this class
3. the sensor-level check uses core sensors only (Temperature, Humidity, Air
   Pressure, Rain Value), excluding Soil Moisture and Battery Voltage for the
   reasons under Findings

## Findings

Dataset: **2025-11-01 to 2026-07-30**, 272 calendar days, 24,833 rows after
validation.

**Overall row completeness is 35.6%** — 24,833 rows against 69,852 scheduled
transmissions (288/day × 171 days + 204/day × 101 days).

| Day class | Days | Share |
|---|---|---|
| Full outage (zero rows) | 120 | 44.1% |
| Minor transmission loss | 50 | 18.4% |
| Partial transmission loss | 39 | 14.3% |
| Good day | 30 | 11.0% |
| Severe transmission loss | 26 | 9.6% |
| Sensor-level issue | 5 | 1.8% |
| Over-baseline (fast-cycling) | 2 | 0.7% |

- **The dominant failure mode is total absence, not degradation.** 120 of 272
  days produced no data at all. Ten outages exceed 3 days; the longest is
  **19.0 days** (2025-12-19 → 2026-01-07, spanning the winter holiday).
- **218 significant outages** (> 30 min) plus 1,517 minor dropouts of 7.5–30 min.
- **Two genuine fast-cycling days**: 2025-12-17 (419 rows, 145% of baseline) and
  2025-12-18 (573 rows, 199%). The node was re-transmitting far faster than its
  5-minute cycle. The old in-sheet audit reported both as perfect days, because
  it capped completeness at 100%.
- **Reliability improves sharply from mid-June 2026.** Over the last 60 days,
  43 of 60 are good or minor-loss days and only 8 are full outages — visible as
  the unbroken green run in `plot_daily_completeness.png`.
- **Lowest-completeness sensor: Soil Moisture at 55.3%**, driven by the
  multi-month anomaly below rather than by transmission loss.
- **Air Pressure has its own unexplained swing**, smaller than Soil Moisture's
  but the same shape: monthly null rate Nov 16% → Dec 0% → Jan 78% → Feb 49% →
  Mar 0% and clean thereafter, with the value spread collapsing from std 211 in
  Nov 2025 to std 6 in Jul 2026. It also carries 110 physically impossible
  readings (92 of them exactly 4.04 hPa) clustered in late Nov 2025.
- **Battery Voltage is not a failing sensor.** It is 100% null Nov–Mar and ~99%
  populated from 2026-04-01 — a feature commissioned mid-deployment. Reporting
  its raw 36% missing rate as a reliability problem would be simply wrong, so the
  script reports completeness both overall and since each sensor's commissioning
  date.

> **Why 35.6% and not 44.8%.** An earlier revision of this audit used 204/day for
> the whole period, which understated the denominator for the 171 days when the
> station ran 24 h and reported 44.8%. It also misclassified 25 ordinary days as
> "over-baseline" purely because 288-regime traffic looks excessive against a
> 204 denominator. Both are fixed by the two-regime baseline.

Per-sensor completeness within received rows:

| Sensor | Overall | Since commissioned | First reading |
|---|---|---|---|
| Rain Value | 99.2% | 99.2% | 2025-11-01 |
| Humidity | 97.9% | 97.9% | 2025-11-01 |
| Temperature | 97.7% | 97.7% | 2025-11-01 |
| Air Pressure | 91.0% | 92.1% | 2025-11-02 |
| Battery Voltage | 63.8% | **98.8%** | 2026-04-01 |
| Soil Moisture | 55.3% | 55.3% | 2025-11-01 |

## Outputs

| File | Contents |
|---|---|
| `outage_intervals.csv` | one row per non-nominal gap: start, end, duration, severity, missed transmissions, `Count` either side |
| `sensor_completeness.csv` | daily per-sensor populated counts and rates |
| `daily_reliability.csv` | daily row counts, completeness (capped and raw), largest gap, per-sensor rates, failure class |
| `plot_daily_completeness.png` | daily row completeness coloured by class, with zero-data days as a rug |
| `plot_daily_largest_gap.png` | largest real outage per day, log scale, severity bands marked |
| `plot_sensor_completeness.png` | per-sensor completeness over time, soil anomaly and no-data days marked |
| `plot_gap_distribution.png` | the gap distribution the thresholds were derived from |

---

## Limitations

Every item here is **flagged and unresolved**. None is silently corrected.

### 1. Corrupted frame (2026-03-05 16:43:14)

One row carries `Count = 939531320` (`0x38001C38`) and
`Soil Moisture = 469762076` (`0x1C00001C`) — two bit patterns from the same
damaged buffer, sitting between neighbouring counts of 3630 and 4356.
`doGet.js` range-checks neither before writing.

Handling: the **row is kept** (it was genuinely received, so it counts toward row
completeness) but all its fields are nulled, because the frame is untrustworthy
and `Count` drives reboot/epoch detection — leaving it would invent a reboot that
never happened. The row's otherwise-plausible `Temperature = 0.0` is discarded
with it and this is logged explicitly. **Root cause is not fixed**; `doGet.js` is
live production code and out of scope here.

### 2. The four deduplicated rows

Exactly two pairs share both timestamp and `Count`
(2025-12-16 07:54:48 / Count 1062, and 2025-12-17 13:03:00 / Count 1416), each
with slightly different sensor values (e.g. −9.3 °C vs −9.4 °C). Rule: group by
exact `(timestamp, Count)`, keep the first in file order, drop the rest, log what
was dropped — 4 rows involved, 2 removed.

**Which reading of each pair is correct is unknown.** "First in file order" is a
convention, not a determination. `Count` alone is *not* a valid dedup key: it is
`RTC_DATA_ATTR`, surviving deep sleep but resetting on power loss, so 20,401 rows
(82.2%) share a `Count` with some other row from a different reboot epoch.
Deduplicating on `Count` would destroy most of the dataset.

### 3. Zero vs missing is conflated at source

`doGet.js` blanks any value of `0` or `NaN` for temperature, humidity, soil,
pressure, rain and battery before writing. A blank cell therefore cannot be
distinguished from a real zero reading.

The data contradicts the filter anyway — literal zeros **do** survive:
Temperature 116, Soil Moisture 353, Humidity 16, Air Pressure 15. Why some zeros
were written when the filter should have blanked them is **unresolved** (possibly
rows written by an earlier `doGet.js` revision, possibly another write path).

Consequence: do not assume every null in these columns means "sensor returned 0",
and do not assume literal 0 never appears. The audit counts nulls as
not-populated and leaves it there.

### 4. Soil Moisture multi-month failure and recovery

Monthly null rate: Nov 0% → Dec 37% → Jan 82% → Feb 100% → Mar 95% → Apr 91% →
May 85% → Jun 5.6% → Jul 0%.

The value distribution shifts too: the first 30 days span the full 0–4095 ADC
range (std ≈ 1051), the last 30 days sit in a narrow 1614–2236 band (std ≈ 97).

So the sensor did not simply fail and get fixed — **it is reporting a
qualitatively different signal after recovery than before.** Whether that
reflects a replaced probe, a relocated probe, changed wiring, or genuinely
different soil conditions is **unknown**. This is distinct from any known
short-duration cold-boot issue.

The data is **not normalised, not imputed and not rescaled**. Soil Moisture is
excluded from the core-sensor day classification so this anomaly does not
masquerade as a transmission fault, and is marked on the sensor plot.

### 5. Backward timestamp jump

One backward jump in file order: 2025-11-02 01:58:38 (Count 1921) →
01:03:33 (Count 1922) → 01:08:28 (Count 1923). `Count` stays monotonic across it,
so the transmitter was fine and only the Apps Script receipt clock moved.

Detected and reported **before** any sort, since sorting silently repairs the
symptom. Rows are then sorted by timestamp for gap analysis and the reorder is
noted in the validation output. The ~55-minute discrepancy is **not explained**.

### 6. Air Pressure swing and impossible readings

Monthly null rate: Nov 16% → Dec 0% → **Jan 78% → Feb 49%** → Mar 0%, clean from
March onward. The value spread collapses the same way Soil Moisture's does —
std 211 in Nov 2025 vs std 6 in Jul 2026.

Separately, **110 physically impossible readings** (92 × 4.04 hPa, 15 × 0, and a
handful of others) cluster between 2025-11-17 and 2025-11-27. Those are
**reported but deliberately not modified**: a present-but-impossible value is a
third state beyond "received" and "populated", and nulling it would quietly move
it into the missing bucket and change every completeness figure here.

Whether the winter gap, the spread change and the stuck 4.04 hPa readings share
one cause (a failing BMP sensor, a loose connection) is **unknown**. Air Pressure
is still counted as a core sensor because it has recovered — monthly populated
rate is 99.9% / 100% / 100% / 94.4% / 100% for Mar–Jul 2026, against 22.2% in
January. If that regresses, revisit `CORE_SENSOR_COLUMNS`.

### 7. Other known limits

- **Timestamps are receipt times, not reading times.** Every figure measures when
  Apps Script wrote the row, so Wi-Fi and Apps Script latency are folded into the
  gap distribution and cannot be separated from transmitter behaviour.
- **The audit cannot attribute a loss to a hop.** A missing row could be the
  transmitter, ESP-NOW, the I2C link, the second ESP32, Wi-Fi, or Apps Script.
  Nothing in the chain logs enough to tell them apart.
- **The two baseline regimes are assumed uniform within themselves.** School
  holidays, weekends and closures are not modelled, so 204/day is applied to
  every calendar day after the changeover and 288/day to every day before it.
  A post-changeover day where the building stayed powered can therefore exceed
  100% legitimately, which the uncapped `row_completeness_raw` column preserves.
- **The changeover is treated as instantaneous.** In reality 2026-04-21 is simply
  the first day with no night traffic; whether the schedule changed that night or
  a few days earlier during the 2026-04-22 → 05-08 outage cannot be determined
  from the data.
- **Reboot epochs are not segmented.** `Count` resets to zero on power loss, so
  the dataset spans many reboot epochs. The audit uses that fact defensively (it
  never treats `Count` as a unique id) but does not partition the data by epoch
  or attribute outages to specific reboot causes.

## Future work

Out of scope here, listed only so the boundary is explicit:

- live alerting on missed transmissions
- ML-based anomaly detection
- a web dashboard
- adding a range check to `doGet.js` and a monotonicity guard on `count`
- logging a device-side sequence number and reading timestamp, so receipt latency
  and per-hop loss become measurable
- modelling the school calendar to make the daily baseline vary by day type
