"""
Prompt section headers.
"""

SYSTEM = "===== SYSTEM ====="

PERSONALITY = "===== PERSONALITY ====="

CONTEXT = "===== CONTEXT ====="

# When "now" is. Two lines, above MEMORY because a recalled event dated
# "yesterday" is meaningless until the reader knows what today is.
TIME = "===== CURRENT TIME ====="

MEMORY = "===== MEMORY ====="

HISTORY = "===== RECENT CONVERSATION ====="

USER = "===== CURRENT USER MESSAGE ====="

# The last three sections are all instructions, ordered by how permanent
# they are, because a model follows the instruction it read most recently
# far more reliably than one from the top of a long prompt.
#
#   IDENTITY   who she is, and does not stop being
#   STYLE      how this particular reply should read
#   USER       what was asked
IDENTITY = "===== WHO YOU ARE ====="

STYLE = "===== RESPONSE STYLE ====="

# Tool sections, and they are two sections for a reason. The catalogue is
# an instruction - what may be asked for, and how to ask - so it belongs
# in the system slot with the other rules. A result is evidence: it is
# what actually happened when Aura ran something, and it is the only
# thing entitled to make her say an action succeeded. See
# brain/tool_calling.py and split_prompt() in brain/providers/base.py.
TOOLS = "===== TOOLS ====="

TOOL_RESULTS = "===== TOOL RESULTS ====="

VISION = "===== VISION ====="

PLUGINS = "===== PLUGINS ====="

DESKTOP = "===== DESKTOP STATE ====="

# Machine-turn sections. A prompt containing these is answered with JSON
# or a single word, for a parser rather than a person, so none of the
# conversational sections above appear alongside them - see
# brain/agent_mode.py for why that separation is absolute.
DEVICE_STATE = "===== DEVICE STATE ====="

ACCESSIBILITY_TREE = "===== ACCESSIBILITY TREE ====="

LAST_ACTION_ERROR = "===== LAST ACTION ERROR ====="

ACTION_HISTORY = "===== COMPLETED ACTIONS ====="

AGENT_RULES = "===== AGENT RULES ====="

INTENT_RULES = "===== INTENT RULES ====="