"""Run settings, read from the environment (`.env`, never printed). Same variables and defaults as the course lab."""
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

ROOT = Path(__file__).resolve().parent.parent
MODEL = os.getenv("OPENROUTER_MODEL", "~deepseek/deepseek-v4-flash-latest")
BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
CHECKPOINT_DB = ROOT / "data" / "checkpoints.db"
DEFAULT_TREASURER = "M001"  # Léa Martin in seeds/base.json; the eval harness passes user_id in the config
DEFAULT_MAX_STEPS = 8


def run_config(thread_id: str, user_id: str = DEFAULT_TREASURER, max_steps: int = DEFAULT_MAX_STEPS,
               model: str | None = None) -> dict:
    """LangGraph config of one run. `recursion_limit` is the safety net behind `max_steps` (each tool call is at most
    4 super-steps: think, guard, approve, tool)."""
    configurable = {"thread_id": thread_id, "user_id": user_id}
    if model:
        configurable["model"] = model
    return {"configurable": configurable, "recursion_limit": 4 * max_steps + 10}
