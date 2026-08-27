from tools.base import Tool
from typing import Dict, Any

class ReactToMessageTool(Tool):
    """
    React to the user's latest message with an emoji.
    """
    name = "react_to_message"
    description = "React to the user's most recent message using an emoji (e.g. â¤ï¸, ðŸ‘, ðŸ˜‚). You MUST use this tool to react to the user's message when they ask you to, or when you feel it is appropriate. Do NOT simply output an emoji in your text response to fulfill a reaction request."
    parameters = {
        "type": "object",
        "properties": {
            "emoji": {
                "type": "string",
                "description": "The emoji to react with. Must be a single emoji character."
            }
        },
        "required": ["emoji"]
    }

    capability = 'chat.react'

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        emoji = params.get("emoji")
        if not emoji:
            return {"status": "error", "message": "No emoji provided"}
        
        bus = context.get("bus") if context else None
        session_id = context.get("session_id") if context else None
        if bus:
            bus.publish(
                "chat.reaction", 
                {"emoji": emoji, "target": "user", "session_id": session_id}
            )
        
        return {"status": "success", "message": f"Reacted with {emoji}"}

