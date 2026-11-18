# Hoja de ruta de expansión de Katheryne

## Objetivo

La meta propuesta es llevar la base de código de producción hacia 120.000 líneas de código Python útiles. El número es un objetivo de expansión a largo plazo, no una autorización para generar archivos repetidos, comentarios de relleno ni lógica sin consumidores.

La medición oficial se obtiene con `python scripts/measure_source.py`. Cuenta líneas físicas no vacías y que no sean comentarios de línea completa, dentro del código Python de runtime. Excluye tests, documentación, scripts de mantenimiento, carpetas ocultas y código generado. Los tests y los documentos se informan por separado.

## Reglas de aceptación

1. Cada módulo nuevo debe tener una responsabilidad concreta, consumidores reales y límites explícitos.
2. Las funciones públicas deben validar entradas, límites de tamaño, tiempos de espera y errores esperables.
3. Toda capacidad que afecte al usuario debe tener pruebas unitarias y, cuando corresponda, integración aislada.
4. Las pruebas no deben necesitar tokens de producción ni invocar servicios externos reales.
5. No se introduce memoria personal permanente ni historial conversacional. El estado duradero se limita a datos operativos o de juego existentes, con retención definida.
6. No se modifica la identidad: Katheryne continúa usando exclusivamente el bot comunitario.
7. No se integra una etapa si falla compilación, análisis estático, seguridad o pruebas de regresión.

## Etapas de arquitectura

### 1. Ciclo de vida y confiabilidad del proceso

- Estado explícito de arranque, disponibilidad, degradación y apagado.
- Supervisión acotada de tareas asíncronas.
- Apagado cooperativo con tiempo máximo y diagnóstico sin datos personales.
- Separación entre liveness (`/health`) y readiness (`/ready`).

### 2. Dominio de Genshin Impact

Extraer progresivamente de `main.py` las consultas de datos, guías, builds, materiales, equipos, noticias y formatos. Definir modelos de dominio, clientes HTTP, cachés con límites y adaptadores de fuentes. Mantener respuestas y comandos existentes mediante fachadas de compatibilidad durante la migración.

### 3. Música y voz

Separar catálogo y proveedores, búsqueda, licencias, descarga, caché, decodificación PCM, mezcla, colas, LiveKit y TTS. Mantener las dependencias multimedia bajo demanda y proteger las rutas críticas del webhook frente a tareas de audio.

### 4. Comunidad y moderación

Separar perfiles, actividad agregada, ajustes por sala, permisos, advertencias, reglas automáticas, encuestas, recompensas y auditoría. Mantener consultas SQL parametrizadas y aplicar retención/anonimización en cada nuevo dato persistente.

### 5. IA, investigación e integraciones

Desacoplar orquestación, selección de proveedor, límites, herramientas, investigación web, extracción, traducción y verificación de fuentes. Las herramientas deben ser explícitas, autorizadas y limitadas por tiempo, tamaño y permisos.

### 6. Transporte y entrega

Separar validación de webhook, firma, deduplicación, despacho, entrega/reintentos, cliente API y protección frente a fallos externos. Mantener el endpoint de salud compatible con los monitores existentes.

### 7. Rendimiento y calidad continua

Añadir pruebas de concurrencia, carga sintética, perfiles de importación y arranque, regresión de latencia, límites de memoria y análisis de consultas. Cada módulo extraído debe demostrar que conserva comportamiento y reduce dependencias cruzadas.

## Política de integración y retorno

Todo el trabajo se desarrolla en ramas y PR separados. El respaldo `backup/pre-50k-expansion-2026-11-17` conserva el commit anterior a esta expansión. Mientras los cambios no se integren en `main`, la versión activa no cambia. Ante una regresión, se cierra o revierte el PR y se vuelve a ese punto, sin tocar credenciales ni infraestructura.
