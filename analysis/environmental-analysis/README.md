# Environmental analysis engine

A framework-independent domain layer that turns Better With Bees station
telemetry into structured, uncertainty-aware environmental intelligence.

It answers one question:

> What environmental events, patterns, responses and anomalies can be
> *defensibly* inferred from this station's telemetry?

and it is built so that the honest answer is often "not that one".

## Relationship to the sibling projects

| Project | Question |
| --- | --- |
| `../reliability-audit/` | Did scheduled telemetry reach the historical dataset? |
| `../sensor-health-analysis/` | When a row arrived, were its fields populated and plausible? |
| `../battery-energy-analysis/` | How did the observed power system behave? |
| `../stationwatch-live/` | Is fresh telemetry reaching Google Sheets right now? |
| **this project** | **What was the environment doing, and how confidently can we say so?** |

This engine is the analytical backend for a future public Garden Intelligence
application. The frontend is meant to be thin: it should render what this
engine returns and perform no scientific calculation of its own.

---

## 1. Scientific scope

The station carries an SHT40 (temperature, relative humidity), a BMP280 (air
pressure), an uncalibrated capacitive soil probe, and an analog rain/wetness
board — all read as raw 12-bit ADC counts where noted, and transmitted every
five minutes while the site has power.

**The engine may say:**

- the wetness surface was wet, for how long, and how far the signal moved;
- what temperature, humidity and pressure were doing before, during and after;
- whether the soil signal changed, in raw counts, and whether the data was good
  enough to tell;
- what a normal month or a normal hour of the day looks like *at this station*;
- which readings are unusual against this station's own record.

**The engine may never say:**

- how much rain fell — there is no calibration from ADC counts to millimetres,
  and the project README states plainly that the rain sensor "does not actually
  measure the rainfall amount";
- what the volumetric water content or percent soil moisture was — the
  capacitive probe has no calibration curve anywhere in this repository;
- whether the soil got *wetter* or *drier* — see §5, the probe's polarity is not
  established;
- what caused a wetting event — rain, dew, fog, sea spray, snowmelt and a
  passing sprinkler are indistinguishable to this hardware;
- anything about plant health, crop water deficit, plant stress, or when to
  irrigate.

These are enforced, not merely documented: `tests/test_invariants.py` fails the
build if an event's serialised output ever contains a millimetre, a volumetric
water content, or a claimed cause.

---

## 2. Architecture

The repository rule is `apps -> analysis`, never the reverse. Nothing here
imports Streamlit, Flask, Django, FastAPI or any UI component, and
`tests/test_architecture.py` walks the whole `analysis/` tree with an AST parser
to prove it on every run. The same test asserts that nothing outside the CLI
prints, that nothing outside the CLI raises `SystemExit`, and that importing the
package does not drag in matplotlib.

```
analysis/environmental-analysis/
├── environmental/
│   ├── __init__.py             public API re-exports
│   ├── api.py                  the service layer a frontend imports
│   ├── cli.py                  development and validation CLI
│   ├── config.py               EVERY threshold, in one place
│   ├── models.py               domain model, enums, JSON serialisation
│   ├── sensors.py              canonical names and sensor semantics
│   ├── errors.py               structured domain errors
│   ├── version.py              engine and per-detector versions
│   ├── data_sources/
│   │   ├── __init__.py
│   │   └── sheets.py           URL handling, HTTP, .env — the ONLY I/O
│   ├── dataset.py              canonical EnvironmentalDataset
│   ├── quality.py              quality gates and gap awareness
│   ├── statistics.py           robust statistical helpers
│   ├── profiling.py            exploratory sensor profiling
│   ├── baselines.py            dry reference + environmental normals
│   ├── events.py               wetness-event detection
│   ├── characterization.py     event context and classification
│   ├── soil.py                 soil-response analysis
│   ├── dynamics.py             post-event trajectory and empirical fits
│   ├── anomalies.py            conditional-baseline anomalies
│   ├── current_state.py        current/recent conditions
│   ├── plots.py                optional diagnostic plots
│   └── _reliability_bridge.py  narrow reuse of the reliability audit
└── tests/
```

### Reuse rather than duplication

`_reliability_bridge.py` puts `../reliability-audit/` on `sys.path` and reuses
exactly two things, following the pattern `sensor-health-analysis` already
established. The dependency direction is
`environmental-analysis -> reliability-audit`, and nothing points back.

