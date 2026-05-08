import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import os

# Resolve project root (works regardless of where called)
PROJECT_ROOT = Path(__file__).resolve().parents[1]

LOG_FILE = PROJECT_ROOT / os.getenv("LOG_FILE", "laigo.log")

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

MAX_LOG_SIZE_MB = int(os.getenv("MAX_LOG_SIZE_MB", 10))  # rotate at 10MB
MAX_BYTES = MAX_LOG_SIZE_MB * 1024 * 1024

logger = logging.getLogger("laigoLOG")
logger.setLevel(logging.DEBUG)

# Prevent duplicate handlers if imported multiple times
if not logger.handlers:

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=1,   # keep only 1 old file (acts like truncation)
        encoding="utf-8"
    )

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(process)d | %(message)s"
    )

    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Optional console logging
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)