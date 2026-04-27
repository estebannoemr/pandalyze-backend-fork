"""
Blueprint de docente: devuelve los alumnos asociados al docente autenticado
con sus estadísticas agregadas (puntos, nivel, desafíos completados, último acceso).
"""

from flask import Blueprint, jsonify
from flask_cors import cross_origin
from flask_jwt_extended import jwt_required, get_jwt_identity

from ..models.user_model import User, ROLE_DOCENTE
from ..models.challenge_result_model import ChallengeResult
from .challenges import _get_level_info, CHALLENGES


bp = Blueprint("teacher", __name__, url_prefix="/teacher")


# Mapa id -> difficulty, construido una vez al importar.
_DIFFICULTY_BY_ID = {c["id"]: c["difficulty"] for c in CHALLENGES}


def _timing_summary_for_user(user_id):
    """
    Para cada dificultad (basico/intermedio/avanzado) devuelve la duracion
    (en segundos) del PRIMER y ULTIMO desafio aprobado por el usuario.

    Usamos ``duration_seconds`` (wall clock desde "Comenzar" hasta aprobar).
    Si un registro no tiene el dato (ej. intentos previos a esta feature),
    lo saltamos; el dato se considera "sin informacion" en ese caso.
    """
    passed = (
        ChallengeResult.query.filter_by(user_id=user_id, passed=True)
        .order_by(ChallengeResult.timestamp.asc())
        .all()
    )

    # Nos quedamos con el primer "pass" por challenge_id (mismo criterio que
    # gamification_status) para no contar reintentos despues del exito.
    seen = set()
    first_passes = []
    for r in passed:
        if r.challenge_id in seen:
            continue
        seen.add(r.challenge_id)
        first_passes.append(r)

    out = {
        "basico": {"first": None, "last": None},
        "intermedio": {"first": None, "last": None},
        "avanzado": {"first": None, "last": None},
    }

    # Agrupamos por dificultad conservando el orden cronologico.
    by_diff = {"basico": [], "intermedio": [], "avanzado": []}
    for r in first_passes:
        diff = _DIFFICULTY_BY_ID.get(r.challenge_id)
        if diff in by_diff and r.duration_seconds is not None:
            by_diff[diff].append(r.duration_seconds)

    for diff, durations in by_diff.items():
        if durations:
            out[diff]["first"] = durations[0]
            out[diff]["last"] = durations[-1]

    return out


@bp.route("/students", methods=["GET"])
@cross_origin()
@jwt_required()
def list_students():
    user_id = int(get_jwt_identity())
    teacher = User.query.get(user_id)
    if teacher is None or teacher.role != ROLE_DOCENTE:
        return jsonify({"error": "Acceso restringido a docentes."}), 403

    students = (
        User.query.filter_by(teacher_id=teacher.id)
        .order_by(User.email.asc())
        .all()
    )

    result = []
    for s in students:
        passed = ChallengeResult.all_passed_for_user(s.id)
        seen = set()
        first_passes = []
        for r in passed:
            if r.challenge_id in seen:
                continue
            seen.add(r.challenge_id)
            first_passes.append(r)

        total_points = sum(r.points_earned for r in first_passes)
        completed_count = len(first_passes)
        level, _ = _get_level_info(total_points)
        timing = _timing_summary_for_user(s.id)

        result.append(
            {
                "id": s.id,
                "email": s.email,
                "total_points": total_points,
                "level": level["level"],
                "level_title": level["title"],
                "completed_count": completed_count,
                "last_seen_at": (
                    s.last_seen_at.isoformat() if s.last_seen_at else None
                ),
                "created_at": (
                    s.created_at.isoformat() if s.created_at else None
                ),
                "timing": timing,
            }
        )

    return (
        jsonify(
            {
                "teacher": {
                    "id": teacher.id,
                    "email": teacher.email,
                    "class_code": teacher.class_code,
                },
                "students": result,
            }
        ),
        200,
    )