- **`localize_timestamps`** — the audit resolves the annual Atlantic fall-back
  from *file order*, pinned with evidence from this dataset. Re-deriving that
  would produce a second, subtly different answer to a solved question.
- **`audit_config`** — the operating schedule, the nominal 5-minute cycle, the
  station timezone, and the physically plausible sensor ranges. The schedule in
  particular is the audit's "single authoritative source of expected", so
  coverage figures here agree with every other coverage figure in the
  repository instead of drifting.

The audit's `load_and_validate_data` is deliberately **not** reused: it takes a
filesystem path, prints to stdout and raises `SystemExit`, none of which belongs
in a library that must serve a web request.

---

## 3. Data flow

```
HISTORICAL_DATA_URL
  → normalise Google Sheets URL to its CSV export form
  → HTTP GET with timeout, size cap, HTML/empty detection
  → parse CSV, validate schema
  → parse and localise timestamps (America/Halifax, DST-correct)
  → rename to canonical sensor names, coerce to numeric
  → null corrupt frames, mask implausible values and ambiguous zeros
  → sort, deduplicate
  → EnvironmentalDataset            ← every stage below sees only this
  → quality masks and gap segmentation
  → sensor profiling
  → dry-reference baseline
  → wetness-event detection → boundaries → merging
  → event characterisation and classification
  → soil-response analysis → post-event dynamics
  → environmental baselines and anomalies
  → EnvironmentalSummary (typed, JSON-serialisable)
```

Network access exists only in `data_sources/sheets.py`. No detection algorithm
can reach the network, which is why every test runs offline.

---

## 4. Google Sheets configuration

The engine reads the same environment variables the rest of the repository
already uses. Copy `.env.example` to `.env`, or export them:

```bash
export HISTORICAL_DATA_URL="https://docs.google.com/spreadsheets/d/<id>/edit?gid=<gid>#gid=<gid>"
export STATIONWATCH_SHEET_URL="https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=0"
```

| Variable | Required | Purpose |
| --- | --- | --- |
| `HISTORICAL_DATA_URL` | yes | historical telemetry; event analysis needs the history |
| `STATIONWATCH_SHEET_URL` | no | live telemetry, for current conditions only |
| `BWB_ENVIRONMENTAL_CSV` | no | **development override**, see below |

Accepted URL shapes: a normal Sheets edit link (normalised to
`export?format=csv&gid=…`), an existing CSV export URL, a `gviz/tq?tqx=out:csv`
URL (passed through, because it selects a tab by name), or any other HTTP(S)
URL serving CSV.

Real environment variables always win over `.env`. When a caller passes an
explicit `environ` mapping — as a test or a service does — `.env` files are not
consulted at all.

### Local CSV policy

There is **no** checked-in CSV, no bundled fixture and no silent fallback. The
production source is the remote Sheet. `BWB_ENVIRONMENTAL_CSV` and the CLI's
`--csv` flag exist for reproducible development only:

- they are consulted only when explicitly set;
- the resolved source always reports which kind it is, and the CLI labels a
  local run as an override;
- the live/current path ignores the override entirely, because stale local data
  must never be presented as current conditions.

`.env` is git-ignored and no Sheet URL is hard-coded anywhere in the engine.

---

## 5. Sensor semantics

| Canonical name | Source column | Unit | Calibrated |
| --- | --- | --- | --- |
| `wetness_signal_raw` | `Rain Value` | raw 12-bit ADC counts | no |
| `soil_signal_raw` | `Soil Moisture` | raw 12-bit ADC counts | no |
| `temperature_c` | `Temperature` | degrees C | yes |
| `humidity_pct` | `Humidity` | % RH | yes |
| `pressure_hpa` | `Air Pressure` | hPa | yes |
| `battery_mv` | `Battery Voltage` | mV | no (battery project is authoritative) |

The two spreadsheet headings that claim more than the hardware delivers are
renamed on the way in. That is the cheapest way to stop the wrong unit leaking
into every downstream calculation and, eventually, onto a public web page.

### The wetness signal is inverted, and that is an empirical finding

The firmware does a bare `analogRead()`; nothing in the repository documents
which direction means wet. Three independent observations in the historical
record establish it:

