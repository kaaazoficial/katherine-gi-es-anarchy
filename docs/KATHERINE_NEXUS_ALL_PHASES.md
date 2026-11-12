# Katherine Nexus · Fases ampliadas

Katherine conserva el nombre y reutiliza las funciones existentes.

## Módulos incluidos
- Interpretación determinista de solicitudes claras en español.
- Consultas multicapa con fuentes independientes consultadas en paralelo, tiempos máximos y degradación segura.
- Progreso editable mediante PATCH dentro de la ventana documentada, con envío final alternativo si no se obtiene un ID de mensaje o la edición falla.
- Centro interactivo con controles para el menú, estado, fuentes, ayuda y cola DJ.
- Eventos comunitarios efímeros, participación voluntaria, capacidad limitada y caducidad en memoria.
- Diagnóstico de salud de fuentes externas, latencias y errores.
- Recuperación de DJ con reintentos acotados y backoff antes de pasar a la siguiente pista.

## Datos y límites
No se añade IA externa, tablas SQLite, DMs, lectura de historial, adjuntos ni operaciones de moderación no documentadas. Los eventos, progreso y nuevos estados se mantienen solo en memoria y desaparecen al reiniciar el proceso.

## Reversión
La rama `rollback/katherine-2.0` preserva `f14714b6d43952d928805af1360743b37e7eede5` como punto de retorno 2.0, con pruebas automáticas y sin Nexus. La rama de despliegue no se modifica hasta pasar las pruebas y revisar el inicio en Render.

## DJ · búsqueda musical multifuente

El comando `!dj buscar <tema>` consulta en paralelo los catálogos disponibles:

- **Internet Archive:** pistas con licencias CC0, CC BY, CC BY-SA o dominio público verificable.
- **Wikimedia Commons:** archivos de audio con metadatos de licencia compatibles y URL multimedia oficial.
- **Jamendo:** catálogo opcional; requiere configurar `JAMENDO_CLIENT_ID` en Render. Solo se muestran pistas cuya descarga esté autorizada y cuya licencia pase el filtro de Katherine.

Los resultados se mezclan entre catálogos para evitar que una única fuente monopolice la búsqueda. Si un proveedor falla, los demás siguen funcionando. Cada resultado conserva la ficha, autor, fuente y licencia para atribución. Las descargas solo aceptan HTTPS, dominios de proveedores permitidos, formatos de audio admitidos y archivos de hasta 150 MB; se valida el audio antes de enviarlo a LiveKit.

Comandos:
- `!dj buscar <tema>`: búsqueda combinada.
- `!dj fuentes`: proveedores disponibles y configuración.
- `!dj pausa / reanudar / saltar / cola`: controles conservados.

Jamendo permanece desactivado hasta que se configure su Client ID. YouTube y Spotify no se tratan como fuentes de descarga directa porque sus APIs no entregan una pista reproducible libremente para este flujo.


## ASTRA NOVA 4.0 · alcance aprobado

### Activación conversacional
- El enrutador natural solo procesa mensajes que comienzan con «Katherine» u «Oye Katherine».
- Los mensajes ordinarios no activan respuestas, menús ni respuestas automáticas; sí conservan el conteo de actividad y las comprobaciones antispam.
- Los comandos clásicos con prefijo siguen disponibles sin cambios.
- Las solicitudes conocidas se traducen a comandos existentes. Las solicitudes no reconocidas reciben una respuesta de capacidades; no se simula una IA generativa ni se ejecutan acciones no implementadas.

### Enciclopedia de Teyvat
\`!enciclopedia <nombre>\` consulta en paralelo personajes, armas, artefactos, enemigos, materiales, comida, dominios, geografía y elementos usando la API existente de Genshin-DB. Se mantiene separada de las guías y calculadoras que no forman parte de este alcance.

### ECLIPSE · DJ y voz
- Recomendaciones por ambiente: estudiar, concentración, trabajar, relajarse, dormir, jugar, combate, triste, feliz y anime.
- Si la descarga o validación de la pista seleccionada falla por un problema del archivo, Katherine prueba las siguientes opciones de la búsqueda. Los errores de sala, permisos o LiveKit no se disfrazan como fallos de canción.
- Favoritos por usuario y sala, solo en memoria, con caducidad de 24 horas y borrado al reiniciar. No se crea una nueva tabla ni se guardan URL de favoritos en SQLite.
- Las playlists existentes siguen siendo persistentes y reproducen pistas locales en cola continua. No se mezclan con favoritos efímeros.
- La cola TTS prioriza mensajes dirigidos a Katherine; conserva una cola normal para el resto de mensajes leídos. Se omiten duplicados recientes y se limita la longitud por intervención.
- Las fuentes musicales y los metadatos de licencia siguen validándose antes de la reproducción.

### GUARDIAN
- Antispam por velocidad, repetición, ráfagas de enlaces y menciones masivas.
- Límites por sala configurables desde \`!antispam\`: \`limite\`, \`ventana\`, \`repeticiones\`, \`enlaces\` y \`menciones\`.
- Advertencias automáticas, retirada administrativa de advertencias, auditoría y panel \`!guardian\`.
- No se promete borrar mensajes, silenciar o expulsar miembros: esas operaciones no están habilitadas en la API de este bot.

### ASTRA CORE técnico
- Helpers puros separados en \`katherine_nexus.py\` y \`katherine_astra.py\`.
- Solicitudes externas con concurrencia limitada, timeouts, reintentos seguros y circuit breaker temporal por host.
- Diagnóstico \`!estado\` con contadores de circuitos, recuperación DJ, TTS y fuentes externas.
- Las nuevas pruebas cubren activación explícita, favoritos efímeros, límites Guardian, circuito externo y fallback DJ.

### Despliegue por fases
1. Rama aislada \`feature/astra-nova-4.0\`.
2. Compilación y pruebas automáticas.
3. Revisión del diff y compatibilidad con la API.
4. Pull request y validación final.
5. Merge a \`main\` solo tras CI verde.
6. Render despliega automáticamente desde \`main\`; luego se verifica \`/health\`, inicio de ambos bots y logs.
7. Si falla el arranque o una regresión crítica, se revierte al commit estable sin modificar \`rollback/katherine-2.0\`.
