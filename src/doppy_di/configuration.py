"""Configuration provider (issue #124).

Loads a configuration tree from YAML, JSON, INI, dict, environment variables,
or Pydantic Settings, interpolates environment variables (``${VAR}`` and
``$VAR``) into string values, and registers namespaced keys so nested values
are reachable via dotted paths (``config.db.host``).

By default the config resolves once at registration time and then caches env
values (``live=False``). Set ``live=True`` to re-interpolate environment
variables on every resolution so later env changes are observed.
"""

from __future__ import annotations

import configparser
import json
import os
import re
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple, cast

from .container import DuplicateKeyError, Rule, RuleSetProtocol
from .providers import Provider

__all__ = [
    "AsyncConfiguration",
    "Configuration",
    "ConfigurationError",
]

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


class ConfigurationError(Exception):
    """Raised when a configuration source cannot be loaded."""


def _interpolate(value: Any, env: Mapping[str, str]) -> Any:
    """Recursively substitute ``${VAR}`` / ``$VAR`` in string values."""
    if isinstance(value, str):

        def _repl(match: re.Match[str]) -> str:
            key = match.group(1) or match.group(2) or ""
            return env.get(key, match.group(0))

        return _ENV_VAR_RE.sub(_repl, value)
    if isinstance(value, dict):
        return {key: _interpolate(item, env) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, env) for item in value]
    return value


def _merge(left: Any, right: Any) -> Any:
    """Deep-merge two config trees; ``right`` wins on conflicts."""
    if isinstance(left, dict) and isinstance(right, dict):
        merged: Dict[str, Any] = dict(left)
        for key, value in right.items():
            if key in merged:
                merged[key] = _merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return right


