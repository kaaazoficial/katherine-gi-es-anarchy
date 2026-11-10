'use strict';

/**
 * ============================================================================
 * KATHERINE BOT SERVER — ARCHITECTURE & CORE ENGINE
 * ============================================================================
 * Entorno: Node.js / Express (Optimizado para Render)
 * Integración: Anarchy Bot API v1
 * ============================================================================
 */

const express = require('express');
const crypto = require('crypto');

const app = express();

// ============================================================================
// 1. CONFIGURACIÓN GENERAL Y CONSTANTES DEL ENTORNO
// ============================================================================

const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;
const PORT = Number(process.env.PORT) || 3000;

const API_URL = 'https://api.anarchyapps.org/bot-api/v1';
const startTime = Date.now();
const MAX_TIMESTAMP_AGE = 300; // 5 minutos de margen para timestamps de webhooks

if (!BOT_TOKEN) {
    throw new Error('[FATAL ERROR] Falta la variable de entorno obligatoria: BOT_TOKEN.');
}

if (!SECRET) {
    console.warn('⚠️ [AVISO DE SEGURIDAD] No se detectó WEBHOOK_SECRET o WEB_TOKEN. La validación HMAC está desactivada para pruebas.');
}

// ============================================================================
// 2. GESTIÓN DE ESTADOS AISLADOS POR THREAD Y COOLDOWNS
// ============================================================================

const threadStates = new Map();
const userCooldowns = new Map();
const auditLogs = new Map();

/**
 * Obtiene o inicializa el estado específico para un hilo de chat (threadId)
 * @param {string} threadId 
 * @returns {Object} Estado del bot en ese hilo
 */
function obtenerEstadoThread(threadId) {
    if (!threadStates.has(threadId)) {
        threadStates.set(threadId, {
            lectura: false,
            conectado: false,
            volumen: 30,
            reproduciendo: false,
            cancionActual: null,
            colaReproduccion: [],
            mensajesProcesadosCount: 0,
            ultimaActividad: Date.now()
        });
    }
    return threadStates.get(threadId);
}

/**
 * Registra una traza de auditoría en memoria para un hilo específico
 * @param {string} threadId 
 * @param {string} evento 
 */
function registrarAuditoria(threadId, evento) {
    if (!auditLogs.has(threadId)) {
        auditLogs.set(threadId, []);
    }
    const logs = auditLogs.get(threadId);
    logs.unshift({ timestamp: new Date().toISOString(), evento });
    if (logs.length > 50) logs.pop(); // Mantener solo los últimos 50 eventos
}

/**
 * Gestiona el sistema de cooldowns para evitar spam por usuario
 * @param {string} userId 
 * @param {number} cooldownMs 
 * @returns {boolean} True si el usuario está en cooldown
 */
function verificarCooldown(userId, cooldownMs = 3000) {
    const ahora = Date.now();
    const ultimoUso = userCooldowns.get(userId) || 0;
    if (ahora - ultimoUso < cooldownMs) {
        return true;
    }
    userCooldowns.set(userId, ahora);
    return false;
}

// ============================================================================
// 3. MIDDLEWARE EXPRESS Y CAPTURA DE RAW BODY
// ============================================================================

app.use(express.json({
    limit: '2mb',
    verify: (req, res, buf) => {
        req.rawBody = buf.toString('utf8');
    }
}));

app.use(express.urlencoded({
    extended: true,
    limit: '2mb'
}));

// ============================================================================
// 4. VALIDACIÓN DE SEGURIDAD HMAC-SHA256
// ============================================================================

