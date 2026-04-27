"""
Blueprint de estadisticas agregadas.

Accesible para admin (scope global o filtrado por docente) y docente
(solo scope de sus propios alumnos).

Endpoints:
- GET /stats/teachers               : lista de docentes (solo admin)
- GET /stats/overview?teacher_id=   : agregados para graficos (ambos roles)
"""

from collections import defaultdict
from datetime import datetime, timedelta
import json
import os

from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import jwt_required, get_jwt_identity

from ..models.user_model import User, ROLE_ALUMNO, ROLE_DOCENTE, ROLE_ADMIN
from ..models.challenge_result_model import ChallengeResult


bp = Blueprint("stats", __name__, url_prefix="/stats")


_CHALLENGES_CACHE = None


def _load_challenges_difficulty_map():
    """Devuelve dict {challenge_id: difficulty} leido del JSON."""
    global _CHALLENGES_CACHE
    if _CHALLENGES_CACHE is not None:
        return _CHALLENGES_CACHE
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.normpath(os.path.join(here, "..", "data", "challenges.json"))
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _CHALLENGES_CACHE = {int(c["id"]): c.get("difficulty", "basico") for c in data}
    except Exception:
        _CHALLENGES_CACHE = {}
    return _CHALLENGES_CACHE


def _get_requester():
    user_id = int(get_jwt_identity())
    user = User.query.get(user_id)
    return user


def _resolve_student_scope(requester, teacher_id_param):
    """
    Decide el conjunto de alumnos cuyas estadisticas deben agregarse.

    - Admin sin teacher_id_param: todos los alumnos.
    - Admin con teacher_id_param: alumnos asociados a ese docente.
    - Docente: siempre sus propios alumnos (teacher_id_param se ignora por seguridad).
    """
    if requester.role == ROLE_ADMIN:
        if teacher_id_param:
            try:
                tid = int(teacher_id_param)
            except (TypeError, ValueError):
                return None, ("teacher_id invalido.", 400)
            teacher = User.query.get(tid)
            if teacher is None or teacher.role != ROLE_DOCENTE:
                return None, ("Docente no encontrado.", 404)
            students = (
                User.query.filter_by(role=ROLE_ALUMNO, teacher_id=tid)
                .order_by(User.email.asc())
                .all()
            )
            return {"students": students, "teacher": teacher, "scope": "teacher"}, None
        students = (
            User.query.filter_by(role=ROLE_ALUMNO)
            .order_by(User.email.asc())
            .all()
        )
        return {"students": students, "teacher": None, "scope": "global"}, None

    if requester.role == ROLE_DOCENTE:
        students = (
            User.query.filter_by(role=ROLE_ALUMNO, teacher_id=requester.id)
            .order_by(User.email.asc())
            .all()
        )
        return {"students": students, "teacher": requester, "scope": "teacher"}, None

    return None, ("Acceso restringido.", 403)


@bp.route("/teachers", methods=["GET"])
@cross_origin()
@jwt_required()
def list_teachers():
    """Lista simplificada de docentes, solo para admin (para el selector)."""
    requester = _get_requester()
    if requester is None or requester.role != ROLE_ADMIN:
        return jsonify({"error": "Acceso restringido a administradores."}), 403
    teachers = (
        User.query.filter_by(role=ROLE_DOCENTE)
        .order_by(User.email.asc())
        .all()
    )
    return (
        jsonify(
            {
                "teachers": [
                    {"id": t.id, "email": t.email, "class_code": t.class_code}
                    for t in teachers
                ]
            }
        ),
        200,
    )


