"""
Blueprint de autenticación.

Endpoints:
- POST /auth/register : crea un usuario alumno. Opcionalmente acepta class_code.
- POST /auth/login    : devuelve un access_token JWT.
- GET  /auth/me       : datos del usuario autenticado.

Rol "docente" solo puede ser asignado por un admin.
Rol "admin" se asigna automáticamente al email configurado en ADMIN_EMAIL.
"""

import re

from flask import Blueprint, request, jsonify, current_app
from flask_cors import cross_origin
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity

from ..extensions import db
from ..models.user_model import User, ROLE_ALUMNO, ROLE_ADMIN


bp = Blueprint("auth", __name__, url_prefix="/auth")


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 6


def _json():
    return request.get_json(silent=True) or {}


def _promote_if_admin_email(user):
    admin_email = (current_app.config.get("ADMIN_EMAIL") or "").strip().lower()
    if admin_email and user.email == admin_email and user.role != ROLE_ADMIN:
        user.role = ROLE_ADMIN
        user.teacher_id = None
        user.class_code = None
        return True
    return False


@bp.route("/register", methods=["POST"])
@cross_origin()
def register():
    data = _json()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    class_code = (data.get("class_code") or "").strip().upper()

    if not EMAIL_RE.match(email):
        return jsonify({"error": "Email inválido."}), 400
    if len(password) < MIN_PASSWORD_LEN:
        return (
            jsonify({
                "error": "La contraseña debe tener al menos %d caracteres." % MIN_PASSWORD_LEN
            }),
            400,
        )

    if User.get_by_email(email) is not None:
        return jsonify({"error": "Ya existe un usuario con ese email."}), 409

    teacher = None
    if class_code:
        teacher = User.get_by_class_code(class_code)
        if teacher is None:
            return jsonify({"error": "Código de clase inválido."}), 400

    user = User(email=email, role=ROLE_ALUMNO)
    user.set_password(password)
    if teacher is not None:
        user.teacher_id = teacher.id

    _promote_if_admin_email(user)

    db.session.add(user)
    db.session.commit()

    access_token = create_access_token(identity=str(user.id))
    return (
        jsonify({"access_token": access_token, "user": user.to_dict()}),
        201,
    )


@bp.route("/login", methods=["POST"])
@cross_origin()
def login():
    data = _json()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = User.get_by_email(email)
    if user is None or not user.check_password(password):
        return jsonify({"error": "Email o contraseña incorrectos."}), 401

    user.touch_last_seen()
    _promote_if_admin_email(user)
    db.session.commit()

    access_token = create_access_token(identity=str(user.id))
    return jsonify({"access_token": access_token, "user": user.to_dict()}), 200


@bp.route("/me", methods=["GET"])
@cross_origin()
@jwt_required()
def me():
    user_id = get_jwt_identity()
    user = User.query.get(int(user_id))
    if user is None:
        return jsonify({"error": "Usuario no encontrado."}), 404
    return jsonify({"user": user.to_dict()}), 200
