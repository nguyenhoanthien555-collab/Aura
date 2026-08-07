"""
Prompt section headers.
"""

SYSTEM = "===== SYSTEM ====="

PERSONALITY = "===== PERSONALITY ====="

CONTEXT = "===== CONTEXT ====="

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

TOOLS = "===== TOOL RESULTS ====="

VISION = "===== VISION ====="

PLUGINS = "===== PLUGINS ====="

DESKTOP = "===== DESKTOP STATE ====="