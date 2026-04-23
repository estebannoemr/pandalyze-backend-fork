from sqlalchemy import Column, Integer, String, Text, ForeignKey, UniqueConstraint

from app.extensions import db


class CSVData(db.Model):
    """CSV asociado a un usuario autenticado o a una sesión de invitado.

    Exactamente uno de user_id / guest_id debe estar presente. Unicidad de
    filename está garantizada dentro de cada scope.
    """

    __tablename__ = "csv_data"
    __table_args__ = (
        UniqueConstraint("filename", "user_id", name="uq_csv_filename_user"),
        UniqueConstraint("filename", "guest_id", name="uq_csv_filename_guest"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(100), nullable=False)
    data = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    guest_id = Column(String(64), nullable=True, index=True)

    def __init__(self, filename, data, user_id=None, guest_id=None):
        if (user_id is None) == (guest_id is None):
            raise ValueError(
                "CSVData requiere exactamente uno de user_id o guest_id."
            )
        self.filename = filename
        self.data = data
        self.user_id = user_id
        self.guest_id = guest_id

    def __repr__(self):
        owner = (
            "user=%s" % self.user_id if self.user_id else "guest=%s" % self.guest_id
        )
        return "<CSV %r (%s)>" % (self.filename, owner)

    @classmethod
    def get_csv_by_id(cls, csv_id, user_id=None, guest_id=None):
        q = cls.query.filter_by(id=csv_id)
        if user_id is not None:
            q = q.filter_by(user_id=user_id)
        elif guest_id is not None:
            q = q.filter_by(guest_id=guest_id)
        return q.first()

    @classmethod
    def get_csv_by_filename(cls, filename, user_id=None, guest_id=None):
        q = cls.query.filter_by(filename=filename)
        if user_id is not None:
            q = q.filter_by(user_id=user_id)
        elif guest_id is not None:
            q = q.filter_by(guest_id=guest_id)
        else:
            return None
        return q.first()
