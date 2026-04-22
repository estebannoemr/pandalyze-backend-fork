# Pandalyze Backend
Backend para la app Pandalyze.


## Resumen
Es el servidor que gestiona las solicitudes provenientes del frontend de la aplicación Pandalyze. Se encarga de recibir archivos CSV y fragmentos de código Python utilizando librerias Pandas y Plotly para ejecutarlos y devolver los resultados.


## Instalación
Definir las cuatro variables de entorno para la conexión con la BD postgres {DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}
Al levantar la app SQLAlchemy deberia crear la tabla para almacenar CSV

Levantar la app localmente:
```
pip install -r requirements.txt
python3 run.py
```

## Endpoints
- /healthCheck es un GET devuelve un 200 OK!, es para ver que el servicio se encuentre levantado.
- /runPythonCode es un POST para correr codigo python.
- /uploadCsv es un POST para almacenar CSVs en la BD
- /bdCheck es un GET que sirve por si SQLAlchemy no crea las tablas al levantar la app, pegarle a este endpoint una vez deberia crearlas

### Contacto
pandalyze.estebanmr@gmail.com