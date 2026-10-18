"""Analyzer hygiene: Group 1 invariants (issue #151)."""

from pathlib import Path

SRC = Path("src/doppy_di")


def test_providers_has_future_annotations():
    text = (SRC / "providers.py").read_text(encoding="utf-8")
    assert "from __future__ import annotations" in text


def test_no_union_subscript_in_annotations():
    bad = []
    for p in [
        SRC / "providers.py",
        SRC / "container.py",
        SRC / "inject.py",
        SRC / "auto_wiring.py",
    ]:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or "noqa" in line or "runtime alias" in line:
                continue
            if "Union[" in line and "typing.Union" not in line and "str(origin)" not in line:
                # allow runtime alias lines with noqa
                bad.append(f"{p.name}:{i}:{line.strip()}")
    assert not bad, f"Union[ remaining: {bad[:10]}"


def test_nosonar_bare_form():
    bad = []
    for p in list(SRC.rglob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "NOSONAR(" in line:
                bad.append(f"{p}:{i}")
    assert not bad, f"parenthesized NOSONAR: {bad[:10]}"


def test_no_unused_noqa_ruff100():
    # inject.py:222,324 previously had unused noqa directives
    text = (SRC / "inject.py").read_text(encoding="utf-8")
    assert "noqa: S1542" not in text
    assert "noqa: S1172" not in text or "# NOSONAR" in text
