"""
Session management for the Aura API.

Each client gets a session_id. Sessions are ephemeral (in-memory) for MVP.
In production, they'd be persisted to Redis or database.
"""
import uuid
import time
from dataclasses import dataclass, field
from typing import Dict, Optional
from threading import Lock


@dataclass
class Session:
    """A chat session."""
    session_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    message_count: int = 0


class SessionManager:
    """Manages chat sessions."""

    def __init__(self):
        self._sessions: Dict[str, Session] = {}
        self._lock = Lock()

    def create_session(self) -> Session:
        """Create a new session."""
        session_id = str(uuid.uuid4())
        session = Session(session_id=session_id)
        with self._lock:
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

    def cleanup_old(self, max_age_seconds: int = 3600) -> int:
        """Remove sessions older than max_age_seconds. Returns count removed."""
        now = time.time()
        with self._lock:
            to_remove = [
                sid for sid, s in self._sessions.items()
                if now - s.last_activity > max_age_seconds
            ]
            for sid in to_remove:
                del self._sessions[sid]
            return len(to_remove)


# Global session manager
session_manager = SessionManager()