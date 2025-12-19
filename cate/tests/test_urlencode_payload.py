import inspect
from types import SimpleNamespace

import pytest

import cate.engine as engine
from cate.models import JobConfig, Target


def _make_config(tmp_path, *, url, payloads, body_template=None, urlencode_payload=False, method="GET"):
    wordlist = tmp_path / "payloads.txt"
    wordlist.write_text("\n".join(payloads) + "\n", encoding="utf-8")

    kwargs = dict(
        target=Target(url=url, method=method, headers=None),
        wordlist_path=str(wordlist),
        concurrency=1,          # deterministic for tests
        timeout_seconds=1.0,
        output_path=None,
        placeholder="{payload}",
        body_template=body_template,
        max_rps=10_000.0,       # effectively no throttle in test
        stop_on_error_rate=1.0, # never stop early in test
        error_window=5,
    )

    # Only set if your JobConfig supports it (it should, since your flag works)
    if "urlencode_payload" in inspect.signature(JobConfig).parameters:
        kwargs["urlencode_payload"] = urlencode_payload

    return JobConfig(**kwargs)


@pytest.mark.asyncio
async def test_urlencode_payload_affects_url_only(tmp_path, monkeypatch):
    seen = []

    async def fake_send_request(client, method, url, headers, data, timeout):
        # record what the engine actually sent
        seen.append({"url": url, "data": data, "method": method})
        return SimpleNamespace(status_code=200, content=b"ok")

    monkeypatch.setattr(engine, "send_request", fake_send_request)

    payloads = ["test", "%00", "../../etc/passwd", "' OR 1=1 --"]

    cfg = _make_config(
        tmp_path,
        url="http://localhost:8080/login?b={payload}",
        payloads=payloads,
        body_template=None,
        urlencode_payload=True,
        method="GET",
    )

    results = await engine.run_job(cfg)
    assert len(results) == len(payloads)

    # Confirm the URL got encoded for special payloads
    urls = [x["url"] for x in seen]
    assert "http://localhost:8080/login?b=test" in urls
    assert "http://localhost:8080/login?b=%2500" in urls
    assert "http://localhost:8080/login?b=..%2F..%2Fetc%2Fpasswd" in urls
    assert "http://localhost:8080/login?b=%27%20OR%201%3D1%20--" in urls

    # No body should be sent for this mode (GET with placeholder in URL)
    assert all(x["data"] is None for x in seen)


@pytest.mark.asyncio
async def test_urlencode_payload_does_not_encode_body_template(tmp_path, monkeypatch):
    seen = []

    async def fake_send_request(client, method, url, headers, data, timeout):
        seen.append({"url": url, "data": data, "method": method})
        return SimpleNamespace(status_code=200, content=b"ok")

    monkeypatch.setattr(engine, "send_request", fake_send_request)

    payloads = ["' OR 1=1 --"]

    cfg = _make_config(
        tmp_path,
        url="http://localhost:8080/login",     # no {payload} in URL
        payloads=payloads,
        body_template="b={payload}",           # payload goes into body
        urlencode_payload=True,                # should NOT affect body
        method="POST",
    )

    results = await engine.run_job(cfg)
    assert len(results) == 1

    assert seen[0]["url"] == "http://localhost:8080/login"
    assert seen[0]["data"] == "b=' OR 1=1 --"   # raw, not encoded