function verificarFirmaWebhook(req) {
    if (!SECRET) {
        return true; // Si no hay secreto configurado, bypass para desarrollo
    }

    const timestamp = req.headers['x-anarchy-bot-timestamp'];
    const signature = req.headers['x-anarchy-bot-signature'];

    console.log('\n----------------------------------------');
    console.log('🔍 [HMAC DIAGNÓSTICO DE SEGURIDAD]');
    console.log('Timestamp recibido:', timestamp ? 'SÍ' : 'NO');
    console.log('Firma recibida:', signature ? 'SÍ' : 'NO');
    console.log('RawBody disponible:', req.rawBody !== undefined);

    if (!timestamp || !signature || req.rawBody === undefined) {
        console.warn('❌ [MOTIVO HMAC] Faltan cabeceras de seguridad o cuerpo raw.');
        return false;
    }

    const time = Number(timestamp);
    if (!Number.isFinite(time)) {
        console.warn('❌ [MOTIVO HMAC] Timestamp no numérico.');
        return false;
    }

    const seconds = time > 1e12 ? time / 1000 : time;
    if (Math.abs(Date.now() / 1000 - seconds) > MAX_TIMESTAMP_AGE) {
        console.warn('❌ [MOTIVO HMAC] Timestamp fuera del margen de tolerancia (5 min).');
        return false;
    }

    try {
        const expectedHmac = crypto
            .createHmac('sha256', SECRET)
            .update(`${timestamp}.${req.rawBody}`)
            .digest('hex');

        const cleanSignature = signature.replace(/^v1=/, '');

        if (!/^[a-fA-F0-9]{64}$/.test(cleanSignature)) {
            console.warn('❌ [MOTIVO HMAC] Formato de firma inválido.');
            return false;
        }

        const expectedBuffer = Buffer.from(expectedHmac, 'hex');
        const receivedBuffer = Buffer.from(cleanSignature, 'hex');

        if (expectedBuffer.length !== receivedBuffer.length) {
            console.warn('❌ [MOTIVO HMAC] Discrepancia en la longitud del buffer de firma.');
            return false;
        }

        const isValid = crypto.timingSafeEqual(expectedBuffer, receivedBuffer);
        console.log('✅ [RESULTADO HMAC]:', isValid ? 'VÁLIDA' : 'INVÁLIDA');
        return isValid;

    } catch (error) {
        console.error('❌ [ERROR CRÍTICO HMAC]:', error.message);
        return false;
    }
}

// ============================================================================
// 5. CLIENTE HTTP OFICIAL PARA LA API DE ANARCHY
// ============================================================================

async function enviarPeticionAnarchy(endpoint, method = 'GET', body = null) {
    try {
        const url = `${API_URL}${endpoint}`;
        const options = {
            method,
            headers: {
                'Authorization': `Bot ${BOT_TOKEN}`,
                'Content-Type': 'application/json',
                'User-Agent': 'KatherineBot-NodeJS/2.0'
            },
            signal: AbortSignal.timeout(12000)
        };

        if (body !== null) {
            options.body = JSON.stringify(body);
        }

        console.log(`\n🌐 [API REQUEST] ${method} -> ${endpoint}`);

        const response = await fetch(url, options);
        const responseText = await response.text();

        let responseData;
        try {
            responseData = responseText ? JSON.parse(responseText) : {};
        } catch {
            responseData = { raw: responseText };
        }

        console.log(`📥 [API RESPONSE] Status: ${response.status} | Body:`, JSON.stringify(responseData));

        return {
            ok: response.ok,
            status: response.status,
            data: responseData
        };

    } catch (err) {
        console.error(`❌ [API EXCEPTION] Error de red en ${endpoint}:`, err.message);
        return {
            ok: false,
            status: 500,
            data: { error: err.message }
        };
    }
}

/**
 * Función auxiliar para enviar respuestas directas a un hilo
 * @param {string} threadId 
 * @param {string} texto 
 */
async function enviarRespuestaChat(threadId, texto) {
    const endpoint = `/threads/${encodeURIComponent(threadId)}/messages`;
    const resultado = await enviarPeticionAnarchy(endpoint, 'POST', { content: texto });
    
    if (resultado.ok) {
        registrarAuditoria(threadId, `Mensaje enviado con éxito.`);
    } else {
        registrarAuditoria(threadId, `Error al enviar mensaje [${resultado.status}]`);
    }
    return resultado;
}

// ============================================================================
// 6. RUTINAS DE VALIDACIÓN INICIAL DEL BOT
// ============================================

