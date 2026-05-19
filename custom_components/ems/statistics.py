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
