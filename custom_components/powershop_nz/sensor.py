"""Sensor platform for powershop_nz."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from datetime import datetime, date, time, timedelta

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import IntegrationBlueprintEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import BlueprintDataUpdateCoordinator
    from .data import IntegrationBlueprintConfigEntry

ENTITY_DESCRIPTIONS = (
    SensorEntityDescription(
        key="consumption_kwh",
        name="Consumption (kWh)",
        icon="mdi:flash",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 Unused function argument: `hass`
    entry: IntegrationBlueprintConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    # Create a sensor per property returned by the coordinator
    coordinator = entry.runtime_data.coordinator
    props: list[dict[str, Any]] = coordinator.data.get("properties", []) if coordinator.data else []

    entities: list[IntegrationBlueprintSensor] = []
    for prop in props:
        entities.append(
            IntegrationBlueprintSensor(
                coordinator=coordinator,
                entity_description=ENTITY_DESCRIPTIONS[0],
                consumer_id=str(prop.get("consumer_id")),
                name=prop.get("name"),
                connection_number=prop.get("connection_number"),
            )
        )

    if entities:
        async_add_entities(entities)


class IntegrationBlueprintSensor(IntegrationBlueprintEntity, SensorEntity):
    """powershop_nz Sensor class."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(
        self,
        coordinator: BlueprintDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
        *,
        consumer_id: str,
        name: str | None,
        connection_number: str | None,
    ) -> None:
        """Initialize the sensor class for a specific property."""
        super().__init__(
            coordinator,
            consumer_id=consumer_id,
            name=name,
            connection_number=connection_number,
        )
        self.entity_description = entity_description
        self._consumer_id = consumer_id
        self._prop_name = name or f"Property {consumer_id}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Publish statistics when the entity is added
        await self._publish_statistics()

    def _handle_coordinator_update(self) -> None:
        # When new data arrives, republish/upsert statistics
        self.hass.async_create_task(self._publish_statistics())
        super()._handle_coordinator_update()

    async def _publish_statistics(self) -> None:
        data = self.coordinator.data or {}
        usages_by_cid: dict[str, Any] = data.get("usages", {})
        usage_payload = usages_by_cid.get(self._consumer_id)
        if not usage_payload or "data" not in usage_payload:
            return
        usage_days = usage_payload["data"].get("usages", [])
        if not usage_days:
            return

        # Sort by date to ensure chronological order
        def _extract_date(d: dict[str, Any]) -> date:
            dstr = d.get("iso8601_date") or d.get("date")
            try:
                return date.fromisoformat(dstr)
            except Exception:
                # Fallback: try parsing common formats
                return datetime.strptime(dstr, "%Y-%m-%d").date()

        usage_days_sorted = sorted(usage_days, key=_extract_date)

        tz = dt_util.get_time_zone(self.hass.config.time_zone) or dt_util.UTC
        running_sum_kwh = 0.0
        stats: list[StatisticData] = []

        for day in usage_days_sorted:
            dstr = day.get("iso8601_date") or day.get("date")
            try:
                day_date = date.fromisoformat(dstr)
            except Exception:
                day_date = datetime.strptime(dstr, "%Y-%m-%d").date()
            base_local = datetime.combine(day_date, time(0, 0, tzinfo=tz))
            values = day.get("usage", []) or []
            # Aggregate half-hour Wh into hourly kWh
            for i in range(0, len(values) - 1, 2):
                try:
                    wh = float(values[i]) + float(values[i + 1])
                except Exception:
                    continue
                kwh = wh / 1000.0
                running_sum_kwh += kwh
                hour_index = i // 2
                start_local = base_local + timedelta(hours=hour_index)
                start_utc = dt_util.as_utc(start_local)
                stats.append(StatisticData(start=start_utc, sum=running_sum_kwh))

        if not stats:
            return

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"{self._prop_name} Consumption",
            source=DOMAIN,
            statistic_id=f"{DOMAIN}:consumption:{self._consumer_id}",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )
        async_add_external_statistics(self.hass, metadata, stats)

    @property
    def native_value(self) -> float | None:
        """Return today's total usage (kWh) for this property (for quick glance)."""
        data = self.coordinator.data or {}
        usages_by_cid: dict[str, Any] = data.get("usages", {})
        usage_payload = usages_by_cid.get(self._consumer_id)
        if not usage_payload or "data" not in usage_payload:
            return None
        usages = usage_payload["data"].get("usages", [])
        if not usages:
            return None
        # Try to find today's date, else use the most recent day
        today_str = datetime.now().strftime("%Y-%m-%d")
        chosen = None
        for day in usages:
            if day.get("date") == today_str or day.get("iso8601_date") == today_str:
                chosen = day
                break
        if chosen is None:
            chosen = usages[-1]
        values = chosen.get("usage", []) or []
        if not values:
            return None
        try:
            total_wh = float(sum(float(v) for v in values))
            return round(total_wh / 1000.0, 3)
        except Exception:
            return None
