"""GET /providers — diagnostic endpoint listing what's registered + active per signal.

No auth — operator/diagnostic surface, leaks no upstream API keys, only configuration
shape. Intentionally PUBLIC so curl-based health probes can use it without scoped tokens.
"""

import logging
from pathlib import Path

import yaml
from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from src.upstreams import VALID_SIGNALS, list_registered

log = logging.getLogger("kitchen.routes.providers")

router = APIRouter(tags=["meta"])

_PROVIDERS_CONFIG = Path(__file__).resolve().parent.parent.parent / "config" / "providers.yaml"


class SignalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal: str
    active: str | None  # current selection from providers.yaml; null if unconfigured
    registered: list[str] = Field(default_factory=list)


class ProvidersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signals: list[SignalState]


@router.get("/providers", status_code=status.HTTP_200_OK, response_model=ProvidersResponse)
async def providers() -> ProvidersResponse:
    config = {}
    if _PROVIDERS_CONFIG.exists():
        try:
            loaded = yaml.safe_load(_PROVIDERS_CONFIG.read_text()) or {}
            if isinstance(loaded, dict):
                config = loaded
        except yaml.YAMLError:
            log.exception("providers.yaml is malformed; treating as empty")

    registered = list_registered()
    states = [
        SignalState(
            signal=signal,
            active=config.get(signal),
            registered=registered.get(signal, []),
        )
        for signal in sorted(VALID_SIGNALS)
    ]
    return ProvidersResponse(signals=states)
