# Pruebas de Katherine

## Suites automatizadas

Instala las dependencias aisladas:

    python -m pip install -r requirements-test.txt

Ejecuta las pruebas unitarias y de regresión:

    python -m pytest -q --ignore=tests/integration --cov=main --cov=katherine_ai --cov=katherine_nexus --cov=katherine_astra --cov=katherine_research --cov=katherine_retention --cov-report=term-missing --timeout=120

Ejecuta las pruebas de integración del webhook:

    python -m pytest -q tests/integration --timeout=30

O ejecuta toda la suite:

    python -m pytest -q --cov=main --cov=katherine_ai --cov=katherine_nexus --cov=katherine_astra --cov=katherine_research --cov=katherine_retention --cov-report=term-missing --timeout=120

## Alcance de integración

tests/integration/test_webhook_flow.py utiliza la aplicación ASGI real de FastAPI
y eventos sintéticos firmados con secretos de prueba. Verifica las rutas de webhook,
validación HMAC y antigüedad del timestamp, selección de identidad developer/community,
deduplicación de eventId, límites de tamaño, despacho hasta el manejador de comandos
y endpoint de salud.

Las pruebas sustituyen los límites de envío/voz/antispam y no realizan llamadas a
Anarchy Apps, Gemini, Groq, OpenRouter ni otros servicios externos. No requieren
variables de entorno de producción ni consumen cuotas de proveedores.

GitHub Actions ejecuta las pruebas unitarias y las de integración en Python 3.11
y 3.14 como trabajos separados para que cada resultado sea visible en CI.
