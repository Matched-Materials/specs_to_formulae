# spec_sheets_to_formulas/src/utils/io.py
from __future__ import annotations
import json
import hashlib
import os
import logging
from pathlib import Path
from typing import Any

def safe_mkdirs(path: Path) -> None:
    """Creates a directory if it doesn't exist."""
    Path(path).mkdir(parents=True, exist_ok=True)

def write_json(path: Path, data: Any) -> None:
    """Writes data to a JSON file atomically."""
    path = Path(path)
    safe_mkdirs(path.parent)
    # Write to a temporary file first, then rename to avoid partial files
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp_path, path)

def read_json(path: Path) -> Any:
    """Reads data from a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def sha256_file(path: Path) -> str:
    """Calculates the SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def setup_logging(log_path: str, name: str) -> logging.Logger:
    """Sets up a dedicated logger for a specific run."""
    # Ensure the directory for the log file exists before creating the handler.
    # This prevents a FileNotFoundError in parallel execution.
    safe_mkdirs(Path(log_path).parent)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_path)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    if not logger.handlers:
        logger.addHandler(handler)
    return logger