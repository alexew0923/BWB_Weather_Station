"""Parameter-driven engineering power-budget model.

Nothing in this module is inferred from battery voltage. Every result is a
modeled quantity calculated only from parameters supplied by the caller.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EnergyModelParameters:
    """Hardware and duty-cycle assumptions for an approximate daily budget."""

    active_current_ma: float
    sleep_current_ma: float
    active_duration_seconds: float
    sleep_duration_seconds: float
    cycles_per_day: float
    sensor_current_ma: float = 0.0
    radio_current_ma: float = 0.0
    battery_nominal_capacity_mah: float | None = None
    panel_rated_power_w: float | None = None
    solar_equivalent_hours: float | None = None
    charging_efficiency: float | None = None


def validate_energy_parameters(parameters):
    """Reject incomplete, negative, or physically empty model inputs."""
    nonnegative = {
        "active_current_ma": parameters.active_current_ma,
        "sleep_current_ma": parameters.sleep_current_ma,
        "active_duration_seconds": parameters.active_duration_seconds,
        "sleep_duration_seconds": parameters.sleep_duration_seconds,
        "sensor_current_ma": parameters.sensor_current_ma,
        "radio_current_ma": parameters.radio_current_ma,
    }
    for name, value in nonnegative.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")

    if parameters.cycles_per_day <= 0:
        raise ValueError("cycles_per_day must be greater than zero")
    if parameters.active_duration_seconds + parameters.sleep_duration_seconds <= 0:
        raise ValueError("cycle duration must be greater than zero")
    if (
        parameters.active_current_ma + parameters.sensor_current_ma
        + parameters.radio_current_ma <= 0
        and parameters.sleep_current_ma <= 0
    ):
        raise ValueError("at least one current draw must be greater than zero")
    if parameters.battery_nominal_capacity_mah is not None and parameters.battery_nominal_capacity_mah <= 0:
        raise ValueError("battery_nominal_capacity_mah must be greater than zero")

    solar = (
        parameters.panel_rated_power_w,
        parameters.solar_equivalent_hours,
        parameters.charging_efficiency,
    )
    supplied = [value is not None for value in solar]
    if any(supplied) and not all(supplied):
        raise ValueError(
            "panel_rated_power_w, solar_equivalent_hours, and "
            "charging_efficiency must be supplied together"
        )
    if all(supplied):
        if parameters.panel_rated_power_w <= 0:
            raise ValueError("panel_rated_power_w must be greater than zero")
        if parameters.solar_equivalent_hours < 0:
            raise ValueError("solar_equivalent_hours must be non-negative")
        if not 0 < parameters.charging_efficiency <= 1:
            raise ValueError("charging_efficiency must be in (0, 1]")


def model_daily_power_budget(parameters):
    """Return labeled modeled quantities with correct mA/s and W/h units."""
    validate_energy_parameters(parameters)
    active_current = (
        parameters.active_current_ma
        + parameters.sensor_current_ma
        + parameters.radio_current_ma
    )
    charge_per_cycle_mah = (
        active_current * parameters.active_duration_seconds
        + parameters.sleep_current_ma * parameters.sleep_duration_seconds
    ) / 3600.0
    daily_charge_mah = parameters.cycles_per_day * charge_per_cycle_mah

    solar_energy_wh = None
    if parameters.panel_rated_power_w is not None:
        solar_energy_wh = (
            parameters.panel_rated_power_w
            * parameters.solar_equivalent_hours
            * parameters.charging_efficiency
        )

    ideal_runtime_days = None
    if parameters.battery_nominal_capacity_mah is not None:
        ideal_runtime_days = parameters.battery_nominal_capacity_mah / daily_charge_mah

    return {
        "quantity_category": "modeled",
        "calibration_status": "uncalibrated engineering approximation",
        "charge_per_cycle_mah": charge_per_cycle_mah,
        "daily_charge_consumption_mah": daily_charge_mah,
        "solar_energy_input_wh": solar_energy_wh,
        "ideal_no_charge_runtime_days": ideal_runtime_days,
        "limitations": [
            "Results depend entirely on caller-supplied hardware and duty-cycle parameters.",
            "Ideal runtime ignores voltage-dependent capacity, conversion losses, load transients, temperature, aging, and charging.",
            "Solar energy input is not directly comparable with charge consumption without a defensible system voltage and conversion model.",
        ],
    }
