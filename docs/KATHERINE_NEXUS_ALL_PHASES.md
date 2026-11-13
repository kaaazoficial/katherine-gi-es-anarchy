# Katherine Nexus · Fases ampliadas

> Documento actualizado para describir el comportamiento vigente. Las referencias a versiones o despliegues anteriores deben tratarse como histórico, no como configuración activa.

## Módulos
- Interpretación de solicitudes naturales y ejecución mediante los comandos existentes.
- Asistencia conversacional con proveedor IA configurable y contexto temporal en memoria.
- Centro de Investigación Avanzada con búsqueda multicapa, síntesis y modos comparar/verificar/cronología/informe.
- Progreso editable con alternativa de mensaje final cuando la edición no está disponible.
- Funciones sociales, moderación y administración sujetas a UID y permisos.
- DJ multifuente con Internet Archive, Wikimedia Commons y Jamendo opcional.
- Funciones de voz y lectura TTS con colas separadas y límites operativos.
- Diagnóstico de salud de fuentes, latencias, errores y métricas agregadas.

## Datos y privacidad
No se crea memoria personal permanente ni historial conversacional persistente. El contexto de IA y los estados interactivos se mantienen en memoria y expiran. Las funciones de análisis de chat son voluntarias, desactivadas por defecto y limitadas temporalmente. Los registros técnicos deben excluir secretos y minimizar identificadores.

## Seguridad y resiliencia
- Webhooks firmados, con límite de tamaño y concurrencia acotada.
- Las redirecciones de audio se validan salto a salto y tienen un máximo de tres.
- Las herramientas IA informan fallos como fallos; una excepción no se presenta como resultado correcto.
- Los comandos administrativos verifican UID y permisos en el manejador original.
- Las fuentes externas se tratan como contenido no confiable y se limitan por tamaño y tiempo.

## Despliegue y persistencia
La configuración versionada está en `render.yaml`. La persistencia real depende de los recursos habilitados en el panel de Render; no se debe asumir que el plan Free conserva archivos SQLite entre reinicios. Para persistencia durable se necesita un disco compatible o una base de datos externa. Los secretos se configuran únicamente como variables de entorno.

## Pruebas
Las workflows de GitHub Actions compilan todos los módulos y ejecutan pruebas unitarias y de regresión en Python 3.11 y 3.14. La auditoría de dependencias y el análisis estático forman parte del pipeline.

## Histórico
Los identificadores de ramas de rollback, SHA y descripciones de fases de versiones anteriores se conservan en el historial de Git. No deben utilizarse como instrucciones de despliegue actuales sin verificar el estado real del repositorio y Render.
