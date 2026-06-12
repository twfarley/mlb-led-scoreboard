"""Fetches and caches Home Assistant entity states for the dashboard."""

import time
from dataclasses import dataclass
from typing import Any, Optional

import requests
from bullpen.api import PluginData, UpdateStatus
from bullpen.logging import LOGGER

from .config import Config

# HA returns these for entities that are offline / not yet initialised.
_UNKNOWN_STATES = {"unknown", "unavailable", "none", ""}


@dataclass
class EntityState:
    entity_id: str
    state: str
    attributes: dict[str, Any]

    @property
    def unit(self) -> Optional[str]:
        return self.attributes.get("unit_of_measurement")

    @property
    def friendly_name(self) -> Optional[str]:
        return self.attributes.get("friendly_name")

    def as_float(self) -> Optional[float]:
        if self.state.lower() in _UNKNOWN_STATES:
            return None
        try:
            return float(self.state)
        except (TypeError, ValueError):
            return None

    def is_on(self) -> bool:
        return self.state.lower() in ("on", "true", "home", "connected")


class HomeAssistantData(PluginData):
    def __init__(self, config: Config) -> None:
        self.config = config
        self.last_update = 0.0
        self.available = False
        self.states: dict[str, EntityState] = {}

        self._session = requests.Session()
        if config.token:
            self._session.headers.update(
                {
                    "Authorization": f"Bearer {config.token}",
                    "Content-Type": "application/json",
                }
            )

        self.update(force=True)

    # ── PluginData API ───────────────────────────────────────────────────────

    def update(self, force: bool = False) -> UpdateStatus:
        if not force and time.time() - self.last_update < self.config.update_interval:
            return UpdateStatus.DEFERRED
        if not self.config.base_url or not self.config.token:
            return UpdateStatus.DEFERRED

        self.last_update = time.time()
        try:
            self._fetch()
            self.available = True
            return UpdateStatus.SUCCESS
        except Exception:
            LOGGER.exception(
                "[HOMEASSISTANT] Failed to fetch states from %s", self.config.base_url
            )
            self.available = False
            return UpdateStatus.FAIL

    # ── Fetch ────────────────────────────────────────────────────────────────

    def _fetch(self) -> None:
        wanted = self.config.all_entity_ids()
        if not wanted:
            return

        # One call to /api/states is cheaper than N calls to /api/states/<id>
        # once we want more than a couple of entities.
        if len(wanted) > 2:
            self._fetch_all(set(wanted))
        else:
            for entity_id in wanted:
                self._fetch_one(entity_id)

    def _fetch_all(self, wanted: set[str]) -> None:
        url = f"{self.config.base_url}/api/states"
        resp = self._session.get(
            url, verify=self.config.verify_ssl, timeout=self.config.timeout
        )
        resp.raise_for_status()
        for item in resp.json():
            eid = item.get("entity_id")
            if eid in wanted:
                self.states[eid] = EntityState(
                    entity_id=eid,
                    state=str(item.get("state", "")),
                    attributes=item.get("attributes", {}) or {},
                )

    def _fetch_one(self, entity_id: str) -> None:
        url = f"{self.config.base_url}/api/states/{entity_id}"
        resp = self._session.get(
            url, verify=self.config.verify_ssl, timeout=self.config.timeout
        )
        if resp.status_code == 404:
            LOGGER.warning("[HOMEASSISTANT] Entity not found: %s", entity_id)
            return
        resp.raise_for_status()
        item = resp.json()
        self.states[entity_id] = EntityState(
            entity_id=entity_id,
            state=str(item.get("state", "")),
            attributes=item.get("attributes", {}) or {},
        )

    # ── Accessors ────────────────────────────────────────────────────────────

    def get(self, entity_id: str) -> Optional[EntityState]:
        return self.states.get(entity_id)

    def get_float(self, entity_id: str, default: float = 0.0) -> float:
        ent = self.states.get(entity_id)
        if ent is None:
            return default
        val = ent.as_float()
        return default if val is None else val

    def has(self, entity_id: str) -> bool:
        return entity_id in self.states
