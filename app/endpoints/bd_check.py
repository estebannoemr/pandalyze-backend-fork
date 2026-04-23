from flask import Blueprint, jsonify
from flask_cors import cross_origin

from app.models.csv_model import CSVData
from app.utils.request_scope import resolve_scope

bp = Blueprint("bd_check", __name__)


@bp.route("/bdCheck")
@cross_origin()
def bd_check():
    user_id, guest_id = resolve_scope()
    try:
        q = CSVData.query
        if user_id is not None:
            q = q.filter_by(user_id=user_id)
        elif guest_id is not None:
            q = q.filter_by(guest_id=guest_id)
        else:
            return jsonify({"message": "OK!", "count": 0}), 200
        return jsonify({"message": "OK!", "count": q.count()}), 200
    except Exception as e:
        from app import db
        db.create_all()
        return jsonify({"message": str(e)}), 200
