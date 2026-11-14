# Katherine · Catálogo y permisos de comandos

La ayuda del chat (`!ayuda`, `!comandos` y `!menu`) muestra el catálogo completo en texto plano, agrupado por categorías y con descripciones breves. No crea botones ni abre un panel interactivo. Este documento conserva el detalle técnico y los permisos; la IA tiene ayuda separada mediante `!ia ayuda`.

## Investigación y herramientas universales

- `!investigar <tema>`: busca páginas públicas en varios resultados independientes, recupera extractos y solicita una síntesis basada en las fuentes.
- Modos del Centro de Investigación Avanzada: `!investigar comparar: <tema>` contrasta fuentes y posturas; `!investigar verificar: <afirmación>` clasifica afirmaciones según la evidencia; `!investigar cronologia: <tema>` ordena acontecimientos con fechas verificables; `!investigar informe: <tema>` genera un informe exhaustivo. Sin modo explícito se usa la investigación general.
- `!enlace <URL>`: analiza una página HTML pública HTTPS; no sigue redirecciones ni acepta hosts locales/privados.
- La investigación combina Wikipedia, Wikidata y búsqueda web general. Según la intención, añade Internet Archive (libros/archivos), OpenAlex y Crossref (bibliografía científica), NASA (biblioteca de imágenes y vídeos espaciales), MusicBrainz y Jamendo (metadatos y catálogo musical), y KeqingMains, HoYoLAB y Fandom (Genshin Impact). Las fuentes especializadas solo se consultan en temas pertinentes; no se mezclan con preguntas generales. Se muestran hasta ocho fuentes de dominios distintos. KeqingMains y Fandom son fuentes comunitarias; HoYoLAB puede contener publicaciones oficiales y de usuarios, por lo que se comprueba la autoría. Jamendo requiere `JAMENDO_CLIENT_ID` en Render; sin ella se omite. MusicBrainz limita las consultas a una por segundo. OpenAlex, Crossref, Wikidata, Internet Archive y NASA usan endpoints públicos sin clave de Katherine.
- `!ia investigar <tema>` y `!ia enlace <URL>`: accesos desde el menú IA.
- Todos los comandos del catálogo y las acciones sociales (por ejemplo, `Katherine investigar comparar: ...`, `Katherine dj pausa`, `Katherine abrazo @usuario` o `Katherine perfil`) pueden invocarse directamente después de «Katherine». Se conservan los permisos originales y la mención aislada no activa el bot.
- La síntesis cita las fuentes por número y dominio; páginas bloqueadas, no HTML o sin texto suficiente pueden omitirse. Los metadatos de Crossref/OpenAlex no garantizan acceso al texto completo y los derechos de Internet Archive/Jamendo dependen de cada ítem. La búsqueda no garantiza exhaustividad.

## Administración por UID

Los comandos marcados como `[ADMIN]` solo aceptan el UID entregado por Anarchy Apps en `message.author.uid`. No se usa el nickname como sustituto para otorgar permisos.

En Render, configura la variable `ADMIN_UIDS` con uno o más UID. Se aceptan separadores de coma, punto y coma o salto de línea. Para conocer tu identificador ejecuta `!miuid` y copia el valor recibido. Tras modificar la variable, aplica los cambios y reinicia/despliega el servicio.

## Comandos privilegiados

- `!estado`, `!fuentes`, `!saludfuentes`, `!metricas`, `!autotest`
- `!centro`, `!nexus`
- `!admin`, `!paneladmin`, `!guardian`
- `!antispam`, `!advertir`, `!advertencias`, `!quitaradvertencia`
- `!perfilbot`, `!avatarbot`
- `!respuestas`, `!respuesta`
- `!ia probar [gemini|groq|openrouter]`, `!ia guardian`, `!ia analista`, `!ia diagnostico`

Los eventos se crean libremente; solo su creador o un UID administrador puede cerrarlos. Los comandos generales de fecha, zona horaria, menú, economía, traducción y multimedia siguen disponibles para todos.

### Métricas y sugerencias de comandos

- `!metricas` muestra solo métricas agregadas del proceso y requiere UID administrador.
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
- `!dj detener` / `!dj stop`: detiene la reproducción y mantiene la conexión a la llamada.
- `!salirsala`: salida explícita de Katherine de la llamada.
- El final de una pista, una cola vacía o un fallo de TTS no deben provocar una salida de la llamada.

## Respuestas a mensajes

Al responder directamente a un mensaje de Katherine, el bot interpreta el texto como una continuación de la conversación. Solo se activa cuando el ID citado corresponde a un mensaje enviado y confirmado por Katherine en esa misma sala.


## Benchmark de calidad sin llamadas a IA

La batería determinista del enrutador se ejecuta con `pytest -q tests/test_katherine_ai_benchmark.py`. Comprueba intención, dificultad, selección mínima de herramientas y conservación del contexto en preguntas de seguimiento. No consume cuotas de Gemini, Groq u OpenRouter.


## Menciones de usuarios en respuestas

Cuando el evento contiene `message.mentions` con UID y nombre, Katherine conserva `@nombre` en el texto y adjunta la referencia estructurada en el campo `mentions` del mensaje saliente. No usa sintaxis de otra plataforma como `<@UID>`. La asociación se limita al mensaje que originó la respuesta y solo vive en memoria; si no hay UID verificable, no inventa una mención.
