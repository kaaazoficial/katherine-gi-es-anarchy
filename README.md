# Katherine — Anarchy Apps

Katherine es un bot modular para Anarchy Apps, desarrollado en Python con FastAPI. Integra comandos, moderación, herramientas sociales, asistencia conversacional, investigación web, música y voz.

## Arquitectura
- `main.py`: webhook, despacho de eventos, comandos, interacciones y voz.
- `katherine_ai.py`: integración IA, clasificación de intención y contexto temporal en memoria.
- `katherine_nexus.py`: interpretación determinista de solicitudes naturales.
- `katherine_research.py`: búsqueda y preparación de fuentes públicas.
- `katherine_config.py`, `katherine_matching.py`, `katherine_metrics.py`: configuración, coincidencias y métricas.
- `tests/`: pruebas unitarias y de regresión.

## Privacidad
Katherine no crea memoria personal permanente ni almacena conversaciones como historial a largo plazo. El contexto de IA es temporal y en memoria; el análisis voluntario tiene límites y expiración. Los registros operativos deben evitar secretos y minimizar datos personales.

## Desarrollo y pruebas
Requiere Python 3.11 o posterior.
```bash
python -m pip install -r requirements-test.txt
python -m pytest -q
python -m compileall -q .
```
Las dependencias de audio/voz son opcionales en pruebas aisladas.

## Persistencia y despliegue
`render.yaml` mantiene el plan Free. En Render Free el sistema de archivos es efímero y no admite Persistent Disks; SQLite local puede reiniciarse al sustituirse el contenedor, reiniciarse o salir del reposo. No se debe asumir que los datos locales sobreviven. Para persistencia real se requiere un servicio con disco persistente o una base de datos administrada. No se cambia el plan ni se crea infraestructura automáticamente. Configura secretos únicamente en el panel del proveedor, nunca en Git.

Consulta [docs/COMMANDS_AND_PERMISSIONS.md](docs/COMMANDS_AND_PERMISSIONS.md) para comandos y permisos.
