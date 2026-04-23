"""
Helper para resolver el "scope" de un request: usuario autenticado (JWT)
o sesión de invitado identificada por el header ``X-Guest-Id``.

Esto permite que endpoints como /saveCsv y /runPythonCode funcionen tanto
para usuarios logueados como para invitados, manteniendo los datos aislados
entre sesiones.
"""

import re

from flask import request
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity


GUEST_ID_HEADER = "X-Guest-Id"
_GUEST_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


def resolve_scope():
    """Devuelve (user_id, guest_id). Exactamente uno es no None.

    - Si hay JWT válido: retorna (int(user_id), None).
    - Si no hay JWT pero el header X-Guest-Id es válido: retorna
      (None, guest_id).
    - En otro caso: retorna (None, None). El endpoint decide si es error.
    """
    user_id = None
    try:
        verify_jwt_in_request(optional=True)
        raw = get_jwt_identity()
        if raw is not None:
            user_id = int(raw)
    except Exception:
        user_id = None

    if user_id is not None:
        return user_id, None

    guest_id = request.headers.get(GUEST_ID_HEADER) or ""
    guest_id = guest_id.strip()
    if _GUEST_ID_RE.match(guest_id):
        return None, guest_id

    return None, None
