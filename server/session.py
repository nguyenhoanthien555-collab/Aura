"""
Session management for the Aura API.

Each client gets a session_id. Sessions are ephemeral (in-memory) for MVP.
In production, they'd be persisted to Redis or database.

A Session is metadata only - when it was created, when it was last used,
how many messages went through it. The conversation itself lives in
`memory/`, so expiring a session here discards no history; it only means
`GET /api/sessions/{id}` stops reporting on it.

That is what makes the expiry below safe. Ids arrive from the client, so
every distinct id a caller invents becomes a dict entry, and nothing used
to remove them: `cleanup_old` existed with no caller at all, which on a
long-lived server is a slow leak that no request ever pays for
(AURA-P1-006). Sweeping happens on the create path, throttled, rather
than in a background thread - a scheduler would be a new subsystem for a
dictionary that holds four floats per entry.
"""
import uuid
import time
from dataclasses import dataclass, field
from typing import Dict, Optional
from threading import Lock

# An hour of silence means the session is over. Only metadata is dropped.
DEFAULT_MAX_AGE_SECONDS = 3600

# How often the create path is allowed to sweep. Bounds the O(n) scan to
# once per interval instead of once per request.
DEFAULT_SWEEP_INTERVAL_SECONDS = 300


@dataclass
class Session:
    """A chat session."""
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    message_count: int = 0


class SessionManager:
    """Manages chat sessions."""

    def __init__(
        self,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        sweep_interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
    ):
        self._sessions: Dict[str, Session] = {}
        self._lock = Lock()
        self.max_age_seconds = max_age_seconds
        self.sweep_interval_seconds = sweep_interval_seconds
        self._last_sweep = time.time()

    def create_session(self) -> Session:
        """Create a new session."""
        session_id = str(uuid.uuid4())
        session = Session(session_id=session_id)
        with self._lock:
            self._maybe_sweep(time.time())
            self._sessions[session_id] = session
        return session

    def ensure_session(self, session_id: Optional[str] = None) -> Session:
        """
        Return the session for `session_id`, creating it if needed.

        Callers supply their own ids (an Android install keeps one across
        launches), so a get-or-create under a single lock is what routes
        actually need - and it keeps them out of `_sessions`.
        """
        if not session_id:
            return self.create_session()

        with self._lock:
            self._maybe_sweep(time.time())
            session = self._sessions.get(session_id)
            if session is None:
                session = Session(session_id=session_id)
                self._sessions[session_id] = session
            return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        with self._lock:
            return self._sessions.get(session_id)

    def update_activity(self, session_id: str) -> bool:
        """Update last activity time. Returns True if session exists."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session.last_activity = time.time()
                session.message_count += 1
                return True
            return False

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False

    def list_sessions(self) -> list[Session]:
        """List all sessions."""
        with self._lock:
            return list(self._sessions.values())

    def cleanup_old(self, max_age_seconds: float | None = None) -> int:
        """
        Remove sessions idle longer than `max_age_seconds`, and report how
        many went.

        Still callable directly - an operator endpoint or a test wants the
        count, and wants it now rather than at the next sweep interval.
        """
        age = self.max_age_seconds if max_age_seconds is None else max_age_seconds

        with self._lock:
            return self._expire(time.time(), age)

    # -- internals: both assume the caller already holds the lock, which
    # -- is not reentrant, so neither may call a public method above.

    def _maybe_sweep(self, now: float) -> int:
        """
        Expire stale sessions, at most once per sweep interval.

        Called from the create paths only. A request that reuses a live
        session is the common case and pays nothing for this.
        """
        if now - self._last_sweep < self.sweep_interval_seconds:
            return 0

        self._last_sweep = now

        return self._expire(now, self.max_age_seconds)

    def _expire(self, now: float, max_age_seconds: float) -> int:

        stale = [
            sid for sid, session in self._sessions.items()
            if now - session.last_activity > max_age_seconds
        ]

        for sid in stale:
            del self._sessions[sid]

        return len(stale)


# Global session manager
session_manager = SessionManager()