1. **The dry state is a hard rail.** 82.5% of all valid readings are exactly
   4095, the top of the 12-bit range, and excursions are strictly one-sided and
   downward (observed minimum 1555).
2. **Sub-rail readings coincide with saturated air.** Readings at the rail have
   a median relative humidity of 77% and a first quartile of 57%. Readings even
   1–10 counts below the rail have a median RH of 96%.
3. **Sub-rail readings peak at dawn.** The fraction of readings below the rail
   runs 0.34 at 06:00 and 0.10 at 14:00 — the shape of dew forming overnight
   and burning off through the afternoon.

The default is therefore `wet_direction = -1` (lower counts mean wetter). It is
a configuration value, not a hard-coded assumption, because a replaced or
rewired sensor board could invert it.

### The soil probe's polarity is NOT established — unresolved

**This is the one scientifically important fact the repository cannot settle,
and the engine refuses to guess it.**

Event-aligned soil changes across the historical record split 46 positive to 38
negative: a coin flip. Nothing in the firmware, the wiring notes or the README
states whether a rising ADC count on this probe means more water or less.

The engine therefore reports soil changes as `INCREASE` or `DECREASE` **in raw
ADC counts** and never translates that into wetter or drier. A
`soil_polarity`-style translation can be added the moment evidence exists.

**To make soil analysis physically meaningful, one of these is needed:**

- the probe's part number or datasheet, plus how it is wired
  (`SOIL_SENSOR_PIN 4`, switched by the transistor on pin 5);
- a two-point field calibration: the raw reading in air (0% water) and the raw
  reading in saturated soil or water;
- or a logged wetting of known volume with the raw reading before and after.

Any one of those would let the engine report a direction with meaning; the
first plus a curve would let it report a physical quantity.

### The soil signal has a diurnal confound

In July 2026 — the densest month, 100% soil field completeness — the soil
signal tracks air temperature with a Spearman correlation of **+0.47**, and its
hour-of-day median swings about **165 counts** between the 06:00 low and the
13:00 high. That is as large as any event-related change in the record. Soil
water content does not oscillate on a 24-hour cycle at that amplitude; the
probe's electronics respond to temperature or supply voltage.

The engine removes a slowly varying time-of-day profile before testing for a
response (§10) and records whether it managed to. The absolute detection floor
(180 counts) is set above the observed diurnal swing so an ordinary sunrise can
never be reported as a soil response.

---

## 6. Data quality rules

Six states are distinguished, and only the last may be interpreted:

1. **station-wide telemetry absence** — no row arrived;
2. **individual sensor absence** — the row arrived with that field blank;
3. **malformed readings** — a cell that is not a number;
4. **physically impossible readings** — outside the audit's plausible range, or
   on a frame whose boot counter proves the buffer was corrupt;
5. **uncertain readings** — present and in range but ambiguous;
6. **valid observations**.

Rules that follow from that:

- **Impossible values are masked, not deleted.** Deleting one would silently
  move it into the "missing" bucket and change every completeness figure. The
  original reading stays readable via `series(sensor, valid_only=False)`.
- **A corrupt frame keeps its row and loses every value.** A boot counter above
  1 000 000 is memory garbage, so nothing on that frame is trustworthy — but
  the row really did arrive, and row-level completeness must still count it.
  This mirrors the reliability audit exactly.
- **A stored soil zero is ambiguous.** `scripts/apps_script/doGet.js` blanks
  zero values before writing, so a zero that survived predates that behaviour
  and cannot be told apart from a sentinel. Zeros are excluded from soil
  analysis and counted; a window with more than 25% of them yields `UNKNOWN`.
- **Nothing is interpolated.** `max_interpolation_gap_minutes` defaults to `0`.
  Interpolation may never create, extend or end an event under any setting.
- **Nothing is assumed constant across a gap.** Rolling statistics, event
  boundaries and response persistence all break at telemetry gaps.
- **"Insufficient" is a permitted answer** and is used in preference to a
  negative one. Absence of evidence is only evidence of absence when the data
  was good enough to have shown the thing.

---

## 7. The wetness-event detection algorithm

### Why not a robust z-score

