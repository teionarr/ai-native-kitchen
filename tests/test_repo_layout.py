"""Placeholder tests verifying the repo scaffold.

These live until PR-2.1 (which adds the FastAPI app + /health) brings real tests.
Their job is to make `pytest` exit 0 on the empty scaffold so CI gates work from PR #1.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_has_required_fields() -> None:
    """pyproject.toml declares the basics every PR depends on."""
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    assert data["project"]["name"] == "ai-native-kitchen"
    assert data["project"]["requires-python"].startswith(">=3.12")
    assert "fastapi" in " ".join(data["project"]["dependencies"]).lower()


def test_docs_present() -> None:
    """Operator + contributor docs must exist before any deployment-affecting code."""
    assert (REPO_ROOT / "docs" / "CONTRIBUTING.md").is_file()
    assert (REPO_ROOT / "docs" / "vm-deploy.md").is_file()


def test_doppler_yaml_points_to_correct_project() -> None:
    """The Doppler config in source should reference the project name; deviations are bugs."""
    import yaml

    data = yaml.safe_load((REPO_ROOT / "doppler.yaml").read_text())
    assert data["setup"]["project"] == "ai-native-kitchen"


def test_gitignore_blocks_env_files() -> None:
    """Defense in depth: gitleaks runs in CI, but .gitignore is the first line."""
    gi = (REPO_ROOT / ".gitignore").read_text()
    assert ".env" in gi
    assert "Doppler" in gi or "doppler" in gi
