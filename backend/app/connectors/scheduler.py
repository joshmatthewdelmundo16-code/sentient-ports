"""In-process poll scheduler — D27 (REST), D28 (Airbyte). Off by default (POLL_SCHEDULER_ENABLED).

Runs on ONE instance only: with several app instances each would poll. It checks every 15 s
for active REST and Airbyte sources whose interval has elapsed and polls each inside its own
organization's scope, in its own session, so a failing source cannot affect another.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

log = logging.getLogger("platform.poller")
_stop = threading.Event()
_thread: threading.Thread | None = None


def run_due_once(session_factory=None) -> int:
    from backend.app.connectors import airbyte
    from backend.app.connectors.sources import poll_rest
    from backend.app.persistence.database import ConnectorSource, SessionLocal
    from backend.app.security.tenancy import tenant_scope

    factory = session_factory or SessionLocal
    now = datetime.now(timezone.utc)
    db = factory()
    try:
        due = []
        for src in db.execute(select(ConnectorSource).where(ConnectorSource.kind.in_(("rest_poll", airbyte.KIND)),
                                                            ConnectorSource.status == "active")).scalars():
            last = src.last_run_at if (src.last_run_at is None or src.last_run_at.tzinfo) else src.last_run_at.replace(tzinfo=timezone.utc)
            if last is None or now - last >= timedelta(seconds=max(30, src.poll_interval_s or 300)):
                due.append((src.id, src.organization_id))
    finally:
        db.close()
    polled = 0
    for source_id, org_id in due:
        db = factory()
        try:
            with tenant_scope(db, org_id):
                src = db.get(ConnectorSource, source_id)
                if src is not None:
                    # Airbyte: re-reading unchanged synced rows is a no-op ("unchanged").
                    (airbyte.ingest if src.kind == airbyte.KIND else poll_rest)(db, src)
                    polled += 1
            db.commit()
        except Exception:  # noqa: BLE001 — one bad source must not stop the others
            db.rollback()
            log.exception("Poll failed for source %s", source_id)
        finally:
            db.close()
    return polled


def start() -> None:
    global _thread
    if _thread is not None:
        return

    def loop() -> None:
        while not _stop.wait(15.0):
            try:
                run_due_once()
            except Exception:  # noqa: BLE001
                log.exception("Poll scheduler iteration failed")

    _thread = threading.Thread(target=loop, name="rest-poll-scheduler", daemon=True)
    _thread.start()
    log.info("REST poll scheduler started (this instance polls).")


def stop() -> None:
    _stop.set()
