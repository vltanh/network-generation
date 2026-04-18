from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: end-to-end pipeline runs; opt in with `pytest -m slow` or `-m 'slow or not slow'`.",
    )


def pytest_collection_modifyitems(config, items):
    # Skip `slow`-marked tests unless explicitly selected via -m.
    marker_expr = config.getoption("-m")
    if marker_expr and "slow" in marker_expr:
        return
    skip_slow = pytest.mark.skip(reason="needs `-m slow` to run")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)


@pytest.fixture(scope="session")
def repo_root():
    return REPO_ROOT
