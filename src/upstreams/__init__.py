"""Upstream provider registry — the heart of the Strategy pattern.

Each *signal* (search / scraping / traffic / funding / people / tech) defines an
abstract class in `<signal>/_base.py`. Concrete provider implementations register
themselves via the `@register("<signal>", "<name>")` decorator. `config/providers.yaml`
selects which concrete provider is active per signal.

Route handlers know nothing about specific providers — they call
`get_active_provider("funding").lookup(...)` and trust the returned pydantic model.
Swapping SEC EDGAR for Crunchbase is a one-line edit in providers.yaml + a restart.

Why this matters: when a paid tier of any upstream is added, the route code doesn't
change. When an upstream's free tier is killed (it happens), we swap to a different
provider via config without touching any business logic.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import yaml

from src.upstreams._base import UpstreamProvider

T = TypeVar("T", bound=UpstreamProvider)

# Default config location. Overridable via KITCHEN_PROVIDERS_CONFIG env var if needed.
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "providers.yaml"

# Registry of {signal: {provider_name: ProviderClass}}. Populated by @register decorators
# at import time (see _import_all_providers below).
_REGISTRY: dict[str, dict[str, type[UpstreamProvider]]] = {
    "search": {},
    "scraping": {},
    "traffic": {},
    "funding": {},
    "people": {},
    "tech": {},
}

VALID_SIGNALS = frozenset(_REGISTRY.keys())


def register(signal: str, name: str) -> Callable[[type[T]], type[T]]:
    """Decorator: register a provider class for a given signal under a given name.

    Usage:
        @register("funding", "sec_edgar")
        class SECEdgarProvider(FundingProvider):
            ...
    """
    if signal not in VALID_SIGNALS:
        raise ValueError(f"unknown signal {signal!r}; valid: {sorted(VALID_SIGNALS)}")

    def decorator(cls: type[T]) -> type[T]:
        if name in _REGISTRY[signal]:
            raise ValueError(f"provider {name!r} already registered for signal {signal!r}")
        _REGISTRY[signal][name] = cls
        return cls

    return decorator


def get_active_provider(signal: str, *, config_path: Path | None = None) -> UpstreamProvider:
    """Return an instance of the provider currently selected for `signal` in providers.yaml."""
    if signal not in VALID_SIGNALS:
        raise ValueError(f"unknown signal {signal!r}")
    config_path = config_path or _DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(
            f"providers.yaml not found at {config_path}. "
            "Required: every signal must have an active provider configured."
        )
    config = yaml.safe_load(config_path.read_text()) or {}
    provider_name = config.get(signal)
    if not provider_name:
        raise ValueError(f"no provider configured for signal {signal!r} in {config_path}")
    if provider_name not in _REGISTRY[signal]:
        raise LookupError(
            f"provider {provider_name!r} configured for {signal!r} but not registered. "
            f"Registered: {sorted(_REGISTRY[signal].keys())}"
        )
    return _REGISTRY[signal][provider_name]()


def list_registered(signal: str | None = None) -> dict[str, list[str]]:
    """Return {signal: [provider_names]}. For diagnostics + the future /providers endpoint."""
    if signal is not None:
        if signal not in VALID_SIGNALS:
            raise ValueError(f"unknown signal {signal!r}")
        return {signal: sorted(_REGISTRY[signal].keys())}
    return {sig: sorted(providers.keys()) for sig, providers in _REGISTRY.items()}


def _import_all_providers() -> None:
    """Import every concrete provider module so its @register decorator fires.

    Adding a new provider: write the module under src/upstreams/<signal>/<name>.py,
    decorate the class with @register, and add an import line below.
    """
    # Funding
    from src.upstreams.funding import sec_edgar  # noqa: F401

    # search / scraping / traffic / people / tech — providers land in subsequent PRs


_import_all_providers()


__all__ = [
    "VALID_SIGNALS",
    "get_active_provider",
    "list_registered",
    "register",
]