The obvious modern choice is `z = (x - rolling_median) / (1.4826 * MAD)`. It is
the wrong tool for this signal. The dry state is a hard rail: **96.7% of
consecutive differences within the dry state are exactly zero**, so the MAD of a
dry window is exactly 0 and the z-score is undefined or infinite. Any floor
placed under it is an absolute threshold wearing a statistical costume. An
absolute deviation from an adaptive dry reference is what the data supports, and
it is honest about being a chosen number.

### The algorithm

1. Take only valid wetness observations. Nothing is filled.
2. **Dry reference**: a centred 7-day rolling 90th percentile of the signal.
   An *upper* quantile, not a median, because the dry state is a ceiling — a
   rolling median sags during a multi-day wet spell and hides the event it is
   supposed to measure, while an upper quantile stays on the dry level as long
   as roughly a tenth of the window is dry. It still adapts to real drift: the
   observed dry level falls from 4095 to about 4062–4074 in late July 2026.
   Windows with fewer than 20 observations fall back to a global quantile.
3. **Deviation** = counts away from the dry reference in the wet direction,
   clipped at zero.
4. **Hysteresis + persistence state machine** over the ordered observations:
   - opens after **3 consecutive** samples at or above **30 counts**, backdated
     to the first of them;
   - closes after **3 consecutive** samples below **10 counts**, ending at the
     last sample before that run;
   - is **cut** by any telemetry gap over **30 minutes**. The station being
     switched off is not the end of a wetting event, and it is not evidence
     that one continued either — the interval is closed at the last observed
     sample and both sides are marked censored.
5. **Merge** intervals separated by less than **60 minutes** of *observed* dry
   time. Never merge across a gap: unobserved dry time cannot be called short.
6. **Discard** intervals shorter than 10 minutes or thinner than 3 samples.

Two thresholds plus persistence remove both classic failure modes — one noisy
sample becoming an event, and a signal hovering at the boundary becoming dozens
of events — without smoothing the data, which would blur the very boundaries
the analysis is trying to measure.

### Event boundaries and censoring

`EventBoundaries` records `start_censored`, `end_censored`, the observed gaps
either side, and the largest gap inside the event. On the historical record
**61 of 95 events are censored at one end or both**, overwhelmingly by the
06:00–23:00 building-power schedule: a wetting event that begins at 02:00 is
first seen at 06:00, and its reported start is when observation resumed, not
when the water arrived. A censored event carries a warning saying so.

### Event identifiers

`wetting-2026-05-14T08:15-0300` — event type plus normalised local start time
**including the UTC offset**. Deterministic: re-running the same analysis over
the same data always produces the same ids. The offset is part of the id
because the annual Atlantic fall-back repeats an hour of local wall-clock time,
and two events an hour apart must not collapse onto one identifier. Ids are
unique within a station; the station id is carried separately on the event.

### Classification

| Classification | Requires |
| --- | --- |
| `probable_wetting_event` | peak ≥ 100 counts **and** duration ≥ 20 min **and** RH ≥ 90% for at least half the interval **and** usable data |
| `candidate_wetting_event` | the excursion is real but one leg is missing |
| `uncertain_wetting_event` | small and uncorroborated, or the window's data is not interpretable |

There is deliberately no `heavy_rain`, no `light_shower` and no intensity class
of any kind: the sensor cannot support one.

Evidence strength is ordinal — `OBSERVED`, `STRONG`, `MODERATE`, `WEAK`,
`INSUFFICIENT` — and never numeric. The engine has no calibrated probability
model, so "87% confidence" would be an invented number dressed as a
measurement.

---

## 8. Parameter justification

Every parameter lives in `config.py` with its reasoning attached. The ones that
matter most, and the sensitivity analysis behind them, run on the real
historical record (24 833 rows, 2025-11-01 → 2026-07-30):

**Entry/exit thresholds and persistence** — event count is remarkably
insensitive across a tenfold range, which is what a hard rail predicts:

| enter / exit (counts) | persistence 2 | 3 | 4 |
| --- | --- | --- | --- |
| 20 / 10 | 100 | **95** | 87 |
| 30 / 10 | 100 | **95** | 86 |
| 50 / 20 | 98 | 88 | 83 |
| 100 / 30 | 86 | 82 | 78 |
| 200 / 60 | 75 | 70 | 68 |

