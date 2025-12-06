# cate/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional
from datetime import datetime


@dataclass
class Target:
    url: str
    method: str = "GET"
    headers: Optional[Dict[str, str]] = None


@dataclass
class JobConfig:
    target: Target
    wordlist_path: str
    concurrency: int = 10
    timeout_seconds: float = 10.0
    output_path: Optional[str] = None  # JSONL log
    placeholder: str = "{payload}"     # placeholder in body or query


@dataclass
class Result:
    payload: str
    status_code: Optional[int]
    elapsed_ms: float
    content_length: Optional[int]
    error: Optional[str]
    timestamp: datetime
