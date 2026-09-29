"""Airflow transport + configuration — D21.

The transport is the only place that talks HTTP to Airflow. It is defined behind a Protocol
so tests inject a fake and never require a running Airflow (Airflow is not installed in the
development environment). The real client uses httpx, which is already a dependency.

Airflow 3.x public REST API is used: base path ``/api/v2``.
  - trigger:  POST /api/v2/dags/{dag_id}/dagRuns   body {"dag_run_id", "conf"}
  - status:   GET  /api/v2/dags/{dag_id}/dagRuns/{dag_run_id}  → {"state": ...}

Credentials are read from the environment only, sent as an Authorization header, and never
logged, never placed in URLs, and never placed in the DAG conf/params.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from backend.app.config import settings


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class AirflowConfigError(Exception):
    """Airflow configuration is missing or incomplete."""


class AirflowTransportError(Exception):
    """Airflow could not be reached, or returned an error response."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AirflowConfig:
    enabled: bool
    base_url: str
    dag_id: str
    auth_token: str
    username: str
    password: str
    timeout_s: float
    verify_tls: bool

    @classmethod
    def from_env(cls) -> "AirflowConfig":
        return cls(
            enabled=settings.AIRFLOW_ENABLED,
            base_url=settings.AIRFLOW_BASE_URL.rstrip("/"),
            dag_id=settings.AIRFLOW_DAG_ID,
            auth_token=settings.AIRFLOW_AUTH_TOKEN,
            username=settings.AIRFLOW_USERNAME,
            password=settings.AIRFLOW_PASSWORD,
            timeout_s=settings.AIRFLOW_POLL_TIMEOUT_S,
            verify_tls=settings.AIRFLOW_VERIFY_TLS,
        )

    def has_auth(self) -> bool:
        return bool(self.auth_token) or bool(self.username and self.password)

    def validate(self) -> None:
        """Raise AirflowConfigError unless a real integration could be attempted.

        Called only when "airflow" is explicitly selected, so that an explicit request
        never silently falls back to the in-process executor.
        """
        missing: list[str] = []
        if not self.enabled:
            missing.append("AIRFLOW_ENABLED=true")
        if not self.base_url:
            missing.append("AIRFLOW_BASE_URL")
        if not self.dag_id:
            missing.append("AIRFLOW_DAG_ID")
        if not self.has_auth():
            missing.append("AIRFLOW_AUTH_TOKEN or AIRFLOW_USERNAME+AIRFLOW_PASSWORD")
        if missing:
            raise AirflowConfigError(
                "Airflow executor was requested but is not configured/available. "
                "Set: " + ", ".join(missing) + "."
            )

    def auth_header(self) -> dict[str, str]:
        """Build the Authorization header. Never log the return value."""
        if self.auth_token:
            return {"Authorization": f"Bearer {self.auth_token}"}
        if self.username and self.password:
            raw = f"{self.username}:{self.password}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        return {}


# ---------------------------------------------------------------------------
# Transport contract
# ---------------------------------------------------------------------------

@runtime_checkable
class AirflowTransport(Protocol):
    def trigger_dag_run(
        self, dag_id: str, dag_run_id: str, conf: dict[str, Any]
    ) -> dict[str, Any]:
        """Trigger a DAG run. Return the created dag-run object (parsed JSON)."""
        ...

    def get_dag_run_state(self, dag_id: str, dag_run_id: str) -> str:
        """Return the raw Airflow dag-run state string (e.g. 'running', 'success')."""
        ...


# ---------------------------------------------------------------------------
# Real httpx transport (Airflow 3.x /api/v2)
# ---------------------------------------------------------------------------

class HttpxAirflowTransport:
    """httpx-backed transport for the Airflow 3.x REST API. Not exercised by the test
    suite (Airflow is not installed); covered indirectly by the fake transport."""

    API_BASE = "/api/v2"

    def __init__(self, config: AirflowConfig) -> None:
        self._cfg = config

    def _client(self):
        import httpx  # imported lazily; httpx is present but keep the boundary explicit

        return httpx.Client(
            base_url=self._cfg.base_url,
            headers={"Content-Type": "application/json", **self._cfg.auth_header()},
            timeout=self._cfg.timeout_s,
            verify=self._cfg.verify_tls,
        )

    def trigger_dag_run(
        self, dag_id: str, dag_run_id: str, conf: dict[str, Any]
    ) -> dict[str, Any]:
        import httpx

        # Minimal payload: correlation id + conf only. No logical_date (D21 correction #3).
        payload = {"dag_run_id": dag_run_id, "conf": conf}
        try:
            with self._client() as client:
                resp = client.post(f"{self.API_BASE}/dags/{dag_id}/dagRuns", json=payload)
        except httpx.HTTPError as exc:  # connection / timeout — never leaks credentials
            raise AirflowTransportError(f"Airflow unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise AirflowTransportError(
                f"Airflow trigger failed (HTTP {resp.status_code})."
            )
        return resp.json()

    def get_dag_run_state(self, dag_id: str, dag_run_id: str) -> str:
        import httpx

        try:
            with self._client() as client:
                resp = client.get(f"{self.API_BASE}/dags/{dag_id}/dagRuns/{dag_run_id}")
        except httpx.HTTPError as exc:
            raise AirflowTransportError(f"Airflow unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise AirflowTransportError(
                f"Airflow status query failed (HTTP {resp.status_code})."
            )
        state = resp.json().get("state")
        if not isinstance(state, str):
            raise AirflowTransportError("Airflow dag-run response missing 'state'.")
        return state
