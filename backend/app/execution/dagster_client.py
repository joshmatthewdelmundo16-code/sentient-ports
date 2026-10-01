"""Dagster transport + configuration — D28.

The transport is the only place that talks to Dagster. It is defined behind a Protocol so the
platform test suite injects a fake and never needs a running Dagster; the real client uses
httpx (already a dependency) against the Dagster webserver's GraphQL API.

  - launch:  mutation launchRun(executionParams: {selector, runConfigData, executionMetadata})
  - status:  query runOrError(runId) { ... on Run { status } }

Credentials (only if a token is configured) are sent as an Authorization header, never logged,
never placed in URLs, and never placed in the run config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from backend.app.config import settings


class DagsterConfigError(Exception):
    """Dagster configuration is missing or incomplete."""


class DagsterTransportError(Exception):
    """Dagster could not be reached, or rejected the request."""


@dataclass(frozen=True)
class DagsterConfig:
    enabled: bool
    graphql_url: str
    location_name: str
    repository_name: str
    job_name: str
    auth_token: str
    timeout_s: float
    verify_tls: bool

    @classmethod
    def from_env(cls) -> "DagsterConfig":
        return cls(
            enabled=settings.DAGSTER_ENABLED,
            graphql_url=settings.DAGSTER_GRAPHQL_URL.rstrip("/"),
            location_name=settings.DAGSTER_LOCATION_NAME,
            repository_name=settings.DAGSTER_REPOSITORY_NAME,
            job_name=settings.DAGSTER_JOB_NAME,
            auth_token=settings.DAGSTER_AUTH_TOKEN,
            timeout_s=settings.DAGSTER_TIMEOUT_S,
            verify_tls=settings.DAGSTER_VERIFY_TLS,
        )

    def validate(self) -> None:
        """Raise DagsterConfigError unless a real integration could be attempted. Called only
        when "dagster" is explicitly selected, so an explicit request never silently falls back."""
        missing: list[str] = []
        if not self.enabled:
            missing.append("DAGSTER_ENABLED=true")
        if not self.graphql_url:
            missing.append("DAGSTER_GRAPHQL_URL")
        for name, value in (("DAGSTER_LOCATION_NAME", self.location_name),
                            ("DAGSTER_REPOSITORY_NAME", self.repository_name),
                            ("DAGSTER_JOB_NAME", self.job_name)):
            if not value:
                missing.append(name)
        if missing:
            raise DagsterConfigError(
                "Dagster executor was requested but is not configured/available. "
                "Set: " + ", ".join(missing) + "."
            )

    def selector(self) -> dict[str, str]:
        return {"repositoryLocationName": self.location_name,
                "repositoryName": self.repository_name,
                "jobName": self.job_name}

    def auth_header(self) -> dict[str, str]:
        """Never log the return value."""
        return {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}


@runtime_checkable
class DagsterTransport(Protocol):
    def launch_run(self, selector: dict[str, str], run_config: dict[str, Any],
                   tags: dict[str, str]) -> str:
        """Launch a job run. Return Dagster's run id."""
        ...

    def get_run_status(self, run_id: str) -> str:
        """Return the raw DagsterRunStatus (e.g. 'QUEUED', 'STARTED', 'SUCCESS')."""
        ...


LAUNCH_RUN = """
mutation LaunchRun($executionParams: ExecutionParams!) {
  launchRun(executionParams: $executionParams) {
    __typename
    ... on LaunchRunSuccess { run { runId status } }
    ... on PythonError { message }
    ... on RunConfigValidationInvalid { errors { message } }
    ... on InvalidSubsetError { message }
    ... on PipelineNotFoundError { message }
    ... on RunConflict { message }
    ... on UnauthorizedError { message }
    ... on PresetNotFoundError { message }
    ... on ConflictingExecutionParamsError { message }
    ... on NoModeProvidedError { message }
  }
}
"""

RUN_STATUS = """
query RunStatus($runId: ID!) {
  runOrError(runId: $runId) {
    __typename
    ... on Run { status }
    ... on RunNotFoundError { message }
    ... on PythonError { message }
  }
}
"""


class HttpxDagsterTransport:
    """httpx-backed transport for the Dagster webserver GraphQL API."""

    def __init__(self, config: DagsterConfig) -> None:
        self._cfg = config

    def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        import httpx

        try:
            with httpx.Client(timeout=self._cfg.timeout_s, verify=self._cfg.verify_tls,
                              headers={"Content-Type": "application/json", **self._cfg.auth_header()}) as c:
                resp = c.post(self._cfg.graphql_url, json={"query": query, "variables": variables})
        except httpx.HTTPError as exc:  # connection / timeout — never leaks credentials
            raise DagsterTransportError(f"Dagster unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise DagsterTransportError(f"Dagster request failed (HTTP {resp.status_code}).")
        body = resp.json()
        if body.get("errors"):
            raise DagsterTransportError(f"Dagster GraphQL error: {body['errors'][0].get('message', '?')}")
        return body.get("data") or {}

    def launch_run(self, selector: dict[str, str], run_config: dict[str, Any],
                   tags: dict[str, str]) -> str:
        data = self._post(LAUNCH_RUN, {"executionParams": {
            "selector": selector,
            "runConfigData": run_config,
            "executionMetadata": {"tags": [{"key": k, "value": v} for k, v in tags.items()]},
        }})
        out = data.get("launchRun") or {}
        if out.get("__typename") != "LaunchRunSuccess":
            detail = out.get("message") or "; ".join(e.get("message", "") for e in out.get("errors") or [])
            raise DagsterTransportError(f"Dagster refused the launch ({out.get('__typename')}): {detail}")
        return out["run"]["runId"]

    def get_run_status(self, run_id: str) -> str:
        out = self._post(RUN_STATUS, {"runId": run_id}).get("runOrError") or {}
        if out.get("__typename") != "Run" or not isinstance(out.get("status"), str):
            raise DagsterTransportError(f"Dagster run {run_id!r} not found ({out.get('__typename')}).")
        return out["status"]
