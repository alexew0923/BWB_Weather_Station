"""Narrow, one-directional reuse of the reliability audit's validated helpers.

``analysis/sensor-health-analysis`` already established this pattern: a sibling
analysis project may reuse the reliability audit's stable, side-effect-free
helpers by putting its directory on ``sys.path``. The dependency direction is

    environmental-analysis -> reliability-audit

and nothing points back, so no cycle is created. The audit imports only pandas,
NumPy and the standard library.

Only two things are reused, and both are things it would be actively wrong to
reimplement:

``localize_timestamps``
    The audit resolves the annual Atlantic fall-back from *file order* rather
    than guessing, and pins the changeover with evidence from this very
    dataset. Re-deriving that here would produce a second, subtly different
    answer to a question that already has a correct one.

``audit_config``
    The operating schedule, the nominal cycle, the station timezone and the
    physically plausible sensor ranges. The schedule in particular is the
    audit's "single authoritative source of expected", and coverage figures
    computed against a second copy of it would drift out of agreement with
    every other reliability number in the repository.

The audit's ``load_and_validate_data`` is deliberately *not* reused: it takes a
filesystem path, prints a report to stdout and raises ``SystemExit``. None of
those behaviours belong in a library that must serve a web service.
"""

import sys
from pathlib import Path

from .errors import ConfigurationError

PROJECT_DIR = Path(__file__).resolve().parents[1]
RELIABILITY_PROJECT = PROJECT_DIR.parent / "reliability-audit"

if str(RELIABILITY_PROJECT) not in sys.path:
    # Appended, not inserted: this package's own modules must keep precedence
    # over sibling modules that happen to share a name.
    sys.path.append(str(RELIABILITY_PROJECT))

try:  # pragma: no cover - exercised implicitly by every other test
    import audit_config
    from data_validation import localize_timestamps
except ImportError as error:  # pragma: no cover - defensive
    raise ConfigurationError(
        f"the reliability-audit project could not be imported from "
        f"{RELIABILITY_PROJECT}: {error}",
        summary=(
            "The environmental analysis engine depends on the reliability-audit "
            "project, which is not importable."
        ),
    ) from error


STATION_TIMEZONE = audit_config.STATION_TIMEZONE
NOMINAL_CYCLE_MINUTES = audit_config.NOMINAL_CYCLE_MINUTES
PLAUSIBLE_SENSOR_RANGES = dict(audit_config.PLAUSIBLE_SENSOR_RANGES)
MAX_PLAUSIBLE_COUNT = audit_config.MAX_PLAUSIBLE_COUNT

scheduled_transmissions_between = audit_config.scheduled_transmissions_between
active_minutes_between = audit_config.active_minutes_between
active_window_for = audit_config.active_window_for
regime_for = audit_config.regime_for

__all__ = [
    "STATION_TIMEZONE",
    "NOMINAL_CYCLE_MINUTES",
    "PLAUSIBLE_SENSOR_RANGES",
    "MAX_PLAUSIBLE_COUNT",
    "localize_timestamps",
    "scheduled_transmissions_between",
    "active_minutes_between",
    "active_window_for",
    "regime_for",
]