class Configuration(Provider):
    """Declarative provider reading a configuration tree.

    Sources are given as keyword arguments and merged in priority order:
    ``dictionary``, ``ini_path``, ``json_path``, ``yaml_path``, ``settings``,
    ``env_prefix``, then ``env`` (last wins).

    The ``live`` flag controls env interpolation. With ``live=False`` (default)
    the config resolves once at assignment time and env values are cached, so
    later env changes are not observed until :meth:`reload`. With
    ``live=True`` environment variables are re-read on every ``get`` so dynamic
    changes are visible, at the cost of per-resolution closure reads.

    Examples:
        >>> import os
        >>> from doppy_di import Container
        >>> from doppy_di.providers import Configuration
        >>> services = Container()
        >>> services.config = Configuration(
        ...     dictionary={"db": {"host": "${DB_HOST}", "port": 5432}}
        ... )
        >>> os.environ["DB_HOST"] = "localhost"
        >>> services.get("config.db.host")
        'localhost'
        >>> services.get("config")
        {'db': {'host': 'localhost', 'port': 5432}}
    """

    def __init__(
        self,
        *,
        dictionary: Optional[Mapping[str, Any]] = None,
        ini_path: Optional[str] = None,
        json_path: Optional[str] = None,
        yaml_path: Optional[str] = None,
        settings: Optional[Any] = None,
        env: Optional[Mapping[str, str]] = None,
        env_prefix: Optional[str] = None,
        live: bool = False,
    ) -> None:
        self.dictionary = dictionary
        self.ini_path = ini_path
        self.json_path = json_path
        self.yaml_path = yaml_path
        self.settings = settings
        self.env = env
        self.env_prefix = env_prefix
        self.live = live

        self._data: Dict[str, Any] = {}
        self._resolved: Dict[str, Any] = {}
        self._paths: List[Tuple[str, ...]] = []

    # -- source loading -------------------------------------------------

    def _load_json(self, path: str) -> Dict[str, Any]:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}

    def _load_yaml(self, path: str) -> Dict[str, Any]:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - depends on env
            raise ConfigurationError(
                "YAML support requires 'PyYAML'; install with 'pip install doppy-di[config]'"
            ) from exc
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        return data if isinstance(data, dict) else {}

    def _load_ini(self, path: str) -> Dict[str, Any]:
        parser = configparser.ConfigParser()
        parser.read(path, encoding="utf-8")
        return {section: dict(parser.items(section)) for section in parser.sections()}

    def _load_settings(self, obj: Any) -> Dict[str, Any]:
        dump = getattr(obj, "model_dump", None)
        if callable(dump):
            return cast("Dict[str, Any]", dump())
        legacy = getattr(obj, "dict", None)
        if callable(legacy):
            return cast("Dict[str, Any]", legacy())
        return {key: value for key, value in vars(obj).items() if not key.startswith("_")}

    def _load_env_prefix(self, prefix: str) -> Dict[str, Any]:
        root: Dict[str, Any] = {}
        for key, value in os.environ.items():
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if not rest:
                continue
            parts = rest.lstrip("_").split("__")
            node = root
            for part in parts[:-1]:
                node = node.setdefault(part.lower(), {})
            node[parts[-1].lower()] = value
        return root

    def _load_sources(self) -> Dict[str, Any]:
        sources: List[Dict[str, Any]] = []
        if self.dictionary is not None:
            sources.append(dict(self.dictionary))
        if self.ini_path is not None:
            sources.append(self._load_ini(self.ini_path))
        if self.json_path is not None:
            sources.append(self._load_json(self.json_path))
        if self.yaml_path is not None:
            sources.append(self._load_yaml(self.yaml_path))
        if self.settings is not None:
            sources.append(self._load_settings(self.settings))
        if self.env_prefix is not None:
            sources.append(self._load_env_prefix(self.env_prefix))
        if self.env is not None:
            sources.append(dict(self.env))
        merged: Dict[str, Any] = {}
        for source in sources:
            merged = _merge(merged, source)
        return merged

    # -- tree helpers ----------------------------------------------------

    def _flatten(self, node: Any, prefix: Tuple[str, ...] = ()) -> List[Tuple[str, ...]]:
        if not isinstance(node, dict):
            return []
        paths: List[Tuple[str, ...]] = []
        for key, value in node.items():
            path = (*prefix, str(key))
            paths.append(path)
            paths.extend(self._flatten(value, path))
        return paths

    def _lookup(self, tree: Dict[str, Any], path: Tuple[str, ...]) -> Any:
        node: Any = tree
        for part in path:
            node = node[part]
        return node

    def _resolve_full(self) -> Dict[str, Any]:
        if self.live:
            return cast("Dict[str, Any]", _interpolate(self._data, os.environ))
        return self._resolved

    def _resolve_path(self, path: Tuple[str, ...]) -> Any:
        if self.live:
            root = _interpolate(self._data, os.environ)
            return self._lookup(root, path)
        return self._lookup(self._resolved, path)

    def _read(self) -> None:
        self._data = self._load_sources()
        self._paths = self._flatten(self._data)
        self._resolved = _interpolate(self._data, os.environ)

    def _child_keys(self, name: str) -> Set[str]:
        return {".".join((name, *path)) for path in self._paths}

    # -- Provider interface ---------------------------------------------

    def pre_validate_registration(self, ruleset: RuleSetProtocol, name: str) -> None:
        """Raise :class:`DuplicateKeyError` if a namespaced child key is taken."""
        self._read()
        reserved = self._child_keys(name)
        if ruleset.has(name):
            raise DuplicateKeyError(name)
        for child in reserved:
            if ruleset.has(child):
                raise DuplicateKeyError(child)

    def _rule(self, key: str, make: Any, *, is_async: bool = False) -> Rule:
        return Rule(key, make, "transient", (), is_async=is_async)

    def to_rules(self, name: str) -> List[Rule]:
        """Register a parent rule plus one rule per dotted config path."""
        self.key = name
        self._read()

        def make_parent() -> Any:
            return self._resolve_full()

        rules: List[Rule] = [self._rule(name, make_parent)]

        def make_leaf(path: Tuple[str, ...]) -> Any:
            return lambda: self._resolve_path(path)

        for path in self._paths:
            child_key = ".".join((name, *path))
            rules.append(self._rule(child_key, make_leaf(path)))
        return rules

    def reload(self) -> None:
        """Re-read all sources and rebuild the resolved snapshot."""
        self._read()


class AsyncConfiguration(Configuration):
    """Async variant of :class:`Configuration` resolved via ``aget()``.

    Rules are marked async so they resolve through the async path; a sync
    ``get()`` on one raises the same error as any other async rule.
    """

    def _rule(self, key: str, make: Any, *, is_async: bool = False) -> Rule:
        async def async_make() -> Any:
            return make()

        return Rule(key, async_make, "transient", ())
