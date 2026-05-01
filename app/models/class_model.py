"""
Modelo de Clase del docente.

Una clase agrupa alumnos bajo el control de un docente y restringe el banco
de desafíos visible para esos alumnos a un subconjunto que el docente elige
al crear la clase. Un mismo docente puede tener varias clases (por ejemplo,
"2026-A" y "2026-B") con códigos de inscripción distintos y distintos
desafíos seleccionados.

La asociación alumno↔clase se hace mediante ``User.class_id`` (FK), que se
agrega vía auto-migración en ``app/__init__.py``. ``User.teacher_id`` se
mantiene por compatibilidad y se actualiza en cascada cuando se asigna una
clase, para no romper queries legacy.
"""

import json
import secrets
import string
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text

from app.extensions import db


CLASS_CODE_ALPHABET = string.ascii_uppercase + string.digits
CLASS_CODE_LENGTH = 6


class Class(db.Model):
    __tablename__ = "class"

    id = Column(Integer, primary_key=True, autoincrement=True)
    teacher_id = Column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    name = Column(String(120), nullable=False)
    # Código de inscripción público. Los alumnos lo ingresan al registrarse
    # (o al editar su perfil) para asociarse a la clase.
    class_code = Column(String(16), unique=True, nullable=False, index=True)
    # Lista de IDs de desafíos seleccionados por el docente, serializada
    # como JSON. Si está vacía o NULL ⇒ no hay desafíos visibles para los
    # alumnos de la clase. El frontend ofrece "seleccionar todo" como
    # atajo, pero la representación interna sigue siendo una lista
    # explícita para que sea estable frente a futuras altas/bajas del
    # banco general.
    selected_challenge_ids = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # ------------------------------------------------------------------
    # Helpers de serialización del listado de desafíos
    # ------------------------------------------------------------------
    def get_selected_ids(self):
        """Devuelve la lista de IDs como ``list[int]`` (vacía si no hay)."""
        if not self.selected_challenge_ids:
            return []
        try:
            data = json.loads(self.selected_challenge_ids)
            if isinstance(data, list):
                return [int(x) for x in data if isinstance(x, (int, str)) and str(x).isdigit()]
        except (ValueError, TypeError):
            pass
        return []

    def set_selected_ids(self, ids):
        """Persiste una lista de IDs deduplicada y ordenada como JSON."""
        if not ids:
            self.selected_challenge_ids = json.dumps([])
            return
        clean = []
        seen = set()
        for x in ids:
            try:
                v = int(x)
            except (ValueError, TypeError):
                continue
            if v not in seen:
                seen.add(v)
                clean.append(v)
        clean.sort()
        self.selected_challenge_ids = json.dumps(clean)

    # ------------------------------------------------------------------
    # Generación de códigos únicos
    # ------------------------------------------------------------------
    @classmethod
    def generate_unique_class_code(cls):
        """Genera un código único contra esta tabla y contra ``user.class_code``
        (el legacy del modelo viejo). Esto evita colisiones durante la
        migración."""
        # Import diferido para evitar ciclo import → usuario.
        from .user_model import User

        while True:
            code = "".join(
                secrets.choice(CLASS_CODE_ALPHABET) for _ in range(CLASS_CODE_LENGTH)
            )
            collision_class = cls.query.filter_by(class_code=code).first()
            collision_user = User.query.filter_by(class_code=code).first()
            if not collision_class and not collision_user:
                return code

    @classmethod
    def get_by_code(cls, code):
        if not code:
            return None
        return cls.query.filter_by(class_code=code.strip().upper()).first()

    # ------------------------------------------------------------------
    # Vista pública / privada
    # ------------------------------------------------------------------
    def to_dict(self, include_students_count=False):
        out = {
            "id": self.id,
            "teacher_id": self.teacher_id,
            "name": self.name,
            "class_code": self.class_code,
            "selected_challenge_ids": self.get_selected_ids(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_students_count:
            # Import diferido para evitar ciclo.
            from .user_model import User, ROLE_ALUMNO

            out["students_count"] = User.query.filter_by(
                class_id=self.id, role=ROLE_ALUMNO
            ).count()
        return out
