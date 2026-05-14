"""Tests for docker-compose.yml.

We can't run docker from inside pytest (no daemon in unit-test env), but we CAN
parse the compose file and verify its shape — services present, hardening flags
set, no host-network, no privileged containers, all images pinned to digests.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE = REPO_ROOT / "docker-compose.yml"

EXPECTED_SERVICES = {"service", "kitchen-redis", "kitchen-postgres"}
DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}$")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_compose_file_parses(compose: dict) -> None:
    assert "services" in compose
    assert isinstance(compose["services"], dict)


def test_all_expected_services_present(compose: dict) -> None:
    actual = set(compose["services"].keys())
    missing = EXPECTED_SERVICES - actual
    assert not missing, f"missing services: {missing}; actual: {actual}"


def test_no_host_network_anywhere(compose: dict) -> None:
    for name, svc in compose["services"].items():
        assert svc.get("network_mode") != "host", f"{name} uses network_mode: host"


def test_no_privileged_containers(compose: dict) -> None:
    for name, svc in compose["services"].items():
        assert svc.get("privileged") is not True, f"{name} runs privileged"


def test_every_service_drops_all_caps(compose: dict) -> None:
    for name, svc in compose["services"].items():
        assert svc.get("cap_drop") == ["ALL"], f"{name} does not drop ALL caps"


def test_every_service_no_new_privileges(compose: dict) -> None:
    for name, svc in compose["services"].items():
        opts = svc.get("security_opt", [])
        assert "no-new-privileges:true" in opts, f"{name} missing no-new-privileges:true"


def test_every_service_runs_as_non_root(compose: dict) -> None:
    for name, svc in compose["services"].items():
        user = svc.get("user", "")
        assert user, f"{name} has no user: directive"
        # uid must not be 0
        uid = str(user).split(":")[0]
        assert uid != "0", f"{name} runs as uid 0 (root)"


def test_every_service_read_only_root_fs(compose: dict) -> None:
    for name, svc in compose["services"].items():
        assert svc.get("read_only") is True, f"{name} root FS is not read-only"


def test_third_party_images_pinned_to_digests(compose: dict) -> None:
    """redis and postgres images must reference an @sha256:... digest, not a floating tag."""
    for name in ("kitchen-redis", "kitchen-postgres"):
        image = compose["services"][name].get("image", "")
        assert DIGEST_RE.search(image), f"{name} image not digest-pinned: {image}"


def test_no_external_ports_beyond_loopback(compose: dict) -> None:
    """Every host port binding must be on 127.0.0.1 — no public ports."""
    for name, svc in compose["services"].items():
        for port_spec in svc.get("ports", []) or []:
            spec = str(port_spec)
            assert spec.startswith("127.0.0.1:"), (
                f"{name} exposes port {spec} on all interfaces; must bind to 127.0.0.1"
            )


def test_isolated_bridge_network(compose: dict) -> None:
    nets = compose.get("networks", {})
    assert "kitchen-net" in nets
    assert nets["kitchen-net"].get("driver") == "bridge"


def test_all_services_use_kitchen_net(compose: dict) -> None:
    for name, svc in compose["services"].items():
        nets = svc.get("networks", [])
        assert "kitchen-net" in nets, f"{name} not on kitchen-net"


def test_named_volumes_declared(compose: dict) -> None:
    expected = {"kitchen-redis-data", "kitchen-postgres-data", "kitchen-briefs"}
    declared = set(compose.get("volumes", {}).keys())
    missing = expected - declared
    assert not missing, f"missing named volumes: {missing}"


def test_service_depends_on_redis_and_postgres_health(compose: dict) -> None:
    deps = compose["services"]["service"].get("depends_on", {})
    assert "kitchen-redis" in deps
    assert "kitchen-postgres" in deps
    for dep_name, dep_cfg in deps.items():
        assert dep_cfg.get("condition") == "service_healthy", (
            f"service depends on {dep_name} without service_healthy condition"
        )
