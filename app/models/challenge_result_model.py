from datetime import datetime
from sqlalchemy import Column, Integer, Boolean, DateTime, ForeignKey

from app.extensions import db


class ChallengeResult(db.Model):
    """
    Modelo que persiste cada intento y resultado de un desafío por usuario.

    Guardamos un registro por cada verificación (exitosa o no) para poder
    calcular la gamificación (puntos, nivel, emblemas, rachas) desde el
    servidor en ``GET /challenges/gamification/status``, siempre filtrando
    por el usuario autenticado.
    """

    __tablename__ = "challenge_result"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    challenge_id = Column(Integer, nullable=False, index=True)
    passed = Column(Boolean, nullable=False, default=False)
    points_earned = Column(Integer, nullable=False, default=0)
    first_try = Column(Boolean, nullable=False, default=False)
    attempts = Column(Integer, nullable=False, default=1)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __init__(
        self,
        user_id,
        challenge_id,
        passed,
        points_earned=0,
        first_try=False,
        attempts=1,
    ):
        self.user_id = user_id
        self.challenge_id = challenge_id
        self.passed = passed
        self.points_earned = points_earned
        self.first_try = first_try
        self.attempts = attempts
        self.timestamp = datetime.utcnow()

    def __repr__(self):
        return (
            f"<ChallengeResult user={self.user_id} "
            f"challenge_id={self.challenge_id} "
            f"passed={self.passed} points={self.points_earned}>"
        )

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "challenge_id": self.challenge_id,
            "passed": self.passed,
            "points_earned": self.points_earned,
            "first_try": self.first_try,
            "attempts": self.attempts,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }

    @classmethod
    def get_attempts_for_challenge(cls, challenge_id, user_id):
        """Cantidad total de intentos registrados para un desafío de un usuario."""
        return cls.query.filter_by(
            challenge_id=challenge_id, user_id=user_id
        ).count()

    @classmethod
    def has_passed(cls, challenge_id, user_id):
        """True si hay al menos un intento exitoso del usuario para ese desafío."""
        return (
            cls.query.filter_by(
                challenge_id=challenge_id, user_id=user_id, passed=True
            ).first()
            is not None
        )

    @classmethod
    def all_passed_for_user(cls, user_id):
        """Resultados exitosos del usuario, ordenados por timestamp ascendente."""
        return (
            cls.query.filter_by(passed=True, user_id=user_id)
            .order_by(cls.timestamp.asc())
            .all()
        )
