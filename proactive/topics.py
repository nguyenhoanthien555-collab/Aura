"""
Where daily topics for evening recap come from.

Collects subjects worked on or discussed today from episodic memory,
companion highlights, or transcript.
"""

from datetime import datetime, time
from typing import Any

from core.temporal import local_now, parse_timestamp


WORK_CATEGORIES = frozenset({"project", "plan", "event", "learning"})
MIN_TOPIC_LENGTH = 6
MAX_TOPIC_LENGTH = 100


class DailyTopicSource:
    """
    Topics and achievements from today, most recent first.

    Callable, so it can be passed directly to `ProactiveEngine` as its
    `daily_topics` source.
    """

    def __init__(
        self,
        episodic_store: Any = None,
        companion: Any = None,
        memory: Any = None,
        clock=local_now,
        limit: int = 3,
    ):
        self.episodic_store = episodic_store
        self.companion = companion
        self.memory = memory
        self.clock = clock
        self.limit = int(limit)

    def __call__(self) -> list[str]:
        return self.topics_today()

    def topics_today(self) -> list[str]:
        now = self.clock()
        today = now.date()
        today_start = datetime.combine(today, time.min)

        topics: list[str] = []

        # 1. First preference: Companion highlights recorded today
        if self.companion is not None and hasattr(self.companion, "highlights"):
            try:
                for hl in self.companion.highlights.recent(limit=10):
                    summary = getattr(hl, "summary", "").strip()
                    at = parse_timestamp(getattr(hl, "at", None))
                    if summary and at and at.date() == today:
                        if summary not in topics:
                            topics.append(summary)
            except Exception:
                pass

        # 2. Second preference: Episodic memories occurred today
        if self.episodic_store is not None and hasattr(self.episodic_store, "since"):
            try:
                episodes = self.episodic_store.since(today_start)
                for ep in episodes:
                    cat = getattr(ep, "category", "")
                    content = getattr(ep, "content", "").strip()
                    if cat in WORK_CATEGORIES and len(content) >= MIN_TOPIC_LENGTH:
                        cleaned = content[:MAX_TOPIC_LENGTH]
                        if cleaned not in topics:
                            topics.append(cleaned)
            except Exception:
                pass

        # 3. Third preference: User messages from today in memory
        if not topics and self.memory is not None and hasattr(self.memory, "get_recent"):
            try:
                messages = self.memory.get_recent(limit=30)
                for msg in messages:
                    if getattr(msg, "role", "") == "user":
                        ts = parse_timestamp(getattr(msg, "timestamp", None))
                        content = getattr(msg, "content", "").strip()
                        if ts and ts.date() == today and len(content) >= MIN_TOPIC_LENGTH:
                            lower = content.lower()
                            if not lower.startswith(("/", "hello", "hi ", "hey", "chào")):
                                if content not in topics:
                                    topics.append(content[:MAX_TOPIC_LENGTH])
            except Exception:
                pass

        return topics[:self.limit]
