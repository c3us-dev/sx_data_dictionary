from pathlib import Path
from loguru import logger


# project root
PROJECT_ROOT = Path(__file__).parent.parent.parent

# data directory
DATA_DIR = PROJECT_ROOT / "data"
CHM_DIR = DATA_DIR / "chm"
HTM_DIR = DATA_DIR / "htm"

# log directory
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
