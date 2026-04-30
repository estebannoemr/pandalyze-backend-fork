"""
Seed de usuarios demo para presentaciones / desarrollo.

Crea dos docentes (Sofía y Claudia) y tres alumnos por cada una. Es
idempotente: si los usuarios ya existen, no se tocan ni sus contraseñas
ni sus class_codes (para no invalidar invitaciones ya repartidas).

El seed se controla con la variable de entorno ``SEED_DEMO_USERS``:
- "1" / "true" / "yes"  → corre el seed al arrancar la app.
- cualquier otro valor (o no definida) → el seed NO corre.

La contraseña por defecto de los usuarios demo se puede sobrescribir con
``SEED_DEMO_PASSWORD``; default razonable para escenarios de demo: "demo1234".

Esta función se invoca desde ``create_app`` después de ``db.create_all()`` y
del bootstrap de admin, así puede asociar alumnos a docentes recién creados.
"""

import os
from datetime import datetime, timedelta

from .extensions import db
from .models.challenge_result_model import ChallengeResult
from .models.user_model import User, ROLE_DOCENTE, ROLE_ALUMNO


# Definimos los datos demo en estructuras simples; agregar/quitar usuarios
# es trivial y se mantiene la idempotencia automáticamente.
DEMO_TEACHERS = [
    {"email": "sofia@pandalyze.test", "display": "Sofía"},
    {"email": "claudia@pandalyze.test", "display": "Claudia"},
]

DEMO_STUDENTS = [
    # Alumnos de Sofía
    {"email": "alumno1.sofia@pandalyze.test", "teacher": "sofia@pandalyze.test"},
    {"email": "alumno2.sofia@pandalyze.test", "teacher": "sofia@pandalyze.test"},
    {"email": "alumno3.sofia@pandalyze.test", "teacher": "sofia@pandalyze.test"},
    # Alumnos de Claudia
    {"email": "alumno1.claudia@pandalyze.test", "teacher": "claudia@pandalyze.test"},
    {"email": "alumno2.claudia@pandalyze.test", "teacher": "claudia@pandalyze.test"},
    {"email": "alumno3.claudia@pandalyze.test", "teacher": "claudia@pandalyze.test"},
    {"email": "contacto.estebanmr@gmail.com", "teacher": "claudia@pandalyze.test"},
]

DEMO_CONTACT_COMPLETED_CHALLENGES = [
    # Primeros 3 básicos, primeros 2 intermedios y primer avanzado.
    # El orden sigue el orden de la lista cargada por la API.
    {"challenge_id": 1, "duration_seconds": 180, "points": 10},
    {"challenge_id": 2, "duration_seconds": 140, "points": 10},
    {"challenge_id": 3, "duration_seconds": 95, "points": 10},
    {"challenge_id": 7, "duration_seconds": 160, "points": 25},
    {"challenge_id": 8, "duration_seconds": 115, "points": 25},
    {"challenge_id": 13, "duration_seconds": 130, "points": 50},
]


def _truthy(value):
    return (value or "").strip().lower() in {"1", "true", "yes", "on", "si"}


def is_seed_enabled():
    """Devuelve True si la variable SEED_DEMO_USERS está activa."""
    return _truthy(os.getenv("SEED_DEMO_USERS"))


def seed_demo_users(app):
    """
    Crea (idempotentemente) los docentes y alumnos demo. Devuelve un dict
    con conteo de creaciones para logging.

    No toca usuarios existentes — si por error algún email demo coincide con
    un usuario real, ese usuario se respeta tal cual.
    """
    password = os.getenv("SEED_DEMO_PASSWORD") or "demo1234"

    created_teachers = 0
    created_students = 0
    created_results = 0

    teacher_by_email = {}

    # 1) Docentes
    for spec in DEMO_TEACHERS:
        email = spec["email"].strip().lower()
        existing = User.query.filter_by(email=email).first()
        if existing is not None:
            teacher_by_email[email] = existing
            continue
        teacher = User(email=email, role=ROLE_DOCENTE)
        teacher.set_password(password)
        teacher.class_code = User.generate_unique_class_code()
        db.session.add(teacher)
        db.session.flush()  # asignar id antes de usarlo como teacher_id
        teacher_by_email[email] = teacher
        created_teachers += 1

    # 2) Alumnos
    for spec in DEMO_STUDENTS:
        email = spec["email"].strip().lower()
        teacher_email = spec["teacher"].strip().lower()
        existing = User.query.filter_by(email=email).first()
        if existing is not None:
            continue
        teacher = teacher_by_email.get(teacher_email)
        if teacher is None:
            # Si por alguna razón no encontramos al docente, saltamos
            # ese alumno en lugar de crear un huérfano.
            app.logger.warning(
                "Seed demo: docente %s no encontrado, salto alumno %s",
                teacher_email,
                email,
            )
            continue
        student = User(email=email, role=ROLE_ALUMNO)
        student.set_password(password)
        student.teacher_id = teacher.id
        db.session.add(student)
        created_students += 1

    # 3) Progreso demo para la cuenta de contacto
    contact = User.query.filter_by(email="contacto.estebanmr@gmail.com").first()
    if contact is not None:
        base_timestamp = datetime.utcnow() - timedelta(days=1)
        for index, spec in enumerate(DEMO_CONTACT_COMPLETED_CHALLENGES):
            already_passed = ChallengeResult.query.filter_by(
                user_id=contact.id,
                challenge_id=spec["challenge_id"],
                passed=True,
            ).first()
            if already_passed is not None:
                continue
            result = ChallengeResult(
                user_id=contact.id,
                challenge_id=spec["challenge_id"],
                passed=True,
                points_earned=spec["points"],
                first_try=True,
                attempts=1,
                started_at=base_timestamp + timedelta(minutes=index * 15),
                duration_seconds=spec["duration_seconds"],
                active_seconds=spec["duration_seconds"],
            )
            db.session.add(result)
            created_results += 1

    if created_teachers or created_students or created_results:
        db.session.commit()
        app.logger.info(
            "Seed demo: %d docente(s), %d alumno(s) y %d resultado(s) creados.",
            created_teachers,
            created_students,
            created_results,
        )
    else:
        app.logger.info("Seed demo: nada que crear (ya estaba todo).")

    return {
        "created_teachers": created_teachers,
        "created_students": created_students,
        "created_results": created_results,
        "password": password,
    }
