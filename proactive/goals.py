"""
Where active goals come from.

Reads the user's active goals from CompanionMemory's GoalStore.
Only goals with priority 'now' or 'soon' are considered active.
"""

from typing import Any


class CompanionGoalSource:
    """
    Active goals the user has recorded.

    Callable, so it can be passed directly to `ProactiveEngine` as its
    `active_goals` source.
    """

    def __init__(self, goal_store_or_companion: Any = None, limit: int = 3):
        self.source = goal_store_or_companion
        self.limit = int(limit)

    def __call__(self) -> list[str]:
        return self.active_goals()

    def active_goals(self) -> list[str]:
        if self.source is None:
            return []

        # If passed CompanionMemory directly, reach into .goals
        store = getattr(self.source, "goals", self.source)
        if store is None:
            return []

        try:
            # Check for active() method first (SqliteGoals or InMemoryGoals)
            if hasattr(store, "active"):
                goals = store.active(limit=self.limit)
            elif hasattr(store, "all"):
                all_goals = store.all()
                goals = [
                    g for g in all_goals
                    if getattr(g, "priority", "") in ("now", "soon")
                ][:self.limit]
            else:
                return []

            results = []
            for goal in goals:
                title = getattr(goal, "title", str(goal)).strip()
                if title and title not in results:
                    results.append(title)
            return results[:self.limit]
        except Exception:
            return []
