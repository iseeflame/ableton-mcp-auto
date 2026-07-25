"""Telemetry decorators — reduced to pass-throughs in this fork.

Upstream these wrappers timed each tool call and shipped the result (plus, with the
default-on consent flag, the user's prompt and MIDI/browser metadata) to the author's
Supabase project. Here they return the function untouched, so the decorators on
server.py's tools still resolve but do nothing.

Returning the original function matters: FastMCP derives each tool's schema from the
wrapped signature, so no functools.wraps shim is involved.

The original implementation is recoverable with:
    git show <upstream-commit>:MCP_Server/telemetry_decorator.py
"""

from typing import Any, Callable


def telemetry_tool(tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """No-op replacement for the upstream basic-tracking decorator."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return func
    return decorator


def rich_telemetry_tool(
    tool_name: str,
    capture_notes: bool = False
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """No-op replacement for the upstream extended-metadata decorator."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return func
    return decorator
