"""Tests for the upstream registry — the Strategy-pattern foundation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src import upstreams
from src.upstreams._base import UpstreamProvider


def test_all_six_signals_have_a_subdirectory() -> None:
    """Every declared signal must have its own _base.py + __init__.py."""
    upstreams_root = Path(upstreams.__file__).parent
    for signal in upstreams.VALID_SIGNALS:
        sig_dir = upstreams_root / signal
        assert sig_dir.is_dir(), f"missing directory for signal {signal!r}"
        assert (sig_dir / "_base.py").is_file(), f"missing _base.py for {signal!r}"
        assert (sig_dir / "__init__.py").is_file(), f"missing __init__.py for {signal!r}"


def test_register_decorator_adds_to_registry() -> None:
    @upstreams.register("funding", "test_provider_x")
    class _TestProvider(UpstreamProvider):
        async def lookup(self, company: str) -> Any:
            return None

    listed = upstreams.list_registered("funding")
    assert "test_provider_x" in listed["funding"]
    # Cleanup so re-running tests doesn't duplicate-register
    del upstreams._REGISTRY["funding"]["test_provider_x"]


def test_register_rejects_unknown_signal() -> None:
    with pytest.raises(ValueError, match="unknown signal"):

        @upstreams.register("not_a_signal", "x")
        class _X(UpstreamProvider): ...


def test_register_rejects_duplicate_name() -> None:
    @upstreams.register("funding", "dup_test")
    class _A(UpstreamProvider): ...

    with pytest.raises(ValueError, match="already registered"):

        @upstreams.register("funding", "dup_test")
        class _B(UpstreamProvider): ...

    del upstreams._REGISTRY["funding"]["dup_test"]


def test_get_active_provider_returns_configured_class(tmp_path: Path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "search": None,
                "scraping": None,
                "traffic": None,
                "funding": "sec_edgar",
                "people": None,
                "tech": None,
            }
        )
    )
    provider = upstreams.get_active_provider("funding", config_path=config)
    assert provider.name == "sec_edgar"


def test_get_active_provider_raises_when_unconfigured(tmp_path: Path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(yaml.safe_dump({"funding": None}))
    with pytest.raises(ValueError, match="no provider configured"):
        upstreams.get_active_provider("funding", config_path=config)


def test_get_active_provider_raises_when_provider_unknown(tmp_path: Path) -> None:
    config = tmp_path / "providers.yaml"
    config.write_text(yaml.safe_dump({"funding": "made_up_provider_name"}))
    with pytest.raises(LookupError, match="not registered"):
        upstreams.get_active_provider("funding", config_path=config)


def test_get_active_provider_raises_when_config_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        upstreams.get_active_provider("funding", config_path=tmp_path / "nonexistent.yaml")


def test_provider_base_class_auto_derives_snake_case_name() -> None:
    class MultiWordTestProvider(UpstreamProvider): ...

    assert MultiWordTestProvider.name == "multi_word_test"


def test_default_providers_yaml_has_funding_set() -> None:
    """The committed providers.yaml should at minimum have the SEC EDGAR provider wired."""
    repo_root = Path(__file__).resolve().parent.parent
    config = yaml.safe_load((repo_root / "config" / "providers.yaml").read_text())
    assert config["funding"] == "sec_edgar"
