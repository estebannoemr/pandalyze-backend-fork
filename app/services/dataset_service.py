"""
Dataset service: resuelve CSVs de los desafíos por ``dataset_key``.

Source of truth: ``app/data/datasets.json`` (registry de 8 datasets con drive_id).
El servicio:
1) Cachea en memoria por TTL (default 24h, configurable por env DATASET_CACHE_TTL).
2) Hace de proxy a Google Drive — evita CORS en el browser y centraliza el manejo
   de confirm-token para archivos grandes.
3) Fallback local: si la env var ``LOCAL_DATASETS_DIR`` apunta a una carpeta con
   los CSVs, los lee del disco. Útil para dev/CI sin red.
4) Errores de red → ``DatasetUnavailable``, los endpoints lo traducen a 503.

Uso típico:

    from app.services.dataset_service import fetch_dataset, get_dataset_meta

    content = fetch_dataset("student_performance")  # str CSV
    meta = get_dataset_meta("student_performance")  # {filename, drive_id, ...}
"""

import io
import json
import os
import re
import threading
import time
from pathlib import Path

import pandas as pd
import requests


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_REGISTRY_PATH = _DATA_DIR / "datasets.json"

_DEFAULT_TTL = 60 * 60 * 24  # 24h
_DRIVE_BASE = "https://drive.google.com/uc"
_USER_AGENT = "Pandalyze/1.0 (dataset-service)"
_TIMEOUT = 20  # seg


class DatasetUnavailable(RuntimeError):
    """Indica que un dataset no pudo resolverse (red caída, drive_id roto, etc.)."""