30/10 with persistence 3 was chosen because 30 counts is comfortably above
single-count quantisation while still catching the light dew events that carry
96% median humidity, and three samples (~10 min) is the shortest persistence
that eliminates single- and double-sample spikes.

**Baseline quantile and window** — the detector is *completely* insensitive:
every combination of quantile ∈ {0.75, 0.90, 0.95, 0.99} and window ∈ {3D, 7D,
14D} yields 95 events (0.75/3D yields 97). 0.90 over 7D was kept as the middle
of a flat region.

**Merge gap** — 30 / 60 / 120 / 240 minutes give 98 / 95 / 90 / 85 events, with
median duration rising 114 → 119 → 132 → 159 minutes. 60 minutes is where an
hour-long dry spell stops looking like part of the same shower.

**Corroborating humidity, 90%** — the classification split moves 78/15/2 at
80%, 70/23/2 at 85%, **58/32/5 at 90%**, 44/44/7 at 95%. 90% was chosen for
false-positive discipline: it sits well inside the wet population (whose median
RH is 96%) and demotes genuinely ambiguous cases, such as the 2025-11-16 14:34
excursion, which has a peak of 1818 counts but a median RH of 47.7% and a
+19 °C temperature rise — the drying tail of that morning's event rather than
new water.

**Soil absolute floor, 180 counts** — set above the ~165-count diurnal swing
the probe shows in its densest month, so a sunrise cannot be a response.

**Soil attribution window, 12 hours after the wetting ends** — added after
manual review. With only a 48-hour post-event window, five of eleven
"detections" had onsets between 13 and 46 hours after a short event, with a dry
night and a different weather system in between. Measured from the event's end
rather than its start because water keeps arriving while the surface is wet, so
a seventeen-hour event may legitimately produce a late onset — measuring from
the start wrongly excluded exactly that case. Twelve hours is a judgement call,
not a measured infiltration time, and it is configurable for that reason.

---

## 9. Data-quality gating in practice

Coverage is measured against the reliability audit's operating schedule, so it
agrees with the rest of the repository. On the real record:

| Month | Rows | Coverage | Evidence | Events |
| --- | ---: | ---: | --- | ---: |
| 2025-11 | 2 738 | 32% | weak | 6 |
| 2025-12 | 1 876 | 21% | weak | 1 |
| 2026-01 | 1 672 | 19% | weak | 4 |
| 2026-02 | 616 | 8% | weak | 1 |
| 2026-03 | 1 895 | 21% | weak | 4 |
| 2026-04 | 4 635 | 59% | moderate | 15 |
| 2026-05 | 2 591 | 41% | moderate | 13 |
| 2026-06 | 3 423 | 56% | moderate | 26 |
| 2026-07 | 5 387 | 90% | strong | 25 |

Event counts track coverage, not weather. A February with 8% coverage cannot be
compared with a July at 90%, and every period carries its coverage and an
explicit evidence strength so a consumer cannot accidentally compare them.

---

## 10. Soil-response methodology

For each wetting event:

1. **Gate first.** The pre-event window (24 h) must hold at least 6 valid soil
   observations spanning at least 120 minutes; the response window (event start
   → end + 48 h) must hold at least 6. Ambiguous zeros above 25% disqualify the
   window. Failing any gate gives `UNKNOWN`.
2. **Remove the daily cycle** where the profile can be estimated: a trailing
   ±3.5-day, same-local-hour median (§5).
3. **Baseline** = median of the pre-event residual.
   **Scale** = `1.4826 × MAD` of that residual, floored at 10 counts, because
   these ADC channels frequently have a MAD of exactly zero.
4. **Threshold** = `max(3 × scale, 180 counts)` — a deviation must clear both a
   noise-relative and an absolute bar.
5. **Persistence.** At least 4 consecutive observations (~20 min) past the
   threshold, in one direction, not stitched across a telemetry gap, and
   **beginning within 12 hours of the event start**.
6. **Verdict.**
   - `DETECTED` — with direction, magnitude in counts, relative change, onset
     delay, time to peak and persistence.
   - `NOT_DETECTED` — **only** when both windows passed the quality gate. This
     is an assertion that a response would have been visible had one occurred.
   - `UNKNOWN` — everything else, with the reasons attached.

`tests/test_invariants.py` enforces the rule that `NOT_DETECTED` can never be
returned on data whose quality is not `USABLE`.

### Post-event dynamics

