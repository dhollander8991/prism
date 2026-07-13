"""
Tests that main.py's CORS configuration is driven by the CORS_ALLOW_ORIGINS
environment variable, not hardcoded.

Design notes
------------
- main.py reads CORS_ALLOW_ORIGINS at *module import time*, so each scenario
  requires a full module reload after mutating os.environ.
- To keep the test hermetic we remove every cached module in the `main`,
  `api.*` sub-tree before each reimport so Python doesn't reuse a previously
  compiled module object from a prior scenario.
- DATABASE_URL must be set (to anything) or database.py raises on import;
  the value does not need to point at a live server.
- No real network calls are made; the CORS middleware is configured at import
  time without any I/O.
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Generator

import pytest
from starlette.middleware.cors import CORSMiddleware


# ---------------------------------------------------------------------------
# Module reload helper
# ---------------------------------------------------------------------------

_MODULES_TO_PURGE = [
    "main",
    "api.connectors",
    "api.pipeline",
    "api.admin",
    "api.insights",
]


def _purge_main_modules() -> None:
    """Remove main and its imported api.* modules from sys.modules so that
    the next `import main` executes the module body from scratch, picking up
    the current os.environ state."""
    for name in list(sys.modules):
        if name == "main" or name.startswith("api."):
            del sys.modules[name]


def _import_main_fresh():
    """Purge cached modules and import main, returning the module object."""
    _purge_main_modules()
    import main as m
    return m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def ensure_database_url() -> Generator[None, None, None]:
    """Guarantee DATABASE_URL is set for every test in this module.

    main.py imports api.pipeline → agents.graph → db.database, which reads
    DATABASE_URL at import time.  A dummy value is fine — no connection is made
    during the CORS-configuration path under test.
    """
    original = os.environ.get("DATABASE_URL")
    os.environ.setdefault("DATABASE_URL", "postgresql://prism:prism@localhost:5433/prism")
    yield
    # Restore previous state to avoid polluting other test modules.
    if original is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = original
    # Always clean up after each test so the next one starts with a blank slate.
    _purge_main_modules()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_cors_defaults_to_wildcard_when_env_unset() -> None:
    """With CORS_ALLOW_ORIGINS absent from the environment, allow_origins must
    be ["*"] — the safe-for-development default the CDK stack also injects."""
    os.environ.pop("CORS_ALLOW_ORIGINS", None)

    m = _import_main_fresh()

    cors_middleware = _find_cors_middleware(m)
    assert cors_middleware is not None, "CORSMiddleware not found in app.user_middleware"
    assert cors_middleware.kwargs["allow_origins"] == ["*"], (
        f"Expected ['*'] but got {cors_middleware.kwargs['allow_origins']}"
    )


def test_cors_uses_single_origin_from_env() -> None:
    """A single URL in CORS_ALLOW_ORIGINS must become a one-element list."""
    os.environ["CORS_ALLOW_ORIGINS"] = "https://prism.vercel.app"

    m = _import_main_fresh()

    cors_middleware = _find_cors_middleware(m)
    assert cors_middleware is not None
    origins = cors_middleware.kwargs["allow_origins"]
    assert origins == ["https://prism.vercel.app"], (
        f"Expected ['https://prism.vercel.app'] but got {origins}"
    )


def test_cors_parses_comma_separated_origins() -> None:
    """Two comma-separated URLs must become a two-element list (the Vercel
    preview + production scenario)."""
    os.environ["CORS_ALLOW_ORIGINS"] = "https://a.com,https://b.com"

    m = _import_main_fresh()

    cors_middleware = _find_cors_middleware(m)
    assert cors_middleware is not None
    origins = cors_middleware.kwargs["allow_origins"]
    assert origins == ["https://a.com", "https://b.com"], (
        f"Expected two origins but got {origins}"
    )


def test_cors_strips_whitespace_around_commas() -> None:
    """Spaces around commas (e.g. from copy-pasting) must be stripped."""
    os.environ["CORS_ALLOW_ORIGINS"] = "  https://a.com , https://b.com  "

    m = _import_main_fresh()

    cors_middleware = _find_cors_middleware(m)
    assert cors_middleware is not None
    origins = cors_middleware.kwargs["allow_origins"]
    assert origins == ["https://a.com", "https://b.com"], (
        f"Whitespace not stripped: {origins}"
    )


def test_cors_ignores_empty_segments() -> None:
    """Trailing commas or double commas must not produce empty-string origins."""
    os.environ["CORS_ALLOW_ORIGINS"] = "https://a.com,,https://b.com,"

    m = _import_main_fresh()

    cors_middleware = _find_cors_middleware(m)
    assert cors_middleware is not None
    origins = cors_middleware.kwargs["allow_origins"]
    # Empty strings must be filtered out.
    assert "" not in origins
    assert "https://a.com" in origins
    assert "https://b.com" in origins


def test_cors_middleware_is_present_exactly_once() -> None:
    """Exactly one CORSMiddleware must be registered — not zero, not two."""
    os.environ.pop("CORS_ALLOW_ORIGINS", None)

    m = _import_main_fresh()

    cors_entries = [
        mw for mw in m.app.user_middleware
        if mw.cls is CORSMiddleware
    ]
    assert len(cors_entries) == 1, (
        f"Expected exactly 1 CORSMiddleware entry, found {len(cors_entries)}"
    )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _find_cors_middleware(m) -> "starlette.middleware.Middleware | None":  # type: ignore[name-defined]
    """Return the first Middleware entry whose cls is CORSMiddleware, or None."""
    for mw in m.app.user_middleware:
        if mw.cls is CORSMiddleware:
            return mw
    return None
