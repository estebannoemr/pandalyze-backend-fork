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
from ..models.class_model import Class


bp = Blueprint("stats", __name__, url_prefix="/stats")


# Buckets fijos para el histograma de tiempos activos (en segundos).
# Elegidos para que cubran desde "lo resolvió rápido" hasta "lo resolvió
# muy largo" sin que ninguna columna domine en exceso.
_TIME_BUCKETS = [
    (0, 30, "0-30s"),
    (30, 60, "30s-1m"),
    (60, 120, "1-2m"),
    (120, 300, "2-5m"),
    (300, 600, "5-10m"),
    (600, None, "10m+"),
]


def _bucket_label_for(active_seconds):
    if active_seconds is None:
        return None
    s = int(active_seconds)
    if s < 0:
        s = 0
    for lo, hi, label in _TIME_BUCKETS:
        if hi is None and s >= lo:
            return label
        if lo <= s < hi:
            return label
    return _TIME_BUCKETS[-1][2]


_CHALLENGES_CACHE = None
_CHALLENGES_META_CACHE = None  # {id: {"title", "difficulty"}}


def _load_static_challenges_meta():
    """Lee los 3 JSONs y arma un dict id -> {title, difficulty}."""
    global _CHALLENGES_META_CACHE
    if _CHALLENGES_META_CACHE is not None:
        return _CHALLENGES_META_CACHE
    here = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.normpath(os.path.join(here, "..", "data"))
    out = {}
    try:
        for filename in ["basico.json", "intermedio.json", "avanzado.json"]:
            path = os.path.join(data_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for c in data:
                out[int(c["id"])] = {
                    "title": c.get("title", f"Desafío {c.get('id')}"),
                    "difficulty": c.get("difficulty", "basico"),
                }
    except Exception:
        out = {}
    _CHALLENGES_META_CACHE = out
    return _CHALLENGES_META_CACHE


def _load_challenges_difficulty_map():
    """Devuelve dict {challenge_id: difficulty} leido de los JSONs en orden."""
    global _CHALLENGES_CACHE
    if _CHALLENGES_CACHE is not None:
        return _CHALLENGES_CACHE
    meta = _load_static_challenges_meta()
    _CHALLENGES_CACHE = {cid: m["difficulty"] for cid, m in meta.items()}
    return _CHALLENGES_CACHE


def _resolve_challenge_meta(challenge_id):
    """
    Devuelve {title, difficulty} para un challenge_id arbitrario, mirando
    primero el banco estático (basico/intermedio/avanzado.json) y, si no
    matchea, la tabla CustomChallenge (IDs >= 100000).
    """
    static_meta = _load_static_challenges_meta()
    if challenge_id in static_meta:
        return static_meta[challenge_id]
    # Importación diferida para evitar ciclo cuando no hay challenges custom
    # cargados al primer hit.
    try:
        from ..models.custom_challenge_model import CustomChallenge

        custom = CustomChallenge.from_external_id(challenge_id)
        if custom is not None:
            return {"title": custom.title, "difficulty": custom.difficulty}
    except Exception:
        pass
    return {"title": f"Desafío {challenge_id}", "difficulty": "basico"}


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


# ---------------------------------------------------------------------------
# Etapa 3: agregados por clase, distribución de tiempos, desempeño por desafío
# ---------------------------------------------------------------------------


def _resolve_classes_scope(requester, teacher_id_param):
    """
    Resuelve el conjunto de clases sobre las que correr la comparativa.

    - Admin sin filtro: todas las clases.
    - Admin con teacher_id: clases del docente seleccionado.
    - Docente: siempre sus propias clases (teacher_id_param se descarta).
    """
    if requester.role == ROLE_ADMIN:
        q = Class.query
        if teacher_id_param:
            try:
                tid = int(teacher_id_param)
            except (TypeError, ValueError):
                return None, ("teacher_id invalido.", 400)
            q = q.filter_by(teacher_id=tid)
        return q.order_by(Class.created_at.asc()).all(), None
    if requester.role == ROLE_DOCENTE:
        return (
            Class.query.filter_by(teacher_id=requester.id)
            .order_by(Class.created_at.asc())
            .all(),
            None,
        )
    return None, ("Acceso restringido.", 403)


@bp.route("/by_class", methods=["GET"])
@cross_origin()
@jwt_required()
def stats_by_class():
    """
    Agregados por clase para construir un bar chart comparativo.

    Por cada clase del scope devolvemos número de alumnos, promedio de
    desafíos completados (primer pass por desafío) y promedio de puntos
    acumulados. Los promedios son por alumno con clase asignada — un
    alumno sin resultados cuenta como 0 completados, 0 puntos, así dos
    clases con cantidades distintas de alumnos siguen siendo comparables.
    """
    requester = _get_requester()
    if requester is None:
        return jsonify({"error": "No autenticado."}), 401
    if requester.role not in {ROLE_ADMIN, ROLE_DOCENTE}:
        return jsonify({"error": "Acceso restringido."}), 403

    classes, err = _resolve_classes_scope(requester, request.args.get("teacher_id"))
    if err is not None:
        msg, code = err
        return jsonify({"error": msg}), code

    out = []
    for klass in classes:
        students = (
            User.query.filter_by(class_id=klass.id, role=ROLE_ALUMNO).all()
        )
        student_ids = [s.id for s in students]

        total_completed = 0
        total_points = 0
        if student_ids:
            results = (
                ChallengeResult.query.filter(
                    ChallengeResult.user_id.in_(student_ids),
                    ChallengeResult.passed.is_(True),
                )
                .order_by(ChallengeResult.timestamp.asc())
                .all()
            )
            seen_per_user = defaultdict(set)
            for r in results:
                if r.challenge_id in seen_per_user[r.user_id]:
                    continue
                seen_per_user[r.user_id].add(r.challenge_id)
                total_completed += 1
                total_points += r.points_earned

        n = len(students)
        out.append(
            {
                "class_id": klass.id,
                "name": klass.name,
                "class_code": klass.class_code,
                "teacher_id": klass.teacher_id,
                "student_count": n,
                "total_completed": total_completed,
                "total_points": total_points,
                "avg_completed": (round(total_completed / n, 2) if n > 0 else 0),
                "avg_points": (round(total_points / n, 2) if n > 0 else 0),
                "selected_challenges_count": len(klass.get_selected_ids()),
            }
        )

    return jsonify({"classes": out}), 200


@bp.route("/time_distribution", methods=["GET"])
@cross_origin()
@jwt_required()
def stats_time_distribution():
    """
    Histograma de active_seconds del scope, separado por dificultad.

    Sólo mira intentos aprobados con active_seconds no nulo (intentos
    anteriores al tracking de timing se ignoran). Devuelve un objeto
    {basico: [...], intermedio: [...], avanzado: [...]} donde cada item
    es {bucket, count}. Los buckets son fijos y siempre se devuelven
    todos (con count=0 si no hay datos), para que el gráfico tenga un
    eje X estable.
    """
    requester = _get_requester()
    if requester is None:
        return jsonify({"error": "No autenticado."}), 401
    if requester.role not in {ROLE_ADMIN, ROLE_DOCENTE}:
        return jsonify({"error": "Acceso restringido."}), 403

    scope_info, err = _resolve_student_scope(requester, request.args.get("teacher_id"))
    if err is not None:
        msg, code = err
        return jsonify({"error": msg}), code

    student_ids = [s.id for s in scope_info["students"]]

    # Estructura de salida: por dificultad → por bucket → contador.
    counts = {
        diff: {label: 0 for _, _, label in _TIME_BUCKETS}
        for diff in ("basico", "intermedio", "avanzado")
    }
    total_with_timing = 0

    if student_ids:
        results = ChallengeResult.query.filter(
            ChallengeResult.user_id.in_(student_ids),
            ChallengeResult.passed.is_(True),
            ChallengeResult.active_seconds.isnot(None),
        ).all()
        for r in results:
            label = _bucket_label_for(r.active_seconds)
            if label is None:
                continue
            meta = _resolve_challenge_meta(r.challenge_id)
            diff = meta.get("difficulty", "basico")
            if diff not in counts:
                diff = "basico"
            counts[diff][label] += 1
            total_with_timing += 1

    distribution = {}
    for diff, by_bucket in counts.items():
        distribution[diff] = [
            {"bucket": label, "count": by_bucket[label]}
            for _, _, label in _TIME_BUCKETS
        ]

    return (
        jsonify(
            {
                "buckets": [label for _, _, label in _TIME_BUCKETS],
                "distribution": distribution,
                "total_results_with_timing": total_with_timing,
                "scope": {
                    "type": scope_info["scope"],
                    "student_count": len(student_ids),
                },
            }
        ),
        200,
    )


@bp.route("/by_challenge", methods=["GET"])
@cross_origin()
@jwt_required()
def stats_by_challenge():
    """
    Desempeño por desafío en el scope.

    Para cada challenge_id que aparece en algún ChallengeResult del scope
    devuelve: title, difficulty, students_total (cuántos alumnos del scope
    intentaron el desafío al menos una vez), students_passed (cuántos
    aprobaron alguna vez), pass_rate, avg_attempts (sobre todos los
    alumnos que lo intentaron), avg_active_seconds (sobre los pases con
    timing). Identifica desafíos demasiado fáciles/difíciles.
    """
    requester = _get_requester()
    if requester is None:
        return jsonify({"error": "No autenticado."}), 401
    if requester.role not in {ROLE_ADMIN, ROLE_DOCENTE}:
        return jsonify({"error": "Acceso restringido."}), 403

    scope_info, err = _resolve_student_scope(requester, request.args.get("teacher_id"))
    if err is not None:
        msg, code = err
        return jsonify({"error": msg}), code

    student_ids = [s.id for s in scope_info["students"]]
    out = []

    if student_ids:
        results = ChallengeResult.query.filter(
            ChallengeResult.user_id.in_(student_ids)
        ).all()

        # Agregamos por (challenge_id, user_id) primero — para distinguir
        # cuántos alumnos distintos lo intentaron / aprobaron.
        # Estructura: per_ch[cid] = {
        #   "users_attempts": {uid: total_attempts},
        #   "users_passed": set(uid),
        #   "active_seconds_passes": [s, s, s],
        # }
        per_ch = defaultdict(
            lambda: {
                "users_attempts": defaultdict(int),
                "users_passed": set(),
                "active_seconds_passes": [],
            }
        )
        for r in results:
            entry = per_ch[r.challenge_id]
            entry["users_attempts"][r.user_id] += 1
            if r.passed:
                entry["users_passed"].add(r.user_id)
                if r.active_seconds is not None:
                    entry["active_seconds_passes"].append(r.active_seconds)

        for cid, entry in per_ch.items():
            meta = _resolve_challenge_meta(cid)
            students_total = len(entry["users_attempts"])
            students_passed = len(entry["users_passed"])
            pass_rate = (
                round(students_passed / students_total, 3)
                if students_total
                else 0.0
            )
            total_attempts = sum(entry["users_attempts"].values())
            avg_attempts = (
                round(total_attempts / students_total, 2)
                if students_total
                else 0.0
            )
            active_list = entry["active_seconds_passes"]
            avg_active = (
                round(sum(active_list) / len(active_list), 1)
                if active_list
                else None
            )
            out.append(
                {
                    "challenge_id": cid,
                    "title": meta["title"],
                    "difficulty": meta["difficulty"],
                    "students_total": students_total,
                    "students_passed": students_passed,
                    "pass_rate": pass_rate,
                    "avg_attempts": avg_attempts,
                    "avg_active_seconds": avg_active,
                    "is_custom": cid >= 100000,
                }
            )

    # Orden estable: dificultad asc (basico → avanzado), después por título.
    diff_order = {"basico": 0, "intermedio": 1, "avanzado": 2}
    out.sort(key=lambda r: (diff_order.get(r["difficulty"], 3), r["title"]))

    return (
        jsonify(
            {
                "challenges": out,
                "scope": {
                    "type": scope_info["scope"],
                    "student_count": len(student_ids),
                },
            }
        ),
        200,
    )
