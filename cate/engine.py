# cate/engine.py
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, List, Optional
from collections import deque

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

    # For global RPS throttling
    recent_times = deque(maxlen=100)

    # For stop-on-error-rate
    recent_errors = deque(maxlen=config.error_window)
    stop_flag = {"stop": False}

    timeout = httpx.Timeout(config.timeout_seconds)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:

        async def worker(payload: str) -> None:
            if stop_flag["stop"]:
                # We've decided to abort; don't do more work
                return

            # ---- RPS THROTTLE ----
            # Keep global rate under config.max_rps
            while True:
                now = time.time()
                if not recent_times:
                    break
                # requests per second we *would* be doing if we fire now
                elapsed = now - recent_times[0]
                if elapsed <= 0:
                    # avoid divide-by-zero and just wait a bit
                    await asyncio.sleep(0.05)
                    continue
                current_rps = len(recent_times) / elapsed
                if current_rps > config.max_rps:
                    await asyncio.sleep(0.05)
                else:
                    break

            recent_times.append(time.time())

            async with sem:
                url = config.target.url
                method = config.target.method
                headers = config.target.headers or {}

                body: Optional[str] = None
                
                # If a body_template is provided, use it and still allow placeholder in URL.
                if config.body_template is not None:
                    # Replace placeholder in URL if present
                    if config.placeholder in url:
                        url = url.replace(config.placeholder, payload)
                    # Replace placeholder in body template
                    body = config.body_template.replace(config.placeholder, payload)
                else:
                    # Legacy behavior: if placeholder is in URL, substitute there,
                    # otherwise send the raw payload as the body.
                    if config.placeholder in url:
                        url = url.replace(config.placeholder, payload)
                    else:
                        body = payload

                start = datetime.utcnow()
                error: Optional[str] = None
                status_code: Optional[int] = None
                content_length: Optional[int] = None

                try:
                    resp = await send_request(
                        client,
                        method=method,
                        url=url,
                        headers=headers,
                        data=body,
                        timeout=config.timeout_seconds,
                    )
                    status_code = resp.status_code
                    content_length = len(resp.content)
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)

                elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000.0
                results.append(
                    Result(
                        payload=payload,
                        status_code=status_code,
                        elapsed_ms=elapsed_ms,
                        content_length=content_length,
                        error=error,
                        timestamp=start,
                    )
                )

                # ---- ERROR RATE CHECK ----
                is_error = error is not None or (status_code is not None and status_code >= 500)
                recent_errors.append(is_error)

                if (
                    not stop_flag["stop"]
                    and len(recent_errors) >= max(5, config.error_window // 2)
                ):
                    error_count = sum(1 for e in recent_errors if e)
                    error_rate = error_count / len(recent_errors)
                    if error_rate >= config.stop_on_error_rate:
                        stop_flag["stop"] = True
                        print(
                            f"[CATE] High error rate detected "
                            f"({error_rate:.2%} over last {len(recent_errors)} requests). "
                            "Stopping further work."
                        )

        tasks = []
        async for payload in iter_wordlist(wordlist_path):
            if stop_flag["stop"]:
                break
            tasks.append(asyncio.create_task(worker(payload)))

        if tasks:
            await asyncio.gather(*tasks)

    return results