Descriptive measures are always computed when the data supports them: peak,
time to peak, change from peak, fraction recovered, time to 50% recovery,
median rate of change, and whether the signal returned to baseline.

Model fitting is **opt-in** (`PostEventDynamicsConfig.fit_models`). Two
candidates are fitted — linear `M(t) = a + b·t` and exponential relaxation
`M(t) = M_inf + A·exp(−k·t)` — with MAE, RMSE, R² and AIC exposed for both. An
exponential is offered because it is the conventional shape for a draining
medium, **not** because anything here shows that this soil drains exponentially;
fits are labelled empirical. A fit whose best rate constant lands on the edge of
the search grid is rejected as unidentified. Fitting uses NumPy only — linear
least squares plus a deterministic grid search over `k` — which keeps repeated
runs bit-identical and keeps SciPy out of the dependency list. There is no
machine learning anywhere in this engine.

---

## 11. Baseline methodology

Monthly period statistics (robust: median, IQR, p10/p90) plus hour-of-day
profiles and daily ranges. Every period carries its telemetry coverage and an
`EvidenceStrength` derived from it: ≥75% is `STRONG`, ≥40% is `MODERATE`, below
that is `WEAK`. Periods with fewer than 100 valid observations for a sensor omit
that sensor's statistics rather than publishing a number computed from a handful
of readings. Daily ranges exclude days with fewer than 20 valid observations,
because a "daily range" from three readings is not one.

Wetting-event frequency is reported per day *of record*, with the record's
coverage attached, and never per day of telemetry.

---

## 12. Anomaly methodology

An anomaly here is a statement about the *environment*, never about the
instrument. Sensor faults, stuck channels and delivery outages belong to
`sensor-health-analysis` and `reliability-audit`; mixing them in would make a
hardware failure look like weather.

For every observation the engine builds a **conditional baseline**: the same
local hour (±1 h) at a comparable point in the year (±15 days), excluding the
observation's own day. An observation is anomalous only when it fails **both**
tests against a baseline of at least 30 comparable observations:

- a robust z-score past 3.5, with a per-sensor scale floor so a quantised
  window cannot produce an infinite z;
- a position outside the 1st/99th percentile envelope.

Requiring both keeps a tight but noisy distribution from producing a constant
stream of findings. Consecutive flagged samples, and samples within 60 minutes
of each other, collapse into one anomaly represented by its most extreme
sample — a six-hour heat spike is one finding, not seventy.

Rate-of-change anomalies use the same idea on first differences, and require
both a robust z past 6 and membership of the top 0.1% of all observed rates —
the same statistical tail `sensor-health-analysis` uses. Without that second
gate the tightly clustered temperature-rate distribution produced 277 findings
on the historical record; with it, 8.

Persistent-wetness anomalies flag events past the 95th percentile of observed
durations, excluding censored starts.

Every anomaly carries the evidence that makes it unusual and this caveat: the
comparison baseline is a single partial year of this station's own record, so a
finding means "unusual for this deployment", never "unusual for the region".

---

## 13. Public Python API

```python
from environmental import load_environmental_dataset, analyze_environment, get_event

dataset = load_environmental_dataset()          # reads HISTORICAL_DATA_URL
summary = analyze_environment(dataset)

events = summary.events
event = get_event(events, "wetting-2026-07-22T06:15-0300")

print(event.classification)            # EventClassification.PROBABLE_WETTING_EVENT
print(event.evidence_strength)         # EvidenceStrength.MODERATE
print(event.soil_response.status)      # SoilResponseStatus.NOT_DETECTED
```

| Function | Returns |
| --- | --- |
| `load_environmental_dataset(...)` | `EnvironmentalDataset` |
| `profile_environment(dataset)` | `{sensor: SensorProfile}` |
| `detect_environmental_events(dataset)` | `tuple[EnvironmentalEvent]` |
| `list_events(events, classification=…, soil_status=…, start=…, end=…)` | filtered tuple |
| `get_event(events, event_id)` | one event, or `UnknownEventError` |
| `get_environmental_baseline(dataset, events)` | `EnvironmentalBaseline` |
| `analyze_environment(dataset)` | `EnvironmentalSummary` |
| `get_environmental_summary(...)` | load + analyse in one call |
| `get_current_environmental_state(...)` | `CurrentEnvironmentalState` |
| `to_serialisable(anything)` | JSON-ready primitives |

