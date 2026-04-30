"""Crea la base de datos PostgreSQL definida en el entorno.

Uso:
    py create_postgres_db.py

Lee primero DATABASE_URL. Si no existe, arma la URL a partir de DB_USER,
DB_PASSWORD, DB_HOST y DB_NAME. El script solo crea la base de datos si no
existe; no toca tablas ni datos.
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
        # CREATE DATABASE no puede ejecutarse dentro de una transacción.
        # Usamos una conexión explícita y autocommit, sin contexto `with`,
        # para evitar que psycopg2 envuelva la operación en un bloque tx.
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (url.database,))
            exists = cur.fetchone() is not None

            if exists:
                print(f"La base '{url.database}' ya existe.")
                return

            print(f"Creando base '{url.database}'...")
            cur.execute(f'CREATE DATABASE "{url.database}"')
            print("Base creada correctamente.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()