# cate/engine.py
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, List, Optional
from collections import deque
from urllib.parse import quote

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
    wordlist_path = Path(config.wordlist_path)

    # Global safety + telemetry
    sem = asyncio.Semaphore(config.concurrency)
    timeout = httpx.Timeout(config.timeout_seconds)

    # For global RPS throttling
    recent_times = deque(maxlen=100)

    # For stop-on-error-rate
    recent_errors = deque(maxlen=config.error_window)
    stop_flag = {"stop": False}

    # Queue + fixed worker pool (prevents “task per payload” blowups)
    q: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=max(100, config.concurrency * 10))

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:

        async def throttle_rps() -> None:
            # Keep global rate under config.max_rps
            while True:
                now = time.time()
                if not recent_times:
                    break
                elapsed = now - recent_times[0]
                if elapsed <= 0:
                    await asyncio.sleep(0.01)
                    continue
                current_rps = len(recent_times) / elapsed
                if current_rps > config.max_rps:
                    await asyncio.sleep(0.01)
                else:
                    break
            recent_times.append(time.time())

        def apply_payload(s: str, payload: str) -> str:
            # Apply payload transform if enabled
            p = quote(payload, safe="") if config.urlencode_payload else payload
            return s.replace(config.placeholder, p)

        async def worker(worker_id: int) -> None:
            while True:
                payload = await q.get()
                try:
                    if payload is None:
                        return

                    # If we've decided to stop, we still need to DRAIN the queue
                    # so producer can finish and q.join() can complete.
                    if stop_flag["stop"]:
                        continue


                    await throttle_rps()

                    async with sem:
                        url = config.target.url
                        method = config.target.method
                        headers = config.target.headers or {}

                        body: Optional[str] = None

                        # Body template mode: substitute into URL (if placeholder exists) + body template
                        if config.body_template is not None:
                            if config.placeholder in url:
                                url = apply_payload(url, payload)
                            body = apply_payload(config.body_template, payload)
                        else:
                            # Legacy mode: substitute into URL if placeholder exists; else body = raw payload
                            if config.placeholder in url:
                                url = apply_payload(url, payload)
                            else:
                                body = payload  # NOTE: do not urlencode body unless you add a separate flag

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

                        # Collect result
                        results.append(
                            Result(
                                payload=payload,
                                status_code=status_code,
                                elapsed_ms=elapsed_ms,
                                content_length=content_length,
                                error=error,
                                timestamp=start,
                                effective_url=url,
                            )
                        )

                        # Error-rate window
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
                finally:
                    q.task_done()

        async def producer() -> None:
            async for payload in iter_wordlist(wordlist_path):
                if stop_flag["stop"]:
                    break
                await q.put(payload)

            # Tell workers to exit
            for _ in range(config.concurrency):
                await q.put(None)


        workers = [asyncio.create_task(worker(i)) for i in range(config.concurrency)]
        prod = asyncio.create_task(producer())

        await prod
        await q.join()

        # Ensure workers exit cleanly
        for w in workers:
            if not w.done():
                w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    return results

