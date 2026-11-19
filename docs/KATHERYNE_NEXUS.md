# Katherine Nexus

Capa de interpretación de lenguaje natural que complementa los comandos existentes de Katherine para Anarchy Apps.

## Alcance
- Permite invocar comandos existentes con «Katherine …» cuando la intención se reconoce con suficiente confianza.
- Reutiliza manejadores, permisos, límites y operaciones de API originales.
- Solicitudes ambiguas o desconocidas no deben convertirse en acciones.
- La mención aislada «Katherine» no activa el asistente.
- La clasificación evita dirigir consultas generales a módulos temáticos ajenos.

## Seguridad y privacidad
Nexus no inventa endpoints ni amplía permisos. Las acciones administrativas verifican el UID real. El contexto conversacional de IA es temporal y no debe convertirse en memoria persistente de usuarios o conversaciones.

## Respuestas automáticas
Las reglas automáticas se evalúan después de descartar comandos con prefijo y solicitudes naturales dirigidas a Katherine. Se conservan límites por sala y usuario para evitar respuestas repetitivas.

## Pruebas y rollback
Los cambios de intención requieren pruebas para solicitudes válidas, ambiguas y fuera de dominio. Los comandos conservan la validación de argumentos y permisos original. Revertir la integración y el módulo permite recuperar el flujo previo.
