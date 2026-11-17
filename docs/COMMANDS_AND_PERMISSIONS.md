# Katherine · Catálogo y permisos de comandos

La ayuda del chat (`!ayuda`) muestra el catálogo completo en siete secciones o una categoría concreta. `!menu` muestra el catálogo completo por categorías. Este documento conserva el detalle técnico y los permisos; la IA tiene ayuda separada mediante `!ia ayuda`.

## Ayuda pública y alias compatibles

- `!ayuda` muestra las siete secciones; `!ayuda genshin`, `!ayuda juegos`, etc. filtran una categoría.
- Se retiró `!comandos`, que duplicaba `!ayuda`. `!menu` se conserva como acceso al catálogo completo.
- Los lanzamientos aleatorios se unifican en `!azar <mín> <máx>`, `!azar dado` y `!azar moneda`.
- Se retiraron los accesos antiguos `!dado`, `!moneda` y `!suerte`; sus funciones se mantienen en `!azar dado`, `!azar moneda` y `!frase`.
- `!economia` fue retirado porque solo repetía la información de puntos y recompensas, que ahora está en `!ayuda juegos`.

## Investigación y herramientas universales

- `!investigar <tema>`: busca páginas públicas en varios resultados independientes, recupera extractos y solicita una síntesis basada en las fuentes.
- Modos del Centro de Investigación Avanzada: `!investigar comparar: <tema>` contrasta fuentes y posturas; `!investigar verificar: <afirmación>` clasifica afirmaciones según la evidencia; `!investigar cronologia: <tema>` ordena acontecimientos con fechas verificables; `!investigar informe: <tema>` genera un informe exhaustivo. Sin modo explícito se usa la investigación general.
- Los enlaces se inspeccionan automáticamente al recibir el mensaje; no existe comando manual de análisis.
- La investigación combina Wikipedia, Wikidata y búsqueda web general. Según la intención, añade Internet Archive (libros/archivos), OpenAlex, Crossref y arXiv (publicaciones académicas y preprints), Europe PMC (biomedicina y ciencias de la vida), GDELT (cobertura periodística reciente), NASA (biblioteca de imágenes y vídeos espaciales), MusicBrainz y Jamendo (metadatos y catálogo musical), KeqingMains, HoYoLAB y Fandom (Genshin Impact), y documentación oficial de herramientas como Python, FastAPI, GitHub, Render, PostgreSQL, Docker, MDN, Node.js y React. Las fuentes especializadas solo se consultan cuando el tema las justifica; no se mezclan con preguntas generales. Se priorizan hasta ocho resultados de dominios distintos. GDELT sirve para localizar cobertura, no para validar por sí solo una noticia; arXiv contiene preprints que pueden no estar revisados por pares. Europe PMC devuelve metadatos y abstracts, no garantiza texto completo. KeqingMains y Fandom son fuentes comunitarias; HoYoLAB puede contener publicaciones oficiales y de usuarios, por lo que se comprueba la autoría. Jamendo requiere `JAMENDO_CLIENT_ID` en Render; sin ella se omite. MusicBrainz limita las consultas a una por segundo. OpenAlex, Crossref, Wikidata, Internet Archive y NASA usan endpoints públicos sin clave de Katherine.
- `!ia investigar <tema>`: acceso alternativo a la investigación. Los enlaces se inspeccionan automáticamente al recibir mensajes.
- Todos los comandos del catálogo y las acciones sociales (por ejemplo, `Katherine investigar comparar: ...`, `Katherine dj pausa`, `Katherine abrazo @usuario` o `Katherine perfil`) pueden invocarse directamente después de «Katherine». Se conservan los permisos originales y la mención aislada no activa el bot.
- La síntesis cita las fuentes por número y dominio; páginas bloqueadas, no HTML o sin texto suficiente pueden omitirse. Los metadatos de Crossref/OpenAlex no garantizan acceso al texto completo y los derechos de Internet Archive/Jamendo dependen de cada ítem. La búsqueda no garantiza exhaustividad.

## Identidad del creador y administración por UID

