"""
Token temporal de recuperación de contraseña.

Cada vez que un usuario pide /auth/forgot-password generamos un token
aleatorio largo, lo persistimos asociado a su user_id y lo enviamos por
email (o lo logueamos si no hay SMTP configurado). El token tiene una
expiración corta (60 min por defecto) y es de un solo uso: una vez
consumido en /auth/reset-password queda marcado con ``used_at``.
"""

import secrets
from datetime import datetime, timedelta

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey

from app.extensions import db


# 60 minutos: largo suficiente para que el alumno revise su mail con
# tranquilidad, corto suficiente para acotar el riesgo si el token leakea.
TOKEN_TTL_MINUTES = 60
# 64 hex chars (256 bits) — overkill pero gratis dado secrets.token_hex.
TOKEN_NBYTES = 32


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_token"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer, ForeignKey("user.id"), nullable=False, index=True
    )
    token = Column(String(128), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)

    @classmethod
    def issue(cls, user_id, ttl_minutes=TOKEN_TTL_MINUTES):
        """Crea y persiste un token nuevo. Devuelve la instancia."""
        token = secrets.token_hex(TOKEN_NBYTES)
        now = datetime.utcnow()
        record = cls(
            user_id=user_id,
            token=token,
            created_at=now,
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
        db.session.add(record)
        db.session.commit()
        return record

    @classmethod
    def get_valid(cls, token):
        """Devuelve el token si existe, no expiró y no fue usado. Si no, None."""
        if not token:
            return None
        record = cls.query.filter_by(token=token).first()
        if record is None:
            return None
        now = datetime.utcnow()
        if record.used_at is not None:
            return None
        if record.expires_at and record.expires_at < now:
            return None
        return record

    def consume(self):
        """Marca el token como usado. No se puede reutilizar después."""
        self.used_at = datetime.utcnow()
        db.session.commit()