Every domain object has `.to_dict()`. No DataFrame crosses the API boundary;
pandas is used internally only.

### Observation vs interpretation

Every piece of evidence is tagged with what kind of statement it is —
`RAW_OBSERVATION`, `COMPUTED_MEASUREMENT`, `STATISTICAL_EVIDENCE` or
`INTERPRETATION` — so a frontend can show the reasoning instead of a bare
verdict, and so an unsupported claim has nowhere to hide.

```
RAW_OBSERVATION       "The wetness signal reached a minimum of 1626 ADC counts
                       across 179 observations."
COMPUTED_MEASUREMENT  "That is 2469 counts away from the dry reference level of
                       4095 counts, sustained for 1003 minutes."
STATISTICAL_EVIDENCE  "Relative humidity was at or above 90% for 100% of the
                       interval (median 98.0%)."
INTERPRETATION        "A probable environmental wetting event."
NEVER                 "23 mm of rain fell."
```

### Current conditions

`get_current_environmental_state()` reads the live Sheet and returns an explicit
freshness state rather than substituting old data:

`current` · `stale` · `awaiting_telemetry` · `insufficient_data` · `unavailable`

`unavailable` is an *observation* failure — the same separation StationWatch
draws between MONITOR ERROR and OFFLINE — and never an environmental claim.
Retrieval failures become states, not exceptions, because "I cannot see the
station" is a legitimate answer that a frontend has to render. As of this
writing the live tab is header-only, and the engine correctly returns
`awaiting_telemetry`.

### Versioning

Every event, soil verdict, baseline and anomaly carries the version of the
component that produced it (`wetness_detector_version`, `soil_response_version`,
…), and `EnvironmentalSummary.versions` carries them all. Bump a component
version whenever a change can alter output for unchanged input.

### Caching

**There is none, deliberately.** The engine fetches the remote Sheet exactly
once per analysis run and then works from the canonical dataset, so there is
nothing for an in-process cache to save *within* a run —
`tests/test_api_and_cli.py` asserts the single fetch. Caching *between* runs is
a deployment concern: a Streamlit app has `st.cache_data`, a web service has its
own layer. Putting a TTL cache in here would add shared mutable state to a
library whose main promise is determinism. Callers cache
`load_environmental_dataset` or `fetch_csv_text`.

---

## 14. CLI

```bash
python -m environmental.cli profile
python -m environmental.cli events --limit 20
python -m environmental.cli events --classification probable_wetting_event
python -m environmental.cli event wetting-2026-07-22T06:15-0300
python -m environmental.cli summary
python -m environmental.cli baseline
python -m environmental.cli current
python -m environmental.cli validate
python -m environmental.cli plots --output-dir output --event-plots 3
```

`--json` on any command emits the structured payload. `--csv PATH` is the
development override. Exit code is 0 on success and 1 on a domain error, which
is printed as a summary and a detail on stderr — never a traceback.

**A frontend must not scrape this output.** It imports the Python API; `--json`
exists for scripting and for eyeballing the exact structure a service receives.

### Diagnostic plots

`environmental/plots.py` provides six research plots — wetness with event
overlays, event-aligned context, event-aligned soil response, duration
distribution, soil response vs event magnitude, and post-event trajectory with
any fitted model. They are validation artefacts, not frontend design, they are
never required by the analysis path, and matplotlib is imported lazily with the
Agg backend so importing the engine never needs a display. Line plots break at
telemetry gaps rather than drawing a confident diagonal across a seven-hour
shutdown.

---

## 15. Testing

```bash
cd analysis/environmental-analysis
python -m unittest discover -s tests -t . -v
```

188 tests, all offline. HTTP is mocked; nothing reads the real Sheet or the real
CSV. Coverage includes URL normalisation and every retrieval failure mode
(missing variable, unreachable host, timeout, HTTP error, HTML instead of CSV,
empty response, oversized response, bad UTF-8); schema validation, header-only
sheets, malformed and duplicate timestamps, numeric coercion, corrupt frames,
and both DST transitions; the full set of synthetic detector scenarios (obvious
event, no event, one- and two-point spikes, noisy baseline, two separate events,
two mergeable intervals, hysteresis flicker, drifting baseline, telemetry gap
mid-event, event crossing midnight, event across the fall-back, missing and
invalid readings); soil response (obvious, delayed, absent, sub-threshold,
noisy baseline, transient spike, insufficient samples, telemetry outage,
zero ambiguity, pure diurnal cycle); baselines under sparse and uneven sampling;
anomalies including the time-of-day-conditional case; determinism; and the
architecture guardrails.