class DatasetNotFound(KeyError):
    """Indica que el ``dataset_key`` no está en el registry."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_registry_cache = {"data": None, "mtime": 0.0}
_registry_lock = threading.Lock()


def _load_registry():
    """Lee datasets.json una vez (recarga si cambió el archivo en disco)."""
    with _registry_lock:
        try:
            mtime = _REGISTRY_PATH.stat().st_mtime
        except OSError:
            raise DatasetUnavailable(f"No existe el registry: {_REGISTRY_PATH}")

        if _registry_cache["data"] is None or _registry_cache["mtime"] != mtime:
            with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
                _registry_cache["data"] = json.load(f)
            _registry_cache["mtime"] = mtime
        return _registry_cache["data"]


def list_datasets():
    """Devuelve el registry completo (dict por key) sin contenido del CSV."""
    return dict(_load_registry())


def get_dataset_meta(key):
    """Devuelve metadata del dataset (filename, drive_id, description)."""
    registry = _load_registry()
    if key not in registry:
        raise DatasetNotFound(key)
    return dict(registry[key])


# ---------------------------------------------------------------------------
# Cache de contenidos
# ---------------------------------------------------------------------------

_content_cache = {}  # key -> {"content": str, "expires_at": float, "fetched_at": float, "source": str}
_content_lock = threading.Lock()
_cache_stats = {"hits": 0, "misses": 0, "errors": 0}


def _ttl_seconds():
    raw = os.getenv("DATASET_CACHE_TTL")
    if raw:
        try:
            return max(60, int(raw))
        except ValueError:
            pass
    return _DEFAULT_TTL


def _local_path_for(filename):
    """Resuelve un archivo del fallback local.

    Orden de búsqueda:
    1. ``LOCAL_DATASETS_DIR/<filename>`` si la env var está seteada.
    2. ``<backend_root>/datasets/<filename>`` (default zero-config: sibling
       de ``app/``). Útil para que el operador suba los CSVs al deploy sin
       tocar variables de entorno.

    Devuelve None si ninguno existe (cae a Drive).
    """
    base = (os.getenv("LOCAL_DATASETS_DIR") or "").strip()
    candidates = []
    if base:
        candidates.append(Path(base) / filename)
    # Default: <backend_root>/datasets/<filename>
    # __file__ = .../app/services/dataset_service.py → backend_root = parent×3
    backend_root = Path(__file__).resolve().parent.parent.parent
    candidates.append(backend_root / "datasets" / filename)

    for p in candidates:
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Drive fetch
# ---------------------------------------------------------------------------

def _looks_like_html(content):
    sample = (content or "").lstrip()[:300].lower()
    return sample.startswith("<!doctype html") or sample.startswith("<html")


_CONFIRM_TOKEN_RE = re.compile(r'confirm=([0-9A-Za-z_\-]+)')


def _drive_download(drive_id):
    """Descarga un CSV de Drive público. Maneja confirm-token para archivos >100MB."""
    session = requests.Session()
    headers = {"User-Agent": _USER_AGENT}

    # Primera tentativa: descarga directa.
    resp = session.get(
        _DRIVE_BASE,
        params={"export": "download", "id": drive_id},
        headers=headers,
        timeout=_TIMEOUT,
        allow_redirects=True,
    )
    resp.raise_for_status()

    # Si Drive devolvió HTML con confirm-token, reintentar con el token.
    content_type = resp.headers.get("Content-Type", "")
    if "text/html" in content_type or _looks_like_html(resp.text):
        token = None
        # Token en cookies (camino tradicional).
        for k, v in resp.cookies.items():
            if k.startswith("download_warning"):
                token = v
                break
        # Token en el body (camino nuevo).
        if not token:
            m = _CONFIRM_TOKEN_RE.search(resp.text or "")
            if m:
                token = m.group(1)
        if token:
            resp = session.get(
                _DRIVE_BASE,
                params={"export": "download", "id": drive_id, "confirm": token},
                headers=headers,
                timeout=_TIMEOUT,
                allow_redirects=True,
            )
            resp.raise_for_status()

    raw = resp.content
    # Decode robusto: utf-8 con BOM (estaciones_ffcc tiene ﻿), fallback latin-1.
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")

    if _looks_like_html(text):
        raise DatasetUnavailable(
            "Drive devolvió HTML en vez de CSV (revisar permisos públicos del archivo)."
        )
    # Sanity check: que parsee al menos la primera fila como CSV.
    try:
        pd.read_csv(io.StringIO(text), nrows=1)
    except Exception as exc:
        raise DatasetUnavailable(f"El contenido descargado no es un CSV válido: {exc}")
    return text


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def fetch_dataset(key, force_refresh=False):
    """
    Devuelve el contenido CSV del dataset como string.

    Orden de resolución:
    1) Cache en memoria si no expiró (a menos que ``force_refresh=True``).
    2) Local fallback si ``LOCAL_DATASETS_DIR`` está seteado y el archivo existe.
    3) Drive download via _DRIVE_BASE.
    """
    meta = get_dataset_meta(key)
    now = time.time()

    if not force_refresh:
        with _content_lock:
            entry = _content_cache.get(key)
            if entry and entry["expires_at"] > now:
                _cache_stats["hits"] += 1
                return entry["content"]

    _cache_stats["misses"] += 1

    # Local fallback primero (más rápido, no toca red).
    local = _local_path_for(meta["filename"])
    source = None
    content = None
    if local is not None:
        try:
            content = local.read_text(encoding="utf-8-sig")
            source = f"local:{local}"
        except Exception:
            content = None

    if content is None:
        drive_id = meta.get("drive_id")
        if not drive_id:
            _cache_stats["errors"] += 1
            raise DatasetUnavailable(f"Dataset '{key}' no tiene drive_id ni copia local.")
        try:
            content = _drive_download(drive_id)
            source = f"drive:{drive_id}"
        except requests.RequestException as exc:
            _cache_stats["errors"] += 1
            raise DatasetUnavailable(f"Fallo al descargar '{key}' de Drive: {exc}")
        except DatasetUnavailable:
            _cache_stats["errors"] += 1
            raise

    ttl = _ttl_seconds()
    with _content_lock:
        _content_cache[key] = {
            "content": content,
            "expires_at": now + ttl,
            "fetched_at": now,
            "source": source,
        }
    return content


def get_cache_status():
    """Snapshot del estado de cache (para endpoint admin de datasets)."""
    now = time.time()
    with _content_lock:
        entries = {}
        for k, v in _content_cache.items():
            entries[k] = {
                "fetched_at": v["fetched_at"],
                "expires_at": v["expires_at"],
                "expired": v["expires_at"] <= now,
                "source": v["source"],
                "size_bytes": len(v["content"].encode("utf-8")),
            }
        return {
            "entries": entries,
            "stats": dict(_cache_stats),
            "ttl_seconds": _ttl_seconds(),
        }


def invalidate(key=None):
    """Invalida una entrada (o todas si key=None). Útil para tests / admin."""
    with _content_lock:
        if key is None:
            _content_cache.clear()
        else:
            _content_cache.pop(key, None)


# ---------------------------------------------------------------------------
# Warm-up
# ---------------------------------------------------------------------------

def warmup_in_background(logger=None):
    """
    Dispara una corrida de ``fetch_dataset`` para cada key del registry en un
    thread daemon. El boot del servidor no se bloquea: si Drive está lento,
    el primer alumno que arranque un desafío puede caer en el path miss y
    pagar la latencia, pero la mayoría ya encontrará la cache caliente.

    Controlado por la env var ``DATASET_WARMUP_ON_BOOT`` — sólo corre si vale
    "1", "true", "yes", "on" o "si".
    """
    raw = (os.getenv("DATASET_WARMUP_ON_BOOT") or "").strip().lower()
    if raw not in ("1", "true", "yes", "on", "si"):
        return

    def _runner():
        try:
            registry = list_datasets()
        except Exception as exc:
            if logger:
                logger.warning("dataset warmup: registry load falló: %s", exc)
            return
        for key in registry:
            try:
                fetch_dataset(key)
                if logger:
                    logger.info("dataset warmup OK: %s", key)
            except Exception as exc:
                if logger:
                    logger.warning("dataset warmup FAIL %s: %s", key, exc)

    t = threading.Thread(
        target=_runner, name="dataset-warmup", daemon=True
    )
    t.start()
    return t
