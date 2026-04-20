"""Shared fixtures for parser regression tests.

Synthetic data under `synthetic/` is treated as source-of-truth for the parser
layer. Tests pin exact counts and specific entries so that any parser drift
surfaces immediately.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC_DIR = REPO_ROOT / "synthetic"


@pytest.fixture(scope="session")
def synthetic_dir() -> Path:
    assert SYNTHETIC_DIR.is_dir(), f"synthetic/ missing at {SYNTHETIC_DIR}"
    return SYNTHETIC_DIR


@pytest.fixture(scope="session")
def regulation_v1_path(synthetic_dir: Path) -> Path:
    return synthetic_dir / "regulation_v1.md"


@pytest.fixture(scope="session")
def regulation_v2_diff_path(synthetic_dir: Path) -> Path:
    return synthetic_dir / "regulation_v2_diff.md"


@pytest.fixture(scope="session")
def regulation_v3_diff_path(synthetic_dir: Path) -> Path:
    return synthetic_dir / "regulation_v3_diff.md"


@pytest.fixture(scope="session")
def meetings_dir(synthetic_dir: Path) -> Path:
    d = synthetic_dir / "meetings"
    assert d.is_dir()
    return d