Katherine separa la identidad del creador de los administradores:
- `SINNER_UID`: un único UID exacto, reservado al creador. Habilita respuestas personalizadas e interacciones exclusivas, además de la consola privada del creador.
- `ADMIN_UIDS`: lista independiente de UID administrativos, separada por comas, punto y coma o saltos de línea. No convierte a esos usuarios en el creador.
- El creador también puede usar las herramientas administrativas, sin tener que incluir su UID en `ADMIN_UIDS`.

Ambas identidades se validan exclusivamente contra `message.author.uid`; el nickname nunca concede privilegios. Ejecuta `!miuid` para consultar el UID de Anarchy Apps. En Render configura tu UID únicamente en `SINNER_UID`; conserva los demás UID en `ADMIN_UIDS`.

## Comandos privilegiados

- `!sistema`, `!diagnostico`, `!rendimiento` y `!laboratorio` [SOLO CREADOR]: consola privada del creador.
- `!estado`, `!fuentes`, `!saludfuentes`, `!metricas`, `!autotest`
- `!centro`, `!nexus`
- `!admin`, `!paneladmin`
- `!antispam`, `!advertir`, `!advertencias`, `!quitaradvertencia`
- `!perfilbot`, `!avatarbot`
- `!respuestas`, `!respuesta`
- `!ia probar [gemini|groq]`, `!ia guardian`, `!ia diagnostico`

Los eventos se crean libremente; solo su creador o un UID administrador puede cerrarlos. Los comandos generales de fecha, zona horaria, ayuda, puntos y recompensas, traducción y multimedia siguen disponibles para todos.

### Métricas y sugerencias de comandos

- `!metricas` muestra solo métricas agregadas del proceso y requiere UID administrador.
  También estima el coste por proveedor/modelo en USD usando tarifas públicas estándar para Gemini 3.5 Flash-Lite y Groq GPT-OSS 20B. Los modelos sin tarifa aparecen como «sin tarifa configurada», nunca como coste cero.
  Las tarifas pueden sobrescribirse con `KATHERINE_AI_COST_RATES`, un objeto JSON cuyas claves son `Proveedor|modelo` y cuyos valores contienen `input` y `output` en USD por millón de tokens. Ejemplo: `{"Google Gemini|gemini-3.5-flash-lite":{"input":0.30,"output":2.50}}`.
  El importe es una estimación con tarifas estándar, no una factura: puede diferir por nivel gratuito, caché, modalidad, descuentos, región, proveedor de enrutamiento o cambios de precios.
  Incluye promedios por respuesta con tokens informados, totales de los últimos 60 minutos y una proyección diaria solo tras 5 minutos y 3 observaciones. La proyección se calcula desde el arranque del proceso y no representa consumo real ni facturación.
  Los contadores y las ventanas temporales viven solo en RAM y se reinician con el proceso; no se guardan usuarios, salas ni conversaciones.
- `GET /metrics` expone métricas Prometheus sin etiquetas de usuario, sala o mensaje. Puede protegerse configurando `PROMETHEUS_METRICS_TOKEN` en Render; cuando se configura, requiere `Authorization: Bearer <token>`.
- Si se escribe mal un comando, Katherine puede sugerir una coincidencia de alta confianza. La sugerencia nunca ejecuta el comando automáticamente.

## Protección adicional

- Si el evento no contiene un UID real, se deniega cualquier comando privilegiado aunque el nickname coincida con un UID configurado.
- Las comprobaciones del panel interactivo vuelven a validar el UID al pulsar botones.
- Los UID configurados se comparan como cadenas exactas y no se aceptan valores vacíos.


### Limpieza de mensajes propios

- `!eliminarmensajes` o «Katherine, elimina tus mensajes»: elimina únicamente mensajes de Katherine cuyos identificadores fueron confirmados por Anarchy Apps durante la ejecución actual del proceso.
- Requiere UID administrador configurado en `ADMIN_UIDS`. Los identificadores se mantienen solo en memoria RAM durante un máximo de 7 días y caducan automáticamente; no se guardan contenidos ni se crea historial persistente.
- Tras un reinicio o despliegue, los identificadores temporales anteriores ya no están disponibles. Los mensajes antiguos no se eliminan si no pueden identificarse con seguridad.


