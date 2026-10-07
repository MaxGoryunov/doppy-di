"""Tests for the Configuration provider (issue #124)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

import pytest

from doppy_di import (
    AsyncDependencyInSyncContextError,
    Container,
    DuplicateKeyError,
    Rule,
    ServiceNotFoundError,
)
from doppy_di.providers import AsyncConfiguration, Configuration


def _write(tmp_path: Any, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_dict_source_whole_and_dotted() -> None:
    services = Container()
    services.config = Configuration(dictionary={"db": {"host": "localhost", "port": 5432}})

    assert services.get("config") == {"db": {"host": "localhost", "port": 5432}}
    assert services.get("config.db.host") == "localhost"
    assert services.get("config.db") == {"host": "localhost", "port": 5432}
    assert services.get("config.db.port") == 5432


def test_env_interpolation_bracket_style(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONF_HOST", "db.internal")
    services = Container()
    services.config = Configuration(
        dictionary={"db": {"host": "${CONF_HOST}"}},
    )
    assert services.get("config.db.host") == "db.internal"


def test_env_interpolation_dollar_style(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONF_PORT", "6432")
    services = Container()
    services.config = Configuration(dictionary={"db": {"port": "$CONF_PORT"}})
    assert services.get("config.db.port") == "6432"


def test_live_false_caches_env_at_load(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONF_LIVE", "one")
    services = Container()
    services.config = Configuration(dictionary={"v": "${CONF_LIVE}"})
    assert services.get("config.v") == "one"
    monkeypatch.setenv("CONF_LIVE", "two")
    assert services.get("config.v") == "one"


def test_live_true_reads_env_on_every_resolve(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONF_LIVE", "one")
    services = Container()
    services.config = Configuration(dictionary={"v": "${CONF_LIVE}"}, live=True)
    assert services.get("config.v") == "one"
    monkeypatch.setenv("CONF_LIVE", "two")
    assert services.get("config.v") == "two"


def test_json_source(tmp_path: Any, monkeypatch: Any) -> None:
    path = _write(
        tmp_path, "c.json", json.dumps({"db": {"host": "${CONF_JSON_HOST}", "port": 5432}})
    )
    monkeypatch.setenv("CONF_JSON_HOST", "json-host")
    services = Container()
    services.config = Configuration(json_path=path)
    assert services.get("config.db.host") == "json-host"
    assert services.get("config.db.port") == 5432


def test_yaml_source(tmp_path: Any, monkeypatch: Any) -> None:
    pytest.importorskip("yaml")
    path = _write(tmp_path, "c.yaml", "db:\n  host: ${CONF_YAML_HOST}\n  port: 5432\n")
    monkeypatch.setenv("CONF_YAML_HOST", "yaml-host")
    services = Container()
    services.config = Configuration(yaml_path=path)
    assert services.get("config.db.host") == "yaml-host"


def test_ini_source(tmp_path: Any, monkeypatch: Any) -> None:
    path = _write(tmp_path, "c.ini", "[db]\nhost = ${CONF_INI_HOST}\nport = 5432\n")
    monkeypatch.setenv("CONF_INI_HOST", "ini-host")
    services = Container()
    services.config = Configuration(ini_path=path)
    assert services.get("config.db.host") == "ini-host"


def test_pydantic_settings_source() -> None:
    pytest.importorskip("pydantic")
    from pydantic import BaseModel

    class Settings(BaseModel):
        debug: bool = True
        name: str = "app"

    services = Container()
    services.config = Configuration(settings=Settings())
    assert services.get("config.debug") is True
    assert services.get("config.name") == "app"


def test_env_mapping_source() -> None:
    services = Container()
    services.config = Configuration(env={"DB_HOST": "env-host", "DB_PORT": "5432"})
    assert services.get("config.DB_HOST") == "env-host"


def test_env_prefix_source_nested(monkeypatch: Any) -> None:
    monkeypatch.setenv("MY_CFG_DB__HOST", "prefix-host")
    monkeypatch.setenv("MY_CFG_DB__PORT", "7777")
    services = Container()
    services.config = Configuration(env_prefix="MY_CFG_")
    assert services.get("config.db.host") == "prefix-host"
    assert services.get("config.db.port") == "7777"


def test_interpolation_in_list_and_scalars(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONF_SCALAR", "99")
    services = Container()
    services.config = Configuration(
        dictionary={
            "nums": ["${CONF_SCALAR}", 1, None],
            "flag": True,
            "nested": {"active": "${CONF_SCALAR}"},
        }
    )
    assert services.get("config.nums") == ["99", 1, None]
    assert services.get("config.flag") is True
    assert services.get("config.nested.active") == "99"


def test_multi_source_merge_dictionary_then_json(tmp_path: Any) -> None:
    path = _write(tmp_path, "c.json", json.dumps({"db": {"host": "json", "port": 5432}}))
    services = Container()
    services.config = Configuration(
        dictionary={"db": {"host": "dict", "port": 1, "extra": True}},
        json_path=path,
    )
    # json overrides shared keys, dictionary keeps extras
    assert services.get("config.db.host") == "json"
    assert services.get("config.db.port") == 5432
    assert services.get("config.db.extra") is True


def test_reload_re_reads_file(tmp_path: Any) -> None:
    path = _write(tmp_path, "c.json", json.dumps({"v": 1}))
    services = Container()
    provider = Configuration(json_path=path)
    services.config = provider
    assert services.get("config.v") == 1

    _write(tmp_path, "c.json", json.dumps({"v": 2}))
    provider.reload()
    assert services.get("config.v") == 2


def test_child_key_namespaced_under_name() -> None:
    services = Container()
    services.config = Configuration(dictionary={"db": {"host": "h"}})
    rules = services.config.ruleset.keys()
    assert "config.db.host" in rules
    assert "config.db" in rules


def test_collision_with_existing_key_raises() -> None:
    services = Container()
    services.config.ruleset.add("config.db.host", Rule("config.db.host", lambda: "occupied"))
    with pytest.raises(DuplicateKeyError):
        services.config = Configuration(dictionary={"db": {"host": "h"}})


def test_two_namespaces_do_not_collide() -> None:
    services = Container()
    services.a = Configuration(dictionary={"x": 1})
    services.b = Configuration(dictionary={"x": 2})
    assert services.get("a.x") == 1
    assert services.get("b.x") == 2


def test_async_configuration_aget() -> None:
    async def run() -> Any:
        services = Container()
        services.config = AsyncConfiguration(dictionary={"v": 42})
        return await services.aget("config.v")

    assert asyncio.run(run()) == 42


def test_live_true_parent_full(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONF_LIST", "a")
    services = Container()
    services.config = Configuration(dictionary={"v": "${CONF_LIST}"}, live=True)
    assert services.get("config") == {"v": "a"}
    monkeypatch.setenv("CONF_LIST", "b")
    assert services.get("config") == {"v": "b"}


def test_settings_legacy_dict_source() -> None:
    class Legacy:
        def dict(self) -> Dict[str, Any]:
            return {"debug": True}

    services = Container()
    services.config = Configuration(settings=Legacy())
    assert services.get("config.debug") is True


def test_settings_plain_object_source() -> None:
    class Plain:
        def __init__(self) -> None:
            self.debug = True
            self.name = "app"

    services = Container()
    services.config = Configuration(settings=Plain())
    assert services.get("config.debug") is True
    assert services.get("config.name") == "app"


def test_env_prefix_exact_prefix_ignored(monkeypatch: Any) -> None:
    monkeypatch.setenv("MY_CFG_", "ignored")
    monkeypatch.setenv("MY_CFG_REAL", "kept")
    services = Container()
    services.config = Configuration(env_prefix="MY_CFG_")
    assert services.get("config.real") == "kept"
    with pytest.raises(ServiceNotFoundError):
        services.get("config.")


def test_parent_name_collision_raises() -> None:
    services = Container()
    services.config.ruleset.add("config", Rule("config", lambda: "occupied"))
    with pytest.raises(DuplicateKeyError):
        services.config = Configuration(dictionary={"a": 1})


def test_async_configuration_sync_get_raises() -> None:
    services = Container()
    services.config = AsyncConfiguration(dictionary={"v": 42})
    with pytest.raises(AsyncDependencyInSyncContextError):
        services.get("config.v")
