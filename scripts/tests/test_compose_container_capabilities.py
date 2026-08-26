"""Validate hardened Linux capability profiles in docker-compose.yml."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

POSTGRES_CAP_ADD = frozenset({"CHOWN", "FOWNER", "SETGID", "SETUID", "DAC_READ_SEARCH"})


def _load_compose_services() -> dict[str, Any]:
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    data = yaml.safe_load(compose_text)
    services = data.get("services")
    assert isinstance(services, dict)
    return services


def _cap_set(service: dict[str, Any], key: str) -> frozenset[str]:
    values = service.get(key)
    if values is None:
        return frozenset()
    assert isinstance(values, list)
    return frozenset(str(value) for value in values)


def test_compose_config_renders_with_hardened_capabilities() -> None:
    result = subprocess.run(
        ["docker", "compose", "--file", str(COMPOSE_FILE), "config", "--quiet"],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr


def test_postgres_uses_measured_minimal_capability_profile() -> None:
    postgres = _load_compose_services()["postgres"]

    assert _cap_set(postgres, "cap_drop") == frozenset({"ALL"})
    assert _cap_set(postgres, "cap_add") == POSTGRES_CAP_ADD
    assert postgres.get("security_opt") == ["no-new-privileges:true"]


def test_apiservice_drops_all_capabilities() -> None:
    apiservice = _load_compose_services()["apiservice"]

    assert _cap_set(apiservice, "cap_drop") == frozenset({"ALL"})
    assert _cap_set(apiservice, "cap_add") == frozenset()
    assert apiservice.get("security_opt") == ["no-new-privileges:true"]


def test_compose_never_grants_cap_add_all() -> None:
    for service_name, service in _load_compose_services().items():
        cap_add = _cap_set(service, "cap_add")
        assert "ALL" not in cap_add, f"{service_name} must not use cap_add: ALL"
