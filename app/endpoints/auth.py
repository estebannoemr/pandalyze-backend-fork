"""
Blueprint de autenticación.

Endpoints:
- POST  /auth/register         : crea un usuario alumno. Opcionalmente class_code.
- POST  /auth/login            : devuelve un access_token JWT (rate-limited).
- GET   /auth/me               : datos del usuario autenticado.
- PATCH /auth/me               : edición de perfil (cambio de password / class_code).
- POST  /auth/forgot-password  : genera token de reset y lo envía por mail.
- POST  /auth/reset-password   : consume token y setea nueva password.

Rol "docente" solo puede ser asignado por un admin.
Rol "admin" se asigna automáticamente al email configurado en ADMIN_EMAIL.
"""

import re

from flask import Blueprint, request, jsonify, current_app
from flask_cors import cross_origin
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity

from ..extensions import db, limiter
from ..models.user_model import User, ROLE_ALUMNO, ROLE_DOCENTE, ROLE_ADMIN
from ..models.password_reset_token_model import PasswordResetToken
from ..services.email_service import send_email


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
@limiter.limit("10 per minute; 50 per hour")
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


@bp.route("/me", methods=["PATCH"])
@cross_origin()
@jwt_required()
def update_me():
    """
    Edición de perfil del usuario autenticado.

    Acepta cambios en password (requiere ``current_password``) y en
    asociación con docente vía ``class_code`` (solo aplica a alumnos).

    Cualquier otro campo del payload se ignora silenciosamente.
    """
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    if user is None:
        return jsonify({"error": "Usuario no encontrado."}), 404

    data = _json()
    new_password = data.get("new_password")
    current_password = data.get("current_password")
    class_code_input = data.get("class_code")

    changed = []

    # ---- Password ----
    if new_password is not None and new_password != "":
        if not current_password:
            return (
                jsonify({
                    "error": "Debés enviar tu contraseña actual para cambiar la contraseña."
                }),
                400,
            )
        if not user.check_password(current_password):
            return jsonify({"error": "La contraseña actual es incorrecta."}), 401
        if len(new_password) < MIN_PASSWORD_LEN:
            return (
                jsonify({
                    "error": "La nueva contraseña debe tener al menos %d caracteres."
                    % MIN_PASSWORD_LEN
                }),
                400,
            )
        user.set_password(new_password)
        changed.append("password")

    # ---- Asociación a docente vía class_code ----
    # Solo permitido para alumnos. Pasar class_code="" desasocia.
    if class_code_input is not None:
        if user.role != ROLE_ALUMNO:
            return (
                jsonify({
                    "error": "Solo los alumnos pueden asociarse a un docente vía class_code."
                }),
                400,
            )
        normalized = (class_code_input or "").strip().upper()
        if normalized == "":
            user.teacher_id = None
            changed.append("teacher_id")
        else:
            teacher = User.get_by_class_code(normalized)
            if teacher is None:
                return jsonify({"error": "Código de clase inválido."}), 400
            user.teacher_id = teacher.id
            changed.append("teacher_id")

    if not changed:
        return jsonify({"user": user.to_dict(), "changed": []}), 200

    db.session.commit()
    return jsonify({"user": user.to_dict(), "changed": changed}), 200


@bp.route("/forgot-password", methods=["POST"])
@cross_origin()
@limiter.limit("5 per minute; 20 per hour")
def forgot_password():
    """
    Genera un token de reset y lo envía al email del usuario.

    Por seguridad respondemos siempre 200 con el mismo mensaje, exista o
    no el email — esto evita usar el endpoint como oráculo de existencia
    de cuentas. Si el SMTP está configurado se manda mail; si no, el
    token aparece en los logs.
    """
    data = _json()
    email = (data.get("email") or "").strip().lower()
    generic_response = (
        jsonify({
            "ok": True,
            "message": (
                "Si el email existe, vas a recibir un enlace para restablecer tu contraseña."
            ),
        }),
        200,
    )

    if not EMAIL_RE.match(email):
        return generic_response

    user = User.get_by_email(email)
    if user is None:
        return generic_response

    record = PasswordResetToken.issue(user.id)

    # Construimos el link a la UI. La UI lee ?token=... y lo manda al
    # endpoint /auth/reset-password.
    base_url = (
        current_app.config.get("FRONTEND_URL")
        or "http://localhost:3000"
    ).rstrip("/")
    reset_link = f"{base_url}/reset-password?token={record.token}"

    body = (
        "Hola,\n\n"
        "Pediste restablecer tu contraseña en Pandalyze. "
        "Ingresá al siguiente enlace (válido por una hora) para elegir una nueva:\n\n"
        f"{reset_link}\n\n"
        "Si no fuiste vos, podés ignorar este mensaje.\n\n"
        "— Pandalyze"
    )
    send_email(current_app, user.email, "Restablecer tu contraseña en Pandalyze", body)

    return generic_response


@bp.route("/reset-password", methods=["POST"])
@cross_origin()
@limiter.limit("10 per minute; 30 per hour")
def reset_password():
    """Consume un token de reset y setea la nueva password."""
    data = _json()
    token = (data.get("token") or "").strip()
    new_password = data.get("new_password") or ""

    record = PasswordResetToken.get_valid(token)
    if record is None:
        return (
            jsonify({"error": "Token inválido o expirado."}),
            400,
        )
    if len(new_password) < MIN_PASSWORD_LEN:
        return (
            jsonify({
                "error": "La nueva contraseña debe tener al menos %d caracteres."
                % MIN_PASSWORD_LEN
            }),
            400,
        )

    user = User.query.get(record.user_id)
    if user is None:
        return jsonify({"error": "Usuario asociado no encontrado."}), 404

    user.set_password(new_password)
    record.consume()  # marca used_at + commit
    db.session.commit()

    return jsonify({"ok": True, "message": "Contraseña actualizada."}), 200
