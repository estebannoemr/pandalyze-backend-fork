"""
Blueprint de Clases (gestión por docente).

Endpoints:
- POST    /classes                    Crea una clase (docente).
- GET     /classes                    Lista clases del usuario (docente: propias; admin: todas).
- GET     /classes/<id>               Detalle.
- PATCH   /classes/<id>               Edita name / selected_challenge_ids / regenera class_code.
- DELETE  /classes/<id>               Elimina la clase y desasocia a sus alumnos (no los borra).
- GET     /classes/<id>/students      Lista de alumnos asociados.

Reglas:
- El docente sólo puede ver/operar sobre clases con teacher_id == self.
- El admin puede sobre cualquiera.
- ``selected_challenge_ids`` se valida contra el banco real (CHALLENGES).
- El ``class_code`` se genera al crear y se puede regenerar pasando
  ``regenerate_code: true`` en el PATCH (útil para rotar códigos por
  cuatrimestre).
"""

from functools import wraps

from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
from flask_jwt_extended import jwt_required, get_jwt_identity

from ..extensions import db
from ..models.class_model import Class
from ..models.user_model import (
    User,
    ROLE_ALUMNO,
    ROLE_DOCENTE,
    ROLE_ADMIN,
)
from .challenges import CHALLENGES


bp = Blueprint("classes", __name__, url_prefix="/classes")


def _json():
    return request.get_json(silent=True) or {}


def _current_user():
    uid = get_jwt_identity()
    if uid is None:
        return None
    try:
        return User.query.get(int(uid))
    except (TypeError, ValueError):
        return None


def teacher_or_admin_required(f):
    """Sólo docentes y admin pueden tocar este blueprint."""

    @wraps(f)
    @jwt_required()
    def wrapper(*args, **kwargs):
        user = _current_user()
        if user is None or user.role not in (ROLE_DOCENTE, ROLE_ADMIN):
            return jsonify({"error": "No autorizado."}), 403
        request._pandalyze_user = user  # cache
        return f(*args, **kwargs)

    return wrapper


def _can_manage(user, klass):
    """El admin maneja todo; el docente sólo si es dueño de la clase."""
    if user.role == ROLE_ADMIN:
        return True
    return user.role == ROLE_DOCENTE and klass.teacher_id == user.id


def _valid_challenge_ids():
    return {c["id"] for c in CHALLENGES}


def _filter_valid_ids(raw_list):
    """Devuelve sólo los IDs que existen en el banco actual."""
    valid = _valid_challenge_ids()
    out = []
    seen = set()
    for x in raw_list or []:
        try:
            v = int(x)
        except (ValueError, TypeError):
            continue
        if v in valid and v not in seen:
            seen.add(v)
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@bp.route("", methods=["POST"])
@cross_origin()
@teacher_or_admin_required
def create_class():
    user = request._pandalyze_user
    data = _json()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "El nombre de la clase es obligatorio."}), 400
    if len(name) > 120:
        return jsonify({"error": "El nombre es demasiado largo (máx 120)."}), 400

    raw_ids = data.get("selected_challenge_ids")
    if raw_ids is None:
        # Compatibilidad: 'select_all' como atajo.
        if data.get("select_all"):
            ids = sorted(_valid_challenge_ids())
        else:
            ids = []
    else:
        ids = _filter_valid_ids(raw_ids)

    # El admin puede asignar la clase a un docente específico vía teacher_id;
    # el docente sólo puede crear clases para sí mismo.
    if user.role == ROLE_ADMIN:
        teacher_id = data.get("teacher_id") or user.id
        try:
            teacher_id = int(teacher_id)
        except (ValueError, TypeError):
            return jsonify({"error": "teacher_id inválido."}), 400
        teacher = User.query.get(teacher_id)
        if teacher is None or teacher.role != ROLE_DOCENTE:
            return jsonify({"error": "El teacher_id no corresponde a un docente."}), 400
    else:
        teacher_id = user.id

    klass = Class(
        teacher_id=teacher_id,
        name=name,
        class_code=Class.generate_unique_class_code(),
    )
    klass.set_selected_ids(ids)
    db.session.add(klass)
    db.session.commit()
    return jsonify({"class": klass.to_dict(include_students_count=True)}), 201


