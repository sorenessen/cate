# cate/engine.py
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, List, Optional

import httpx

from .models import JobConfig, Result
from .http_client import send_request


async def iter_wordlist(path: Path) -> AsyncIterator[str]:
    # Simple async iterator over wordlist lines
    loop = asyncio.get_event_loop()
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            payload = line.rstrip("\r\n")
            if not payload:
                continue
            # yield from loop to avoid blocking
            await asyncio.sleep(0)
            yield payload


async def run_job(config: JobConfig) -> List[Result]:
    results: List[Result] = []
    sem = asyncio.Semaphore(config.concurrency)
    wordlist_path = Path(config.wordlist_path)

    timeout = httpx.Timeout(config.timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:

        async def worker(payload: str) -> None:
            async with sem:
                url = config.target.url
                method = config.target.method
                headers = config.target.headers or {}

                # Simple strategy: if placeholder is in URL, swap there.
                # Otherwise, send as raw body.
                body: Optional[str] = None
                if config.placeholder in url:
                    url = url.replace(config.placeholder, payload)
                else:
                    body = payload

                start = datetime.utcnow()
                try:
                    resp = await send_request(
                        client,
                        method=method,
                        url=url,
                        headers=headers,
                        data=body,
                        timeout=config.timeout_seconds,
                    )
                    elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000.0
                    results.append(
                        Result(
                            payload=payload,
                            status_code=resp.status_code,
                            elapsed_ms=elapsed_ms,
                            content_length=len(resp.content),
                            error=None,
                            timestamp=start,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000.0
                    results.append(
                        Result(
                            payload=payload,
                            status_code=None,
                            elapsed_ms=elapsed_ms,
                            content_length=None,
                            error=str(exc),
                            timestamp=start,
                        )
                    )

        tasks = []
        async for payload in iter_wordlist(wordlist_path):
            tasks.append(asyncio.create_task(worker(payload)))

        if tasks:
            await asyncio.gather(*tasks)

    return results
