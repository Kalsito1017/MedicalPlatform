"""SSE delivery: authorization, replay, dedup, heartbeat, revocation, cleanup.

`httpx.ASGITransport` buffers complete response bodies, which cannot terminate
for an infinite SSE stream. HTTP tests therefore cover 401/403; the streaming
behaviour is exercised by driving the real route generator directly with
authenticated Starlette requests (same filters, bus and auth refresh code).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.api.v1 import system_logs
from app.auth.identity import resolve_principal
from app.auth.sessions import revoke_session
from app.logs.bus import bus
from app.logs.service import record_operational_event

pytestmark = pytest.mark.postgres

LOGS_URL = "/api/v1/admin/logs"


@pytest.fixture(autouse=True)
def fast_heartbeat(monkeypatch):
    """Keep stream tests fast without changing production timing."""
    monkeypatch.setattr(system_logs, "HEARTBEAT_SECONDS", 0.1)
    bus.reset()
    yield
    bus.reset()


async def _admin(auth_client, make_user):
    account = await make_user(roles=("clinical_admin",))
    return account, await auth_client(account)


def _request(app, raw_token: str) -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"{LOGS_URL}/stream",
        "raw_path": f"{LOGS_URL}/stream".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"cookie", f"cap_session={raw_token}".encode())],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "app": app,
    }
    return Request(scope)


async def _open_stream(app, auth_client, **kwargs):
    raw_token = auth_client.cookies.get("cap_session")
    assert raw_token
    request = _request(app, raw_token)
    principal = await resolve_principal(request)
    assert principal is not None
    response = await system_logs.stream_system_logs(
        request=request,
        start=None,
        end=None,
        severity=None,
        category=None,
        event_type=None,
        actor_id=None,
        request_id=None,
        outcome=None,
        q=kwargs.get("q"),
        last_event_id=kwargs.get("last_event_id"),
        _unknown=None,
        principal=principal,
    )
    return response, response.body_iterator.__aiter__()


def _parse(chunk: str) -> dict[str, Any]:
    if chunk.strip() == ": heartbeat":
        return {"kind": "heartbeat"}
    event_name = None
    event_id = None
    data: list[str] = []
    for line in chunk.strip().splitlines():
        if line.startswith("id: "):
            event_id = line[4:]
        elif line.startswith("event: "):
            event_name = line[7:]
        elif line.startswith("data: "):
            data.append(line[6:])
    if event_name == "auth_expired":
        return {"kind": "auth_expired", "data": json.loads("\n".join(data))}
    assert event_name == "system", chunk
    return {
        "kind": "event",
        "event_id": event_id,
        "data": json.loads("\n".join(data)),
    }


async def _next_chunk(iterator, *, timeout: float = 5.0) -> dict[str, Any]:
    return _parse(await asyncio.wait_for(anext(iterator), timeout=timeout))


async def _next_event(iterator, *, timeout: float = 5.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AssertionError("no SSE event arrived before the timeout")
        chunk = await _next_chunk(iterator, timeout=remaining)
        if chunk["kind"] == "event":
            return chunk


async def _next_heartbeat(iterator, *, timeout: float = 5.0) -> None:
    """Assert the next meaningful chunk is a heartbeat (no duplicate event)."""
    chunk = await _next_chunk(iterator, timeout=timeout)
    assert chunk["kind"] == "heartbeat", chunk


async def _wait_for_no_subscribers(timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while bus.subscriber_count:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("disconnected stream subscribers were not cleaned up")
        await asyncio.sleep(0.02)


async def test_stream_requires_authentication_and_admin(
    client, auth_client, make_user
) -> None:
    assert (await client.get(LOGS_URL)).status_code == 401
    assert (await client.get(f"{LOGS_URL}/stream")).status_code == 401
    for role in ("doctor", "nurse"):
        account = await make_user(roles=(role,))
        unauthorized = await auth_client(account)
        assert (await unauthorized.get(f"{LOGS_URL}/stream")).status_code == 403
    _, admin = await _admin(auth_client, make_user)
    assert (await admin.get(LOGS_URL)).status_code == 200


async def test_live_events_reach_multiple_admin_streams(
    app, auth_client, make_user
) -> None:
    _, first = await _admin(auth_client, make_user)
    _, second = await _admin(auth_client, make_user)
    token = uuid.uuid4().hex[:10]
    first_response, first_stream = await _open_stream(
        app, first, q=token
    )
    second_response, second_stream = await _open_stream(
        app, second, q=token
    )
    assert first_response.media_type == "text/event-stream"
    assert first_response.headers["cache-control"] == "no-cache"
    try:
        await _next_heartbeat(first_stream)
        await _next_heartbeat(second_stream)
        assert bus.subscriber_count == 2

        assert await record_operational_event(
            event_type="ai.request.completed",
            request_id=f"{token}-live",
            outcome="success",
            metadata={"duration_ms": 120, "model": "deepseek-v4.1-flash"},
        )

        payload_one = (await _next_event(first_stream))["data"]
        payload_two = (await _next_event(second_stream))["data"]
    finally:
        await first_stream.aclose()
        await second_stream.aclose()

    assert payload_one == payload_two
    assert payload_one["id"].startswith("op:")
    assert payload_one["request_id"] == f"{token}-live"
    assert payload_one["metadata"] == {
        "duration_ms": 120,
        "model": "deepseek-v4.1-flash",
    }
    await _wait_for_no_subscribers()


async def test_cursor_replay_order_dedup_and_heartbeats(
    app, auth_client, db_engine, make_user
) -> None:
    _, admin = await _admin(auth_client, make_user)
    token = uuid.uuid4().hex[:10]
    assert await record_operational_event(
        event_type="voice.transcription.completed",
        request_id=f"{token}-one",
        outcome="success",
        metadata={"language": "en", "duration_ms": 210},
    )
    assert await record_operational_event(
        event_type="voice.transcription.completed",
        request_id=f"{token}-two",
        outcome="success",
        metadata={"language": "bg", "duration_ms": 310},
    )

    _, replay = await _open_stream(app, admin, q=token)
    try:
        first = (await _next_event(replay))["data"]
        second = (await _next_event(replay))["data"]
    finally:
        await replay.aclose()
    await _wait_for_no_subscribers()
    assert (first["timestamp"], first["id"]) <= (second["timestamp"], second["id"])
    assert {first["request_id"], second["request_id"]} == {
        f"{token}-one",
        f"{token}-two",
    }

    # Reconnecting from the last delivered event replays nothing twice.
    last_id = max((first, second), key=lambda item: (item["timestamp"], item["id"]))["id"]
    _, resumed = await _open_stream(
        app, admin, q=token, last_event_id=last_id
    )
    try:
        await _next_heartbeat(resumed)
        assert await record_operational_event(
            event_type="voice.transcription.completed",
            request_id=f"{token}-three",
            outcome="success",
            metadata={"language": "en", "duration_ms": 410},
        )
        third = (await _next_event(resumed))["data"]
    finally:
        await resumed.aclose()
    assert third["request_id"] == f"{token}-three"
    assert (third["timestamp"], third["id"]) > (second["timestamp"], second["id"])

    _, deduped = await _open_stream(
        app, admin, q=token, last_event_id=third["id"]
    )
    try:
        await _next_heartbeat(deduped)
    finally:
        await deduped.aclose()

    async with AsyncSession(db_engine, expire_on_commit=False) as db:
        assert (
            await db.scalar(
                text("SELECT count(*) FROM admin.system_event WHERE request_id LIKE :p"),
                {"p": f"{token}-%"},
            )
            == 3
        )
    await _wait_for_no_subscribers()


async def test_stream_heartbeat_without_events(app, auth_client, make_user) -> None:
    _, admin = await _admin(auth_client, make_user)
    _, stream = await _open_stream(app, admin, q=uuid.uuid4().hex[:10])
    try:
        await _next_heartbeat(stream)
    finally:
        await stream.aclose()


async def test_revoked_session_stops_the_stream(
    app, auth_client, db_engine, make_user
) -> None:
    _, admin = await _admin(auth_client, make_user)
    _, stream = await _open_stream(app, admin, q=uuid.uuid4().hex[:10])
    try:
        await _next_heartbeat(stream)
        raw_token = admin.cookies.get("cap_session")
        assert raw_token
        async with AsyncSession(db_engine, expire_on_commit=False) as db:
            assert await revoke_session(db, raw_token)
            await db.commit()

        chunk = await _next_chunk(stream)
        assert chunk == {
            "kind": "auth_expired",
            "data": {"reason": "session_expired"},
        }
    finally:
        await stream.aclose()
    await _wait_for_no_subscribers()


async def test_disconnect_cleans_up_subscription(app, auth_client, make_user) -> None:
    _, admin = await _admin(auth_client, make_user)
    _, stream = await _open_stream(app, admin, q=uuid.uuid4().hex[:10])
    await _next_heartbeat(stream)
    assert bus.subscriber_count == 1
    await stream.aclose()
    await _wait_for_no_subscribers()
