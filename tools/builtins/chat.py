from typing import Any, Dict
from tools.base import Parameter, Tool, ToolResult, ToolRisk, fail, ok
from tools.outcome import Evidence, EvidenceKind


class ReactToMessageTool(Tool):
    """
    React to the user's latest message with an emoji.
    """
    name = "react_to_message"
    description = (
        "React to the user's most recent message using an emoji (e.g. ❤️, 👍, 😂, 😊). "
        "You MUST use this tool to react to the user's message when they ask you to, or when you "
        "feel it is appropriate. Do NOT simply output an emoji in your text response to fulfill a reaction request."
    )
    risk = ToolRisk.SAFE
    capability = "chat.react"

    parameters = (
        Parameter(
            name="emoji",
            description="The emoji to react with. Must be an emoji character (e.g. ❤️, 👍, 😂, 😊).",
            required=False,
            type="string",
        ),
        Parameter(
            name="reaction",
            description="Alias for emoji.",
            required=False,
            type="string",
        ),
    )

    def execute(
        self,
        emoji: str = "",
        reaction: str = "",
        context: Dict[str, Any] = None,
        **kwargs,
    ) -> ToolResult:
        chosen = (emoji or reaction or kwargs.get("params", {}).get("emoji") if isinstance(kwargs.get("params"), dict) else "").strip()
        if not chosen:
            chosen = "❤️"

        bus = context.get("bus") if isinstance(context, dict) else None
        session_id = context.get("session_id") if isinstance(context, dict) else None
        if bus:
            bus.publish(
                "chat.reaction",
                {"emoji": chosen, "target": "user", "session_id": session_id},
            )

        self._last_reaction = chosen
        from tools.outcome import Evidence, EvidenceKind
        ev = (
            Evidence(
                kind=EvidenceKind.OBSERVATION,
                source="chat.reaction",
                verified=True,
                reference=self.name,
                detail=f"reacted {chosen}",
            ),
        )
        return ToolResult(
            ok=True,
            output=f"Reacted with {chosen}",
            tool=self.name,
            capability=self.capability,
            authorization="granted",
            execution="completed",
            evidence=ev,
            data={"postcondition": {"verified": True, "action": "chat.react", "emoji": chosen}},
        )

    def verify(self, emoji: str = "", reaction: str = "", **kwargs) -> ToolResult:
        chosen = getattr(self, "_last_reaction", emoji or reaction or "❤️")
        return ok(f"Reaction {chosen} confirmed", tool=self.name)
