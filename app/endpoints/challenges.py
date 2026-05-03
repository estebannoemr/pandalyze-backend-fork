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
import io
import os
import re
from datetime import datetime
from pathlib import Path
from functools import wraps
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from flask import Blueprint, request, jsonify, Response
from flask_cors import cross_origin
from flask_jwt_extended import jwt_required, get_jwt_identity
import pandas as pd

from ..extensions import db, limiter
from ..models.challenge_result_model import ChallengeResult
from ..models.user_model import User, ROLE_ALUMNO, ROLE_DOCENTE, ROLE_ADMIN
from ..models.custom_challenge_model import (
    CustomChallenge,
)


bp = Blueprint("challenges", __name__)

# Carga de desafíos desde JSONs externos en orden: básico → intermedio → avanzado
# Facilita mantener los desafíos organizados por dificultad
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_BASICO_PATH = _DATA_DIR / "basico.json"
_INTERMEDIO_PATH = _DATA_DIR / "intermedio.json"
_AVANZADO_PATH = _DATA_DIR / "avanzado.json"
# _CHALLENGE_PATHS = [_BASICO_PATH, _INTERMEDIO_PATH, _AVANZADO_PATH]

CHALLENGES = []
for path in [_BASICO_PATH, _INTERMEDIO_PATH, _AVANZADO_PATH]:
    with open(path, "r", encoding="utf-8") as _f:
        CHALLENGES.extend(json.load(_f))

"""

_CHALLENGES_SIGNATURE = None


def _challenge_files_signature():
    # Firma de archivos para detectar cambios sin reiniciar el proceso.
    return tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in _CHALLENGE_PATHS)


def _reload_static_challenges_if_changed(force=False):
    # Recarga el banco estático sólo si cambió alguno de los JSON.
    global CHALLENGES, _CHALLENGES_SIGNATURE

    signature = _challenge_files_signature()
    if not force and signature == _CHALLENGES_SIGNATURE:
        return

    loaded = []
    for path in _CHALLENGE_PATHS:
        with open(path, "r", encoding="utf-8") as _f:
            loaded.extend(json.load(_f))

    CHALLENGES = loaded
    _CHALLENGES_SIGNATURE = signature


_reload_static_challenges_if_changed(force=True)

"""


def _all_challenges():
    # _reload_static_challenges_if_changed()
    try:
        custom = [
            c.to_runtime_dict()
            for c in CustomChallenge.query.filter_by(is_active=True)
            .order_by(CustomChallenge.created_at.asc())
            .all()
        ]
        return CHALLENGES + custom
    except Exception:
        # Fallback defensivo: si hay un desajuste de esquema en
        # custom_challenge, no rompemos los desafíos base.
        return CHALLENGES


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
    for ch in _all_challenges():
        if ch["id"] == challenge_id:
            return ch
    return None


def _get_custom_challenge(challenge_id):
    try:
        return CustomChallenge.from_external_id(int(challenge_id))
    except Exception:
        return None


def _can_manage_custom(user, custom_challenge):
    if user is None or custom_challenge is None:
        return False
    if user.role == ROLE_ADMIN:
        return True
    return int(custom_challenge.creator_id) == int(user.id)


def teacher_or_admin_required(fn):
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        uid = get_jwt_identity()
        user = User.query.get(int(uid)) if uid is not None else None
        if user is None or user.role not in (ROLE_DOCENTE, ROLE_ADMIN):
            return jsonify({"error": "No autorizado."}), 403
        request._pandalyze_user = user
        return fn(*args, **kwargs)

    return wrapper


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


def _normalize_csv_download_url(csv_url):
    """Convierte links compartidos conocidos a URLs de descarga CSV directa."""
    parsed = urlparse(csv_url)
    host = parsed.netloc.lower()
    path = parsed.path or ""

    if host in ("drive.google.com", "www.drive.google.com"):
        match = re.search(r"/file/d/([^/]+)", path)
        file_id = (
            match.group(1) if match else parse_qs(parsed.query).get("id", [None])[0]
        )
        if file_id:
            query = urlencode({"export": "download", "id": file_id})
            return f"https://drive.google.com/uc?{query}"

    if host in ("docs.google.com", "www.docs.google.com") and "/spreadsheets/d/" in path:
        gid = parse_qs(parsed.query).get("gid", ["0"])[0]
        sheet_path = path.split("/edit", 1)[0].rstrip("/")
        return f"https://docs.google.com{sheet_path}/export?{urlencode({'format': 'csv', 'gid': gid})}"

    return csv_url


