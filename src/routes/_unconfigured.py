"""Generic 503 handler for signals whose providers haven't landed yet.

Used by /people, /tech, /traffic, /discover, /domain/{slug} until concrete providers
are wired in subsequent PRs. Returns a clear, machine-parseable response so plugin
clients know to fall back to direct MCP/API calls.
"""

from fastapi import HTTPException, status


def raise_signal_unconfigured(signal: str) -> None:
    """Raise 503 with a body that tells the client exactly which signal lacks a provider."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "signal_unconfigured",
            "signal": signal,
            "message": (
                f"no provider configured for signal {signal!r}. "
                "Set one in config/providers.yaml + restart, "
                "or have the calling client fall back to direct API/MCP calls."
            ),
        },
    )
