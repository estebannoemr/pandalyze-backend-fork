from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager

db = SQLAlchemy()
jwt = JWTManager()


# Flask-Limiter es opcional en tiempo de import: si todavía no se hizo
# pip install -r requirements.txt, exponemos un stub que no impone límites.
# Esto permite que la app arranque incluso sin la dependencia, y los
# decoradores @limiter.limit(...) en los endpoints se vuelven no-ops.
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[],  # Sin límite por defecto; cada endpoint elige.
        storage_uri="memory://",
        # En producción real cambiar a redis://... para que los límites
        # sobrevivan entre workers.
    )
    LIMITER_ENABLED = True
except ImportError:  # pragma: no cover
    class _NoopLimiter:
        def init_app(self, app):  # noqa: D401
            return None

        def limit(self, *args, **kwargs):
            def deco(fn):
                return fn

            return deco

        def exempt(self, fn):
            return fn

    limiter = _NoopLimiter()
    LIMITER_ENABLED = False
