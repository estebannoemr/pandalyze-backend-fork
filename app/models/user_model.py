"""
Modelo de Usuario con soporte para roles alumno/docente/admin.
"""

import re
import secrets
import string
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


ROLE_ALUMNO = "alumno"
ROLE_DOCENTE = "docente"
ROLE_ADMIN = "admin"
VALID_ROLES = {ROLE_ALUMNO, ROLE_DOCENTE, ROLE_ADMIN}

CLASS_CODE_ALPHABET = string.ascii_uppercase + string.digits
CLASS_CODE_LENGTH = 6


def _email_to_display_name(email):
    if not email:
        return None
    local = email.split("@", 1)[0].strip()
    if not local:
        return None
    parts = [p for p in re.split(r"[._-]+", local) if p]
    if not parts:
        return None
    return " ".join(p.capitalize() for p in parts)


class User(db.Model):
    __tablename__ = "user"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)
    class_code = Column(String(16), unique=True, nullable=True, index=True)
    teacher_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=True)

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def touch_last_seen(self):
        self.last_seen_at = datetime.utcnow()

    def to_dict(self):
        teacher_email = None
        teacher_name = None
        if self.teacher_id:
            teacher = self.__class__.query.get(self.teacher_id)
            if teacher is not None:
                teacher_email = teacher.email
                teacher_name = _email_to_display_name(teacher.email)

        return {
            "id": self.id,
            "email": self.email,
            "role": self.role,
            "class_code": self.class_code,
            "teacher_id": self.teacher_id,
            "teacher_email": teacher_email,
            "teacher_name": teacher_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_seen_at": (
                self.last_seen_at.isoformat() if self.last_seen_at else None
            ),
        }

    @classmethod
    def generate_unique_class_code(cls):
        while True:
            code = "".join(
                secrets.choice(CLASS_CODE_ALPHABET) for _ in range(CLASS_CODE_LENGTH)
            )
            if not cls.query.filter_by(class_code=code).first():
                return code

    @classmethod
    def get_by_email(cls, email):
        if not email:
            return None
        return cls.query.filter_by(email=email.strip().lower()).first()

    @classmethod
    def get_by_class_code(cls, code):
        if not code:
            return None
        return cls.query.filter_by(
            class_code=code.strip().upper(), role=ROLE_DOCENTE
        ).first()
