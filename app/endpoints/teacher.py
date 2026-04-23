"""
Blueprint de docente: devuelve los alumnos asociados al docente autenticado
con sus estadísticas agregadas (puntos, nivel, desafíos completados, último acceso).
"""

from flask import Blueprint, jsonify
from flask_cors import cross_origin
from flask_jwt_extended import jwt_required, get_jwt_identity

from ..models.user_model import User, ROLE_DOCENTE
from ..models.challenge_result_model import ChallengeResult
from .challenges import _get_level_info


bp = Blueprint("teacher", __name__, url_prefix="/teacher")


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