@bp.route("", methods=["GET"])
@cross_origin()
@teacher_or_admin_required
def list_classes():
    user = request._pandalyze_user
    q = Class.query
    if user.role == ROLE_DOCENTE:
        q = q.filter_by(teacher_id=user.id)
    classes = q.order_by(Class.created_at.desc()).all()
    return (
        jsonify(
            {"classes": [c.to_dict(include_students_count=True) for c in classes]}
        ),
        200,
    )


@bp.route("/<int:class_id>", methods=["GET"])
@cross_origin()
@teacher_or_admin_required
def get_class(class_id):
    user = request._pandalyze_user
    klass = Class.query.get(class_id)
    if klass is None:
        return jsonify({"error": "Clase no encontrada."}), 404
    if not _can_manage(user, klass):
        return jsonify({"error": "No autorizado para ver esta clase."}), 403
    return jsonify({"class": klass.to_dict(include_students_count=True)}), 200


@bp.route("/<int:class_id>", methods=["PATCH"])
@cross_origin()
@teacher_or_admin_required
def update_class(class_id):
    user = request._pandalyze_user
    klass = Class.query.get(class_id)
    if klass is None:
        return jsonify({"error": "Clase no encontrada."}), 404
    if not _can_manage(user, klass):
        return jsonify({"error": "No autorizado."}), 403

    data = _json()
    changed = []

    if "name" in data:
        new_name = (data.get("name") or "").strip()
        if not new_name:
            return jsonify({"error": "Nombre vacío."}), 400
        if len(new_name) > 120:
            return jsonify({"error": "Nombre demasiado largo."}), 400
        klass.name = new_name
        changed.append("name")

    if "selected_challenge_ids" in data or data.get("select_all"):
        if data.get("select_all"):
            ids = sorted(_valid_challenge_ids())
        else:
            ids = _filter_valid_ids(data.get("selected_challenge_ids"))
        klass.set_selected_ids(ids)
        changed.append("selected_challenge_ids")

    if data.get("regenerate_code"):
        klass.class_code = Class.generate_unique_class_code()
        changed.append("class_code")

    if not changed:
        return jsonify({"class": klass.to_dict(include_students_count=True), "changed": []}), 200

    db.session.commit()
    return (
        jsonify(
            {"class": klass.to_dict(include_students_count=True), "changed": changed}
        ),
        200,
    )


@bp.route("/<int:class_id>", methods=["DELETE"])
@cross_origin()
@teacher_or_admin_required
def delete_class(class_id):
    user = request._pandalyze_user
    klass = Class.query.get(class_id)
    if klass is None:
        return jsonify({"error": "Clase no encontrada."}), 404
    if not _can_manage(user, klass):
        return jsonify({"error": "No autorizado."}), 403

    # Desasociamos alumnos: NO los borramos. Quedan sin clase pero conservan
    # sus resultados, su email y su acceso a la app.
    User.query.filter_by(class_id=klass.id).update({"class_id": None})
    db.session.delete(klass)
    db.session.commit()
    return jsonify({"ok": True}), 200


@bp.route("/<int:class_id>/students", methods=["GET"])
@cross_origin()
@teacher_or_admin_required
def list_students_of_class(class_id):
    user = request._pandalyze_user
    klass = Class.query.get(class_id)
    if klass is None:
        return jsonify({"error": "Clase no encontrada."}), 404
    if not _can_manage(user, klass):
        return jsonify({"error": "No autorizado."}), 403
    students = (
        User.query.filter_by(class_id=klass.id, role=ROLE_ALUMNO)
        .order_by(User.email.asc())
        .all()
    )
    return jsonify({"students": [s.to_dict() for s in students]}), 200
