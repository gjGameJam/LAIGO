import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import multiprocessing
import os

# D-009: only the PARENT process owns the rotating file handler. Worker
# subprocesses (spawned by ProcessPoolExecutor) import this module too; if each
# opened its own handle to laigo.log, concurrent workers would interleave/​tear
# writes and races the rotation rename (WinError 32 on Windows). Workers stream
# to stdout only — uvicorn/Render capture that. parent_process() is None in the
# main process and a Process in spawned children.
_IS_WORKER = multiprocessing.parent_process() is not None

# Resolve project root (works regardless of where called)
PROJECT_ROOT = Path(__file__).resolve().parents[1]

LOG_FILE = PROJECT_ROOT / os.getenv("LOG_FILE", "laigo.log")

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

MAX_LOG_SIZE_MB = int(os.getenv("MAX_LOG_SIZE_MB", 10))  # rotate at 10MB
MAX_BYTES = MAX_LOG_SIZE_MB * 1024 * 1024

logger = logging.getLogger("laigoLOG")
logger.setLevel(logging.DEBUG)
# D-002: do not bubble up to the root logger. Main.py attaches a StreamHandler
# to root via logging.basicConfig; with propagation on, every laigoLOG line was
# emitted to stdout twice (once by our handler, once by root's). This logger
# owns its own sinks.
logger.propagate = False

# Prevent duplicate handlers if imported multiple times
if not logger.handlers:

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(process)d | %(message)s"
    )

    # D-009: parent process only — workers must not open the file.
    if not _IS_WORKER:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=1,   # keep only 1 old file (acts like truncation)
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Console logging (parent + worker; uvicorn captures worker stdout)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)