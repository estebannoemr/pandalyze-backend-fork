import traceback
import sys, threading, re, pandas, io, ast
from flask import Blueprint, request, jsonify
from flask_cors import cross_origin
import plotly.express as plotly
import plotly.io as pio
import folium

from ..services.csv_service import read_csv as _read_csv_scoped
from ..services.error_formatter_service import ExceptionFormatter
from ..endpoints.map_visualization import generate_map
from ..utils.request_scope import resolve_scope

bp = Blueprint('run_python_code', __name__)

control_structures_regex = re.compile(r'\b(if|else|elif|for|while|try|except|finally)\b')
import_statement_regex = re.compile(r'\bimport\b')


def is_safe_code(code):
    if control_structures_regex.search(code):
        return False
    if import_statement_regex.search(code):
        return False
    return True


def execute_code(code, globals_dict, result_list, timeout=15):
    def exec_wrapper(code, globals_dict):
        from run import app
        try:
            with app.app_context():
                parsed_code = ast.parse(code.strip())
                body = parsed_code.body
                exprs = [node for node in body if isinstance(node, ast.Expr)]
                last_expr = body.pop(body.index(exprs[-1])) if exprs else None
                if body:
                    exec(
                        compile(ast.Module(body, type_ignores=[]), "<user-code>", "exec"),
                        globals_dict,
                    )
                if last_expr:
                    result_list.append(
                        eval(
                            compile(ast.Expression(last_expr.value), "<user-code>", "eval"),
                            globals_dict,
                        )
                    )
        except Exception as e:
            result_list.append(e)

    thread = threading.Thread(target=exec_wrapper, args=(code, globals_dict))
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        result_list.append(TimeoutError("La ejecución del código superó el tiempo límite."))


@bp.route('/runPythonCode', methods=['POST'])
@cross_origin()
def run_code():
    user_id, guest_id = resolve_scope()
    if user_id is None and guest_id is None:
        return jsonify({"error": "Se requiere autenticación o X-Guest-Id."}), 401

    code = request.json['code']

    # ``inline_csvs`` permite mandar el contenido del CSV en el mismo request
    # en lugar de buscarlo por id en la tabla csv_data. Es el mecanismo que
    # usa el flujo de Desafíos para no persistir el dataset del desafío en
    # la base de datos del servidor: el cliente lo descarga vía
    # /challenges/<id>/download y lo adjunta acá. Las claves del dict son
    # los csv_id stringificados que el frontend asigna determinísticamente
    # (DJB2 sobre el filename), las mismas que usa el bloque read_csv.
    inline_csvs_raw = request.json.get('inline_csvs') or {}
    inline_dataframes = {}
    if isinstance(inline_csvs_raw, dict):
        for k, v in inline_csvs_raw.items():
            if not isinstance(v, str) or not v:
                continue
            try:
                inline_dataframes[str(k)] = pandas.read_csv(io.StringIO(v))
            except Exception:
                # CSV mal formado: lo ignoramos silenciosamente para que
                # el read_csv() del usuario falle con su mensaje habitual
                # ("No se encontró el CSV solicitado") en lugar de tirar
                # un 500 acá antes de ejecutar el código.
                continue

    def read_csv(csv_id):
        # Prioridad: si el csv_id está entre los inline, usamos esa copia.
        # Sólo si no aparece, caemos al lookup en la tabla csv_data.
        key = str(csv_id)
        if key in inline_dataframes:
            return inline_dataframes[key]
        return _read_csv_scoped(csv_id, user_id=user_id, guest_id=guest_id)

    try:
        if not is_safe_code(code):
            raise ValueError("El código contiene sentencias no permitidas")

        pandas.options.display.max_columns = None
        pandas.set_option('display.max_colwidth', 20)
        pandas.set_option('display.colheader_justify', 'center')
        pandas.set_option('display.width', 9999)

        output_catcher = io.StringIO()
        sys.stdout = output_catcher

        json_plots = []
        original_show = pio.show

        def custom_show(fig, *args, **kwargs):
            plot_json = pio.to_json(fig)
            json_plots.append(plot_json)

        pio.show = custom_show

        exec_globals = {
            'read_csv': read_csv,
            'plotly': plotly,
            'pio': pio,
            'pd': pandas,
            '_jsonPlots_': json_plots,
            'generate_map': generate_map,
        }

        result_list = []
        execute_code(code, exec_globals, result_list)

        sys.stdout = sys.__stdout__
        pio.show = original_show

        text_output = output_catcher.getvalue()
        result_obj = result_list[0] if result_list else None

        if isinstance(result_obj, Exception):
            raise result_obj

        if isinstance(result_obj, folium.Map):
            map_html = result_obj._repr_html_()
            return jsonify({'output': map_html, 'type': 'map'})

        if text_output.strip().startswith('<div id="map_'):
            return jsonify({'output': text_output, 'type': 'map'})

        return jsonify({'output': text_output, 'plots': json_plots}), 200

    except Exception as e:
        sys.stdout = sys.__stdout__
        if 'original_show' in locals():
            pio.show = original_show

        print("--- ERROR CAPTURADO EN EL ENDPOINT ---")
        print(traceback.format_exc())
        print("------------------------------------")

        try:
            formatter = ExceptionFormatter(e)
            personalized_exception = formatter.get_personalized_exception()
        except Exception as formatter_error:
            print("Formatter fallo:", formatter_error)
            personalized_exception = "Error de ejecucion: " + str(e)

        return jsonify({'error': personalized_exception}), 500