@bp.route("/overview", methods=["GET"])
@cross_origin()
@jwt_required()
def overview():
    """
    Devuelve agregados para los 4 graficos:

    - per_student: [{email, completed, points}]  (sirve para barras y ranking)
    - by_difficulty: {basico, intermedio, avanzado}
    - timeline: [{date: 'YYYY-MM-DD', passed: N}] (ultimos 30 dias)
    - summary: totales globales del scope
    """
    requester = _get_requester()
    if requester is None:
        return jsonify({"error": "No autenticado."}), 401
    if requester.role not in {ROLE_ADMIN, ROLE_DOCENTE}:
        return jsonify({"error": "Acceso restringido."}), 403

    teacher_id_param = request.args.get("teacher_id")
    scope_info, err = _resolve_student_scope(requester, teacher_id_param)
    if err is not None:
        msg, code = err
        return jsonify({"error": msg}), code

    students = scope_info["students"]
    student_ids = [s.id for s in students]

    difficulty_map = _load_challenges_difficulty_map()

    per_student = []
    by_difficulty = {"basico": 0, "intermedio": 0, "avanzado": 0}
    timeline_counter = defaultdict(int)

    # Rango temporal: ultimos 30 dias.
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=29)

    total_completed = 0
    total_points = 0

    # Timing agregado por dificultad: acumulamos duraciones del "primer" y
    # "ultimo" desafio aprobado por cada alumno dentro de cada dificultad.
    timing_firsts = {"basico": [], "intermedio": [], "avanzado": []}
    timing_lasts = {"basico": [], "intermedio": [], "avanzado": []}

    if student_ids:
        # Traemos todos los resultados aprobados del scope en una sola query.
        results = (
            ChallengeResult.query.filter(
                ChallengeResult.user_id.in_(student_ids),
                ChallengeResult.passed.is_(True),
            )
            .order_by(ChallengeResult.timestamp.asc())
            .all()
        )

        # Agrupamos por usuario y deduplicamos por challenge_id (solo primer pass).
        per_user_first_pass = defaultdict(dict)
        for r in results:
            if r.challenge_id not in per_user_first_pass[r.user_id]:
                per_user_first_pass[r.user_id][r.challenge_id] = r

        for s in students:
            first_passes = list(per_user_first_pass.get(s.id, {}).values())
            # Ordenados cronologicamente gracias al order_by de la query.
            pts = sum(r.points_earned for r in first_passes)
            per_student.append(
                {
                    "id": s.id,
                    "email": s.email,
                    "completed": len(first_passes),
                    "points": pts,
                }
            )
            total_completed += len(first_passes)
            total_points += pts

            # Agrupamos las duraciones por dificultad para calcular "primer" y
            # "ultimo" por alumno y por dificultad.
            per_diff_durations = {"basico": [], "intermedio": [], "avanzado": []}

            for r in first_passes:
                diff = difficulty_map.get(r.challenge_id, "basico")
                if diff in by_difficulty:
                    by_difficulty[diff] += 1
                ts_date = r.timestamp.date() if r.timestamp else None
                if ts_date and ts_date >= start_date and ts_date <= today:
                    timeline_counter[ts_date.isoformat()] += 1
                if diff in per_diff_durations and r.duration_seconds is not None:
                    per_diff_durations[diff].append(r.duration_seconds)

            for diff, durations in per_diff_durations.items():
                if durations:
                    timing_firsts[diff].append(durations[0])
                    timing_lasts[diff].append(durations[-1])

    # Timeline denso: aseguramos que cada dia del rango tenga entrada.
    timeline = []
    for i in range(30):
        d = start_date + timedelta(days=i)
        key = d.isoformat()
        timeline.append({"date": key, "passed": timeline_counter.get(key, 0)})

    def _avg(values):
        if not values:
            return None
        return round(sum(values) / len(values), 1)

    timing_avg = {}
    for diff in ("basico", "intermedio", "avanzado"):
        timing_avg[diff] = {
            "first_avg_seconds": _avg(timing_firsts[diff]),
            "last_avg_seconds": _avg(timing_lasts[diff]),
            "sample_size": len(timing_firsts[diff]),
        }

    scope_meta = {
        "type": scope_info["scope"],
        "requester_role": requester.role,
        "student_count": len(students),
    }
    if scope_info["teacher"]:
        scope_meta["teacher"] = {
            "id": scope_info["teacher"].id,
            "email": scope_info["teacher"].email,
        }

    return (
        jsonify(
            {
                "scope": scope_meta,
                "per_student": per_student,
                "by_difficulty": by_difficulty,
                "timeline": timeline,
                "timing_avg": timing_avg,
                "summary": {
                    "total_students": len(students),
                    "total_completed": total_completed,
                    "total_points": total_points,
                },
            }
        ),
        200,
    )
