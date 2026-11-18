# Katheryne — Genshin Impact ES en Anarchy

Katheryne es el bot comunitario de Genshin Impact ES en Anarchy, desarrollado en Python con FastAPI. Anarchy Apps es su plataforma anfitriona. Integra comandos de Genshin, moderación, herramientas sociales, asistencia conversacional, investigación web, música y voz.

## Identidad y credenciales

Katheryne utiliza una sola identidad: el bot comunitario. Configura `COMMUNITY_BOT_TOKEN` y `COMMUNITY_WEBHOOK_SECRET` en el proveedor. Las credenciales heredadas `BOT_TOKEN` y `WEBHOOK_SECRET` ya no forman parte del runtime. Los endpoints `/`, `/webhook` y `/community` validan el mismo secreto comunitario.

## Arquitectura
- `main.py`: orquestación de webhooks, despacho y compatibilidad con los comandos de Anarchy.
- `katherine_runtime.py`: ciclo de vida, estados de disponibilidad y supervisión acotada de tareas asíncronas.
- `katherine_database.py`: conexiones, transacciones y creación del esquema SQLite.
- `katherine_ai.py`: núcleo de IA, proveedores Gemini/Groq, validación de herramientas y contexto temporal.
- `katherine_nexus.py`: interpretación determinista de solicitudes naturales.
- `katherine_research.py`: búsqueda y preparación de fuentes públicas.
- `katherine_link_analysis.py`, `katherine_images.py`, `katherine_utilities.py`: inspección automática de enlaces, validación de imágenes y cálculo seguro.
- `katherine_config.py`, `katherine_matching.py`, `katherine_metrics.py`: configuración, coincidencias y métricas.
- `katherine_economy.py`: monedero transaccional, libro mayor, transferencias, tienda, inventario y equipamiento cosmético.
- `katherine_polls.py`: encuestas persistentes por sala, votos anónimos y caducidad automática.
- `katherine_progression.py`: experiencia, niveles, misiones diarias y logros con límites contra farmeo.
- `tests/`: pruebas unitarias y de regresión.

## Privacidad
Katheryne no crea memoria personal permanente ni almacena conversaciones como historial a largo plazo. El contexto de IA es temporal y en memoria; no se recopilan mensajes de sala para resúmenes ni se registran preguntas y respuestas de IA. Los registros operativos deben evitar secretos y minimizar datos personales. Las encuestas se eliminan tras su cierre y un periodo de gracia de 24 horas; los votos anónimos almacenan un hash del votante. Los contadores de misiones se purgan tras 14 días; el historial detallado de puntos caduca a los 90 días y el XP/logros se eliminan tras 90 días sin actividad. El saldo e inventario son estado de juego; nada de esto conserva mensajes ni conversaciones.

## Desarrollo y pruebas

Mide el código de producción sin contar pruebas, documentación, comentarios de línea completa ni líneas vacías:
```bash
python scripts/measure_source.py
```
Requiere Python 3.11 o posterior.
```bash
python -m pip install -r requirements-test.txt
python -m pytest -q
python -m compileall -q .
```
Las dependencias de audio/voz son opcionales en pruebas aisladas.

## Persistencia y despliegue
`render.yaml` declara un servicio Free y no declara un Persistent Disk ni una base de datos externa. Por tanto, el repositorio por sí solo no garantiza persistencia de SQLite entre sustituciones o reinicios del contenedor. El proceso detecta un montaje externo y advierte si no lo encuentra, pero esa comprobación no revela si el panel tiene recursos añadidos manualmente. Antes de depender de datos persistentes, verifica el almacenamiento real en el proveedor y configura una base de datos o volumen compatible. Esta auditoría no modifica el plan ni la infraestructura. Configura secretos únicamente en el panel del proveedor, nunca en Git.

El despacho principal permanece en `main.py`; las responsabilidades estables se separan en módulos específicos para reducir acoplamiento. Consulta [docs/COMMANDS_AND_PERMISSIONS.md](docs/COMMANDS_AND_PERMISSIONS.md) para comandos y permisos.
