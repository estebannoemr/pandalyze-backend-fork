import pandas as pd
from io import StringIO
from app.models.csv_model import CSVData
from app.extensions import db


def save_csv_data(filename, data, user_id=None, guest_id=None):
    """Guarda un CSV asociado al usuario o a la sesión de invitado.
    Exactamente uno de user_id / guest_id debe estar presente."""
    csv_data = CSVData(
        filename=filename, data=data, user_id=user_id, guest_id=guest_id
    )
    db.session.add(csv_data)
    db.session.commit()
    return csv_data.id, get_csv_columns_names(data)


def read_csv(csv_id, user_id=None, guest_id=None):
    """Lee un CSV respetando el scope del solicitante (usuario autenticado
    o sesión de invitado). Uno de los dos ids debe estar presente."""
    csvData = CSVData.get_csv_by_id(csv_id, user_id=user_id, guest_id=guest_id)
    if csvData is None:
        raise ValueError(
            "No se encontró el CSV solicitado o no pertenece a tu sesión."
        )
    df = pd.read_csv(StringIO(csvData.data))
    return df


def get_csv_by_content(csv_content, user_id=None, guest_id=None):
    """Busca un CSV existente con el mismo contenido dentro del scope del
    solicitante. Evita duplicar datasets idénticos subidos por el mismo
    alumno o dentro de la misma sesión de invitado."""
    q = CSVData.query.filter_by(data=csv_content)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    elif guest_id is not None:
        q = q.filter_by(guest_id=guest_id)
    else:
        return None, None
    csv = q.first()
    if csv:
        return csv.id, get_csv_columns_names(csv.data)
    return None, None


def get_csv_columns_names(data):
    return list(pd.read_csv(StringIO(data)).columns)
