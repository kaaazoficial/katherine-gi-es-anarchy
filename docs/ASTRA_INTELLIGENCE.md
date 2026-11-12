# ASTRA INTELLIGENCE · Katherine AI

Esta expansión excluye CREATIVITY AI. La IA es una capa opcional sobre los comandos y fuentes que Katherine ya tiene; no reemplaza el enrutador determinista ni las herramientas actuales.

## Componentes
- **Katherine AI Core:** Responses API compatible con OpenAI o Groq, con salida JSON validada. Puede responder o proponer un comando de una lista permitida.
- **Memoria contextual:** aislada por usuario y sala, solo RAM, caduca a los 15 minutos y conserva hasta 12 turnos.
- **Teyvat AI:** delega consultas en enciclopedia, guías, equipos, rotaciones, materiales, reacciones y noticias oficiales.
- **Guardian AI:** revisión contextual manual; recomienda revisión humana, no sanciona ni borra mensajes.
- **AI Chat Analyst:** resume solo mensajes recibidos desde la activación administrativa. Buffer en RAM, hasta 1 hora y máximo 200 mensajes. Desactivar borra el buffer.
- **Eclipse AI:** transforma descripciones musicales en consultas optimizadas para el DJ.
- **Language AI:** traducción contextual.
- **Astra Admin AI:** explica diagnósticos reales; no ejecuta cambios ni revela secretos.

## Configuración del proveedor
No se añade dependencia nueva; usa HTTPX.

Variables:
- \`KATHERINE_AI_API_KEY\`: clave API. También acepta \`OPENAI_API_KEY\`.
- \`KATHERINE_AI_MODEL\`: identificador del modelo que soporte Responses API y Structured Outputs.
- \`KATHERINE_AI_API_URL\`: endpoint compatible; default \`https://api.openai.com/v1/responses\`.

Sin clave, la IA informa que no está conectada y los comandos clásicos siguen disponibles. No se inserta una clave ficticia ni se copia una credencial al repositorio. Las solicitudes envían \`store: false\`.

## Comandos
- \`!ia estado\`, \`!ia probar\` (administrador; valida la conexión real con el proveedor configurado), \`!ia ayuda\`, \`!ia olvidar\`.
- \`!ia preguntar <consulta>\`, \`!ia genshin <consulta>\`.
- \`!ia musica <descripción>\`, \`!ia traducir idioma | texto\`.
- \`!ia guardian revisar <texto>\` (administradores).
- \`!ia analista activar|resumen|limpiar|desactivar\` (administradores).
- \`!ia diagnostico\` (administradores).

Las solicitudes naturales explícitas se envían al núcleo si hay clave configurada; si no, conservan el enrutador determinista. La mención aislada «Katherine» nunca activa el bot.

## Seguridad y límites
- Los comandos propuestos pasan por allowlist, validación de argumentos y el handler original.
- No se permiten acciones administrativas, sanciones, pagos, transferencias ni cambios de perfil desde el modelo.
- El analizador está desactivado por defecto y requiere UID en \`ADMIN_UIDS\`.
- Solo observa mensajes recibidos mientras está activo; no lee historial antiguo.
- No añade reconocimiento de imágenes entrantes ni recepción de audio, no expuestos por la API del bot.
- Las respuestas pasan por el filtro global de enlaces de \`send_message\`.
- CREATIVITY AI no está incluido.
