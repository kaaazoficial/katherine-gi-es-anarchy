'use strict';

const express = require('express');
const crypto = require('crypto');

const app = express();

const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;
const PORT = Number(process.env.PORT) || 3000;
const startTime = Date.now();

if (!BOT_TOKEN) {
    throw new Error('[FATAL ERROR] Falta la variable de entorno obligatoria: BOT_TOKEN.');
}

// ============================================================================
// SEGURIDAD ANTI-CRASH
// ============================================================================
process.on('uncaughtException', (err) => {
    console.error('💥 [EXCEPCIÓN NO CAPTURADA]:', err);
});

process.on('unhandledRejection', (reason) => {
    console.error('💥 [PROMESA RECHAZADA]:', reason);
});

// ============================================================================
// GESTIÓN DE ESTADOS POR HILO (THREAD)
// ============================================================================
const threadStates = new Map();

function obtenerEstadoThread(threadId) {
    if (!threadStates.has(threadId)) {
        threadStates.set(threadId, {
            lectura: false,
            conectado: false,
            volumen: 30,
            reproduciendo: false,
            cancionActual: null,
            mensajesEnviados: 0,
            ultimoError: 'Ninguno'
        });
    }
    return threadStates.get(threadId);
}

// ============================================================================
// MIDDLEWARE EXPRESS
// ============================================================================
app.use(express.json({
    limit: '2mb',
    verify: (req, res, buf) => {
        req.rawBody = buf.toString('utf8');
    }
}));

app.use(express.urlencoded({ extended: true, limit: '2mb' }));

