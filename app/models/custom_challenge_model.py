from datetime import datetime
import json

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean

from app.extensions import db


CUSTOM_CHALLENGE_ID_OFFSET = 100000


class CustomChallenge(db.Model):
    __tablename__ = "custom_challenge"

    id = Column(Integer, primary_key=True, autoincrement=True)
    creator_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    difficulty = Column(String(20), nullable=False)
    category = Column(String(40), nullable=True)
    points = Column(Integer, nullable=False, default=10)
    description = Column(Text, nullable=False)
    instructions_json = Column(Text, nullable=False, default="[]")
    hint = Column(Text, nullable=True)
    csv_filename = Column(String(255), nullable=False)
    csv_content = Column(Text, nullable=False)
    csv_url = Column(String(1000), nullable=True)
    theory_url = Column(String(500), nullable=True)
    expected_keyword = Column(String(255), nullable=False)
    solution_code = Column(Text, nullable=False)
    feedback_correct = Column(Text, nullable=False)
    feedback_incorrect = Column(Text, nullable=False)
    suggestion = Column(Text, nullable=True)
    time_limit_seconds = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    @property
    def external_id(self):
        return CUSTOM_CHALLENGE_ID_OFFSET + int(self.id)

    @classmethod
    def from_external_id(cls, challenge_id):
        internal_id = int(challenge_id) - CUSTOM_CHALLENGE_ID_OFFSET
        if internal_id <= 0:
            return None
        return cls.query.get(internal_id)

    def _instructions(self):
        try:
            data = json.loads(self.instructions_json or "[]")
            if isinstance(data, list):
                return [str(x) for x in data if str(x).strip()]
        except Exception:
            pass
        return []

    def to_runtime_dict(self):
        return {
            "id": self.external_id,
            "is_custom": True,
            "creator_id": self.creator_id,
            "title": self.title,
            "difficulty": self.difficulty,
            "points": self.points,
            "description": self.description,
            "instructions": self._instructions(),
            "hint": self.hint or "",
            "csv_filename": self.csv_filename,
            "csv_content": self.csv_content,
            "csv_url": self.csv_url,
            "theory_url": self.theory_url,
            "category": self.category,
            "time_limit_seconds": self.time_limit_seconds,
            "expected_keyword": self.expected_keyword,
            "solution_code": self.solution_code,
            "feedback_correct": self.feedback_correct,
            "feedback_incorrect": self.feedback_incorrect,
            "suggestion": self.suggestion,
        }