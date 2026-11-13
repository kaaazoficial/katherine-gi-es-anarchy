# Katherine · Catálogo y permisos de comandos

El catálogo de usuario se genera con `!ayuda` y `!comandos` en `main.py`. Debe mantenerse sincronizado con los manejadores reales.

## Investigación y herramientas universales

- `!investigar <tema>`: busca páginas públicas en varios resultados independientes, recupera extractos y solicita una síntesis basada en las fuentes.
- `!enlace <URL>`: analiza una página HTML pública HTTPS; no sigue redirecciones ni acepta hosts locales/privados.
- `!ia investigar <tema>` y `!ia enlace <URL>`: accesos desde el menú IA.
- Katherine también reconoce peticiones explícitas como «Katherine, investiga…» y «Katherine, analiza este enlace…». La mención aislada sigue sin activar el bot.
- La síntesis cita las fuentes por número y dominio; páginas bloqueadas, no HTML o sin texto suficiente pueden omitirse. La búsqueda no garantiza exhaustividad.

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
- `!ia probar`, `!ia guardian`, `!ia analista`, `!ia diagnostico`

Los eventos se crean libremente; solo su creador o un UID administrador puede cerrarlos. Los comandos generales de fecha, zona horaria, menú, economía, traducción y multimedia siguen disponibles para todos.

### Métricas y sugerencias de comandos

- `!metricas` muestra solo métricas agregadas del proceso y requiere UID administrador.
- `GET /metrics` expone métricas Prometheus sin etiquetas de usuario, sala o mensaje. Puede protegerse configurando `PROMETHEUS_METRICS_TOKEN` en Render; cuando se configura, requiere `Authorization: Bearer <token>`.
- Si se escribe mal un comando, Katherine puede sugerir una coincidencia de alta confianza. La sugerencia nunca ejecuta el comando automáticamente.

## Protección adicional

- Si el evento no contiene un UID real, se deniega cualquier comando privilegiado aunque el nickname coincida con un UID configurado.
- Las comprobaciones del panel interactivo vuelven a validar el UID al pulsar botones.
- Los UID configurados se comparan como cadenas exactas y no se aceptan valores vacíos.