// ============================================================================
// CLIENTE HTTP CON LOGS DE COMUNICACIÓN DETALLADOS
// ============================================================================
async function anarchyAPI(endpoint, method = 'GET', body = null) {
    const url = `https://api.anarchyapps.org/bot-api/v1${endpoint}`;
    console.log(`\n🌐 [API COMM CHECK] ${method} -> ${endpoint}`);

    try {
        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bot ${BOT_TOKEN}`
            },
            signal: AbortSignal.timeout(12000)
        };

        if (body !== null) options.body = JSON.stringify(body);

        const response = await fetch(url, options);
        const text = await response.text();

        let data;
        try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }

        if (response.ok) {
            console.log(`✅ [API SUCCESS] Status: ${response.status} en ${endpoint}`);
        } else {
            console.warn(`⚠️ [API WARNING/ERROR] Status: ${response.status} en ${endpoint} | Respuesta:`, JSON.stringify(data));
        }

        return { ok: response.ok, status: response.status, data };
    } catch (err) {
        console.error(`❌ [API EXCEPTION] Error de red en ${endpoint}:`, err.message);
        return { ok: false, status: 500, data: { error: err.message } };
    }
}

// Verificación de credenciales al iniciar
async function verificarTokenBot() {
    console.log('\n============================================================');
    console.log('🔍 [DIAGNÓSTICO INICIAL] Verificando token con Anarchy...');
    console.log('============================================================');
    
    const resultado = await anarchyAPI('/me', 'GET');
    
    if (resultado.ok) {
        console.log('✅ [DIAGNÓSTICO EXITOSO] El bot está autenticado correctamente.');
        console.log('📦 Datos del Bot:', JSON.stringify(resultado.data, null, 2));
    } else {
        console.error('❌ [DIAGNÓSTICO FALLIDO] El token BOT_TOKEN es inválido o la API no responde.');
    }
    console.log('============================================================\n');
}

// Sistema de envío con diagnóstico de comunicación en vivo
const enviarRespuestaChat = async (threadId, texto) => {
    const estado = obtenerEstadoThread(threadId);
    console.log(`\n📤 [INTENTO DE ENVÍO] Hacia Thread ID: ${threadId}`);

    // Intento 1: Ruta específica del thread
    let resApi = await anarchyAPI(`/threads/${encodeURIComponent(threadId)}/messages`, 'POST', { content: texto });

    // Intento 2: Fallback al endpoint general si da 403 o 404
    if (!resApi.ok && (resApi.status === 403 || resApi.status === 404)) {
        console.warn(`⚠️ [FALLBACK ACTIVADO] Falló la ruta del thread (${resApi.status}). Probando /messages general...`);
        resApi = await anarchyAPI('/messages', 'POST', {
            threadId: threadId,
            content: texto
        });
    }

    if (resApi.ok) {
        estado.mensajesEnviados++;
        estado.ultimoError = 'Ninguno (Último envío exitoso)';
        console.log(`🎉 [COMUNICACIÓN EXITOSA] Mensaje entregado correctamente en thread ${threadId}`);
    } else {
        estado.ultimoError = `Status ${resApi.status}: ${JSON.stringify(resApi.data)}`;
        console.error(`🚨 [FALLO DE COMUNICACIÓN] No se pudo enviar el mensaje. Razón:`, estado.ultimoError);
    }

    return resApi;
};

// ============================================================================
// ENDPOINT PRINCIPAL DEL WEBHOOK
// ============================================================================
app.post('/', async (req, res) => {
    // Responder 200 OK inmediatamente a Anarchy
    res.status(200).send('OK');

    const evento = req.body || {};
    const threadId = evento.threadId || evento.message?.threadId;
    const contenidoTexto = evento.message && evento.message.content;

    if (!contenidoTexto || !threadId) return;

    const texto = contenidoTexto.trim();

    // 🛑 FILTRO ESTRICTO: Solo procesar comandos con "!"
    if (!texto.startsWith('!')) {
        return;
    }

    const args = texto.split(/\s+/);
    const comando = args[0].toLowerCase();
    const estado = obtenerEstadoThread(threadId);
    const autor = evento.message?.author?.nickname || evento.message?.author?.name || 'Desconocido';

    console.log(`\n🎯 [COMANDO RECIBIDO] "${comando}" | Usuario: ${autor} | Thread: ${threadId}`);

    let respuesta = null;

    switch (comando) {
        case '!ayuda':
            respuesta = "🤖 **KATHERINE — CENTRO DE CONTROL**\n\n" +
                        "📖 **COMANDOS DISPONIBLES**\n" +
                        "🔊 `!leerchat` — Activar lectura.\n" +
                        "🔇 `!noleerchat` — Desactivar lectura.\n" +
                        "📊 `!estadochat` — Consultar estado.\n" +
                        "🩺 `!diagnostico` — Ver estado de comunicación y logs.\n" +
                        "🏓 `!ping` — Probar latencia.\n\n" +
                        "🎵 **MÚSICA**\n" +
                        "▶️ `!play <canción>` — Reproducir.\n" +
                        "🔊 `!volumen <0-100>` — Ajustar volumen.";
            break;

        case '!ping': {
            const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
            respuesta = `🏓 **PONG!** Servidor en línea.\n⏱️ **Uptime:** ${uptimeSec}s\n⚡ Webhook conectado.`;
            break;
        }

        case '!estadochat':
            respuesta = `📊 **KATHERINE — ESTADO**\n🏠 **Thread ID:** \`${threadId}\`\n🔊 **Lectura:** ${estado.lectura ? "🟢" : "🔴"}\n📤 **Envíos exitosos:** ${estado.mensajesEnviados}`;
            break;

        case '!diagnostico': {
            const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
            const mem = process.memoryUsage();
            respuesta = "🩺 **KATHERINE — REPORTE DE DIAGNÓSTICO**\n\n" +
                        `⏱️ **Uptime del Bot:** ${uptimeSec}s\n` +
                        `🧠 **Memoria RAM:** ${Math.round(mem.heapUsed / 1024 / 1024)} MB\n` +
                        `🧵 **Hilos activos en caché:** ${threadStates.size}\n` +
                        `📤 **Mensajes enviados con éxito:** ${estado.mensajesEnviados}\n` +
                        `⚠️ **Último error registrado:** \`${estado.ultimoError}\`\n\n` +
                        `💡 *Revisa los logs de Render para ver la respuesta detallada de la API de Anarchy.*`;
            break;
        }

        case '!leerchat':
            estado.lectura = true;
            respuesta = "🔊 **Katherine:** Lectura del chat **ACTIVADA**.";
            break;

        case '!noleerchat':
            estado.lectura = false;
            respuesta = "🔇 **Katherine:** Lectura del chat **DESACTIVADA**.";
            break;

        case '!play': {
            const busqueda = args.slice(1).join(' ');
            if (!busqueda) {
                respuesta = "⚠️ Especifica la canción. Ejemplo: `!play Follow Me`";
            } else {
                estado.reproduciendo = true;
                estado.cancionActual = busqueda;
                respuesta = `🎵 **KATHERINE MUSIC**\n\n▶️ **Reproduciendo:** ${busqueda}\n🎚️ **Volumen:** ${estado.volumen}/100`;
            }
            break;
        }

        case '!volumen': {
            const vol = Number(args[1]);
            if (isNaN(vol) || vol < 0 || vol > 100) {
                respuesta = `🎚️ Volumen actual: **${estado.volumen}/100**. Usa \`!volumen <0-100>\`.`;
            } else {
                estado.volumen = vol;
                respuesta = `🎚️ Volumen ajustado a **${vol}/100**.`;
            }
            break;
        }

        default:
            return;
    }

    if (respuesta) {
        await enviarRespuestaChat(threadId, respuesta);
    }
});

app.get('/', (req, res) => {
    res.status(200).json({ 
        bot: 'Katherine', 
        status: 'online', 
        uptimeSeconds: Math.floor((Date.now() - startTime) / 1000),
        activeThreads: threadStates.size 
    });
});

const server = app.listen(PORT, '0.0.0.0', () => {
    console.log(`[KATHERINE SERVER] Servidor activo en el puerto ${PORT}`);
    verificarTokenBot();
});

server.on('error', (err) => console.error('💥 [SERVER ERROR]:', err));