def _looks_like_html(content):
    sample = (content or "").lstrip()[:300].lower()
    return sample.startswith("<!doctype html") or sample.startswith("<html")


def _fetch_csv_from_url(csv_url):
    """Descarga contenido CSV desde una URL http/https de forma acotada."""
    if not csv_url:
        return None
    parsed = urlparse(csv_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("La URL del CSV debe empezar con http:// o https://")

    download_url = _normalize_csv_download_url(csv_url)
    req = Request(download_url, headers={"User-Agent": "Pandalyze/1.0"})
    with urlopen(req, timeout=12) as response:
        raw = response.read()
    csv_content = raw.decode("utf-8-sig", errors="replace")

    if _looks_like_html(csv_content):
        raise ValueError(
            "El link no devolviÃ³ un CSV directo. UsÃ¡ un enlace pÃºblico de descarga CSV."
        )
    try:
        pd.read_csv(io.StringIO(csv_content), nrows=1)
    except Exception as exc:
        raise ValueError("El contenido descargado no parece ser un CSV vÃ¡lido.") from exc
    return csv_content


def _resolve_challenge_csv(challenge):
    """
    Resuelve el CSV fuente de un desafío:
    1) csv_content (embebido)
    2) csv_url (descarga on-demand)
    """
    csv_content = challenge.get("csv_content") or ""
    if csv_content.strip():
        return csv_content

    csv_url = challenge.get("csv_url")
    if csv_url:
        return _fetch_csv_from_url(csv_url)

    return ""


def _ensure_csv_filename(filename):
    value = (filename or "").strip()
    if not value:
        return ""
    return value if value.lower().endswith(".csv") else f"{value}.csv"


def _upload_csv_via_webhook(filename, content):
    """
    Hook opcional para subir CSV a Drive mediante un webhook externo.
    Espera env var GOOGLE_DRIVE_UPLOAD_WEBHOOK_URL y respuesta JSON con {url}.
    """
    webhook_url = (os.getenv("GOOGLE_DRIVE_UPLOAD_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        return None

    body = json.dumps({"filename": filename, "content": content}).encode("utf-8")
    req = Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "Pandalyze/1.0"},
        method="POST",
    )
    with urlopen(req, timeout=15) as response:
        raw = response.read().decode("utf-8", errors="replace")
    payload = json.loads(raw or "{}")
    url = (payload.get("url") or "").strip()
    return url or None


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

    visible = _all_challenges()

    if user is not None and user.role == ROLE_ALUMNO and user.class_id is not None:
        # Import diferido para evitar ciclo de imports.
        from ..models.class_model import Class as _Class

        klass = _Class.query.get(user.class_id)
        if klass is not None:
            allowed = set(klass.get_selected_ids())
            visible = [c for c in visible if c["id"] in allowed]

    public_rows = []
    for c in visible:
        row = _public_view(c)
        is_custom = bool(c.get("is_custom"))
        row["is_custom"] = is_custom
        if is_custom:
            creator_id = c.get("creator_id")
            row["can_manage"] = bool(
                user
                and (
                    user.role == ROLE_ADMIN
                    or (creator_id is not None and int(creator_id) == int(user.id))
                )
            )
        else:
            row["can_manage"] = False
        public_rows.append(row)

    return jsonify(public_rows), 200


@bp.route("/challenges", methods=["POST"])
@cross_origin()
@teacher_or_admin_required
def create_challenge():
    """
    Crea un desafío custom persistido en DB.
    Disponible para docente/admin.
    """
    user = request._pandalyze_user
    payload = request.get_json(silent=True) or {}

    title = (payload.get("title") or "").strip()
    difficulty = (payload.get("difficulty") or "").strip().lower()
    description = (payload.get("description") or "").strip()
    csv_filename = _ensure_csv_filename(payload.get("csv_filename"))
    csv_content = payload.get("csv_content") or ""
    csv_url = (payload.get("csv_url") or "").strip()
    expected_keyword = (payload.get("expected_keyword") or "").strip()
    solution_code = payload.get("solution_code") or ""

    if not title:
        return jsonify({"error": "El título es obligatorio."}), 400
    if difficulty not in POINTS_BY_DIFFICULTY:
        return jsonify({"error": "Dificultad inválida."}), 400
    if not description:
        return jsonify({"error": "La descripción es obligatoria."}), 400
    has_csv_content = isinstance(csv_content, str) and csv_content.strip() != ""
    has_csv_url = csv_url != ""

    if not has_csv_content and not has_csv_url:
        return jsonify({"error": "Debés enviar csv_content o csv_url."}), 400

    if has_csv_url:
        parsed = urlparse(csv_url)
        if parsed.scheme not in ("http", "https"):
            return jsonify({"error": "csv_url debe ser http:// o https://"}), 400
        if not csv_filename:
            path_name = (parsed.path or "").split("/")[-1].strip()
            csv_filename = _ensure_csv_filename(path_name or "challenge.csv")

    if not csv_filename:
        return jsonify({"error": "El nombre del CSV es obligatorio."}), 400

    # Opcional: si se envía contenido CSV y hay webhook configurado,
    # intentamos subirlo a Drive y guardar sólo la URL resultante.
    if has_csv_content and not has_csv_url:
        try:
            uploaded_url = _upload_csv_via_webhook(csv_filename, csv_content)
            if uploaded_url:
                csv_url = uploaded_url
                has_csv_url = True
        except Exception:
            # No bloqueamos creación del desafío por falla del webhook.
            pass
    if not expected_keyword:
        return jsonify({"error": "La palabra clave esperada es obligatoria."}), 400
    if not isinstance(solution_code, str) or not solution_code.strip():
        return jsonify({"error": "La solución de referencia es obligatoria."}), 400

    instructions = payload.get("instructions")
    if isinstance(instructions, str):
        instructions = [x.strip() for x in instructions.split("\n") if x.strip()]
    if not isinstance(instructions, list):
        instructions = []

    points = payload.get("points")
    try:
        points = int(points) if points is not None else POINTS_BY_DIFFICULTY[difficulty]
    except (TypeError, ValueError):
        return jsonify({"error": "Puntos inválidos."}), 400
    if points < 1 or points > 1000:
        return jsonify({"error": "Los puntos deben estar entre 1 y 1000."}), 400

    time_limit_seconds = payload.get("time_limit_seconds")
    if time_limit_seconds in (None, ""):
        time_limit_seconds = None
    else:
        try:
            time_limit_seconds = int(time_limit_seconds)
        except (TypeError, ValueError):
            return jsonify({"error": "Límite de tiempo inválido."}), 400
        if time_limit_seconds <= 0:
            time_limit_seconds = None

    challenge = CustomChallenge(
        creator_id=user.id,
        title=title,
        difficulty=difficulty,
        category=(payload.get("category") or "").strip().lower() or None,
        points=points,
        description=description,
        instructions_json=json.dumps(instructions),
        hint=(payload.get("hint") or "").strip(),
        csv_filename=csv_filename,
        csv_content=csv_content if (has_csv_content and not has_csv_url) else "",
        csv_url=csv_url or None,
        theory_url=(payload.get("theory_url") or "").strip() or None,
        expected_keyword=expected_keyword,
        solution_code=solution_code,
        feedback_correct=(payload.get("feedback_correct") or "¡Excelente trabajo!").strip(),
        feedback_incorrect=(payload.get("feedback_incorrect") or "Todavía no coincide con lo esperado.").strip(),
        suggestion=(payload.get("suggestion") or "").strip() or None,
        time_limit_seconds=time_limit_seconds,
        is_active=True,
    )
    db.session.add(challenge)
    db.session.commit()

    return jsonify({"challenge": _public_view(challenge.to_runtime_dict())}), 201


@bp.route("/challenges/<int:challenge_id>/manage", methods=["GET"])
@cross_origin()
@teacher_or_admin_required
def get_challenge_manage(challenge_id):
    user = request._pandalyze_user
    custom = _get_custom_challenge(challenge_id)
    if custom is None or not custom.is_active:
        return jsonify({"error": "Sólo se pueden gestionar desafíos custom."}), 404
    if not _can_manage_custom(user, custom):
        return jsonify({"error": "No autorizado para editar este desafío."}), 403
    return jsonify({"challenge": custom.to_runtime_dict()}), 200


@bp.route("/challenges/<int:challenge_id>", methods=["PATCH"])
@cross_origin()
@teacher_or_admin_required
def update_challenge(challenge_id):
    user = request._pandalyze_user
    custom = _get_custom_challenge(challenge_id)
    if custom is None or not custom.is_active:
        return jsonify({"error": "Sólo se pueden editar desafíos custom."}), 404
    if not _can_manage_custom(user, custom):
        return jsonify({"error": "No autorizado para editar este desafío."}), 403

    payload = request.get_json(silent=True) or {}

    if "title" in payload:
        title = (payload.get("title") or "").strip()
        if not title:
            return jsonify({"error": "El título es obligatorio."}), 400
        custom.title = title

    if "difficulty" in payload:
        difficulty = (payload.get("difficulty") or "").strip().lower()
        if difficulty not in POINTS_BY_DIFFICULTY:
            return jsonify({"error": "Dificultad inválida."}), 400
        custom.difficulty = difficulty

    if "description" in payload:
        description = (payload.get("description") or "").strip()
        if not description:
            return jsonify({"error": "La descripción es obligatoria."}), 400
        custom.description = description

    if "csv_filename" in payload:
        custom.csv_filename = _ensure_csv_filename(payload.get("csv_filename"))

    if "csv_content" in payload:
        csv_content = payload.get("csv_content") or ""
        custom.csv_content = csv_content
        if csv_content.strip():
            custom.csv_url = None

    if "csv_url" in payload:
        csv_url = (payload.get("csv_url") or "").strip()
        if csv_url:
            parsed = urlparse(csv_url)
            if parsed.scheme not in ("http", "https"):
                return jsonify({"error": "csv_url debe ser http:// o https://"}), 400
            custom.csv_url = csv_url
            custom.csv_content = ""
            if not custom.csv_filename:
                path_name = (parsed.path or "").split("/")[-1].strip()
                custom.csv_filename = _ensure_csv_filename(path_name or "challenge.csv")
        else:
            custom.csv_url = None

    if not custom.csv_filename:
        return jsonify({"error": "El nombre del CSV es obligatorio."}), 400
    if not (custom.csv_content or custom.csv_url):
        return jsonify({"error": "Debés definir csv_content o csv_url."}), 400

    if "category" in payload:
        custom.category = (payload.get("category") or "").strip().lower() or None
    if "hint" in payload:
        custom.hint = (payload.get("hint") or "").strip()
    if "theory_url" in payload:
        custom.theory_url = (payload.get("theory_url") or "").strip() or None
    if "expected_keyword" in payload:
        expected_keyword = (payload.get("expected_keyword") or "").strip()
        if not expected_keyword:
            return jsonify({"error": "La palabra clave esperada es obligatoria."}), 400
        custom.expected_keyword = expected_keyword
    if "solution_code" in payload:
        solution_code = payload.get("solution_code") or ""
        if not solution_code.strip():
            return jsonify({"error": "La solución de referencia es obligatoria."}), 400
        custom.solution_code = solution_code
    if "feedback_correct" in payload:
        custom.feedback_correct = (payload.get("feedback_correct") or "").strip() or "¡Excelente trabajo!"
    if "feedback_incorrect" in payload:
        custom.feedback_incorrect = (payload.get("feedback_incorrect") or "").strip() or "Todavía no coincide con lo esperado."
    if "suggestion" in payload:
        custom.suggestion = (payload.get("suggestion") or "").strip() or None
    if "instructions" in payload:
        instructions = payload.get("instructions")
        if isinstance(instructions, str):
            instructions = [x.strip() for x in instructions.split("\n") if x.strip()]
        if not isinstance(instructions, list):
            instructions = []
        custom.instructions_json = json.dumps(instructions)

    if "points" in payload:
        try:
            points = int(payload.get("points"))
        except (TypeError, ValueError):
            return jsonify({"error": "Puntos inválidos."}), 400
        if points < 1 or points > 1000:
            return jsonify({"error": "Los puntos deben estar entre 1 y 1000."}), 400
        custom.points = points

    if "time_limit_seconds" in payload:
        raw_t = payload.get("time_limit_seconds")
        if raw_t in (None, ""):
            custom.time_limit_seconds = None
        else:
            try:
                t = int(raw_t)
            except (TypeError, ValueError):
                return jsonify({"error": "Límite de tiempo inválido."}), 400
            custom.time_limit_seconds = t if t > 0 else None

    db.session.commit()
    out = custom.to_runtime_dict()
    out["can_manage"] = True
    return jsonify({"challenge": out}), 200


@bp.route("/challenges/<int:challenge_id>", methods=["DELETE"])
@cross_origin()
@teacher_or_admin_required
def delete_challenge(challenge_id):
    user = request._pandalyze_user
    custom = _get_custom_challenge(challenge_id)
    if custom is None or not custom.is_active:
        return jsonify({"error": "Sólo se pueden eliminar desafíos custom."}), 404
    if not _can_manage_custom(user, custom):
        return jsonify({"error": "No autorizado para eliminar este desafío."}), 403

    custom.is_active = False
    db.session.commit()
    return jsonify({"ok": True}), 200


@bp.route("/challenges/<int:challenge_id>/csv", methods=["GET"])
@cross_origin()
@jwt_required()
def get_challenge_csv(challenge_id):
    challenge = _get_challenge(challenge_id)
    if challenge is None:
        return jsonify({"error": "Desafío no encontrado"}), 404

    try:
        csv_content = _resolve_challenge_csv(challenge)
    except (ValueError, URLError, HTTPError) as e:
        return jsonify({"error": f"No se pudo obtener el CSV: {str(e)}"}), 502

    return (
        jsonify(
            {
                "csv_content": csv_content,
                "csv_filename": challenge["csv_filename"],
            }
        ),
        200,
    )


@bp.route("/challenges/<int:challenge_id>/download", methods=["GET"])
@cross_origin()
@jwt_required()
def download_challenge_csv(challenge_id):
    """
    Devuelve el CSV del desafío como un archivo descargable (text/csv) con
    Content-Disposition: attachment, listo para que el cliente lo guarde
    o lo cargue en memoria sin que el servidor persista nada.

    Esta ruta complementa a ``/csv`` (que devuelve JSON con el contenido):
    es la ruta canónica que el frontend usa para el flujo de "carga
    client-side sin persistir" — al iniciar un desafío, el alumno descarga
    el CSV desde acá, lo registra en el BlocksService de su navegador y
    lo manda inline en cada llamada a /runPythonCode. Nunca toca la tabla
    csv_data.
    """
    challenge = _get_challenge(challenge_id)
    if challenge is None:
        return jsonify({"error": "Desafío no encontrado, por favor descargá el CSV en tu compu y cargalo con la opción de 'Cargar CSV'"}), 404

    try:
        csv_content = _resolve_challenge_csv(challenge)
    except (ValueError, URLError, HTTPError) as e:
        return jsonify({"error": f"No se pudo obtener el CSV: {str(e)}"}), 502

    filename = challenge.get("csv_filename") or f"challenge_{challenge_id}.csv"

    response = Response(csv_content, mimetype="text/csv; charset=utf-8")
    # ``attachment`` fuerza el download cuando se abre directo en el navegador;
    # como utility de fetch sólo importa el body, pero deja el comportamiento
    # consistente para ambos usos.
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{filename}"'
    )
    response.headers["X-Challenge-Filename"] = filename
    response.headers["X-Challenge-Id"] = str(challenge_id)
    return response


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
    difficulty_by_id = {c["id"]: c["difficulty"] for c in _all_challenges()}
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
