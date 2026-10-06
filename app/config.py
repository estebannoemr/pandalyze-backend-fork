import os
import logging
from datetime import timedelta
from dotenv import load_dotenv


class Config:
    load_dotenv()

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---------- CORS ----------
    # Orígenes permitidos, separados por coma en la variable de entorno
    # CORS_ORIGINS. Flask-Cors (los decoradores @cross_origin de los
    # endpoints) toma esta config. Default: localhost para desarrollo.
    # En producción setear, por ejemplo:
    #   CORS_ORIGINS=https://pandalyze2.netlify.app,http://localhost:3000
    CORS_ORIGINS = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if o.strip()
    ]

    # Determina el entorno segun la variable de entorno FLASK_ENV, o defaultea a development
    env = os.getenv("FLASK_ENV", "development")

    # Configuración común
    DEBUG = True if env == "development" else False
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST")
    DB_NAME = os.getenv("DB_NAME")
    # Prefer DATABASE_URL if provided (12-factor apps). Fallback to explicit
    # DB_* vars. If neither provided, usamos SQLite para desarrollo.
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    if not SQLALCHEMY_DATABASE_URI:
        if DB_USER and DB_HOST and DB_NAME:
            # Construir URI postgres si se definieron variables por separado
            SQLALCHEMY_DATABASE_URI = (
                f"postgresql://{DB_USER}:{DB_PASSWORD or ''}@{DB_HOST}/{DB_NAME}"
            )
        else:
            SQLALCHEMY_DATABASE_URI = "sqlite:///project.db"

    # ---------- JWT ----------
    _jwt_secret = os.getenv("JWT_SECRET_KEY")
    if not _jwt_secret:
        logging.warning(
            "JWT_SECRET_KEY no está definido. Usando un default inseguro para "
            "desarrollo. Definí JWT_SECRET_KEY en .env antes de producción."
        )
        _jwt_secret = "dev-insecure-pandalyze-secret-change-me"

    JWT_SECRET_KEY = _jwt_secret
    # Duración de la sesión. Pasado ese tiempo el token vence y el usuario
    # tiene que volver a iniciar sesión. El default (8 horas) cubre una
    # jornada de clase sin dejar la cuenta abierta durante días en una
    # computadora compartida. Se puede ajustar con JWT_EXPIRES_HOURS.
    try:
        _jwt_hours = float(os.getenv("JWT_EXPIRES_HOURS") or 8)
        if _jwt_hours <= 0:
            _jwt_hours = 8
    except ValueError:
        _jwt_hours = 8
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=_jwt_hours)

    # ---------- Admin ----------
    # Email del usuario admin. Si ADMIN_PASSWORD también está definido, el
    # admin se crea automáticamente al arrancar la aplicación (bootstrap),
    # de modo que no haga falta registrarlo manualmente. Si el usuario ya
    # existe, sigue siendo promovido a rol admin (idempotente).
    ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD") or ""


def get_config():
    return Config()
