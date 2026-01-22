"""Logging configuration."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if hasattr(record, "mac"):
            log_data["mac"] = record.mac
        if hasattr(record, "device"):
            log_data["device"] = record.device
        if hasattr(record, "interface"):
            log_data["interface"] = record.interface
        if hasattr(record, "switch"):
            log_data["switch"] = record.switch
        if hasattr(record, "port"):
            log_data["port"] = record.port
        if hasattr(record, "status"):
            log_data["status"] = record.status

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class KeyValueFormatter(logging.Formatter):
    """Key-value log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        parts = [
            f"ts={timestamp}",
            f"level={record.levelname}",
            f"msg=\"{record.getMessage()}\"",
        ]

        if hasattr(record, "mac"):
            parts.append(f"mac={record.mac}")
        if hasattr(record, "device"):
            parts.append(f"device={record.device}")
        if hasattr(record, "interface"):
            parts.append(f"interface={record.interface}")
        if hasattr(record, "switch"):
            parts.append(f"switch={record.switch}")
        if hasattr(record, "port"):
            parts.append(f"port={record.port}")
        if hasattr(record, "status"):
            parts.append(f"status={record.status}")

        return " ".join(parts)


def setup_logging(
    level: str = "INFO",
    format_type: str = "text",  # "text", "json", "kv"
):
    """Configure logging for the application."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)

    if format_type == "json":
        handler.setFormatter(JSONFormatter())
    elif format_type == "kv":
        handler.setFormatter(KeyValueFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )

    root_logger.addHandler(handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