async function verificarCredencialesBot() {
    console.log('\n============================================================');
    console.log('🔍 [INICIO] Verificando credenciales de Katherine con Anarchy API...');
    console.log('============================================================');

    const resultado = await enviarPeticionAnarchy('/me', 'GET');

    if (resultado.ok && resultado.data) {
        const botData = resultado.data.bot || resultado.data;
        console.log('✅ [AUTENTICACIÓN EXITOSA]');
        console.log(`   - Nombre del Bot: ${botData.name || 'Katherine'}`);
        console.log(`   - Bot ID:         ${botData.botId || 'N/A'}`);
        console.log(`   - Prefijo Oficial: ${botData.commandPrefix || '!'}`);
        console.log(`   - NDC ID:         ${botData.ndcId || 'N/A'}`);
    } else {
        console.error('❌ [ERROR DE AUTENTICACIÓN]: No se pudo validar el Token en /me.');
        console.error('   Detalles:', resultado.data);
    }
    console.log('============================================================\n');
}

// ============================================================================
// 7. SISTEMA CENTRAL DE PROCESAMIENTO DE COMANDOS
// ============================================================================

async function procesarComandoEntrante(evento) {
    const threadId = evento.threadId;
    const mensaje = evento.message;

    if (!threadId || typeof mensaje?.content !== 'string') {
        console.log('⚠️ [EVENTO IGNORADO] El payload no contiene threadId o contenido válido.');
        return;
    }

    const contenidoTexto = mensaje.content.trim();
    if (!contenidoTexto) return;

    const args = contenidoTexto.split(/\s+/);
    const comando = args[0].toLowerCase();
    const userId = mensaje.author?.uid || mensaje.author?.id || 'anonimo';
    const usuarioNombre = mensaje.author?.nickname || mensaje.author?.name || 'Anónimo';

    console.log(`\n🎯 [COMANDO CAPTURADO] "${comando}" | Usuario: ${usuarioNombre} (${userId}) | Thread: ${threadId}`);

    // Control de Cooldown (3 segundos por usuario)
    if (verificarCooldown(userId, 3000)) {
        console.log(`⏳ [COOLDOWN] El usuario ${usuarioNombre} está enviando comandos muy rápido.`);
        return;
    }

    const estado = obtenerEstadoThread(threadId);
    estado.mensajesProcesadosCount++;
    estado.ultimaActividad = Date.now();

    let respuestaTexto = null;

    switch (comando) {
        // --- COMANDO: AYUDA ---
        case '-ayuda':
        case '!ayuda':
            respuestaTexto = 
                '🤖 **KATHERINE — CENTRO DE CONTROL AVANZADO**\n\n' +
                '📖 **COMANDOS GENERALES**\n' +
                '🔊 `-leerchat` — Activar lectura automática.\n' +
                '🔇 `-noleerchat` — Desactivar lectura automática.\n' +
                '📊 `-estadochat` — Consultar métricas del hilo.\n' +
                '📈 `-stats` — Ver rendimiento y memoria de Render.\n' +
                '🏓 `-ping` — Comprobar latencia y uptime.\n\n' +
                '🎵 **MÚSICA Y AUDIO**\n' +
                '▶️ `-play <canción>` — Añadir a la cola.\n' +
                '⏸️ `-pausa` — Pausar reproducción.\n' +
                '⏹️ `-stop` — Detener y limpiar cola.\n' +
                '🎚️ `-volumen <0-100>` — Ajustar nivel de audio.';
            break;

        // --- COMANDO: PING & UPTIME ---
        case '-ping':
        case '!ping': {
            const uptimeSegundos = Math.floor((Date.now() - startTime) / 1000);
            const horas = Math.floor(uptimeSegundos / 3600);
            const minutos = Math.floor((uptimeSegundos % 3600) / 60);
            const segundos = uptimeSegundos % 60;

            respuestaTexto = 
                '🏓 **PONG! KATHERINE ESTÁ EN LÍNEA**\n\n' +
                '🟢 **Estado del Servidor:** Operativo\n' +
                `⏱️ **Uptime:** ${horas}h ${minutos}m ${segundos}s\n` +
                '⚡ **Infraestructura:** Render Cloud (Node.js)';
            break;
        }

        // --- COMANDO: ESTADO DEL CHAT ---
        case '-estadochat':
        case '!estadochat':
            respuestaTexto = 
                '📊 **KATHERINE — ESTADO DEL HILO**\n\n' +
                `🏠 **Thread ID:** \`${threadId}\`\n` +
                `🔊 **Modo Lectura:** ${estado.lectura ? '🟢 ACTIVADO' : '🔴 DESACTIVADO'}\n` +
                `🎙️ **Llamada de Voz:** ${estado.conectado ? '🟢 CONECTADO' : '⚪ DESCONECTADO'}\n` +
                `🎵 **Reproductor:** ${estado.reproduciendo ? '🟢 REPRODUCIENDO' : '⚪ INACTIVO'}\n` +
                `🎚️ **Volumen Actual:** ${estado.volumen}/100\n` +
                `💬 **Mensajes Procesados:** ${estado.mensajesProcesadosCount}`;
            break;

        // --- COMANDO: ESTADÍSTICAS DEL SISTEMA ---
        case '-stats':
        case '!stats': {
            const usoMemoria = process.memoryUsage();
            const memoriaRSS = Math.round(usoMemoria.rss / 1024 / 1024);
            const memoriaHeap = Math.round(usoMemoria.heapUsed / 1024 / 1024);

            respuestaTexto = 
                '📈 **KATHERINE — DIAGNÓSTICO DE RENDIMIENTO**\n\n' +
                `🧠 **Memoria RSS:** ${memoriaRSS} MB\n` +
                `💻 **Heap Usado:** ${memoriaHeap} MB\n` +
                `🧵 **Hilos Activos en Memoria:** ${threadStates.size}\n` +
                `⚙️ **Node.js Versión:** ${process.version}`;
            break;
        }

        // --- COMANDO: ACTIVAR LECTURA ---
        case '-leerchat':
        case '!leerchat':
            if (estado.lectura) {
                respuestaTexto = '🔊 El modo lectura ya se encontraba activado en este hilo.';
            } else {
                estado.lectura = true;
                respuestaTexto = '🔊 **Katherine:** Modo lectura del chat **ACTIVADO**.';
                registrarAuditoria(threadId, 'Modo lectura activado.');
            }
            break;

        // --- COMANDO: DESACTIVAR LECTURA ---
        case '-noleerchat':
        case '!noleerchat':
            if (!estado.lectura) {
                respuestaTexto = '🔇 El modo lectura ya se encontraba desactivado.';
            } else {
                estado.lectura = false;
                respuestaTexto = '🔇 **Katherine:** Modo lectura del chat **DESACTIVADO**.';
                registrarAuditoria(threadId, 'Modo lectura desactivado.');
            }
            break;

        // --- COMANDO: VOLUMEN ---
        case '-volumen':
        case '!volumen': {
            if (!args[1]) {
                respuestaTexto = `🎚️ Nivel de volumen actual: **${estado.volumen}/100**`;
                break;
            }
            const nuevoVolumen = Number(args[1]);
            if (!Number.isInteger(nuevoVolumen) || nuevoVolumen < 0 || nuevoVolumen > 100) {
                respuestaTexto = '⚠️ Por favor, introduce un valor numérico entero entre **0 y 100**.';
                break;
            }
            estado.volumen = nuevoVolumen;
            respuestaTexto = `🎚️ Volumen ajustado correctamente a **${nuevoVolumen}/100**.`;
            registrarAuditoria(threadId, `Volumen modificado a ${nuevoVolumen}`);
            break;
        }

        // --- COMANDO: REPRODUCCIÓN (MÚSICA) ---
        case '-play':
        case '!play': {
            const consulta = args.slice(1).join(' ');
            if (!consulta) {
                respuestaTexto = '⚠️ Debes especificar el nombre o enlace de la canción. Ejemplo: `-play Follow Me`';
                break;
            }
            estado.reproduciendo = true;
            estado.cancionActual = consulta;
            estado.colaReproduccion.push(consulta);
            respuestaTexto = `🎵 **KATHERINE MUSIC SYSTEM**\n\n▶️ **Agregado a la cola:** ${consulta}\n🎚️ **Posición en cola:** ${estado.colaReproduccion.length}`;
            registrarAuditoria(threadId, `Canción añadida: ${consulta}`);
            break;
        }

        case '-pausa':
        case '!pausa':
            estado.reproduciendo = false;
            respuestaTexto = '⏸️ Reproducción de audio **pausada** temporalmente.';
            break;

        case '-stop':
        case !stop:
            estado.reproduciendo = false;
            estado.cancionActual = null;
            estado.colaReproduccion = [];
            respuestaTexto = '⏹️ Reproducción detenida y cola de música vaciada.';
            break;

        default:
            if (comando.startsWith('-') || comando.startsWith('!')) {
                respuestaTexto = `❓ Comando desconocido: \`${comando}\`. Utiliza \`-ayuda\` para ver la lista de comandos disponibles.`;
            }
            break;
    }

    if (respuestaTexto) {
        await enviarRespuestaChat(threadId, respuestaTexto);
    }
}

