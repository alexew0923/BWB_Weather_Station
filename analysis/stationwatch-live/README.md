# stationwatch-live

`stationwatch-live` is a small terminal tool for answering one question: is
fresh Better With Bees weather-station telemetry currently reaching Google
Sheets?

It downloads the public Sheet as CSV, finds the newest valid value in the
`Timestamp` column, interprets it in the `America/Halifax` time zone, and
compares it with the current time.

## Run it

Python 3.9 or newer is required (for the standard-library `zoneinfo` module).
No third-party packages are needed.

```sh
python3 main.py
```

## Statuses

- **HEALTHY:** the newest telemetry is no more than 10 minutes old.
- **DELAYED:** the newest telemetry is more than 10 but less than 30 minutes old.
- **OFFLINE:** the newest telemetry is at least 30 minutes old.

Change `HEALTHY_AFTER_MINUTES` and `OFFLINE_AFTER_MINUTES` near the top of
`main.py` after reviewing the station's normal inter-arrival times.

`OFFLINE` only means that fresh telemetry has not reached Google Sheets. It does
not prove that the physical weather station has failed. Google Sheets is this
tool's only observation point, so it cannot distinguish a station problem from
a network, upload, or Sheets problem.
