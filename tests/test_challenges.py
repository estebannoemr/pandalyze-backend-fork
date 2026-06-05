"""Tests automaticos del banco de desafios.

Para cada uno de los 27 desafios:
  1. Verifica los campos obligatorios.
  2. Resuelve el dataset via dataset_service.fetch_dataset(dataset_key).
  3. Ejecuta solution_code reemplazando read_csv(csv_id) por el DataFrame.
  4. Captura stdout y verifica que expected_keyword aparezca.

Con LOCAL_DATASETS_DIR apuntando a Z-DATASETS corre offline.
"""

import contextlib
import io
import json
from pathlib import Path

import pandas as pd
import pytest

from app.services import dataset_service


DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "data"
CHALLENGES_PATHS = [
    DATA_DIR / "basico.json",
    DATA_DIR / "intermedio.json",
    DATA_DIR / "avanzado.json",
]


def _load_all_challenges():
    """Carga los 3 JSON. Strip de null bytes y whitespace trailing por si
    el filesystem (algunos mounts de WSL/Windows) padde con \x00."""
    out = []
    for p in CHALLENGES_PATHS:
        raw = p.read_bytes().rstrip(b"\x00").rstrip()
        out.extend(json.loads(raw.decode("utf-8")))
    return out


CHALLENGES = _load_all_challenges()
CHALLENGE_IDS = [c["id"] for c in CHALLENGES]


class _StubPlotlyFig:
    def show(self):
        pass


class _StubPlotly:
    def bar(self, **kw):
        return _StubPlotlyFig()

    def line(self, **kw):
        return _StubPlotlyFig()

    def scatter(self, **kw):
        return _StubPlotlyFig()

    def pie(self, **kw):
        return _StubPlotlyFig()


def _stub_generate_map(**kw):
    df = kw.get("dataframe")
    cat = kw.get("category_col")
    if df is not None and cat is not None and cat in df.columns:
        print(df[cat].head(20).to_string())


def _run_solution(solution_code, df):
    def read_csv(_id):
        return df.copy()

    buf = io.StringIO()
    ns = {
        "csv_id": "TEST_CSV_ID",
        "read_csv": read_csv,
        "pd": pd,
        "plotly": _StubPlotly(),
        "generate_map": _stub_generate_map,
        "print": print,
    }
    with contextlib.redirect_stdout(buf):
        exec(solution_code, ns)
    return buf.getvalue()


REQUIRED_FIELDS = (
    "id",
    "title",
    "difficulty",
    "category",
    "points",
    "description",
    "instructions",
    "hint",
    "dataset_key",
    "csv_filename",
    "expected_keyword",
    "solution_code",
    "feedback_correct",
    "feedback_incorrect",
)


@pytest.mark.parametrize("ch", CHALLENGES, ids=[str(i) for i in CHALLENGE_IDS])
def test_required_fields(ch):
    for field in REQUIRED_FIELDS:
        assert field in ch, f"Falta '{field}' en desafio {ch.get('id')}"
        assert ch[field] not in (None, "", []), (
            f"'{field}' vacio en desafio {ch.get('id')}"
        )


@pytest.mark.parametrize("ch", CHALLENGES, ids=[str(i) for i in CHALLENGE_IDS])
def test_dataset_key_in_registry(ch):
    registry = dataset_service.list_datasets()
    assert ch["dataset_key"] in registry, (
        f"dataset_key '{ch['dataset_key']}' del desafio {ch['id']} "
        f"no esta en datasets.json"
    )


def test_unique_ids():
    ids = [c["id"] for c in CHALLENGES]
    assert len(ids) == len(set(ids)), f"IDs duplicados: {ids}"


def test_total_count():
    assert len(CHALLENGES) == 27, f"Esperado 27, encontrado {len(CHALLENGES)}"
    by_diff = {"basico": 0, "intermedio": 0, "avanzado": 0}
    for c in CHALLENGES:
        by_diff[c["difficulty"]] += 1
    assert by_diff == {"basico": 9, "intermedio": 9, "avanzado": 9}, by_diff


@pytest.mark.parametrize("ch", CHALLENGES, ids=[str(i) for i in CHALLENGE_IDS])
def test_solution_produces_expected_keyword(ch):
    content = dataset_service.fetch_dataset(ch["dataset_key"])
    df = pd.read_csv(io.StringIO(content))
    output = _run_solution(ch["solution_code"], df)
    keyword = ch["expected_keyword"]
    assert keyword in output, (
        f"Desafio {ch['id']} ({ch['title']}): keyword '{keyword}' "
        f"no aparecio en output.\n--- OUTPUT ---\n{output[:1500]}"
    )


@pytest.mark.parametrize("ch", CHALLENGES, ids=[str(i) for i in CHALLENGE_IDS])
def test_solution_uses_read_csv(ch):
    assert "read_csv" in ch["solution_code"], (
        f"Desafio {ch['id']}: solution_code no usa read_csv"
    )


@pytest.mark.parametrize("ch", CHALLENGES, ids=[str(i) for i in CHALLENGE_IDS])
def test_points_match_difficulty(ch):
    expected = {"basico": 10, "intermedio": 25, "avanzado": 50}
    assert ch["points"] == expected[ch["difficulty"]], (
        f"Desafio {ch['id']}: puntos {ch['points']} no coinciden "
        f"con dificultad {ch['difficulty']}"
    )

