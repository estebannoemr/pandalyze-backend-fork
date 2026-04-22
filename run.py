from app import create_app
from flask_cors import CORS
from app import db
from app.models.csv_model import CSVData
from app.models.challenge_result_model import ChallengeResult

# Creamos la instancia de la aplicación Flask
app = create_app()
CORS(app)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        CSVData.query.all()
        ChallengeResult.query.all()

    app.run()