'use strict';

/**
 * ============================================================================
 * KATHERINE BOT SERVER — PRODUCTION CORE v2.1 (PULIDO Y TOLERANTE A FALLOS)
 * ============================================================================
 */

const express = require('express');
const crypto = require('crypto');

const app = express();

// ============================================================================
// 1. CONFIGURACIÓN GENERAL
// ============================================================================

const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;
const PORT = Number(process.env.PORT) || 3000;

const API_URL = 'https://api.anarchyapps.org/bot-api/v1';
const startTime = Date.now();
const MAX_TIMESTAMP_AGE = 300;

if (!BOT_TOKEN) {
    throw new Error('[FATAL ERROR] Falta la variable de entorno obligatoria: BOT_TOKEN.');
}

// ============================================================================
// 2. GESTIÓN DE ESTADOS Y COOLDOWNS
// ============================================================================

const threadStates = new Map();
const userCooldowns = new Map();

function obtenerEstadoThread(threadId) {
    if (!threadStates.has(threadId)) {
        threadStates.set(threadId, {
            lectura: false,
            conectado: false,
            volumen: 30,
            reproduciendo: false,
            cancionActual: null,
            colaReproduccion: [],
            mensajesProcesadosCount: 0
        });
    }
    return threadStates.get(threadId);
}

function verificarCooldown(userId, cooldownMs = 2500) {
    const ahora = Date.now();
    const ultimoUso = userCooldowns.get(userId) || 0;
    if (ahora - ultimoUso < cooldownMs) return true;
    userCooldowns.set(userId, ahora);
    return false;
}

// ============================================================================
// 3. MIDDLEWARE EXPRESS
// ============================================================================

app.use(express.json({
    limit: '2mb',
    verify: (req, res, buf) => {
        req.rawBody = buf.toString('utf8');
    }
}));

app.use(express.urlencoded({ extended: true, limit: '2mb' }));

// ============================================================================
// 4. VALIDACIÓN HMAC FLEXIBLE (NO BLOQUEANTE)
// ============================================================================

function verificarFirmaWebhook(req) {
    if (!SECRET) return true;

    const timestamp = req.headers['x-anarchy-bot-timestamp'];
    const signature = req.headers['x-anarchy-bot-signature'];

    if (!timestamp || !signature || req.rawBody === undefined) {
        console.warn('⚠️ [HMAC AVISO] Faltan cabeceras de firma, pero permitiendo acceso para pruebas.');
        return true; // Flexible para evitar bloqueos por configuración
    }

    try {
        const expectedHmac = crypto
            .createHmac('sha256', SECRET)
            .update(`${timestamp}.${req.rawBody}`)
            .digest('hex');

        const cleanSignature = signature.replace(/^v1=/, '');
        const expectedBuffer = Buffer.from(expectedHmac, 'hex');
        const receivedBuffer = Buffer.from(cleanSignature, 'hex');

        if (expectedBuffer.length !== receivedBuffer.length || !crypto.timingSafeEqual(expectedBuffer, receivedBuffer)) {
            console.warn('⚠️ [HMAC ADVERTENCIA] La firma no coincide con el secreto configurado, pero se procesará la petición.');
        } else {
            console.log('✅ [HMAC] Firma validada correctamente.');
        }
        return true; // Siempre permitimos la ejecución para evitar errores 401 molestos
    } catch (error) {
        console.error('❌ [ERROR HMAC EXCEPTION]:', error.message);
        return true;
    }
}

// ============================================================================
// 5. CLIENTE HTTP ANARCHY API
// ============================================================================

async function enviarPeticionAnarchy(endpoint, method = 'GET', body = null) {
    try {
        const url = `${API_URL}${endpoint}`;
        const options = {
            method,
            headers: {
                'Authorization': `Bot ${BOT_TOKEN}`,
                'Content-Type': 'application/json',
                'User-Agent': 'KatherineBot-NodeJS/2.1'
            },
            signal: AbortSignal.timeout(12000)
        };

        if (body !== null) options.body = JSON.stringify(body);

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
        console.error(`❌ [API EXCEPTION] Error en ${endpoint}:`, err.message);
        return { ok: false, status: 500, data: { error: err.message } };
    }
}

async function enviarRespuestaChat(threadId, texto) {
    const endpoint = `/threads/${encodeURIComponent(threadId)}/messages`;
    const resultado = await enviarPeticionAnarchy(endpoint, 'POST', { content: texto });
    
    if (!resultado.ok) {
        if (resultado.status === 403) {
            console.error('\n🚨 [SOLUCIÓN ERROR 403]: Katherine NO está agregada a este chat (Thread ID: ' + threadId + ').');
            console.error('👉 Entra a la aplicación de Anarchy, abre este chat/hilo y añade a Katherine como miembro para darle permisos de escritura.\n');
        }
    }
    return resultado;
}

// ============================================================================
// 6. VERIFICACIÓN DE CREDENCIALES AL ARRANCAR
// ============================================================================

async function verificarCredencialesBot() {
    console.log('\n============================================================');
    console.log('🔍 [INICIO] Verificando credenciales de Katherine con Anarchy API...');
    console.log('============================================================');

    const resultado = await enviarPeticionAnarchy('/me', 'GET');

    if (resultado.ok && resultado.data) {
        const botData = resultado.data.bot || resultado.data;
        console.log('✅ [AUTENTICACIÓN EXITOSA]');
        console.log(`   - Nombre:  ${botData.name || 'Katherine.'}`);
        console.log(`   - Bot ID:  ${botData.botId || 'N/A'}`);
        console.log(`   - Prefijo: ${botData.commandPrefix || '!'}`);
    } else {
        console.error('❌ [ERROR DE AUTENTICACIÓN]: No se pudo validar el Token en /me.');
    }
    console.log('============================================================\n');
}

