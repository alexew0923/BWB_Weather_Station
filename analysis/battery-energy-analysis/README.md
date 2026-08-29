# Battery Analytics and Energy Modeling

This project investigates battery-voltage and power-system behavior in the
Better With Bees solar-powered sensing node. It is deliberately separate from
`../reliability-audit/`: the reliability audit measures telemetry delivery,
outages, and gaps, while this project studies voltage behavior and its possible
relationship to those reliability events.

It includes a reproducible CLI analysis engine. The unified Streamlit UI under
`../../apps/station-monitor/` calls the same functions in `battery_analysis.py`;
it does not maintain a second set of formulas or write over CLI artifacts.

## Current capabilities

- battery commissioning, validity, missingness, and observed voltage range;
- daily first/last/minimum/maximum/mean/median/range and net voltage change;
- daily min/max timing, active-window change, and scheduled-inactive boundary
  change where the authoritative operating regime supports them;
- 24-hour and 72-hour changes, rolling statistics, and OLS voltage slopes;
- charging-like, discharging-like, and mixed voltage-behavior proxies;
- evidence-bounded battery context for significant outages;
- descriptive battery/reliability and battery/temperature correlations; and
- an uncalibrated, parameter-driven charge-consumption and solar-input model.

Rolling and pre-outage slopes require at least 12 valid readings and coverage of
at least 80% of the selected time window. Sparse endpoints do not become trends.

## Running the analysis

Install this project's minimal dependencies in an environment of your choice:

```bash
python -m pip install -r requirements.txt
```

Then pass the shared historical CSV explicitly. Paths are resolved from the
caller or from this file, so the command does not depend on one working directory:

```bash
python battery_analysis.py ../reliability-audit/data/HistoricalData.csv
```

By default outputs go to this project's ignored `output/` directory. The CLI
prefers the reliability audit's exported `outage_intervals.csv` and
`daily_reliability.csv`; if those are absent, it falls back to the audit's stable
validation, schedule, and gap helpers without copying that logic.

An alternate output location and reliability-output directory can be supplied:

```bash
python battery_analysis.py /path/to/HistoricalData.csv /path/to/battery-output \
  --reliability-output-dir /path/to/reliability-output
```

Run the tests with:

```bash
python -m unittest -v
```

## Outputs

- `battery_daily_metrics.csv`
- `battery_rolling_metrics.csv`
- `battery_outage_context.csv`
- `battery_relationships.csv`
- `battery_summary.json`
- `plot_battery_voltage.png`
- `plot_battery_daily_profile.png`
- `plot_battery_reliability_relationship.png`
- `plot_battery_outage_context.png` when usable outage context exists

These files are generated only under the selected battery output directory, not
inside the reliability-audit output directory.

## Observed, derived, and modeled quantities

**Observed** values come directly from validated telemetry, such as a battery
reading in volts or temperature in degrees Celsius.

**Derived** values are calculations from observations: daily voltage change,
rolling slope, completeness, correlations, and outage context. Voltage recovery
or decline is only a proxy for changing electrical conditions; it is not direct
energy measurement.

**Modeled** values come exclusively from caller-supplied hardware parameters in
`energy_model.py`. `EnergyModelParameters` accepts active/sleep current and
duration, cycles per day, optional sensor/radio load, nominal battery capacity,
panel power, equivalent sun hours, and charging efficiency. No repository value
is silently substituted, and the model is explicitly uncalibrated.

## Limitations

- Voltage is not battery percentage, state of charge, stored energy, battery
  health, or degradation.
- Battery chemistry, capacity, measurement calibration, charging circuit,
  current draw, panel specification, orientation, and conversion efficiency are
  not documented in this dataset.
- Voltage is affected by nonlinear chemistry, load, charging, temperature, and
  measurement conditions.
- Telemetry gaps hide battery behavior during the outages of greatest interest.
- Historical hardware, firmware, or field-semantic changes cannot be ruled out.
- Day-level observations are serially dependent. Correlations are descriptive;
  p-values are withheld and correlation is not causation.
- Raw rain/wetness semantics are too ambiguous for headline physical claims.

## Future research

The project is intended to support measured current-draw experiments, physical
model calibration, controlled temperature/charging tests, hardware and firmware
version comparisons, and later outage-risk modeling if the resulting evidence
becomes adequate. It does not implement predictive machine learning today.
