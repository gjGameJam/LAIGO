import logging
from pathlib import Path


class TruncatingFileHandler(logging.FileHandler):
    """
    FileHandler that keeps the log file under `max_lines` by
    removing oldest lines when the file grows too large.
    """
    def __init__(self, filename, max_lines=10000, mode='a', encoding=None):
        super().__init__(filename, mode=mode, encoding=encoding)
        self.max_lines = max_lines
        self.filename = Path(filename)
        self.filename.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, record):
        super().emit(record)
        self.truncate_file()

    def truncate_file(self):
        # Read all lines
        with open(self.filename, "r", encoding=self.encoding or "utf-8") as f:
            lines = f.readlines()

        # If exceeds max_lines, truncate from the start
        if len(lines) > self.max_lines:
            with open(self.filename, "w", encoding=self.encoding or "utf-8") as f:
                f.writelines(lines[-self.max_lines:])


# -----------------------------
# Logger setup
# -----------------------------
HERE = Path(__file__).resolve().parent # Path to this file
PROJECT_ROOT = HERE.parent # Project root = parent of scripts folder
LOG_FILE = (PROJECT_ROOT / "laigoLOG.log").resolve()
logger = logging.getLogger("laigo")
logger.setLevel(logging.DEBUG)

file_handler = TruncatingFileHandler(LOG_FILE, max_lines=10000)
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Optional: also log to console
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# -----------------------------
# Usage example
# -----------------------------
# logger.info("Logger initialized")
# logger.debug("Debugging info")
# logger.error("An error occurred")