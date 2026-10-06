import os
from app import create_app
from app import db
from app.models.csv_model import CSVData
from app.models.challenge_result_model import ChallengeResult
from app.models.user_model import User

# Creamos la instancia de la aplicación Flask.
# El CORS se resuelve por los decoradores @cross_origin de cada endpoint,
# que respetan la lista Config.CORS_ORIGINS (variable de entorno CORS_ORIGINS).
# Se evita el CORS(app) global para no duplicar el header Access-Control-Allow-Origin.
app = create_app()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        User.query.all()
        CSVData.query.all()
        ChallengeResult.query.all()

    debug_enabled = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_enabled, use_reloader=debug_enabled)
