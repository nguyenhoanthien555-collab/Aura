"""
Clock.

The reference SAFE tool: no arguments, no side effects, no way to make
it do harm. Also genuinely useful, because a language model has no idea
what time it is.
"""

from datetime import datetime

from tools.base import Parameter, Tool, ToolRisk


class CurrentTimeTool(Tool):

    name = "current_time"
    description = "Get the current local date and time"
    risk = ToolRisk.SAFE
    capability = 'system.time'

    parameters = (
        Parameter(
            name="format",
            description="strftime format string",
            required=False,
        ),
    )

    def __init__(self, clock=None):
        """`clock` is injectable so tests do not depend on the wall clock."""

        self.clock = clock or datetime.now

    def execute(self, format: str = "%A %d %B %Y, %H:%M") -> str:

        try:
            return self.clock().strftime(format)
        except (ValueError, TypeError) as error:
            raise ValueError(f"invalid time format: {error}") from error
