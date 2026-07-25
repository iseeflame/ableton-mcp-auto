"""Telemetry — removed in this fork.

Upstream shipped a Supabase-backed telemetry client that reported tool usage, and
(because its consent flag defaulted to True) user prompts, MIDI notes and track/clip
names as well. This fork keeps the module's public API so server.py stays unchanged,
but every entry point is a no-op and nothing leaves the machine.

The original implementation is recoverable with:
    git show <upstream-commit>:MCP_Server/telemetry.py
"""

import logging
from typing import Any, Optional

logger = logging.getLogger("ableton-mcp-telemetry")


class EventType:
    """Kept so existing imports resolve; values are never transmitted."""
    STARTUP = "startup"
    TOOL_CALL = "tool_call"
    ERROR = "error"


class _NullTelemetry:
    """Drop-in replacement for the upstream Telemetry client. Does nothing."""

    enabled = False

    def record_event(self, *args: Any, **kwargs: Any) -> None:
        return None

    def record_tool_call(self, *args: Any, **kwargs: Any) -> None:
        return None

    def flush(self, *args: Any, **kwargs: Any) -> None:
        return None

    def shutdown(self, *args: Any, **kwargs: Any) -> None:
        return None


_null_telemetry = _NullTelemetry()


def get_telemetry() -> _NullTelemetry:
    return _null_telemetry


def set_telemetry_consent(consent: bool) -> None:
    """No-op: there is no collection to consent to in this fork."""
    return None


def get_telemetry_consent() -> bool:
    return False


def record_startup(ableton_version: Optional[str] = None) -> None:
    """No-op: upstream reported a startup event here for DAU/MAU counting."""
    return None
