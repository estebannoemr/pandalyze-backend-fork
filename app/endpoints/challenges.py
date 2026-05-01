"""
Blueprint de Desafíos con gamificación.

Expone:
- GET  /challenges
- GET  /challenges/<id>/csv
- POST /challenges/<id>/validate
- GET  /challenges/<id>/solution
- GET  /challenges/gamification/status

No se expone al cliente ``expected_keyword`` ni ``solution_code``.
La persistencia de intentos se hace en ``ChallengeResult``.
"""

import json
from datetime import datetime
from pathlib import Path

from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
from flask_jwt_extended import jwt_required, get_jwt_identity

from ..extensions import db, limiter
from ..models.challenge_result_model import ChallengeResult
from ..models.user_model import User


bp = Blueprint("challenges", __name__)

# Carga de desafíos desde JSONs externos en orden: básico → intermedio → avanzado
# Facilita mantener los desafíos organizados por dificultad
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_BASICO_PATH = _DATA_DIR / "basico.json"
_INTERMEDIO_PATH = _DATA_DIR / "intermedio.json"
_AVANZADO_PATH = _DATA_DIR / "avanzado.json"

CHALLENGES = []
for path in [_BASICO_PATH, _INTERMEDIO_PATH, _AVANZADO_PATH]:
    with open(path, "r", encoding="utf-8") as _f:
        CHALLENGES.extend(json.load(_f))


# ---------------------------------------------------------------------------
# Configuración de niveles y emblemas
# ---------------------------------------------------------------------------

LEVELS = [
    {"min": 0,   "max": 49,   "title": "Analista Trainee",     "level": 1},
    {"min": 50,  "max": 149,  "title": "Analista Junior",      "level": 2},
    {"min": 150, "max": 299,  "title": "Analista",             "level": 3},
    {"min": 300, "max": 499,  "title": "Analista Semi Senior", "level": 4},
    {"min": 500, "max": 9999, "title": "Analista Senior",      "level": 5},
]
# Nota: con 18 desafíos el máximo teórico de puntos es 510 (6×10 + 6×25 + 6×50).
# El nivel 5 cubre holgadamente ese tope.

BADGES = [
    {
        "id": "primer_desafio",
        "name": "Primeros pasos",
        "emoji": "🐣",
        "description": "Completá tu primer desafío",
    },
    {
        "id": "sin_errores",
        "name": "Sin errores",
        "emoji": "⚡",
        "description": "Completá un desafío en el primer intento",
    },
    {
        "id": "basico_completo",
        "name": "Bases sólidas",
        "emoji": "🧱",
        "description": "Completá todos los desafíos básicos",
    },
    {
        "id": "intermedio_completo",
        "name": "En progreso",
        "emoji": "📈",
        "description": "Completá todos los desafíos intermedios",
    },
    {
        "id": "avanzado_completo",
        "name": "Experto en datos",
        "emoji": "🏆",
        "description": "Completá todos los desafíos avanzados",
    },
    {
        "id": "todo_completo",
        "name": "Pandalyze Master",
        "emoji": "🐼",
        "description": "Completá todos los desafíos disponibles",
    },
    {
        "id": "racha_3",
        "name": "En racha",
        "emoji": "🔥",
        "description": "Completá 3 desafíos seguidos",
    },
]

POINTS_BY_DIFFICULTY = {
    "basico": 10,
    "intermedio": 25,
    "avanzado": 50,
}





# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_challenge(challenge_id):
    for ch in CHALLENGES:
        if ch["id"] == challenge_id:
            return ch
    return None


def _public_view(challenge):
    """Devuelve el desafío sin los campos sensibles (``expected_keyword``, ``solution_code``...)."""
    keys = (
        "id",
        "title",
        "difficulty",
        "points",
        "description",
        "instructions",
        "hint",
        "csv_filename",
        "theory_url",
        "category",
        # Campo opcional para desafíos contrareloj. Si está presente, el
        # frontend muestra un countdown visible al alumno; si no, el desafío
        # se comporta exactamente como antes (sin reloj).
        "time_limit_seconds",
    )
    return {k: challenge.get(k) for k in keys}


