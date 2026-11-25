"""Statistics and profiling procedures for EMS integration."""
import logging
from datetime import datetime, timedelta
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .utils import ems_log

_LOGGER = logging.getLogger(__name__)

def _calculate_hourly_averages(stats_list: list, tzinfo) -> dict[int, dict[int, float]] | None:
    """Calculate average consumption per weekday and hour from statistics.

    This helper is executed in the thread pool to avoid blocking the Event Loop.
    """
    if not stats_list:
        return None

    # Group values by weekday and hour
    # hourly_data: { weekday: { hour: [deltas] } }
    hourly_data: dict[int, dict[int, list[float]]] = {}

    for i in range(1, len(stats_list)):
        prev_sum = stats_list[i - 1].get("sum")
        curr_sum = stats_list[i].get("sum")
        if prev_sum is None or curr_sum is None:
            continue

        delta = curr_sum - prev_sum
        if delta < 0:
            # Skip resets or negative consumption changes
            continue

        start = stats_list[i].get("start")
        if isinstance(start, (int, float)):
            entry_dt = datetime.fromtimestamp(start, tz=tzinfo)
        elif isinstance(start, datetime):
            entry_dt = start
        else:
            continue

        weekday = entry_dt.weekday()
        hour = entry_dt.hour
        hourly_data.setdefault(weekday, {}).setdefault(hour, []).append(delta)

    # Average the collected values
    averages: dict[int, dict[int, float]] = {}
    for weekday, hours in hourly_data.items():
        averages[weekday] = {}
        for hour, values in hours.items():
            if values:
                averages[weekday][hour] = sum(values) / len(values)
            else:
                averages[weekday][hour] = 0.0

    return averages

async def async_get_average_hourly_consumption(
    hass: HomeAssistant, sensor_id: str, days: int
) -> dict[int, dict[int, float]] | None:
    """Query statistics from the database and compute average hourly consumption.

    Returns a dictionary mapping weekday (0-6) -> hour (0-23) -> average kWh.
    """
    if "recorder" not in hass.config.components:
        ems_log(hass, _LOGGER, logging.WARNING, "Recorder component is not loaded, cannot fetch statistics")
        return None

    try:
        from homeassistant.components.recorder.statistics import statistics_during_period
    except ImportError:
        ems_log(hass, _LOGGER, logging.WARNING, "Failed to import statistics helper from recorder")
        return None

    now = dt_util.now()
    start_time = now - timedelta(days=days)

    ems_log(
        hass,
        _LOGGER,
        logging.DEBUG,
        "Fetching statistics for %s from %s to %s",
        sensor_id, start_time, now
    )

    try:
        # Fetch statistics for the specified period in the executor thread
        stats = await hass.async_add_executor_job(
            statistics_during_period,
            hass,
            start_time,
            now,
            {sensor_id},
            "hour",
            None,
            {"sum"},
        )
    except Exception as err:
        ems_log(hass, _LOGGER, logging.ERROR, "Error querying statistics for %s: %s", sensor_id, err, exc_info=True)
        return None

    sensor_stats = stats.get(sensor_id, [])
    if not sensor_stats:
        ems_log(hass, _LOGGER, logging.WARNING, "No historical statistics found for entity %s", sensor_id)
        return None

    # Calculate hourly averages in the executor thread to avoid blocking Event Loop
    averages = await hass.async_add_executor_job(
        _calculate_hourly_averages,
        sensor_stats,
        now.tzinfo
    )

    return averages


