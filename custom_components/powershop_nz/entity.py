"""BlueprintEntity class."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION
from .coordinator import BlueprintDataUpdateCoordinator


class IntegrationBlueprintEntity(CoordinatorEntity[BlueprintDataUpdateCoordinator]):
    """Base entity for a Powershop property-backed device."""

    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: BlueprintDataUpdateCoordinator, *, consumer_id: str, name: str | None, connection_number: str | None) -> None:
        """Initialize entity for a specific property (consumer_id)."""
        super().__init__(coordinator)
        # Unique per property within this integration
        self._consumer_id = consumer_id
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{consumer_id}"
        # Device name = property name; model = connection_number
        self._attr_device_info = DeviceInfo(
            identifiers={(coordinator.config_entry.domain, f"consumer_{consumer_id}")},
            name=name,
            model=connection_number,
            manufacturer="Powershop NZ",
        )
