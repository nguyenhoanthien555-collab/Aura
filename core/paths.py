"""
Project paths.

Every path in Aura is resolved from the project root, not from the
current working directory. This keeps config, prompts and the database
findable no matter where Aura is launched from.
"""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = PROJECT_ROOT / "config.yaml"

DATA_DIR = PROJECT_ROOT / "data"

PROMPTS_DIR = PROJECT_ROOT / "prompts"

CONTEXTS_DIR = PROMPTS_DIR / "contexts"

LOGS_DIR = PROJECT_ROOT / "logs"
