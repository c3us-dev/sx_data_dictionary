import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

# project root
PROJECT_ROOT = Path(__file__).parent.parent.parent

# data directory
DATA_DIR = PROJECT_ROOT / "data"
CHM_DIR = DATA_DIR / "chm"
HTM_DIR = DATA_DIR / "htm"
JSON_DIR = DATA_DIR / "json"
JSON_DIR.mkdir(parents=True, exist_ok=True)

# log directory
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)


# logger config
def configure_logging():

    # format string for logs
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # generate log file name with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"dictionary_processing_{timestamp}.log"

    # remove default handler (console)
    logger.remove()

    # rdd file handler
    logger.add(
        str(log_file),
        format=log_format,
        level="DEBUG",
        retention="3 days",
        backtrace=True,
        diagnose=True,
    )

    # add console handler with less verbose output
    logger.add(
        sys.stderr,
        format="{time:HH:mm:ss} | {level} | {message}",
        level="INFO",
        colorize=True,
    )

    logger.info(f"Logging configured. Log file: {log_file}")
    return log_file