def _get_level_info(total_points):
    """Devuelve (level_dict, next_level_points)."""
    current = LEVELS[0]
    for level in LEVELS:
        if level["min"] <= total_points <= level["max"]:
            current = level
            break
        if total_points > level["max"]:
            current = level
    # siguiente nivel
    idx = LEVELS.index(current)
    if idx + 1 < len(LEVELS):
        next_points = LEVELS[idx + 1]["min"]
    else:
        next_points = current["max"]
    return current, next_points


def _compute_badges(completed_ids, first_try_ids, longest_streak):
    """Calcula qué emblemas se obtuvieron en base a los resultados persistidos."""
    earned = []

    if len(completed_ids) >= 1:
        earned.append("primer_desafio")

    if len(first_try_ids) >= 1:
        earned.append("sin_errores")

    basicos = [c["id"] for c in CHALLENGES if c["difficulty"] == "basico"]
    intermedios = [c["id"] for c in CHALLENGES if c["difficulty"] == "intermedio"]
    avanzados = [c["id"] for c in CHALLENGES if c["difficulty"] == "avanzado"]

    if basicos and all(cid in completed_ids for cid in basicos):
        earned.append("basico_completo")
    if intermedios and all(cid in completed_ids for cid in intermedios):
        earned.append("intermedio_completo")
    if avanzados and all(cid in completed_ids for cid in avanzados):
        earned.append("avanzado_completo")

    total_ids = [c["id"] for c in CHALLENGES]
    if total_ids and all(cid in completed_ids for cid in total_ids):
        earned.append("todo_completo")

    if longest_streak >= 3:
        earned.append("racha_3")

    return earned


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@bp.route("/challenges", methods=["GET"])
@cross_origin()
@jwt_required()
def list_challenges():
    """
    Lista de desafíos visible para el usuario autenticado.

    - Docentes y admin ven el banco completo (necesitan armar clases con todo
      el catálogo disponible).
    - Alumnos asociados a una clase ven sólo los desafíos seleccionados por
      esa clase.
    - Alumnos sin clase ven el banco completo (mantiene el comportamiento
      previo y permite que un alumno suelto siga practicando).
    """
    uid = get_jwt_identity()
    user = User.query.get(int(uid)) if uid is not None else None

    visible = CHALLENGES
    # Import diferido para evitar ciclo en tiempo de import del módulo.
    from ..models.user_model import ROLE_ALUMNO as _ROLE_ALUMNO

    if user is not None and user.role == _ROLE_ALUMNO and user.class_id is not None:
        # Import diferido para evitar ciclo de imports.
        from ..models.class_model import Class as _Class

        klass = _Class.query.get(user.class_id)
        if klass is not None:
            allowed = set(klass.get_selected_ids())
            visible = [c for c in CHALLENGES if c["id"] in allowed]

    return jsonify([_public_view(c) for c in visible]), 200


@bp.route("/challenges/<int:challenge_id>/csv", methods=["GET"])
@cross_origin()
@jwt_required()
def get_challenge_csv(challenge_id):
    challenge = _get_challenge(challenge_id)
    if challenge is None:
        return jsonify({"error": "Desafío no encontrado"}), 404

    return (
        jsonify(
            {
                "csv_content": challenge["csv_content"],
                "csv_filename": challenge["csv_filename"],
            }
        ),
        200,
    )


