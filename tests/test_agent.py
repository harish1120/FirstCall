import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agent as agent_module
from app.agent import build_response, clear_session, get_session_meta
from app.triage import Severity


def make_redis_mock(store: dict) -> AsyncMock:
    mock = AsyncMock()
    mock.get.side_effect = lambda k: json.dumps(store[k]).encode() if k in store else None
    mock.setex.side_effect = lambda k, ttl, v: store.update({k: json.loads(v)})
    mock.delete.side_effect = lambda k: store.pop(k, None)
    return mock


@pytest.fixture
def store() -> dict:
    return {}


@pytest.fixture(autouse=True)
def mock_redis(store: dict):
    with patch.object(agent_module, "r", make_redis_mock(store)):
        yield


async def test_get_session_meta_invalid_callsid() -> None:
    assert await get_session_meta("abc123") == {}


async def test_get_session_meta_valid_callsid(store: dict) -> None:
    store["test123"] = {"messages": [], "severity": "ROUTINE", "condition": "test"}
    result = await get_session_meta("test123")
    assert result["condition"] == "test"


async def test_clear_session_invalid_callsid() -> None:
    await clear_session("abc123")


async def test_clear_session_valid_callsid(store: dict) -> None:
    store["test123"] = {"messages": [], "severity": "ROUTINE", "condition": "test"}
    await clear_session("test123")
    assert await get_session_meta("test123") == {}


async def test_build_response_critical(store: dict) -> None:
    # mock_choice = MagicMock()
    # mock_choice.message.content = "Call 911 now!"
    # mock_response = MagicMock()
    # mock_response.choices = [mock_choice]

    async def mock_stream():
        mock_chunk = MagicMock()
        mock_chunk.choices[0].delta.content = "Call 911 now!"
        yield mock_chunk

    with patch(
        "app.agent.client.chat.completions.create", new=AsyncMock(return_value=mock_stream())
    ):
        result = "".join(
            [chunk async for chunk in build_response("my neighbor is not breathing", "call_001")]
        )

    meta = await get_session_meta("call_001")
    assert result == "Call 911 now!"
    assert meta["severity"] == Severity.CRITICAL
