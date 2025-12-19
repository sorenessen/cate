# cate/http_client.py
from __future__ import annotations

import httpx
from typing import Optional, Dict


async def send_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[str] = None,
    timeout: float = 10.0,
) -> httpx.Response:
    resp = await client.request(
        method=method.upper(),
        url=url,
        headers=headers,
        content=data,
        timeout=timeout,
    )
    return resp

def extract_forensic_headers(resp) -> dict:
    # keep it tight: only your demo/forensics headers
    want = ["x-cate-waf", "x-cate-limit", "x-cate-upstream"]
    out = {}
    for k in want:
        v = resp.headers.get(k)
        if v is not None:
            out[k] = v
    return out