@bp.route("/challenges/<int:challenge_id>/validate", methods=["POST"])
@cross_origin()
@jwt_required()
@limiter.limit("30 per minute; 200 per hour")
def validate_challenge(challenge_id):
    user_id = int(get_jwt_identity())
    challenge = _get_challenge(challenge_id)
    if challenge is None:
        return jsonify({"error": "Desafío no encontrado"}), 404

    payload = request.get_json(silent=True) or {}
    user_output = (payload.get("output") or "").strip()

    # Timing (oculto para el alumno, visible sólo para docente/admin).
    # start_time: ISO string enviado desde el frontend cuando el alumno hizo
    # click en "Comenzar". Usamos wall clock (ahora - started_at) como duracion
    # total y active_seconds como suma de intervalos con el modal abierto.
    raw_start = payload.get("start_time")
    started_at = None
    if raw_start:
        try:
            # Aceptamos tanto "...Z" como offset explicito. strip final 'Z'.
            _s = raw_start.rstrip("Z")
            started_at = datetime.fromisoformat(_s)
        except Exception:
            started_at = None

    now = datetime.utcnow()
    duration_seconds = None
    if started_at is not None:
        try:
            delta = now - started_at
            duration_seconds = max(0, int(delta.total_seconds()))
        except Exception:
            duration_seconds = None

    raw_active = payload.get("active_seconds")
    active_seconds = None
    try:
        if raw_active is not None:
            active_seconds = max(0, int(raw_active))
    except (TypeError, ValueError):
        active_seconds = None

    # Cantidad de intentos previos del usuario para este desafío
    previous_attempts = ChallengeResult.get_attempts_for_challenge(
        challenge_id, user_id
    )
    already_passed = ChallengeResult.has_passed(challenge_id, user_id)

    expected = challenge["expected_keyword"]
    passed = expected in user_output and user_output != ""

    # primer_try = pasó y no había intentos previos fallidos para este desafío
    is_first_try = passed and previous_attempts == 0

    # Puntos: sólo se otorgan la primera vez que el usuario aprueba.
    points_earned = 0
    if passed and not already_passed:
        points_earned = challenge["points"]

    # Persistir el intento
    result = ChallengeResult(
        user_id=user_id,
        challenge_id=challenge_id,
        passed=passed,
        points_earned=points_earned,
        first_try=is_first_try,
        attempts=previous_attempts + 1,
        started_at=started_at,
        duration_seconds=duration_seconds,
        active_seconds=active_seconds,
    )
    db.session.add(result)
    db.session.commit()

    if passed:
        message = "🎉 ¡Correcto!"
        feedback = challenge["feedback_correct"]
        suggestion = None
    else:
        message = "Todavía no lo lograste. ¡Seguí intentando!"
        feedback = challenge["feedback_incorrect"]
        suggestion = challenge.get("suggestion")

    return (
        jsonify(
            {
                "passed": passed,
                "message": message,
                "points_earned": points_earned,
                "first_try": is_first_try,
                "feedback": feedback,
                "suggestion": suggestion,
            }
        ),
        200,
    )


@bp.route("/challenges/<int:challenge_id>/solution", methods=["GET"])
@cross_origin()
@jwt_required()
def get_challenge_solution(challenge_id):
    challenge = _get_challenge(challenge_id)
    if challenge is None:
        return jsonify({"error": "Desafío no encontrado"}), 404

    return jsonify({"solution_description": challenge["solution_code"]}), 200


@bp.route("/challenges/gamification/status", methods=["GET"])
@cross_origin()
@jwt_required()
def gamification_status():
    user_id = int(get_jwt_identity())
    # Nos quedamos con el primer "pass" por challenge_id (así no sumamos puntos duplicados).
    passed_results = ChallengeResult.all_passed_for_user(user_id)

    seen = set()
    first_passes = []
    for r in passed_results:
        if r.challenge_id in seen:
            continue
        seen.add(r.challenge_id)
        first_passes.append(r)

    completed_ids = [r.challenge_id for r in first_passes]
    first_try_ids = [r.challenge_id for r in first_passes if r.first_try]
    total_points = sum(r.points_earned for r in first_passes)

    # Racha: cantidad máxima de "passes" consecutivos en orden cronológico,
    # considerando intentos aprobados (primer pass por desafío).
    longest_streak = 0
    current_streak = 0
    for r in first_passes:
        if r.passed:
            current_streak += 1
            longest_streak = max(longest_streak, current_streak)
        else:
            current_streak = 0

    current_level, next_level_points = _get_level_info(total_points)

    # Distribución por dificultad
    difficulty_by_id = {c["id"]: c["difficulty"] for c in CHALLENGES}
    by_diff = {"basico": 0, "intermedio": 0, "avanzado": 0}
    for cid in completed_ids:
        d = difficulty_by_id.get(cid)
        if d in by_diff:
            by_diff[d] += 1

    earned_badges = _compute_badges(completed_ids, first_try_ids, longest_streak)

    return (
        jsonify(
            {
                "total_points": total_points,
                "level": current_level["level"],
                "level_title": current_level["title"],
                "next_level_points": next_level_points,
                "completed_challenges": completed_ids,
                "badges": earned_badges,
                "all_badges": BADGES,
                "challenges_by_difficulty": by_diff,
            }
        ),
        200,
    )


