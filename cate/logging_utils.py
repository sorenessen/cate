# cate/logging_utils.py
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .models import Result


def write_results_jsonl(path: Path, results: Iterable[Result]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in results:
            data = asdict(r)
            # ISO format for datetime
            data["timestamp"] = r.timestamp.isoformat() + "Z"
            f.write(json.dumps(data) + "\n")