// ============================================================================
// 8. ENDPOINTS DEL SERVIDOR EXPRESS (WEBHOOK Y STATUS)
// ============================================================================

app.post('/', (req, res) => {
    console.log('\n============================================================');
    console.log('⚡ [WEBHOOK RECIBIDO] Petición entrante desde Anarchy');
    console.log('============================================================');

    if (!verificarFirmaWebhook(req)) {
        console.warn('⛔ [SEGURIDAD] Solicitud rechazada por fallo en la firma HMAC.');
        return res.status(401).json({ error: 'Invalid signature or unauthorized request' });
    }

    // Responder inmediatamente con 200 OK para cumplir con el tiempo límite de los webhooks
    res.status(200).send('OK');

    // Procesamiento asíncrono para no bloquear el hilo principal de Node.js
    setImmediate(async () => {
        try {
            const evento = req.body || {};
            await procesarComandoEntrante(evento);
        } catch (error) {
            console.error('❌ [ERROR CRÍTICO EN PROCESAMIENTO DE EVENTO]:', error);
        }
    });
});

// Ruta de estado general para monitorización HTTP GET en Render
app.get('/', (req, res) => {
    const uptimeSegundos = Math.floor((Date.now() - startTime) / 1000);
    res.status(200).json({
        bot: 'Katherine',
        architecture: 'Node.js / Express',
        status: 'online',
        uptimeSeconds: uptimeSegundos,
        activeThreads: threadStates.size,
        timestamp: new Date().toISOString()
    });
});

