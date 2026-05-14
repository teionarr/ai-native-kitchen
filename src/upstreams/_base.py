"""Shared base class every concrete upstream provider extends from.

Subclasses are organized by signal (search / scraping / traffic / funding / people / tech).
Each signal's `<signal>/_base.py` declares the signal-specific abstract method + the
pydantic result model that every provider in that signal must return.

The single common piece here: `name` for telemetry / logging.
"""

from __future__ import annotations

from abc import ABC


class UpstreamProvider(ABC):  # noqa: B024 — intentionally empty; abstract methods live in <signal>/_base.py
    """Marker base class. Real abstract methods are declared per signal in <signal>/_base.py."""

    #: Stable identifier used in logs, telemetry, and provider-name fields. Defaults to the
    #: snake_case class name minus the "Provider" suffix; concrete providers can override.
    name: str = ""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not cls.name:
            n = cls.__name__
            if n.endswith("Provider"):
                n = n[: -len("Provider")]
            # Naive snake_case
            out = []
            for i, ch in enumerate(n):
                if ch.isupper() and i > 0:
                    out.append("_")
                out.append(ch.lower())
            cls.name = "".join(out)
