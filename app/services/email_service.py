"""
Helper de envío de email para flujos transaccionales (recuperación de
contraseña, etc.). Está diseñado para no acoplar el resto del código al
proveedor: la app llama ``send_email(to, subject, body)`` sin saber si
detrás hay SMTP, SendGrid, Mailgun o un simple ``logger.info``.

En desarrollo / tesis, el helper detecta si hay configuración SMTP en el
entorno (``SMTP_HOST`` + credenciales) y envía por SMTP nativo. Si no hay
configuración, simplemente loguea el contenido del email — útil para
demos y para iterar sin depender de un MTA real.
"""

import os
import smtplib
from email.message import EmailMessage


def _smtp_config():
    """Devuelve dict con la config SMTP si está completa, o None."""
    host = (os.getenv("SMTP_HOST") or "").strip()
    if not host:
        return None
    return {
        "host": host,
        "port": int(os.getenv("SMTP_PORT") or "587"),
        "user": os.getenv("SMTP_USER") or "",
        "password": os.getenv("SMTP_PASSWORD") or "",
        "from_addr": (
            os.getenv("SMTP_FROM")
            or os.getenv("SMTP_USER")
            or "no-reply@pandalyze.local"
        ),
        "use_tls": (os.getenv("SMTP_USE_TLS") or "1").strip() in {"1", "true", "yes", "on"},
    }


def send_email(app, to, subject, body):
    """
    Envia un email plano. Si no hay SMTP configurado, loguea el contenido
    completo en INFO. Devuelve True si se envió por SMTP, False si fue solo
    log. Cualquier error se loguea como warning sin levantar excepción —
    el endpoint que llama no debería fallar por un problema de mail.
    """
    config = _smtp_config()
    if config is None:
        # Modo dev: dejamos el contenido en logs para que el operador (o el
        # desarrollador) pueda copiar el link de reset desde la consola.
        app.logger.info(
            "[email_service:no-smtp] to=%s subject=%s body=\n%s",
            to,
            subject,
            body,
        )
        return False

    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = config["from_addr"]
        msg["To"] = to
        msg.set_content(body)

        with smtplib.SMTP(config["host"], config["port"], timeout=10) as s:
            if config["use_tls"]:
                s.starttls()
            if config["user"]:
                s.login(config["user"], config["password"])
            s.send_message(msg)
        app.logger.info("[email_service:sent] to=%s subject=%s", to, subject)
        return True
    except Exception as e:  # pragma: no cover - depende de la red
        app.logger.warning(
            "[email_service:error] to=%s subject=%s err=%s", to, subject, e
        )
        # Loguear el contenido para que el operador pueda actuar manualmente.
        app.logger.info(
            "[email_service:fallback-log] body=\n%s", body
        )
        return False
