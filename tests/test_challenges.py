"""
Tests automáticos del banco de desafíos.

Cada vez que ``app/data/challenges.json`` cambia (se agrega un desafío,
se edita un solution_code, se cambia un expected_keyword), este módulo
detecta inconsistencias antes de que lleguen al alumno:

- Carga el JSON.
- Para cada desafío:
    1. Verifica los campos obligatorios.
    2. Parsea el ``csv_content`` embebido como un DataFrame.
    3. Ejecuta el ``solution_code`` reemplazando el wrapper
       ``read_csv(csv_id)`` por el DataFrame ya cargado.
    4. Captura el ``stdout`` y verifica que el ``expected_keyword``
       efectivamente aparezca en la salida.

Pensado para correr en CI con `pytest tests/`. Si algún desafío rompe la
verificación, el job falla y el merge se bloquea.

Cómo correrlo localmente:
    pip install -r requirements.txt pytest
    pytest tests/test_challenges.py -v
"""

import contextlib
import io
import json
import re
from pathlib import Path

import pandas as pd
import pytest


CHALLENGES_PATH = (
    Path(__file__).resolve().parent.parent / "app" / "data" / "challenges.json"
)


REQUIRED_FIELDS = {
    "id",
    "title",
    "difficulty",
    "points",
    "description",
    "instructions",
    "csv_filename",
    "csv_content",
    "expected_keyword",
    "solution_code",
}

VALID_DIFFICULTIES = {"basico", "intermedio", "avanzado"}
EXPECTED_POINTS = {"basico": 10, "intermedio": 25, "avanzado": 50}


def _load_challenges():
    with open(CHALLENGES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _execute_solution(challenge):
    """Ejecuta solution_code con el csv_content embebido y devuelve stdout."""
    df = pd.read_csv(io.StringIO(challenge["csv_content"]))
    code = challenge["solution_code"]

    # Reemplazo del wrapper que usan los alumnos: en el código de solución
    # pueden aparecer llamadas como ``read_csv(123)`` o ``read_csv("foo")``;
    # las sustituimos por una variable ``__df`` que apunta al DataFrame ya
    # cargado en memoria. Si no hay matches, el solution_code probablemente
    # asume que ``df`` ya está disponible: lo inyectamos también como ``df``.
    code_executable = re.sub(r"read_csv\([^\)]*\)", "__df", code)

    namespace = {
        "__df": df,
        "df": df,
        "pd": pd,
        "__builtins__": __builtins__,
    }

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code_executable, namespace)
    return buf.getvalue()


CHALLENGES = _load_challenges()


@pytest.mark.parametrize(
    "challenge", CHALLENGES, ids=[f"id={c['id']}-{c['title'][:30]}" for c in CHALLENGES]
)
def test_challenge_required_fields(challenge):
    missing = REQUIRED_FIELDS - set(challenge.keys())
    assert not missing, f"Faltan campos obligatorios: {missing}"


@pytest.mark.parametrize(
    "challenge", CHALLENGES, ids=[f"id={c['id']}" for c in CHALLENGES]
)
def test_challenge_difficulty_and_points(challenge):
    diff = challenge.get("difficulty")
    assert diff in VALID_DIFFICULTIES, f"Dificultad inválida: {diff}"
    expected = EXPECTED_POINTS[diff]
    assert challenge.get("points") == expected, (
        f"Desafío {challenge['id']}: para dificultad '{diff}' esperaba "
        f"{expected} puntos, encontró {challenge.get('points')}"
    )


def test_challenge_ids_are_unique():
    ids = [c["id"] for c in CHALLENGES]
    assert len(ids) == len(set(ids)), "Hay IDs de desafío duplicados."


@pytest.mark.parametrize(
    "challenge", CHALLENGES, ids=[f"id={c['id']}-{c['title'][:30]}" for c in CHALLENGES]
)
def test_challenge_solution_produces_expected_keyword(challenge):
    """
    El test pedagógico-clave: la solución oficial, ejecutada sobre el CSV
    embebido, produce un output que contiene el ``expected_keyword``.

    Si esta aserción falla, significa que un alumno que escriba la solución
    correcta NO la pasaría — un bug del banco de desafíos, no del alumno.
    """
    output = _execute_solution(challenge)
    expected = challenge["expected_keyword"]
    assert expected in output, (
        f"Desafío {challenge['id']} ({challenge['title']!r}): el output de la "
        f"solución oficial no contiene el expected_keyword {expected!r}.\n"
        f"Output capturado:\n{output}"
    )
