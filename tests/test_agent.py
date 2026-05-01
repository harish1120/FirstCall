from unittest.mock import AsyncMock, MagicMock, patch

from app.agent import build_response, clear_session, get_session_meta, sessions
from app.triage import Severity

sessions["test123"] = "hello from the other side"


def test_get_session_meta_invalid_callsid() -> None:
    assert (get_session_meta("abc123")) == {}


def test_get_session_meta_valid_callsid() -> None:
    assert get_session_meta("test123") == "hello from the other side"
    assert type(sessions) is dict


def test_clear_session_invalid_callsid() -> None:
    clear_session("abc123")


def test_clear_session_valid_callsid() -> None:
    clear_session("test123")
    assert get_session_meta("test123") == {}


async def test_build_response_critical() -> None:
    mock_choice = MagicMock()
    mock_choice.message.content = "Call 911 now!"  # sets on this specific object
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    with patch(
        "app.agent.client.chat.completions.create", new=AsyncMock(return_value=mock_response)
    ):
        result = await build_response("my neighbor is not breathing", "call_001")

    meta = get_session_meta("call_001")
    assert result == "Call 911 now!"
    assert meta["severity"] == Severity.CRITICAL
