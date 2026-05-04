from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
RETAIL_DIR = RAW_DIR / "retail"
WHOLESALE_DIR = RAW_DIR / "wholesale"
LOG_DIR = RAW_DIR / "logs"
PROCESSED_DIR = DATA_DIR / "processed"
REFERENCE_DIR = DATA_DIR / "reference"


def ensure_directories(paths: Iterable[Path] | None = None) -> None:
    """Create the standard project directories when they do not exist yet."""
    default_paths = [
        DATA_DIR,
        RAW_DIR,
        RETAIL_DIR,
        WHOLESALE_DIR,
        LOG_DIR,
        PROCESSED_DIR,
        REFERENCE_DIR,
    ]
    for path in paths or default_paths:
        path.mkdir(parents=True, exist_ok=True)


def normalize_text(value: object | None) -> str:
    """Collapse whitespace so hashes and CSV output stay stable."""
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def current_timestamp() -> datetime:
    return datetime.now()


def timestamp_for_filename(dt: datetime | None = None) -> str:
    return (dt or current_timestamp()).strftime("%Y%m%d_%H%M%S")


def timestamp_iso(dt: datetime | None = None) -> str:
    return (dt or current_timestamp()).replace(microsecond=0).isoformat()


def generate_station_id(name: str | None, address: str | None) -> str:
    """Stable station identifier from the normalized name/address pair."""
    payload = f"{normalize_text(name).lower()}|{normalize_text(address).lower()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def log_message(message: str, level: str = "INFO", log_name: str = "pipeline.log") -> Path:
    """Append a simple timestamped line to the raw logs directory."""
    ensure_directories([LOG_DIR])
    log_path = LOG_DIR / log_name
    line = f"{timestamp_iso()} [{level}] {message}"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)
    return log_path
