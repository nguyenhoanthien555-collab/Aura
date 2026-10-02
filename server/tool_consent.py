"""
Persistent user tool consent store.

Tracks which tools the user has explicitly approved (or auto-approved
via 30s timeout). Once a tool name appears here, it is never asked again.
"""
import time
from threading import RLock

from core.logger import logger
from memory.models import UserToolConsent
from memory.sqlite import SessionLocal, db_lock, init_consent_tables


class ToolConsentStore:
    """Thread-safe persistent tool consent manager."""

    def __init__(self):
        self._cache: set[str] = set()
        self._loaded = False
        self._lock = RLock()

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            init_consent_tables()
            with db_lock:
                session = SessionLocal()
                try:
                    rows = session.query(UserToolConsent).all()
                    self._cache = {r.tool_name for r in rows}
                    self._loaded = True
                finally:
                    session.close()
        except Exception as error:
            logger.warning("Failed to load user tool consents: %s", error)

    def is_approved(self, tool_name: str) -> bool:
        """Check if a tool has been approved previously."""
        with self._lock:
            self._load()
            return tool_name in self._cache

    def approve(self, tool_name: str, auto: bool = False) -> None:
        """Approve a tool and persist the decision."""
        with self._lock:
            self._load()
            if tool_name in self._cache:
                return
            self._cache.add(tool_name)
            try:
                with db_lock:
                    session = SessionLocal()
                    try:
                        record = UserToolConsent(
                            tool_name=tool_name,
                            approved_at=time.time(),
                            auto_approved=auto,
                        )
                        session.merge(record)
                        session.commit()
                        logger.info(
                            "Tool consent granted for '%s' (auto=%s)",
                            tool_name,
                            auto,
                        )
                    finally:
                        session.close()
            except Exception as error:
                logger.error("Failed to persist tool consent for '%s': %s", tool_name, error)

    def list_approved(self) -> list[str]:
        """List all approved tool names."""
        with self._lock:
            self._load()
            return sorted(self._cache)

    def revoke(self, tool_name: str) -> None:
        """Revoke consent for a tool."""
        with self._lock:
            self._load()
            self._cache.discard(tool_name)
            try:
                with db_lock:
                    session = SessionLocal()
                    try:
                        session.query(UserToolConsent).filter_by(
                            tool_name=tool_name
                        ).delete()
                        session.commit()
                        logger.info("Tool consent revoked for '%s'", tool_name)
                    finally:
                        session.close()
            except Exception as error:
                logger.error("Failed to revoke tool consent for '%s': %s", tool_name, error)


_store: ToolConsentStore | None = None


def get_consent_store() -> ToolConsentStore:
    """Return process-wide ToolConsentStore singleton."""
    global _store
    if _store is None:
        _store = ToolConsentStore()
    return _store
