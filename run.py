import os
from app import create_app
from flask_cors import CORS
from app import db
from app.models.csv_model import CSVData
from app.models.challenge_result_model import ChallengeResult
from app.models.user_model import User

# Creamos la instancia de la aplicación Flask
app = create_app()
CORS(app)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        User.query.all()
        CSVData.query.all()
        ChallengeResult.query.all()

    debug_enabled = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_enabled, use_reloader=debug_enabled)
