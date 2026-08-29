# stationwatch-live

`stationwatch-live` answers one operational question: is fresh Better With Bees
weather-station telemetry reaching Google Sheets?

Each invocation performs one check, compares the result with `state.json`, sends
an email only when the status changes, saves the result, and exits. The supplied
macOS `launchd` job runs it approximately every five minutes.

## Statuses

- **HEALTHY:** newest telemetry is no more than 10 minutes old.
- **DELAYED:** newest telemetry is more than 10 but less than 30 minutes old.
- **OFFLINE:** newest telemetry is at least 30 minutes old.

The first run establishes a baseline without sending an alert. Later transitions
send one email, including recovery to `HEALTHY`; an unchanged status sends
nothing. Adjust `HEALTHY_THRESHOLD_MINUTES` and `OFFLINE_THRESHOLD_MINUTES` near
the top of `station_watch.py` if needed.

## Email configuration

The program uses SMTP with STARTTLS and requires these environment variables:

```sh
export STATIONWATCH_SMTP_HOST="smtp.example.com"
export STATIONWATCH_SMTP_PORT="587"       # optional; 587 is the default
export STATIONWATCH_SMTP_USER="you@example.com"
export STATIONWATCH_SMTP_PASSWORD="your-app-password"
export STATIONWATCH_EMAIL_TO="you@example.com"
export STATIONWATCH_EMAIL_FROM="you@example.com"  # optional; defaults to SMTP user
```

Use an email-provider app password where available. Never put a password in the
Python file, plist, or Git. No third-party Python packages are required.

## Run and test manually

```sh
python3 station_watch.py
python3 -m unittest -v
```

Send safe synthetic alerts without downloading the Sheet or changing
`state.json`:

```sh
python3 station_watch.py --test-alert OFFLINE
python3 station_watch.py --test-alert HEALTHY
```

These messages use synthetic timestamps. They do not alter production data or
the monitor's transition state.

## Schedule it on macOS

The supplied `com.betterwithbees.stationwatch.plist` uses this project's actual
path and the installed Python 3.14 executable. There is no project `.venv` at
present. Make the SMTP variables available to your user `launchd` session with
`launchctl setenv`. Prompt for the password so it does not appear directly in
the command:

```sh
launchctl setenv STATIONWATCH_SMTP_HOST "smtp.example.com"
launchctl setenv STATIONWATCH_SMTP_PORT "587"
launchctl setenv STATIONWATCH_SMTP_USER "you@example.com"
launchctl setenv STATIONWATCH_EMAIL_TO "you@example.com"
read -s "smtp_password?SMTP app password: "
launchctl setenv STATIONWATCH_SMTP_PASSWORD "$smtp_password"
unset smtp_password
```

Install and start the job:

```sh
cp com.betterwithbees.stationwatch.plist ~/Library/LaunchAgents/
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.betterwithbees.stationwatch.plist
```

Verify it and inspect output:

```sh
launchctl print "gui/$(id -u)/com.betterwithbees.stationwatch"
tail -n 50 stationwatch.log
tail -n 50 stationwatch-error.log
```

Stop and disable it:

```sh
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/com.betterwithbees.stationwatch.plist
```

The `launchctl setenv` values apply to the current logged-in launchd session and
may need to be set again after logout or restart. The plist is only supplied;
this project does not install or start it automatically.

## Observation limits

`OFFLINE` means fresh telemetry is not reaching Google Sheets. A download or
parsing failure is instead reported as `MONITOR ERROR`, and the saved station
status is not changed.

StationWatch does **not** diagnose the transmitter, receiver, ESP-NOW link,
Wi-Fi, sensors, Apps Script, or root cause. Google Sheets remains its only
observation point; those diagnostic layers are future work.
