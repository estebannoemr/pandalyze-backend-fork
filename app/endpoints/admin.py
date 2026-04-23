"""
Blueprint de administración.

Solo accesible para usuarios con rol "admin" (configurado vía ADMIN_EMAIL).

Endpoints:
- GET    /admin/users           : listar usuarios, filtro ?q=<substring email>
- PATCH  /admin/users/<id>      : actualizar role / teacher_id / class_code
- DELETE /admin/users/<id>      : eliminar usuario (cascada a CSV y resultados)
"""

from functools import wraps

from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import jwt_required, get_jwt_identity
from sqlalchemy import or_

from ..extensions import db
from ..models.user_model import (
    User,
    ROLE_ALUMNO,
    ROLE_DOCENTE,
    ROLE_ADMIN,
)
from ..models.csv_model import CSVData
from ..models.challenge_result_model import ChallengeResult


bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(fn):
    """Decorador: exige JWT válido y rol admin."""

    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        user_id = get_jwt_identity()
        user = User.query.get(int(user_id))
        if user is None or user.role != ROLE_ADMIN:
            return jsonify({"error": "Acceso restringido a administradores."}), 403
        return fn(*args, **kwargs)

    return wrapper


def _serialize_user_row(u):
    completed = ChallengeResult.all_passed_for_user(u.id)
    seen = set()
    unique_passes = []
    for r in completed:
        if r.challenge_id in seen:
            continue
        seen.add(r.challenge_id)
        unique_passes.append(r)

    total_points = sum(r.points_earned for r in unique_passes)

    teacher_email = None
    if u.teacher_id:
        t = User.query.get(u.teacher_id)
        teacher_email = t.email if t else None

    return {
        "id": u.id,
        "email": u.email,
        "role": u.role,
        "class_code": u.class_code,
        "teacher_id": u.teacher_id,
        "teacher_email": teacher_email,
        "total_points": total_points,
        "completed_count": len(unique_passes),
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
    }


@bp.route("/users", methods=["GET"])
@cross_origin()
@admin_required
def list_users():
    q = (request.args.get("q") or "").strip().lower()
    role_filter = (request.args.get("role") or "").strip().lower()

    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(User.email.ilike(like))
    if role_filter in {ROLE_ALUMNO, ROLE_DOCENTE, ROLE_ADMIN}:
        query = query.filter(User.role == role_filter)

    users = query.order_by(User.email.asc()).all()
    return jsonify({"users": [_serialize_user_row(u) for u in users]}), 200


@bp.route("/users/<int:user_id>", methods=["PATCH"])
@cross_origin()
@admin_required
def update_user(user_id):
    user = User.query.get(user_id)
    if user is None:
        return jsonify({"error": "Usuario no encontrado."}), 404

    # Proteger al admin: no permitir auto-degradación.
    current_admin_id = int(get_jwt_identity())
    if user.id == current_admin_id:
        return (
            jsonify({"error": "No podés modificar tu propio usuario admin."}),
            400,
        )

    data = request.get_json(silent=True) or {}

    # --- Cambio de rol ---
    if "role" in data:
        new_role = (data.get("role") or "").strip().lower()
        if new_role not in {ROLE_ALUMNO, ROLE_DOCENTE}:
            return (
                jsonify({"error": "Rol inválido. Solo 'alumno' o 'docente'."}),
                400,
            )
        if new_role != user.role:
            user.role = new_role
            if new_role == ROLE_DOCENTE:
                # Los docentes tienen class_code y no tienen teacher_id.
                if not user.class_code:
                    user.class_code = User.generate_unique_class_code()
                user.teacher_id = None
            else:  # ROLE_ALUMNO
                # Los alumnos no tienen class_code. Limpiamos.
                user.class_code = None
                # Los alumnos que antes eran docentes pierden a sus alumnos:
                # los alumnos asociados quedan sin teacher.
                affected = User.query.filter_by(teacher_id=user.id).all()
                for a in affected:
                    a.teacher_id = None

    # --- Cambio de teacher_id (solo aplica si es alumno) ---
    if "teacher_id" in data:
        if user.role != ROLE_ALUMNO:
            return (
                jsonify(
                    {"error": "Solo los alumnos pueden tener un docente asignado."}
                ),
                400,
            )
        raw = data.get("teacher_id")
        if raw is None or raw == "":
            user.teacher_id = None
        else:
            try:
                tid = int(raw)
            except (TypeError, ValueError):
                return jsonify({"error": "teacher_id inválido."}), 400
            teacher = User.query.get(tid)
            if teacher is None or teacher.role != ROLE_DOCENTE:
                return (
                    jsonify({"error": "El usuario indicado no es un docente."}),
                    400,
                )
            user.teacher_id = teacher.id

    db.session.commit()
    return jsonify({"user": _serialize_user_row(user)}), 200


@bp.route("/users/<int:user_id>", methods=["DELETE"])
@cross_origin()
@admin_required
def delete_user(user_id):
    user = User.query.get(user_id)
    if user is None:
        return jsonify({"error": "Usuario no encontrado."}), 404

    current_admin_id = int(get_jwt_identity())
    if user.id == current_admin_id:
        return jsonify({"error": "No podés eliminarte a vos mismo."}), 400

    # Cascada manual: resultados, CSVs, y desasociar alumnos si era docente.
    ChallengeResult.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    CSVData.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    if user.role == ROLE_DOCENTE:
        students = User.query.filter_by(teacher_id=user.id).all()
        for s in students:
            s.teacher_id = None

    db.session.delete(user)
    db.session.commit()
    return jsonify({"ok": True}), 200
