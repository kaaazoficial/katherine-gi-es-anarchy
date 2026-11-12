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
