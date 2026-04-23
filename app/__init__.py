from flask import Flask, request
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from datetime import datetime

from .config import get_config
from .extensions import db, jwt


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    db.init_app(app)
    jwt.init_app(app)

    with app.app_context():
        from .endpoints.health_check import bp as health_check_bp
        from .endpoints.bd_check import bp as bd_check_bp
        from .endpoints.run_python_code import bp as run_python_code_bp
        from .endpoints.save_csv import bp as save_csv_bp
        from .endpoints.map_visualization import map_bp
        from .endpoints.challenges import bp as challenges_bp
        from .endpoints.auth import bp as auth_bp
        from .endpoints.teacher import bp as teacher_bp
        from .endpoints.admin import bp as admin_bp
        from .endpoints.stats import bp as stats_bp

        from .models.csv_model import CSVData  # noqa: F401
        from .models.challenge_result_model import ChallengeResult  # noqa: F401
        from .models.user_model import User  # noqa: F401

        app.register_blueprint(health_check_bp)
        app.register_blueprint(bd_check_bp)
        app.register_blueprint(run_python_code_bp)
        app.register_blueprint(save_csv_bp)
        app.register_blueprint(map_bp)
        app.register_blueprint(challenges_bp)
        app.register_blueprint(auth_bp)
        app.register_blueprint(teacher_bp)
        app.register_blueprint(admin_bp)
        app.register_blueprint(stats_bp)

    @app.before_request
    def _update_last_seen():
        if request.method == "OPTIONS":
            return
        try:
            verify_jwt_in_request(optional=True)
            user_id = get_jwt_identity()
        except Exception:
            user_id = None
        if user_id is None:
            return
        try:
            from .models.user_model import User as _User
            user = _User.query.get(int(user_id))
            if user is not None:
                user.last_seen_at = datetime.utcnow()
                db.session.commit()
        except Exception:
            db.session.rollback()

    return app
