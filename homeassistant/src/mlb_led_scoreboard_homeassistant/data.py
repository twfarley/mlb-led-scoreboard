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
        except requests.exceptions.RequestException as e:
            # Network hiccup (timeout, connection refused, ...). Keep showing
            # the last-known values rather than flipping to "unavailable", and
            # log a one-line warning instead of a full stack trace.
            LOGGER.warning(
                "[HOMEASSISTANT] Fetch from %s failed: %s", self.config.base_url, e
            )
            return UpdateStatus.FAIL
        except Exception:
            LOGGER.exception(
                "[HOMEASSISTANT] Failed to fetch states from %s", self.config.base_url
            )
            self.available = False
            return UpdateStatus.FAIL

    # ── Fetch ────────────────────────────────────────────────────────────────

    def _fetch(self) -> None:
        # Fetch only the entities we actually display, one targeted request
        # each. The /api/states bulk endpoint returns *every* entity in the HA
        # instance (hundreds of them, fully serialised) — far more work for the
        # server than a handful of /api/states/<id> calls, and the cause of the
        # read timeouts when several dashboards polled it. Requests reuse the
        # session's keep-alive connection, so the per-entity calls are cheap.
        for entity_id in self.config.all_entity_ids():
            self._fetch_one(entity_id)

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
