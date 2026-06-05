"""
Blueprint de datasets externos (registry + cache + admin tools).

Endpoints:
- GET /datasets                 → lista el registry (sólo metadata, sin contenido).
- GET /datasets/<key>/preview   → primeras filas parseadas como JSON (docente/admin).
- POST /datasets/<key>/refresh  → invalida la cache del dataset (admin).
- GET /datasets/cache/status    → snapshot del estado de cache (admin).

El contenido CSV crudo de los datasets de los desafíos se sirve por
``/challenges/<id>/download`` — esta API es para inspección/operación.
"""

from functools import wraps
import io

from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import jwt_required, get_jwt_identity
import pandas as pd

from ..models.user_model import User, ROLE_DOCENTE, ROLE_ADMIN
from ..services import dataset_service
from ..services.dataset_service import (
    DatasetNotFound,
    DatasetUnavailable,
)


bp = Blueprint("datasets", __name__)


def _get_user():
    uid = get_jwt_identity()
    return User.query.get(int(uid)) if uid is not None else None


def teacher_or_admin_required(fn):
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        user = _get_user()
        if user is None or user.role not in (ROLE_DOCENTE, ROLE_ADMIN):
            return jsonify({"error": "No autorizado."}), 403
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        user = _get_user()
        if user is None or user.role != ROLE_ADMIN:
            return jsonify({"error": "Sólo admin."}), 403
        return fn(*args, **kwargs)
    return wrapper


@bp.route("/datasets", methods=["GET"])
@cross_origin()
@teacher_or_admin_required
def list_datasets():
    """Devuelve el registry (key → filename, drive_id, description)."""
    try:
        registry = dataset_service.list_datasets()
    except DatasetUnavailable as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"datasets": registry}), 200


@bp.route("/datasets/<key>/preview", methods=["GET"])
@cross_origin()
@teacher_or_admin_required
def preview_dataset(key):
    """Primeras N filas del dataset como JSON (default 10, max 50)."""
    try:
        rows = int(request.args.get("rows", 10))
    except (TypeError, ValueError):
        rows = 10
    rows = max(1, min(50, rows))

    try:
        content = dataset_service.fetch_dataset(key)
    except DatasetNotFound:
        return jsonify({"error": f"Dataset '{key}' no encontrado."}), 404
    except DatasetUnavailable as e:
        return jsonify({"error": str(e)}), 503

    try:
        df = pd.read_csv(io.StringIO(content), nrows=rows)
    except Exception as e:
        return jsonify({"error": f"No se pudo parsear el CSV: {e}"}), 500

    return jsonify({
        "key": key,
        "columns": list(df.columns),
        "rows": df.fillna("").astype(str).to_dict(orient="records"),
        "row_count": len(df),
    }), 200


@bp.route("/datasets/<key>/refresh", methods=["POST"])
@cross_origin()
@admin_required
def refresh_dataset(key):
    """Invalida la cache de un dataset (la próxima lectura lo redescarga)."""
    try:
        dataset_service.get_dataset_meta(key)
    except DatasetNotFound:
        return jsonify({"error": f"Dataset '{key}' no encontrado."}), 404
    dataset_service.invalidate(key)
    return jsonify({"ok": True, "invalidated": key}), 200


@bp.route("/datasets/cache/status", methods=["GET"])
@cross_origin()
@admin_required
def cache_status():
    return jsonify(dataset_service.get_cache_status()), 200
