"""Validate hardened Linux capability profiles in docker-compose.yml."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

POSTGRES_CAP_ADD = frozenset({"CHOWN", "FOWNER", "SETGID", "SETUID", "DAC_READ_SEARCH"})


def _service_section(service: str) -> str:
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    match = re.search(
        rf"^  {re.escape(service)}:\n(.*?)(?=^  [a-zA-Z].*:$|\Z)",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"service {service!r} not found in docker-compose.yml"
    return match.group(1)


def _list_block(section: str, key: str) -> list[str]:
    match = re.search(rf"^    {re.escape(key)}:\n((?:      - .+\n)+)", section, flags=re.MULTILINE)
    if match is None:
        return []
    return re.findall(r"^      - (.+)$", match.group(1), flags=re.MULTILINE)


def _cap_set(section: str, key: str) -> frozenset[str]:
    return frozenset(_list_block(section, key))


def test_postgres_uses_measured_minimal_capability_profile() -> None:
    postgres = _service_section("postgres")

    assert _cap_set(postgres, "cap_drop") == frozenset({"ALL"})
    assert _cap_set(postgres, "cap_add") == POSTGRES_CAP_ADD
    assert _list_block(postgres, "security_opt") == ["no-new-privileges:true"]


def test_apiservice_drops_all_capabilities() -> None:
    apiservice = _service_section("apiservice")

    assert _cap_set(apiservice, "cap_drop") == frozenset({"ALL"})
    assert _cap_set(apiservice, "cap_add") == frozenset()
    assert _list_block(apiservice, "security_opt") == ["no-new-privileges:true"]


def test_compose_never_grants_cap_add_all() -> None:
    text = COMPOSE_FILE.read_text(encoding="utf-8")
    for service in re.findall(r"^  ([a-zA-Z0-9_-]+):$", text, flags=re.MULTILINE):
        section = _service_section(service)
        cap_add = _cap_set(section, "cap_add")
        assert "ALL" not in cap_add, f"{service} must not use cap_add: ALL"
