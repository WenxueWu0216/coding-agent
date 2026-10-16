
import config

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def should_log(level, threshold=None):
    threshold = threshold or config.LOG_LEVEL
    return LEVELS.index(level) >= LEVELS.index(threshold)


def log(level, msg, sink=print):
    if should_log(level):
        sink(f"[{level}] {msg}")
