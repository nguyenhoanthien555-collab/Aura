"""
HTTP Sync Client for communicating with AURA Relay or Peer nodes.
Ensures network timeouts never cause data loss and state transitions are explicit.
"""

from __future__ import annotations

import json
from typing import Any

from core.logger import logger
from core.sync.models import SyncEvent


class SyncNetworkException(Exception):
    """Network connection error, socket drop, or server unreachable."""
    pass


class SyncTimeoutException(SyncNetworkException):
    """Network request timed out. State is UNKNOWN/PENDING, NEVER failed."""
    pass


class SyncRelayClient:
    def __init__(self, relay_url: str, auth_token: str = "", timeout: float = 10.0):
        self.relay_url = relay_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    def push_events(self, node_id: str, events: list[SyncEvent]) -> tuple[list[str], list[dict]]:
        """
        Push local events to relay.
        Returns (acknowledged_ids, list_of_conflicts).
        Raises SyncTimeoutException on timeout so event remains PENDING in outbox!
        """
        import httpx

        url = f"{self.relay_url}/api/sync/events/push"
        payload = {
            "node_id": node_id,
            "events": [e.to_dict() for e in events],
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(url, json=payload, headers=self._headers())
                if res.status_code == 200:
                    data = res.json()
                    return data.get("acknowledged", []), data.get("conflicts", [])
                elif res.status_code in (401, 403):
                    raise PermissionError(f"Relay authentication failed: {res.status_code}")
                else:
                    raise SyncNetworkException(f"Relay returned HTTP {res.status_code}: {res.text}")
        except httpx.TimeoutException as e:
            raise SyncTimeoutException(f"Push timed out after {self.timeout}s: {e}") from e
        except httpx.RequestError as e:
            raise SyncNetworkException(f"Push request error: {e}") from e

    def pull_events(
        self,
        node_id: str,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[list[SyncEvent], int, bool]:
        """
        Pull events from relay destined for or after cursor.
        Returns (events, server_cursor, has_more).
        """
        import httpx

        url = f"{self.relay_url}/api/sync/events/pull"
        params = {
            "node_id": node_id,
            "after_sequence": after_sequence,
            "limit": limit,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.get(url, params=params, headers=self._headers())
                if res.status_code == 200:
                    data = res.json()
                    events = [SyncEvent.from_dict(d) for d in data.get("events", [])]
                    return events, data.get("cursor", after_sequence), data.get("has_more", False)
                elif res.status_code in (401, 403):
                    raise PermissionError(f"Relay authentication failed: {res.status_code}")
                else:
                    raise SyncNetworkException(f"Relay returned HTTP {res.status_code}: {res.text}")
        except httpx.TimeoutException as e:
            raise SyncTimeoutException(f"Pull timed out after {self.timeout}s: {e}") from e
        except httpx.RequestError as e:
            raise SyncNetworkException(f"Pull request error: {e}") from e

    def ack_events(self, node_id: str, event_ids: list[str]) -> bool:
        import httpx

        url = f"{self.relay_url}/api/sync/events/ack"
        payload = {"node_id": node_id, "event_ids": event_ids}

        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(url, json=payload, headers=self._headers())
                return res.status_code == 200
        except Exception:
            return False
