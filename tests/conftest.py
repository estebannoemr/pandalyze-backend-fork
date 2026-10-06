"""Pytest fixtures y configuración compartida."""

import pandas
import pytest


@pytest.fixture(autouse=True)
def _pandas_display_como_en_la_app():
    """Usa las mismas opciones de impresión que /runPythonCode.

    El endpoint real imprime los DataFrames sin truncar columnas. Sin esto,
    pandas recorta la salida con "..." y los tests de desafíos cuyo resultado
    esperado está en una columna del medio fallan aunque en la app aprueben.
    """
    with pandas.option_context(
        "display.max_columns", None,
        "display.max_colwidth", 20,
        "display.colheader_justify", "center",
        "display.width", 9999,
    ):
        yield


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
