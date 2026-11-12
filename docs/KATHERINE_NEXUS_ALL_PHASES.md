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
