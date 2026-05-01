"""Elimina y recrea la base de datos PostgreSQL.

Uso:
    py drop_and_recreate_db.py
"""

from __future__ import annotations

import os
from urllib.parse import quote

import psycopg2
from dotenv import load_dotenv
from sqlalchemy.engine import make_url


def _build_database_url() -> str:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    db_user = os.getenv("DB_USER")
    db_password = os.getenv("DB_PASSWORD") or ""
    db_host = os.getenv("DB_HOST")
    db_name = os.getenv("DB_NAME")
    if not (db_user and db_host and db_name):
        raise SystemExit(
            "Faltan variables de entorno. Definí DATABASE_URL o DB_USER/DB_PASSWORD/DB_HOST/DB_NAME."
        )

    password_part = f":{quote(db_password)}" if db_password else ""
    return f"postgresql://{db_user}{password_part}@{db_host}/{db_name}"


def main() -> None:
    load_dotenv()
    database_url = _build_database_url()
    url = make_url(database_url)

    if url.drivername not in {"postgresql", "postgresql+psycopg2"}:
        raise SystemExit("DATABASE_URL debe apuntar a PostgreSQL.")

    if not url.database:
        raise SystemExit("DATABASE_URL debe incluir el nombre de la base de datos.")

    admin_db = os.getenv("POSTGRES_ADMIN_DB") or "postgres"
    conn_kwargs = {
        "user": url.username,
        "password": url.password,
        "host": url.host or "localhost",
        "port": url.port or 5432,
        "dbname": admin_db,
    }

    print(f"Conectando a PostgreSQL en {conn_kwargs['host']}:{conn_kwargs['port']}...")
    conn = psycopg2.connect(**conn_kwargs)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            # Eliminar bases de datos que estén usando la BD destino
            print(f"Eliminando base '{url.database}'...")
            cur.execute(f"""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
            """, (url.database,))
            
            cur.execute(f'DROP DATABASE IF EXISTS "{url.database}"')
            print(f"Base '{url.database}' eliminada.")

            # Crear base nueva
            print(f"Creando base '{url.database}'...")
            cur.execute(f'CREATE DATABASE "{url.database}"')
            print("Base creada correctamente.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
