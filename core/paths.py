"""
Project paths.

Every path in Aura is resolved from the project root, not from the
current working directory. This keeps config, prompts and the database
findable no matter where Aura is launched from.
"""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

_config_env = os.environ.get("AURA_CONFIG_PATH")
CONFIG_PATH = Path(_config_env) if _config_env else (PROJECT_ROOT / "config.yaml")

_data_env = os.environ.get("AURA_DATA_DIR")
DATA_DIR = Path(_data_env) if _data_env else (PROJECT_ROOT / "data")

PROMPTS_DIR = PROJECT_ROOT / "prompts"

CONTEXTS_DIR = PROMPTS_DIR / "contexts"

_logs_env = os.environ.get("AURA_LOGS_DIR")
LOGS_DIR = Path(_logs_env) if _logs_env else (PROJECT_ROOT / "logs")

_brains_env = os.environ.get("AURA_BRAINS_DIR")
BRAINS_DIR = Path(_brains_env) if _brains_env else (PROJECT_ROOT / "brains")

BACKUPS_DIR = DATA_DIR / "backups"

