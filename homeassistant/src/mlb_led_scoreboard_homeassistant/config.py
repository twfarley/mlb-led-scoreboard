"""Configuration parsing for the Home Assistant dashboard plugin.

The plugin reads its settings from the ``plugins.homeassistant`` key of the
scoreboard's ``config.json``. See the plugin README for the full schema and
worked examples.
"""

from dataclasses import dataclass, field
from typing import Any, Optional

import bullpen.api as api
from bullpen.logging import LOGGER


@dataclass
class Tile:
    """A single value read from a Home Assistant entity, shown on the grid layout."""

    entity: str
    label: str = ""
    unit: Optional[str] = None          # override HA's unit_of_measurement; "" hides it
    decimals: int = 1                   # rounding for numeric states
    scale: float = 1.0                  # multiply the numeric state (e.g. 0.001 for W -> kW)
    color: Optional[list] = None        # [r, g, b] for the value text
    label_color: Optional[list] = None  # [r, g, b] for the label text
    hide_when_unavailable: bool = False  # drop the tile entirely when its state has no data

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> "Tile":
        return Tile(
            entity=raw.get("entity", ""),
            label=raw.get("label", ""),
            unit=raw.get("unit"),
            decimals=int(raw.get("decimals", 1)),
            scale=float(raw.get("scale", 1.0)),
            color=raw.get("color"),
            label_color=raw.get("label_color"),
            hide_when_unavailable=bool(raw.get("hide_when_unavailable", False)),
        )


# Default HA entity ids for the Tesla integration's Powerwall. Users override
# any of these in config; anything left blank is simply not drawn.
_POWERWALL_DEFAULT_ENTITIES = {
    "solar": "sensor.solar_power",
    "home": "sensor.load_power",
    "grid": "sensor.grid_power",
    "battery": "sensor.battery_power",
    "charge": "sensor.percentage_charged",
    "solar_today": "",
    "grid_status": "",
}


class Config(api.PluginConfig):
    def __init__(self, base: api.MLBConfig) -> None:
        self.scrolling_speed = base.scrolling_speed

        cfg = base.plugin_config

        # ── Connection ───────────────────────────────────────────────────────
        self.base_url: str = str(cfg.get("base_url", "")).rstrip("/")
        self.token: str = cfg.get("token", "")
        self.verify_ssl: bool = cfg.get("verify_ssl", True)
        self.update_interval: int = int(cfg.get("update_interval", 30))
        self.timeout: float = float(cfg.get("timeout", 10))

        # ── Dashboard ────────────────────────────────────────────────────────
        dashboard = cfg.get("dashboard", {}) or {}
        self.layout_mode: str = dashboard.get("layout", "grid")
        self.title: str = dashboard.get("title", "")

        # grid layout
        self.tiles: list[Tile] = [
            Tile.from_dict(t) for t in dashboard.get("tiles", []) if t.get("entity")
        ]
        self.columns: int = int(dashboard.get("columns", 2))

        # powerwall layout
        entities = dict(_POWERWALL_DEFAULT_ENTITIES)
        entities.update(dashboard.get("entities", {}) or {})
        self.entities: dict[str, str] = entities
        # How the HA power entities report values: "W" (default for the Tesla
        # integration) or "kW". We normalise everything to kW internally.
        self.power_unit: str = str(dashboard.get("power_unit", "W")).lower()

        self._validate()

    def all_entity_ids(self) -> list[str]:
        """Every entity id this dashboard needs to fetch, de-duplicated."""
        ids: list[str] = []
        if self.layout_mode == "powerwall":
            ids = [e for e in self.entities.values() if e]
        else:
            ids = [t.entity for t in self.tiles if t.entity]
        # preserve order, drop dupes
        seen: set[str] = set()
        unique = []
        for e in ids:
            if e not in seen:
                seen.add(e)
                unique.append(e)
        return unique

    @property
    def power_scale(self) -> float:
        """Multiplier to convert a power entity's native value into kW."""
        return 0.001 if self.power_unit == "w" else 1.0

    def _validate(self) -> None:
        if not self.base_url:
            LOGGER.warning(
                "[HOMEASSISTANT] No 'base_url' configured. Plugin cannot fetch data."
            )
        if not self.token:
            LOGGER.warning(
                "[HOMEASSISTANT] No 'token' configured. Create a long-lived access "
                "token in Home Assistant (Profile -> Security) and add it to config."
            )
        if self.layout_mode not in ("grid", "powerwall"):
            LOGGER.warning(
                "[HOMEASSISTANT] Unknown dashboard layout '%s'; falling back to 'grid'.",
                self.layout_mode,
            )
            self.layout_mode = "grid"
        if self.layout_mode == "grid" and not self.tiles:
            LOGGER.warning(
                "[HOMEASSISTANT] Grid dashboard has no 'tiles' configured; nothing to show."
            )
