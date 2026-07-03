"""Nox sessions for local and CI checks."""

from __future__ import annotations

import sys

import nox

nox.options.sessions = ["lint", "typecheck", "tests"]


def uv(session: nox.Session, *args: str) -> None:
    """Run uv through the active Python executable."""
    session.run(sys.executable, "-m", "uv", *args, external=True)


@nox.session(python=False)
def lint(session: nox.Session) -> None:
    """Run Ruff format check and lint."""
    uv(session, "run", "ruff", "format", "--check", ".")
    uv(session, "run", "ruff", "check", ".")


@nox.session(python=False)
def typecheck(session: nox.Session) -> None:
    """Run mypy in strict mode."""
    uv(session, "run", "mypy")


@nox.session(python=False)
def tests(session: nox.Session) -> None:
    """Run tests in the current uv environment."""
    uv(session, "run", "pytest")
