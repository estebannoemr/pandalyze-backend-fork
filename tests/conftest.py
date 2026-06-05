"""Pytest fixtures y configuración compartida."""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "network: tests que requieren acceso a internet (Drive, etc.)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip tests marcados 'network' a menos que se pase -m network."""
    if config.getoption("-m") and "network" in config.getoption("-m"):
        return
    skip_network = pytest.mark.skip(reason="requiere red; correr con -m network")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)
