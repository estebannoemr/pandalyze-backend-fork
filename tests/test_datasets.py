"""
Tests del registry de datasets + dataset_service.

Cubre:
- Registry: carga datasets.json y expone metadata.
- Fallback local (LOCAL_DATASETS_DIR): lee del disco sin tocar la red.
- Cache TTL: una segunda lectura no re-fetcha.
- Errores: dataset_key inválido → DatasetNotFound.

Los tests que requieren red están marcados con @pytest.mark.network y se
skipean por defecto (correr con ``pytest -m network`` para incluirlos).
"""

import io
import os
from pathlib import Path

import pandas as pd
import pytest

from app.services import dataset_service
from app.services.dataset_service import (
    DatasetNotFound,
    DatasetUnavailable,
)


# Path al Z-DATASETS del repo (afuera del backend). Lo usamos como
# LOCAL_DATASETS_DIR para los tests offline.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_Z_DATASETS = _REPO_ROOT / "Z-DATASETS"


@pytest.fixture(autouse=True)
def _clean_cache():
    """Limpia cache antes y después de cada test."""
    dataset_service.invalidate()
    yield
    dataset_service.invalidate()


@pytest.fixture
def local_datasets(monkeypatch):
    """Apunta LOCAL_DATASETS_DIR a Z-DATASETS si existe."""
    if not _Z_DATASETS.exists():
        pytest.skip(f"Z-DATASETS no disponible en {_Z_DATASETS}")
    monkeypatch.setenv("LOCAL_DATASETS_DIR", str(_Z_DATASETS))
    yield _Z_DATASETS


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_loads():
    registry = dataset_service.list_datasets()
    assert isinstance(registry, dict)
    assert len(registry) >= 8
    # Keys esperadas mínimas
    expected_keys = {
        "ar_airports",
        "area_protegida",
        "establecimientos",
        "estaciones_ffcc",
        "felinos",
        "internet_localidades",
        "student_performance",
        "sube_laplata",
    }
    assert expected_keys.issubset(set(registry.keys()))


def test_registry_meta_has_required_fields():
    registry = dataset_service.list_datasets()
    for key, meta in registry.items():
        assert "filename" in meta and meta["filename"], f"{key} sin filename"
        assert "drive_id" in meta and meta["drive_id"], f"{key} sin drive_id"
        assert meta["filename"].lower().endswith(".csv")


def test_get_dataset_meta_known():
    meta = dataset_service.get_dataset_meta("student_performance")
    assert meta["filename"] == "Student_Performance.csv"
    assert meta["drive_id"]


def test_get_dataset_meta_unknown():
    with pytest.raises(DatasetNotFound):
        dataset_service.get_dataset_meta("no_existe_xyz")


# ---------------------------------------------------------------------------
# Fallback local
# ---------------------------------------------------------------------------

def test_fetch_uses_local_when_env_set(local_datasets):
    """Con LOCAL_DATASETS_DIR seteado y archivo presente, no toca red."""
    content = dataset_service.fetch_dataset("student_performance")
    assert isinstance(content, str)
    assert content.strip().startswith("student_id")
    # Debería poder parsearse.
    df = pd.read_csv(io.StringIO(content), nrows=5)
    assert "math_score" in df.columns


def test_fetch_local_for_all_keys(local_datasets):
    """Todos los datasets se resuelven contra Z-DATASETS local."""
    registry = dataset_service.list_datasets()
    for key in registry:
        content = dataset_service.fetch_dataset(key)
        assert len(content) > 0, f"{key} devolvió vacío"
        # Sanity parse: primera fila como CSV.
        pd.read_csv(io.StringIO(content), nrows=1)
        dataset_service.invalidate(key)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_cache_hit_does_not_refetch(local_datasets, monkeypatch):
    """Segunda llamada usa cache, no toca el disco."""
    key = "student_performance"
    first = dataset_service.fetch_dataset(key)

    # Apuntamos LOCAL_DATASETS_DIR a un dir vacío: si la cache funciona,
    # la segunda lectura sigue devolviendo el contenido viejo.
    monkeypatch.setenv("LOCAL_DATASETS_DIR", "/tmp/no_existe_pandalyze")
    second = dataset_service.fetch_dataset(key)
    assert first == second


def test_force_refresh_bypasses_cache(local_datasets):
    key = "felinos"
    first = dataset_service.fetch_dataset(key)
    # force_refresh debería re-leer (mismo contenido, pero pasa por el path).
    second = dataset_service.fetch_dataset(key, force_refresh=True)
    assert first == second


def test_invalidate_clears_cache(local_datasets):
    key = "ar_airports"
    dataset_service.fetch_dataset(key)
    status_before = dataset_service.get_cache_status()
    assert key in status_before["entries"]

    dataset_service.invalidate(key)
    status_after = dataset_service.get_cache_status()
    assert key not in status_after["entries"]


def test_invalidate_all(local_datasets):
    dataset_service.fetch_dataset("ar_airports")
    dataset_service.fetch_dataset("felinos")
    assert len(dataset_service.get_cache_status()["entries"]) == 2
    dataset_service.invalidate()
    assert len(dataset_service.get_cache_status()["entries"]) == 0


def test_cache_status_shape(local_datasets):
    dataset_service.fetch_dataset("sube_laplata")
    status = dataset_service.get_cache_status()
    assert "entries" in status
    assert "stats" in status
    assert "ttl_seconds" in status
    entry = status["entries"]["sube_laplata"]
    assert entry["size_bytes"] > 0
    assert entry["source"].startswith(("local:", "drive:"))


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------

def test_fetch_unknown_key_raises():
    with pytest.raises(DatasetNotFound):
        dataset_service.fetch_dataset("no_existe_xyz")


# ---------------------------------------------------------------------------
# Red (opcional)
# ---------------------------------------------------------------------------

@pytest.mark.network
def test_fetch_from_drive_real(monkeypatch):
    """Smoke test contra Drive real. Requiere internet y archivo público."""
    monkeypatch.delenv("LOCAL_DATASETS_DIR", raising=False)
    content = dataset_service.fetch_dataset("ar_airports")
    assert content
    df = pd.read_csv(io.StringIO(content), nrows=5)
    assert "name" in df.columns
