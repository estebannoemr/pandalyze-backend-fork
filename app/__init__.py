from flask import Flask, request
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity
from datetime import datetime

from .config import get_config
from .extensions import db, jwt, limiter


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    db.init_app(app)
    jwt.init_app(app)
    # Rate limiting: opcional. Si flask-limiter no está instalado, el stub
    # de extensions.py absorbe init_app sin efecto.
    try:
        limiter.init_app(app)
    except Exception as _e:  # pragma: no cover
        app.logger.warning("Limiter init falló: %s", _e)

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
        from .models.password_reset_token_model import PasswordResetToken  # noqa: F401

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

        # Aseguramos que las tablas existen antes de migrar/bootstrappear.
        # Es idempotente, así que correrlo aquí no rompe el create_all de run.py.
        try:
            db.create_all()
        except Exception:
            pass

        # Auto-migracion best-effort: agrega columnas de timing si no existen.
        # Esto evita tener que borrar la DB al actualizar el schema.
        try:
            from sqlalchemy import text as _sa_text

            with db.engine.connect() as _conn:
                inspector_rows = _conn.exec_driver_sql(
                    "PRAGMA table_info(challenge_result)"
                ).fetchall()
                existing_cols = {row[1] for row in inspector_rows}
                alter_stmts = []
                if "started_at" not in existing_cols:
                    alter_stmts.append(
                        "ALTER TABLE challenge_result ADD COLUMN started_at DATETIME"
                    )
                if "duration_seconds" not in existing_cols:
                    alter_stmts.append(
                        "ALTER TABLE challenge_result ADD COLUMN duration_seconds INTEGER"
                    )
                if "active_seconds" not in existing_cols:
                    alter_stmts.append(
                        "ALTER TABLE challenge_result ADD COLUMN active_seconds INTEGER"
                    )
                for stmt in alter_stmts:
                    _conn.execute(_sa_text(stmt))
                if alter_stmts:
                    _conn.commit()
        except Exception:
            # Si la tabla no existe todavia (primera corrida), create_all se encarga.
            pass

        # Bootstrap del admin: si ADMIN_EMAIL y ADMIN_PASSWORD están definidos
        # en el entorno, garantizamos que ese usuario exista con rol admin sin
        # necesidad de registrarlo manualmente.
        # - Si no existe, lo creamos.
        # - Si existe pero no es admin, lo promovemos.
        # - Si existe y ya es admin, no tocamos su password (no pisamos lo que
        #   el admin haya cambiado a mano luego del primer arranque).
        try:
            admin_email = (app.config.get("ADMIN_EMAIL") or "").strip().lower()
            admin_password = app.config.get("ADMIN_PASSWORD") or ""
            if admin_email and admin_password:
                from .models.user_model import (
                    User as _User,
                    ROLE_ADMIN as _ROLE_ADMIN,
                )
                existing = _User.query.filter_by(email=admin_email).first()
                if existing is None:
                    new_admin = _User(email=admin_email, role=_ROLE_ADMIN)
                    new_admin.set_password(admin_password)
                    new_admin.teacher_id = None
                    new_admin.class_code = None
                    db.session.add(new_admin)
                    db.session.commit()
                    app.logger.info(
                        "Admin bootstrap: creado usuario %s con rol admin.",
                        admin_email,
                    )
                elif existing.role != _ROLE_ADMIN:
                    existing.role = _ROLE_ADMIN
                    existing.teacher_id = None
                    existing.class_code = None
                    db.session.commit()
                    app.logger.info(
                        "Admin bootstrap: promovido %s a rol admin.",
                        admin_email,
                    )
        except Exception as _e:
            db.session.rollback()
            app.logger.warning("Admin bootstrap fallo: %s", _e)

        # Seed de usuarios demo (docentes Sofía/Claudia + alumnos), controlado
        # por la env var SEED_DEMO_USERS. Idempotente: no recrea usuarios ya
        # existentes ni toca sus contraseñas. Para desactivar, basta con quitar
        # SEED_DEMO_USERS del .env (o ponerla en cualquier valor que no sea
        # truthy).
        try:
            from .seeds import is_seed_enabled, seed_demo_users
            if is_seed_enabled():
                seed_demo_users(app)
        except Exception as _e:
            db.session.rollback()
            app.logger.warning("Seed demo fallo: %s", _e)

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