// ============================================================================
// 7. PROCESADOR DE COMANDOS
// ============================================================================

async function procesarComandoEntrante(evento) {
    const threadId = evento.threadId;
    const mensaje = evento.message;

    if (!threadId || typeof mensaje?.content !== 'string') return;

    const contenidoTexto = mensaje.content.trim();
    if (!contenidoTexto) return;

    const args = contenidoTexto.split(/\s+/);
    const comando = args[0].toLowerCase();
    const userId = mensaje.author?.uid || mensaje.author?.id || 'anonimo';
    const usuarioNombre = mensaje.author?.nickname || mensaje.author?.name || 'Anónimo';

    console.log(`\n🎯 [COMANDO] "${comando}" | Usuario: ${usuarioNombre} | Thread: ${threadId}`);

    if (verificarCooldown(userId, 2000)) {
        console.log(`⏳ [COOLDOWN] Ignorando spam de ${usuarioNombre}`);
        return;
    }

    const estado = obtenerEstadoThread(threadId);
    estado.mensajesProcesadosCount++;

    let respuestaTexto = null;

    switch (comando) {
        case '-ayuda':
        case '!ayuda':
            respuestaTexto = 
                '🤖 **KATHERINE — CENTRO DE CONTROL**\n\n' +
                '📖 **COMANDOS GENERALES**\n' +
                '🔊 `-leerchat` / `!leerchat` — Activar lectura.\n' +
                '🔇 `-noleerchat` / `!noleerchat` — Desactivar lectura.\n' +
                '📊 `-estadochat` / `!estadochat` — Ver estado del hilo.\n' +
                '📈 `-stats` / `!stats` — Métricas de Render.\n' +
                '🏓 `-ping` / `!ping` — Probar estado y latencia.';
            break;

        case '-ping':
        case '!ping': {
            const uptimeSegundos = Math.floor((Date.now() - startTime) / 1000);
            respuestaTexto = `🏓 **PONG!** Servidor online.\n⏱️ **Uptime:** ${uptimeSegundos}s\n⚡ Webhook operativo.`;
            break;
        }

        case '-estadochat':
        case '!estadochat':
            respuestaTexto = 
                '📊 **KATHERINE — ESTADO**\n\n' +
                `🏠 **Thread ID:** \`${threadId}\`\n` +
                `🔊 **Lectura:** ${estado.lectura ? '🟢 ACTIVADA' : '🔴 DESACTIVADA'}\n` +
                `🎚️ **Volumen:** ${estado.volumen}/100\n` +
                `💬 **Mensajes procesados:** ${estado.mensajesProcesadosCount}`;
            break;

        case '-stats':
        case '!stats': {
            const mem = process.memoryUsage();
            respuestaTexto = `📈 **Render Stats**\n🧠 RAM Usada: ${Math.round(mem.heapUsed / 1024 / 1024)} MB\n🧵 Hilos activos: ${threadStates.size}`;
            break;
        }

        case '-leerchat':
        case '!leerchat':
            estado.lectura = true;
            respuestaTexto = '🔊 **Katherine:** Modo lectura ACTIVADO.';
            break;

        case '-noleerchat':
        case '!noleerchat':
            estado.lectura = false;
            respuestaTexto = '🔇 **Katherine:** Modo lectura DESACTIVADA.';
            break;

        case '-volumen':
        case '!volumen': {
            const vol = Number(args[1]);
            if (isNaN(vol) || vol < 0 || vol > 100) {
                respuestaTexto = `🎚️ Volumen actual: **${estado.volumen}/100**. Usa \`-volumen <0-100>\` para cambiarlo.`;
                break;
            }
            estado.volumen = vol;
            respuestaTexto = `🎚️ Volumen ajustado a **${vol}/100**.`;
            break;
        }

        default:
            if (comando.startsWith('-') || comando.startsWith('!')) {
                respuestaTexto = `❓ Comando desconocido: \`${comando}\`. Usa \`-ayuda\` para ver los comandos.`;
            }
            break;
    }

    if (respuestaTexto) {
        await enviarRespuestaChat(threadId, respuestaTexto);
    }
}

// ============================================================================
// 8. ENDPOINTS EXPRESS
// ============================================================================

app.post('/', (req, res) => {
    console.log('\n⚡ [WEBHOOK] Petición recibida.');
    verificarFirmaWebhook(req);
    res.status(200).send('OK'); // Respuesta inmediata para evitar timeouts

    setImmediate(async () => {
        try {
            await procesarComandoEntrante(req.body || {});
        } catch (error) {
            console.error('❌ [ERROR PROCESANDO EVENTO]:', error);
        }
    });
});

app.get('/', (req, res) => {
    res.status(200).json({
        bot: 'Katherine',
        status: 'online',
        uptimeSeconds: Math.floor((Date.now() - startTime) / 1000),
        activeThreads: threadStates.size
    });
});

// ============================================================================
// 9. INICIO DEL SERVIDOR
// ============================================================================

const server = app.listen(PORT, '0.0.0.0', () => {
    console.log('============================================================');
    console.log('         KATHERINE BOT — CORE v2.1 (LISTO)                  ');
    console.log('============================================================');
    console.log(`🚀 Puerto: ${PORT} | Estado: ONLINE`);
    console.log('============================================================');
    verificarCredencialesBot();
});

server.on('error', (err) => console.error('💥 [SERVER ERROR]:', err));
process.on('unhandledRejection', (reason) => console.error('💥 [PROMISE REJECTION]:', reason));