def _anonymize_email(email):
    """Anonimiza un email mostrando los primeros 2 caracteres + '***'.

    Mantiene la consistencia con la lógica que usa StatsDashboard en el
    frontend (`anonymize()`), de modo que un mismo alumno se vea igual en
    ambos lugares y no haya forma de cruzar identidades.
    """
    if not email:
        return "-"
    at = email.find("@")
    name = email if at <= 0 else email[:at]
    if len(name) <= 2:
        return name + "***"
    return name[:2] + "***"


@bp.route("/challenges/leaderboard", methods=["GET"])
@cross_origin()
@jwt_required()
def leaderboard():
    """
    Top 10 alumnos por puntos acumulados, con emails anonimizados.

    El leaderboard es público para cualquier usuario autenticado: sirve como
    motivación para los alumnos. Se anonimizan los emails para preservar
    privacidad y evitar que los alumnos identifiquen a otros alumnos por
    nombre.

    Solo se cuentan usuarios con rol "alumno"; docentes y admin no compiten.
    """
    # Traemos en una sola query: alumnos + sus aprobaciones únicas por desafío.
    # Nos quedamos con el primer pass por challenge_id para no contar duplicados.
    students = User.query.filter_by(role="alumno").all()

    rows = []
    for s in students:
        passed = ChallengeResult.all_passed_for_user(s.id)
        seen = set()
        unique_first = []
        for r in passed:
            if r.challenge_id in seen:
                continue
            seen.add(r.challenge_id)
            unique_first.append(r)
        if not unique_first:
            continue
        total_points = sum(r.points_earned for r in unique_first)
        completed = len(unique_first)
        rows.append({
            "anon_email": _anonymize_email(s.email),
            "points": total_points,
            "completed": completed,
        })

    # Orden: puntos desc, completed desc como desempate.
    rows.sort(key=lambda r: (-r["points"], -r["completed"]))
    top = rows[:10]

    # Devolvemos también la posición del usuario autenticado (anónima en
    # forma de "sos el #N de M") para que pueda saber donde está parado
    # aunque no esté en el top.
    requester_id = int(get_jwt_identity())
    requester = User.query.get(requester_id)
    my_rank = None
    if requester is not None and requester.role == "alumno":
        # Reconstruimos el ranking conservando la asociación a student_id
        # (que perdimos en `rows` al anonimizar) para localizar al requester.
        ranked = []
        for s in students:
            passed = ChallengeResult.all_passed_for_user(s.id)
            seen2 = set()
            uniq2 = []
            for r in passed:
                if r.challenge_id in seen2:
                    continue
                seen2.add(r.challenge_id)
                uniq2.append(r)
            pts = sum(r.points_earned for r in uniq2)
            ranked.append((s.id, pts, len(uniq2)))
        ranked.sort(key=lambda t: (-t[1], -t[2]))
        for idx, (sid, _pts, _c) in enumerate(ranked, start=1):
            if sid == requester_id:
                my_rank = {"position": idx, "of": len(ranked)}
                break

    return (
        jsonify({
            "top": top,
            "my_rank": my_rank,
        }),
        200,
    )