// Endpoint opcional para consultar logs de auditoría de un hilo específico vía HTTP
app.get('/audit/:threadId', (req, res) => {
    const threadId = req.params.threadId;
    if (!auditLogs.has(threadId)) {
        return res.status(404).json({ error: 'No hay registros de auditoría para este hilo.' });
    }
    res.status(200).json({
        threadId,
        logs: auditLogs.get(threadId)
    });
});

// ============================================================================
// 9. INICIO Y ESCUCHA DEL SERVIDOR EN EL PUERTO ASIGNADO
// ============================================================================

const server = app.listen(PORT, '0.0.0.0', () => {
    console.log('\n============================================================');
    console.log('                 KATHERINE BOT — CORE v2.0                  ');
    console.log('============================================================');
    console.log(`🚀 Puerto de escucha: ${PORT}`);
    console.log(`🌐 Entorno:           ${process.env.NODE_ENV || 'production'}`);
    console.log(`🔒 Seguridad HMAC:    ${SECRET ? 'ACTIVADA' : 'DESACTIVADA (Modo Pruebas)'}`);
    console.log('============================================================');

    // Ejecutar verificación de credenciales al arrancar
    verificarCredencialesBot();
});

// Manejo defensivo de errores globales del proceso Node.js
server.on('error', (error) => {
    console.error('💥 [SERVER ERROR EVENT]:', error);
});

process.on('unhandledRejection', (reason, promise) => {
    console.error('💥 [UNHANDLED PROMISE REJECTION]:', reason);
});

process.on('uncaughtException', (error) => {
    console.error('💥 [UNCAUGHT EXCEPTION CRÍTICA]:', error);
});