Invariant tests assert that event end ≥ start, durations are non-negative, ids
are unique, events do not overlap, soil response is never `NOT_DETECTED` on
insufficient data, an `UNKNOWN` verdict asserts no magnitude, every event
carries detector-version metadata, invalid telemetry never creates an event, no
event claims a cause, and no serialised event ever contains a rainfall quantity
or a volumetric water content.

---

## 16. Known limitations

1. **Coverage is 36% over the record.** Event counts track telemetry
   availability at least as strongly as they track weather.
2. **61 of 95 events are censored** at one or both ends, mostly by the nightly
   06:00–23:00 power schedule. Reported durations are lower bounds.
3. **Soil polarity is unknown** (§5). Soil verdicts are directional statements
   about a raw signal, nothing more.
4. **The soil channel is 54% complete overall** and effectively unusable for
   long stretches: 0% in February 2026, under 20% for much of January–May.
   That is why 52 of 95 events have an `UNKNOWN` soil verdict.
   In November 2025 the channel is complete but wildly unstable, ranging from 1
   to 4095 counts. The noise-aware threshold scales with that scatter -- it
   reaches 1 403 counts for one November event -- but a sustained regime shift
   can still clear it, so the two November detections should be read as
   "the signal moved a great deal", not as evidence about soil water.
5. **The soil signal has a diurnal confound** of the same magnitude as any
   event response (§5).
6. **Timestamps are Apps Script receipt times**, not sensor read times.
7. **Pressure is corrupt in November 2025** — 110 impossible readings, mostly a
   repeated ≈4.04 hPa. They are masked and counted, never repaired.
8. **The record is a single partial year**, so "seasonal" baselines and
   anomalies are local windows inside one deployment. Near the start and end of
   the record the ±15-day comparison window is one-sided, and a strong seasonal
   trend inside it can present as an anomaly — the early-November warm nights
   flagged in the historical run are of this kind.
9. **An episode lasting several days contaminates its own baseline.** Excluding
   the observation's own day fixes this for shorter episodes only.
10. **A record with no observed dry state yields no events**, by design: there
    is no reference to measure a departure from. This matters for short windows,
    not for the historical record, whose dry reference is 4095 for over 99% of
    rows.
11. **Detection is not validated against an independent rain gauge.** See §17.
12. **Wetness persistence is board drying, not rainfall duration.** A long event
    may be a short shower followed by slow evaporation.

---

## 17. Future extensions

Clean extension points exist for, and none of this is built:

- **An external weather reference.** The engine functions without one. When one
  is added, three things must stay distinct and separately labelled: *local
  observation* (this station's signal), *reference observation* (a nearby
  official station), and *inference* (the comparison between them). That would
  make it possible to check whether detected events coincide with recorded
  precipitation, and to say what a given wetness deviation tends to correspond
  to — without ever claiming the station measured it.
- **Calibrated sensor metadata**, which would upgrade soil verdicts from
  directional to physical.
- **Multiple stations** — the domain model is already namespaced by station.
- **Irrigation records and deployment metadata**, which would let the engine
  distinguish causes it currently refuses to guess.
- **Additional sensors**, via `sensors.py`.

Deliberately not built, and deliberately not needed: authentication, user
accounts, a database, message queues, distributed workers, microservices, or an
ML pipeline.

---

## 18. Statements this system cannot make

Kept last because it is the most important section.

- ✗ how much rain fell, in millimetres or any other unit
- ✗ precipitation rate, intensity or accumulation
- ✗ volumetric water content or percent soil moisture
- ✗ whether the soil became wetter or drier
- ✗ what caused a wetting event
- ✗ plant health, plant stress, or crop water deficit
- ✗ an irrigation recommendation
- ✗ a numeric confidence or probability for any conclusion
- ✗ that a station-wide outage was a period of stable conditions
- ✗ that the soil did not respond, when the soil data could not have shown it
