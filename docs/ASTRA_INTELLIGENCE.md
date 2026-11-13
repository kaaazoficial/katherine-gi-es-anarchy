# ASTRA INTELLIGENCE · Katherine AI

Esta expansión excluye CREATIVITY AI. La IA es una capa opcional sobre los comandos y fuentes que Katherine ya tiene; no reemplaza el enrutador determinista ni las herramientas actuales.

## Componentes
- **Katherine AI Core:** Gemini 3.5 Flash-Lite es el proveedor principal, Groq GPT-OSS 20B el respaldo y OpenRouter (`openrouter/free`) el tercer proveedor opcional. Solo se consultan proveedores con credencial configurada. Las respuestas normales se limitan a 512 tokens; Groq usa razonamiento bajo. Los resultados de comandos se entregan sin una segunda llamada de IA.
- **Memoria contextual:** aislada por usuario y sala, solo RAM, caduca a los 5 minutos y conserva hasta 2 intercambios.
- **Teyvat AI:** delega consultas en enciclopedia, guías, equipos, rotaciones, materiales, reacciones y noticias oficiales.
- **Guardian AI:** revisión contextual manual; recomienda revisión humana, no sanciona ni borra mensajes.
- **AI Chat Analyst:** resume solo mensajes recibidos desde la activación administrativa. Buffer en RAM, hasta 15 minutos y máximo 60 mensajes; se guardan etiquetas efímeras de participantes, no sus nombres; al solicitar un resumen, el contenido seleccionado se envía al proveedor IA configurado. Desactivar borra el buffer.
- **Eclipse AI:** transforma descripciones musicales en consultas optimizadas para el DJ.
- **Language AI:** traducción contextual.
- **Astra Admin AI:** explica diagnósticos reales; no ejecuta cambios ni revela secretos.

## Configuración del proveedor
Katherine valida las opciones de proceso con Pydantic Settings. Las credenciales permanecen en variables de entorno y nunca se devuelven en diagnósticos. El reconocimiento aproximado de comandos usa RapidFuzz y solo propone sugerencias; no ejecuta automáticamente una coincidencia. Las métricas Prometheus son agregadas y no incluyen etiquetas de usuario, sala ni contenido.

Variables principales:
- `GEMINI_API_KEY`: clave del proveedor principal.
- `GEMINI_MODEL`: modelo principal; default `gemini-3.5-flash-lite`.
- `GROQ_API_KEY`: credencial secundaria.
- `GROQ_MODEL`: modelo secundario; default `openai/gpt-oss-20b`.
- `OPENROUTER_API_KEY`: credencial opcional del tercer proveedor.
- `OPENROUTER_MODEL`: modelo terciario; default `openrouter/free`.
- `OPENROUTER_API_URL`: endpoint Responses; default `https://openrouter.ai/api/v1/responses`.
- `OPENROUTER_HTTP_REFERER`: encabezado HTTP-Referer opcional.
- `KATHERINE_AI_PRIMARY_PROVIDER`: se mantiene en `gemini`.
- Compatibilidad: `KATHERINE_AI_API_KEY`, `KATHERINE_AI_MODEL` y `KATHERINE_AI_API_URL` siguen aceptándose en instalaciones heredadas.

Sin clave, la IA informa que no está conectada y los comandos clásicos siguen disponibles. No se inserta una clave ficticia ni se copia una credencial al repositorio. A OpenAI se envía store=false; el endpoint Responses de Groq no admite ese parámetro y Katherine lo omite. Ninguna de las dos opciones sustituye la política de retención del proveedor.

## Comandos de IA
La ayuda de IA está separada del catálogo clásico `!ayuda` y se consulta con `!ia ayuda`.
- `!asistente <petición>` o «Katherine, ...»: conversación y acciones en lenguaje natural.
- `!ia preguntar <consulta>`, `!ia genshin <consulta>`.
- `!investigar <tema>` / `!ia investigar <tema>`; `!enlace <URL>` / `!ia enlace <URL>`.
- `!ia musica <descripción>`, `!ia traducir <idioma> | <texto>`.
- `!ia estado`, `!ia olvidar`.
- Solo administradores: `!ia probar [gemini|groq|openrouter]`, `!ia trazas`, `!ia guardian`, `!ia analista`, `!ia diagnostico`.

Las solicitudes naturales explícitas se envían al núcleo si hay clave configurada; si no, conservan el enrutador determinista. Las herramientas de Teyvat solo se seleccionan cuando la consulta menciona Genshin/Teyvat o una entidad reconocible del juego; preguntas generales sobre Anarchy Apps, Katherine, bots, APIs o programación nunca se derivan a la enciclopedia. La mención aislada «Katherine» nunca activa el bot.

