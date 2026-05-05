import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agent as agent_module
from app.agent import build_response, clear_session, get_session_meta
from app.triage import Severity


def make_redis_mock(store: dict) -> MagicMock:
    mock = MagicMock()
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


def test_get_session_meta_invalid_callsid() -> None:
    assert get_session_meta("abc123") == {}


def test_get_session_meta_valid_callsid(store: dict) -> None:
    store["test123"] = {"messages": [], "severity": "ROUTINE", "condition": "test"}
    result = get_session_meta("test123")
    assert result["condition"] == "test"


def test_clear_session_invalid_callsid() -> None:
    clear_session("abc123")


def test_clear_session_valid_callsid(store: dict) -> None:
    store["test123"] = {"messages": [], "severity": "ROUTINE", "condition": "test"}
    clear_session("test123")
    assert get_session_meta("test123") == {}


async def test_build_response_critical(store: dict) -> None:
    mock_choice = MagicMock()
    mock_choice.message.content = "Call 911 now!"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch(
        "app.agent.client.chat.completions.create", new=AsyncMock(return_value=mock_response)
    ):
        result = await build_response("my neighbor is not breathing", "call_001")

    meta = get_session_meta("call_001")
    assert result == "Call 911 now!"
    assert meta["severity"] == Severity.CRITICAL
