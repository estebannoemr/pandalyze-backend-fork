import os
import logging
from datetime import timedelta
from dotenv import load_dotenv


class Config:
    load_dotenv()

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Determina el entorno segun la variable de entorno FLASK_ENV, o defaultea a development
    env = os.getenv("FLASK_ENV", "development")

    # Configuración común
    DEBUG = True if env == "development" else False
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    DB_HOST = os.getenv("DB_HOST")
    DB_NAME = os.getenv("DB_NAME")
    # SQLALCHEMY_DATABASE_URI = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"
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
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)

    # ---------- Admin ----------
    # Email que será promovido automáticamente a rol admin al registrarse o
    # loguearse. Si queda vacío, no existe admin en el sistema.
    ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip().lower()


def get_config():
    return Config()
