import os
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from io import TextIOWrapper
from flask_cors import cross_origin

from ..services.csv_service import save_csv_data, get_csv_by_content
from ..utils.request_scope import resolve_scope

bp = Blueprint("save_csv", __name__)

MAX_CONTENT_LENGTH_IN_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {"csv"}


def allowed_file(filename):
    return (
        "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


@bp.route("/uploadCsv", methods=["POST"])
@cross_origin()
def upload_csv():
    user_id, guest_id = resolve_scope()
    if user_id is None and guest_id is None:
        return jsonify({"error": "Se requiere autenticación o X-Guest-Id."}), 401

    file = request.files.get("csv")
    if not file:
        return jsonify({"error": "No file part"}), 400
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "File not allowed"}), 400

    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    if file_size > MAX_CONTENT_LENGTH_IN_BYTES:
        return (
            jsonify({"error": "El archivo CSV no puede superar los 10 megabytes"}),
            400,
        )

    filename = secure_filename(file.filename)
    csv_content = TextIOWrapper(file, encoding="utf-8-sig").read()

    csv_id, columns_names = get_csv_by_content(
        csv_content, user_id=user_id, guest_id=guest_id
    )
    if not csv_id:
        csv_id, columns_names = save_csv_data(
            filename, csv_content, user_id=user_id, guest_id=guest_id
        )

    return (
        jsonify({
            "fileName": filename,
            "csvId": csv_id,
            "columnsNames": columns_names,
        }),
        201,
    )
