"""Tests for the Configuration provider (issue #124)."""

from __future__ import annotations

import asyncio
import json
import os
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


def test_env_interpolation_bracket_style() -> None:
    os.environ["CONF_HOST"] = "db.internal"
    try:
        services = Container()
        services.config = Configuration(
            dictionary={"db": {"host": "${CONF_HOST}"}},
        )
        assert services.get("config.db.host") == "db.internal"
    finally:
        os.environ.pop("CONF_HOST", None)


def test_env_interpolation_dollar_style() -> None:
    os.environ["CONF_PORT"] = "6432"
    try:
        services = Container()
        services.config = Configuration(dictionary={"db": {"port": "$CONF_PORT"}})
        assert services.get("config.db.port") == "6432"
    finally:
        os.environ.pop("CONF_PORT", None)


def test_live_false_caches_env_at_load() -> None:
    os.environ["CONF_LIVE"] = "one"
    try:
        services = Container()
        services.config = Configuration(dictionary={"v": "${CONF_LIVE}"})
        assert services.get("config.v") == "one"
        os.environ["CONF_LIVE"] = "two"
        assert services.get("config.v") == "one"
    finally:
        os.environ.pop("CONF_LIVE", None)


def test_live_true_reads_env_on_every_resolve() -> None:
    os.environ["CONF_LIVE"] = "one"
    try:
        services = Container()
        services.config = Configuration(dictionary={"v": "${CONF_LIVE}"}, live=True)
        assert services.get("config.v") == "one"
        os.environ["CONF_LIVE"] = "two"
        assert services.get("config.v") == "two"
    finally:
        os.environ.pop("CONF_LIVE", None)


def test_json_source(tmp_path: Any) -> None:
    path = _write(
        tmp_path, "c.json", json.dumps({"db": {"host": "${CONF_JSON_HOST}", "port": 5432}})
    )
    os.environ["CONF_JSON_HOST"] = "json-host"
    try:
        services = Container()
        services.config = Configuration(json_path=path)
        assert services.get("config.db.host") == "json-host"
        assert services.get("config.db.port") == 5432
    finally:
        os.environ.pop("CONF_JSON_HOST", None)


def test_yaml_source(tmp_path: Any) -> None:
    pytest.importorskip("yaml")
    path = _write(tmp_path, "c.yaml", "db:\n  host: ${CONF_YAML_HOST}\n  port: 5432\n")
    os.environ["CONF_YAML_HOST"] = "yaml-host"
    try:
        services = Container()
        services.config = Configuration(yaml_path=path)
        assert services.get("config.db.host") == "yaml-host"
    finally:
        os.environ.pop("CONF_YAML_HOST", None)


def test_ini_source(tmp_path: Any) -> None:
    path = _write(tmp_path, "c.ini", "[db]\nhost = ${CONF_INI_HOST}\nport = 5432\n")
    os.environ["CONF_INI_HOST"] = "ini-host"
    try:
        services = Container()
        services.config = Configuration(ini_path=path)
        assert services.get("config.db.host") == "ini-host"
    finally:
        os.environ.pop("CONF_INI_HOST", None)


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


def test_env_prefix_source_nested() -> None:
    os.environ["MY_CFG_DB__HOST"] = "prefix-host"
    os.environ["MY_CFG_DB__PORT"] = "7777"
    try:
        services = Container()
        services.config = Configuration(env_prefix="MY_CFG_")
        assert services.get("config.db.host") == "prefix-host"
        assert services.get("config.db.port") == "7777"
    finally:
        os.environ.pop("MY_CFG_DB__HOST", None)
        os.environ.pop("MY_CFG_DB__PORT", None)


def test_interpolation_in_list_and_scalars() -> None:
    os.environ["CONF_SCALAR"] = "99"
    try:
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
    finally:
        os.environ.pop("CONF_SCALAR", None)


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


def test_live_true_parent_full() -> None:
    os.environ["CONF_LIST"] = "a"
    try:
        services = Container()
        services.config = Configuration(dictionary={"v": "${CONF_LIST}"}, live=True)
        assert services.get("config") == {"v": "a"}
        os.environ["CONF_LIST"] = "b"
        assert services.get("config") == {"v": "b"}
    finally:
        os.environ.pop("CONF_LIST", None)


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


def test_env_prefix_exact_prefix_ignored() -> None:
    os.environ["MY_CFG_"] = "ignored"
    os.environ["MY_CFG_REAL"] = "kept"
    try:
        services = Container()
        services.config = Configuration(env_prefix="MY_CFG_")
        assert services.get("config.real") == "kept"
        with pytest.raises(ServiceNotFoundError):
            services.get("config.")
    finally:
        os.environ.pop("MY_CFG_", None)
        os.environ.pop("MY_CFG_REAL", None)


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
