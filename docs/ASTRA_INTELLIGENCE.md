# ASTRA INTELLIGENCE · Katherine AI

Esta expansión excluye CREATIVITY AI. La IA es una capa opcional sobre los comandos y fuentes que Katherine ya tiene; no reemplaza el enrutador determinista ni las herramientas actuales.

## Componentes
- **Katherine AI Core:** Responses API compatible con OpenAI o Groq, con salida JSON validada (Structured Outputs o JSON Object según proveedor/modelo). Puede responder o proponer un comando de una lista permitida.
- **Memoria contextual:** aislada por usuario y sala, solo RAM, caduca a los 10 minutos y conserva hasta 6 turnos.
- **Teyvat AI:** delega consultas en enciclopedia, guías, equipos, rotaciones, materiales, reacciones y noticias oficiales.
- **Guardian AI:** revisión contextual manual; recomienda revisión humana, no sanciona ni borra mensajes.
- **AI Chat Analyst:** resume solo mensajes recibidos desde la activación administrativa. Buffer en RAM, hasta 15 minutos y máximo 60 mensajes; se guardan etiquetas efímeras de participantes, no sus nombres; al solicitar un resumen, el contenido seleccionado se envía al proveedor IA configurado. Desactivar borra el buffer.
- **Eclipse AI:** transforma descripciones musicales en consultas optimizadas para el DJ.
- **Language AI:** traducción contextual.
- **Astra Admin AI:** explica diagnósticos reales; no ejecuta cambios ni revela secretos.

## Configuración del proveedor
No se añade dependencia nueva; usa HTTPX.

Variables:
- \`KATHERINE_AI_API_KEY\`: clave API. También acepta \`OPENAI_API_KEY\`.
- \`KATHERINE_AI_MODEL\`: identificador del modelo disponible en el endpoint configurado; el modo de salida estructurada depende del proveedor/modelo.
- \`KATHERINE_AI_API_URL\`: endpoint compatible; default \`https://api.openai.com/v1/responses\`.

Sin clave, la IA informa que no está conectada y los comandos clásicos siguen disponibles. No se inserta una clave ficticia ni se copia una credencial al repositorio. A OpenAI se envía store=false; el endpoint Responses de Groq no admite ese parámetro y Katherine lo omite. Ninguna de las dos opciones sustituye la política de retención del proveedor.

## Comandos
- \`!ia estado\`, \`!ia probar\` (administrador; valida la conexión real con el proveedor configurado), \`!ia ayuda\`, \`!ia olvidar\`.
- \`!ia preguntar <consulta>\`, \`!ia genshin <consulta>\`.
- \`!ia musica <descripción>\`, \`!ia traducir idioma | texto\`.
- \`!ia guardian revisar <texto>\` (administradores).
- \`!ia analista activar|resumen|limpiar|desactivar\` (administradores).
- \`!ia diagnostico\` (administradores).

Las solicitudes naturales explícitas se envían al núcleo si hay clave configurada; si no, conservan el enrutador determinista. Las herramientas de Teyvat solo se seleccionan cuando la consulta menciona Genshin/Teyvat o una entidad reconocible del juego; preguntas generales sobre Anarchy Apps, Katherine, bots, APIs o programación nunca se derivan a la enciclopedia. La mención aislada «Katherine» nunca activa el bot.

## Seguridad y límites
- Los comandos propuestos pasan por allowlist, validación de argumentos y el handler original.
- No se permiten acciones administrativas, sanciones, pagos, transferencias ni cambios de perfil desde el modelo.
- El analizador está desactivado por defecto y requiere UID en \`ADMIN_UIDS\`.
- Solo observa mensajes recibidos mientras está activo; no lee historial antiguo. La activación informa que el contenido se transmite al proveedor cuando se solicita el resumen.
- No añade reconocimiento de imágenes entrantes ni recepción de audio, no expuestos por la API del bot.
- Las respuestas pasan por el filtro global de enlaces de \`send_message\`.
- CREATIVITY AI no está incluido.


## Planes de acciones encadenadas

El núcleo de IA puede devolver un plan de hasta tres herramientas. Katherine valida todos los pasos antes de ejecutar el primero; si uno no está permitido o no tiene argumentos válidos, rechaza el plan completo. Los pasos se ejecutan secuencialmente a través de los manejadores originales, por lo que conservan sus validaciones. No se permiten comandos administrativos ni acciones destructivas dentro de los planes.


## Retención y privacidad
- El contexto conversacional de IA existe solo en RAM, expira como máximo a los 10 minutos y conserva como máximo 6 turnos.
- El analizador es opcional, está desactivado por defecto y conserva como máximo 60 mensajes durante 15 minutos; no usa historiales previos.
- Los textos de bio/estado caducan a los 30 días desde su última modificación.
- Los registros de actividad se conservan 7 días; regalos 30 días y un máximo de 5 por destinatario; auditoría 30 días; advertencias 90 días.
- Los datos de usuario y sus estados de juego se eliminan después de 90 días de inactividad. Las configuraciones comunitarias se mantienen, pero se anonimiza el identificador del creador a los 90 días.
- Las cachés, favoritos, límites y resultados temporales se eliminan automáticamente al caducar. No se guardan transcripciones de conversación en SQLite.
- La retención local no controla las políticas del proveedor externo de IA; revisa las condiciones del proveedor configurado.


## Enrutamiento natural y ejecución de comandos

- El enrutador clasifica conversación, acción, investigación, Genshin y solicitudes múltiples; el dominio de Genshin se detecta antes de las palabras genéricas de investigación.
- El catálogo de herramientas se construye por solicitud y por permisos: los comandos administrativos solo se muestran a la IA cuando el UID verificado pertenece a `ADMIN_UIDS`. El manejador original vuelve a comprobarlos.
- Las peticiones naturales dirigidas a Katherine pueden invocar los comandos existentes de comunidad, perfiles propios, economía, juegos, Teyvat, investigación, multimedia, DJ y voz.
- Los comandos con efectos se ejecutan solo ante una instrucción explícita. Las transferencias requieren destinatario y cantidad válidos. Los comandos administrativos conservan sus comprobaciones de UID.
- Los planes admiten hasta tres acciones de consulta/investigación independientes, validadas por completo antes de ejecutarse. No se incluyen pagos, cambios de perfil, moderación, creación/cierre de eventos ni otras acciones con efectos.
- La ejecución silenciosa captura el resultado real del comando y genera una única respuesta final. Si no hay salida verificable, Katherine informa que no pudo confirmar el resultado. Los comandos interactivos mantienen sus botones y controles nativos.
- Una mención aislada de «Katherine» sigue sin activar el bot; una petición explícita no puede ser interceptada por las respuestas automáticas por palabras clave.
