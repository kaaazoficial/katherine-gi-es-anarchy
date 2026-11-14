# Katherine AI · Núcleo y herramientas

Katherine es el bot comunitario de Genshin Impact ES. Anarchy Apps es la plataforma donde funciona; Katherine no es un bot oficial de la plataforma.

## Núcleo y proveedores
- Google Gemini es el proveedor principal; Groq es el respaldo opcional.
- Groq solo se intenta cuando el proveedor principal falla y hay credenciales válidas.
- Las credenciales se configuran en Render y nunca se escriben en el repositorio.
- Los comandos deterministas no hacen una segunda llamada al modelo.
- OpenRouter ya no forma parte de la cadena de proveedores. Las variables antiguas de OpenRouter pueden retirarse manualmente de Render después de confirmar que ningún otro proceso las utiliza.

## Componentes
- **Katherine AI Core:** conversación, clasificación de intención, ejecución validada de herramientas y planes de hasta tres consultas de solo lectura.
- **Teyvat AI:** enciclopedia, guías, equipos, rotaciones, materiales, reacciones, jefes, banners y calendario.
- **Guardian AI:** revisión preventiva bajo solicitud administrativa; no sanciona ni borra mensajes.
- **Astra Admin AI:** interpreta métricas y diagnósticos técnicos; no ejecuta cambios ni revela secretos.
- **Investigación:** búsqueda web y fuentes especializadas seleccionadas según el tema.
- **Traducción y música:** usan el mismo núcleo, sin capas de IA separadas.
- **Contexto breve:** aislado por usuario y sala, solo RAM, hasta 5 minutos y 2 intercambios.

## Configuración
- `GEMINI_API_KEY`: credencial del proveedor principal.
- `GEMINI_MODEL`: modelo principal.
- `GROQ_API_KEY`: credencial secundaria.
- `GROQ_MODEL`: modelo de respaldo.
- `KATHERINE_AI_PRIMARY_PROVIDER`: proveedor principal; normalmente `gemini`.
- Compatibilidad heredada: `KATHERINE_AI_API_KEY`, `KATHERINE_AI_MODEL` y `KATHERINE_AI_API_URL`.

## Comandos de IA
- `!asistente <petición>` o «Katherine, ...»: conversación y acciones naturales.
- `!ia preguntar <consulta>` y `!ia genshin <consulta>`: consulta general o de Teyvat.
- `!investigar <tema>`: investigación y síntesis de fuentes.
- `!ia musica <descripción>`: optimiza una búsqueda musical para el DJ.
- `!ia traducir <idioma> | <texto>`: traducción contextual.
- `!ia estado` y `!ia olvidar`: estado del proveedor y eliminación del contexto temporal.
- Administradores: `!ia probar [gemini|groq]`, `!ia guardian revisar <texto>` y `!ia diagnostico`.

No existe un comando manual de análisis de enlaces: la inspección se realiza automáticamente al recibir mensajes con URLs, antes de procesarlos.

## Seguridad y privacidad
- Los comandos propuestos por IA pasan por allowlist, validación de argumentos y el manejador original.
- Los pagos, transferencias, cambios administrativos y moderación no se ejecutan desde planes de IA.
- Los comandos administrativos vuelven a validar el UID autorizado.
- No hay analizador de chat ni captura de conversaciones para trazas. Las métricas conservan contadores agregados, sin UID, sala ni contenido.
- El contexto de IA vive solo en RAM y se elimina al caducar o reiniciar el proceso.
- La retención local no controla las políticas del proveedor externo; revisa las condiciones del proveedor configurado.
- La API de Anarchy no expone recepción nativa de imágenes entrantes ni de audio para análisis.