## Conversación general y control de contexto

- Una petición que solicite ayuda sobre «un tema» sin identificarlo pide el tema antes de consultar al proveedor; no se adivina Genshin ni otro dominio.
- El contexto de Genshin solo se conserva para referencias breves que parezcan una continuación del último turno de Genshin. Una pregunta con tema distinto descarta ese contexto temporal.
- Si el modelo genera una herramienta o respuesta de Genshin para una consulta ajena, se hace un único reintento sin herramientas y sin historial anterior. Si vuelve a desviarse, Katherine reconoce el error y pide concretar la consulta, sin rechazarla por no ser de Teyvat.
- Los planes solo se aceptan cuando la petición contiene varias acciones explícitas y todos los pasos son herramientas de solo lectura validadas. La ejecución vuelve a comprobar esa lista segura y se realiza en silencio para entregar un único resultado.

## Seguridad y límites
- Los comandos propuestos pasan por allowlist, validación de argumentos y el handler original.
- No se permiten acciones administrativas, sanciones, pagos, transferencias ni cambios de perfil desde el modelo. El comando `!metricas` sigue siendo exclusivo de UID administradores.
- El analizador está desactivado por defecto y requiere UID en \`ADMIN_UIDS\`.
- Solo observa mensajes recibidos mientras está activo; no lee historial antiguo. La activación informa que el contenido se transmite al proveedor cuando se solicita el resumen.
- No añade reconocimiento de imágenes entrantes ni recepción de audio, no expuestos por la API del bot.
- Las respuestas pasan por el filtro global de enlaces de \`send_message\`.
- CREATIVITY AI no está incluido.


## Planes de acciones encadenadas

El núcleo de IA puede devolver un plan de hasta tres herramientas. Katherine valida todos los pasos antes de ejecutar el primero; si uno no está permitido o no tiene argumentos válidos, rechaza el plan completo. Los pasos se ejecutan secuencialmente a través de los manejadores originales, por lo que conservan sus validaciones. No se permiten comandos administrativos ni acciones destructivas dentro de los planes.


## Retención y privacidad
- El contexto conversacional de IA existe solo en RAM, expira como máximo a los 5 minutos y conserva como máximo 2 intercambios.
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


## Protección de cuotas y entrega de respuestas

- Gemini, Groq y OpenRouter mantienen esperas, cuotas y estados de recuperación separados. Groq y OpenRouter ajustan el ritmo con cabeceras de cuota cuando están disponibles. OpenRouter usa `openrouter/free` por defecto; sus variantes gratuitas tienen límites externos estrictos y pueden cambiar de modelo en cada llamada. Las respuestas usan un máximo de 512 tokens y los resultados de comandos evitan una segunda llamada de IA.
- Cada UID verificado queda limitado a 8 solicitudes IA por minuto y por sala. Si la plataforma no entrega un identificador estable, se aplica el límite a la sala completa en lugar de confiar en nicknames. Las claves se derivan con sal aleatoria y viven solo en RAM.
- Un proveedor que acumula dos fallos consecutivos abre un circuito durante 30 segundos; los intervalos aumentan hasta cinco minutos. También se respetan los tiempos de espera y cuotas diarias informados por el proveedor. Los proveedores en recuperación se omiten y se intenta automáticamente el siguiente disponible.
- Los HTTP 429 se reintentan respetando `Retry-After` y los encabezados de reinicio. Cuando el error o las cabeceras indican agotamiento de cuota diaria, Katherine deja de reintentar en bucle y comunica que la IA volverá a estar disponible tras el reinicio.
- Los bloques de mensajes rechazados explícitamente con HTTP 429 se pueden recuperar en una tarea temporal de RAM. La cola admite hasta 100 tareas concurrentes, conserva los bloques pendientes durante un máximo de 10 minutos y se pierde al reiniciar el proceso. Los errores distintos de 429 no se reenvían automáticamente para evitar duplicados cuando el resultado del primer envío es incierto.
- Los mensajes largos mantienen el límite de transporte de 2000 unidades UTF-16 por bloque; no se recorta el contenido generado. Si un bloque no puede entregarse, los posteriores esperan a la recuperación para conservar el orden.
- `!metricas` y `/metrics` exponen contadores agregados de solicitudes, límites 429, reintentos, tokens informados por el proveedor, espera de cola y recuperación de bloques. No incluyen UID, salas, nombres ni contenido de mensajes.
- Las colas de entrega contienen temporalmente solo los bloques que el proveedor de chat rechazó con HTTP 429. No se escriben en SQLite ni se conservan tras reinicios; su expiración máxima es de 10 minutos.