### Identidad del bot: perfil y avatar

- `!perfilbot [ambos|desarrollador|comunidad] Nombre | Estado`: actualiza el perfil de la identidad seleccionada. Por defecto actualiza ambas.
- `!avatarbot [ambos|desarrollador|comunidad] URL`: actualiza el icono de la identidad seleccionada. Por defecto actualiza ambas, para mantener sincronizados el perfil de chat y la identidad que entra a las llamadas.
- Los comandos siguen requiriendo un UID administrador exacto. Si una de las identidades falla, el resultado informa el éxito parcial en vez de afirmar que ambas se actualizaron.


## Llamadas y música

- `!join`: conecta a Katherine a la llamada de la sala.
- `!voz participantes`, `!voz hablando` y `!voz estado`: muestran participantes detectados por los eventos de la llamada. El estado vive solo en RAM, caduca tras 30 minutos sin actualización y se elimina al terminar la sala o salir Katherine del chat. Los UID nunca se muestran.
- `!dj detener` / `!dj stop`: detiene la reproducción y mantiene la conexión a la llamada.
- `!salirsala`: salida explícita de Katherine de la llamada.
- El final de una pista, una cola vacía o un fallo de TTS no deben provocar una salida de la llamada.

## Respuestas a mensajes

Al responder directamente a un mensaje de Katherine, el bot interpreta el texto como una continuación de la conversación. Solo se activa cuando el ID citado corresponde a un mensaje enviado y confirmado por Katherine en esa misma sala.


## Benchmark de calidad sin llamadas a IA

La batería determinista del enrutador se ejecuta con `pytest -q tests/test_katherine_ai_benchmark.py`. Comprueba intención, dificultad, selección mínima de herramientas y conservación del contexto en preguntas de seguimiento. No consume cuotas de Gemini, Gemini o Groq.


## Menciones de usuarios en respuestas

Anarchy incluye `message.mentions` en los eventos entrantes, por lo que Katherine puede identificar menciones recibidas. Sin embargo, el esquema documentado para enviar mensajes solo incluye `content`, `buttons` y `replyMessageId`; no documenta un campo de menciones salientes. Por eso `@nombre` se envía como texto normal y no como una mención enlazada al perfil. No se usa sintaxis de Discord ni campos no documentados.


## Límites de moderación de enlaces

Katherine inspecciona automáticamente enlaces en mensajes entrantes y en ediciones, y deja de procesar el contenido cuando detecta un destino no permitido o no verificable. La API de Anarchy no permite que un bot elimine mensajes escritos por otros usuarios: el mensaje original y su enlace siguen visibles. Katherine avisa en el chat y un moderador debe retirarlo manualmente. No se informa de una eliminación si Anarchy no la ha realizado.

## Retención de actividad

`!actividad` utiliza agregados diarios por usuario con un máximo de siete días de retención automática. No se guardan textos de mensajes. Los mensajes ordinarios ya no actualizan el registro general de usuarios ni un contador acumulado de por vida; los datos necesarios para funciones explícitas (por ejemplo, puntos, afinidad o recompensas) siguen sujetos a sus plazos de expiración.


### Consola privada del creador

Los comandos siguientes se validan contra el UID exacto de `SINNER_UID`, no contra `ADMIN_UIDS`, y no se muestran en la ayuda pública:

- `!sistema`: estado operativo, conexión con Anarchy, uptime, commit si Render lo expone, tareas activas y cantidad de administradores adicionales.
- `!diagnostico`: integridad de SQLite, persistencia, tamaño de la base y estado de los proveedores.
- `!rendimiento`: CPU no bloqueante, RSS y pico de RAM, memoria del contenedor, hilos, tareas, latencia y contadores de IA/audio.
- `!laboratorio`: menú privado con `pruebas`, `ia` y `audio`. Las pruebas son deterministas y locales; las comprobaciones de IA/audio son pasivas, no llaman a proveedores, no consumen tokens y no cargan dependencias ni inician reproducción.