def compute_monthly_counter_from_stats(
    stats_list: list, month_start: datetime, tzinfo
) -> dict | None:
    """Compute monthly counter delta and boundary baseline from HA long-term statistics.

    Uses statistics rows before `month_start` as the exact 00:00:00 boundary snapshot
    and sums closed hourly `sum` increments so mid-month meter resets are handled cleanly.
    """
    if not stats_list:
        return None

    parsed: list[tuple[datetime, dict]] = []
    for row in stats_list:
        if not isinstance(row, dict):
            continue
        start = row.get("start")
        if isinstance(start, (int, float)):
            entry_dt = datetime.fromtimestamp(start, tz=tzinfo)
        elif isinstance(start, datetime):
            entry_dt = start.astimezone(tzinfo) if start.tzinfo else start.replace(tzinfo=tzinfo)
        else:
            continue
        parsed.append((entry_dt, row))

    if not parsed:
        return None

    parsed.sort(key=lambda item: item[0])

    # Rows before month_start reflect values accumulated up to month_start 00:00:00
    pre_rows = [
        (dt, r) for dt, r in parsed
        if dt < month_start and (r.get("sum") is not None or r.get("state") is not None)
    ]
    all_valid = [
        (dt, r) for dt, r in parsed
        if r.get("sum") is not None or r.get("state") is not None
    ]
    if not all_valid:
        return None

    last_dt, last_row = all_valid[-1]

    if pre_rows:
        _, boundary_row = pre_rows[-1]
        baseline_state = float(boundary_row["state"]) if boundary_row.get("state") is not None else None
        if last_dt < month_start:
            closed_hours_delta = 0.0
            last_closed_state = baseline_state
        else:
            if boundary_row.get("sum") is not None and last_row.get("sum") is not None:
                closed_hours_delta = max(0.0, float(last_row["sum"]) - float(boundary_row["sum"]))
            elif boundary_row.get("state") is not None and last_row.get("state") is not None:
                closed_hours_delta = max(0.0, float(last_row["state"]) - float(boundary_row["state"]))
            else:
                return None
            last_closed_state = float(last_row["state"]) if last_row.get("state") is not None else None
        return {
            "closed_hours_delta": round(closed_hours_delta, 4),
            "last_closed_state": last_closed_state,
            "baseline_state": baseline_state,
            "baseline_source": "lts_boundary",
        }

    # No pre-month boundary row in recorder: fall back to earliest row in current month
    first_dt, first_row = all_valid[0]
    baseline_state = float(first_row["state"]) if first_row.get("state") is not None else None
    if first_row.get("sum") is not None and last_row.get("sum") is not None:
        closed_hours_delta = max(0.0, float(last_row["sum"]) - float(first_row["sum"]))
    elif first_row.get("state") is not None and last_row.get("state") is not None:
        closed_hours_delta = max(0.0, float(last_row["state"]) - float(first_row["state"]))
    else:
        closed_hours_delta = 0.0
    last_closed_state = float(last_row["state"]) if last_row.get("state") is not None else None
    return {
        "closed_hours_delta": round(closed_hours_delta, 4),
        "last_closed_state": last_closed_state,
        "baseline_state": baseline_state,
        "baseline_source": "lts_partial_month",
    }


async def async_get_monthly_counter_stats(
    hass: HomeAssistant, sensor_id: str, month_start: datetime
) -> dict | None:
    """Query recorder long-term statistics from just before month_start up to now."""
    if "recorder" not in hass.config.components:
        ems_log(hass, _LOGGER, logging.WARNING, "Recorder component is not loaded, cannot fetch monthly statistics")
        return None

    try:
        from homeassistant.components.recorder.statistics import statistics_during_period
    except ImportError:
        ems_log(hass, _LOGGER, logging.WARNING, "Failed to import statistics_during_period from recorder")
        return None

    now = dt_util.now()
    query_start = month_start - timedelta(hours=6)

    try:
        stats = await hass.async_add_executor_job(
            statistics_during_period,
            hass,
            query_start,
            now,
            {sensor_id},
            "hour",
            None,
            {"sum", "state"},
        )
    except Exception as err:
        ems_log(hass, _LOGGER, logging.ERROR, "Error querying monthly statistics for %s: %s", sensor_id, err, exc_info=True)
        return None

    sensor_stats = stats.get(sensor_id, [])
    if not sensor_stats:
        return None

    return await hass.async_add_executor_job(
        compute_monthly_counter_from_stats,
        sensor_stats,
        month_start,
        now.tzinfo,
    )
