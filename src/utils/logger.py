import logging
import os
import sys

from dotenv import load_dotenv

_ROOT_LOGGER_NAME = "owl"
_configured = False

# Load environment variables (like LOG_LEVEL) from .env file
load_dotenv()


def configure_logging(level: str | None = None, log_file: str | None = None) -> None:
    """
    Call once at application startup.
    Subsequent calls are no-ops.
    If 'level' is not provided, the value of the LOG_LEVEL environment
    variable is used (defaults to INFO).
    """
    global _configured
    if _configured:
        return

    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")

    fmt = "%(asctime)s | %(levelname)-8s | %(name)-18s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    root = logging.getLogger(_ROOT_LOGGER_NAME)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Always log to stdout
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # Optionally also write to a file
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for noisy in ("httpx", "httpcore", "anthropic", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(module_name: str) -> logging.Logger:
    """
    Return a logger named `parsea.<last_segment_of_module_name>`.
    Keeps log lines short while still being traceable to the source file.

    Example:
        get_logger("parsea.parsers") -> "parsea.parsers"
        get_logger(__name__)         -> "parsea.<filename>"
    """
    # Ensure logging is configured before returning the first logger
    if not _configured:
        configure_logging()

    # Strip any leading package path so names stay short in the log output
    short_name = module_name.split(".")[-1]
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{short_name}")
