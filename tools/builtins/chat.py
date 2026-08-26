from tools.base import Tool
from typing import Dict, Any

class ReactToMessageTool(Tool):
    """
    React to the user's latest message with an emoji.
    """
    name = "react_to_message"
    description = "React to the user's most recent message using an emoji (e.g. ❤️, 👍, 😂, 😲, 😢, 🙏)."
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
