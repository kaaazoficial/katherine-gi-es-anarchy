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
// GESTIÓN DE ESTADOS POR HILO
// ============================================================================
const threadStates = new Map();

function obtenerEstadoThread(threadId) {
    if (!threadStates.has(threadId)) {
        threadStates.set(threadId, {
            lectura: false,
            conectado: false,
            volumen: 30,
            reproduciendo: false,
            cancionActual: null
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
// CLIENTE HTTP OFICIAL ANARCHY API
// ============================================================================
async function anarchyAPI(endpoint, method = 'GET', body = null) {
    try {
        const url = `https://api.anarchyapps.org/bot-api/v1${endpoint}`;
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

        return { ok: response.ok, status: response.status, data };
    } catch (err) {
        console.error(`❌ [ERROR CRÍTICO FETCH]:`, err.message);
        return { ok: false, status: 500, data: { error: err.message } };
    }
}

// Envío con manejo de permisos
const enviarRespuestaChat = async (threadId, texto) => {
    let resApi = await anarchyAPI(`/threads/${encodeURIComponent(threadId)}/messages`, 'POST', { content: texto });

    if (!resApi.ok && (resApi.status === 403 || resApi.status === 404)) {
        resApi = await anarchyAPI('/messages', 'POST', {
            threadId: threadId,
            content: texto
        });
    }

    return resApi;
};

// ============================================================================
// ENDPOINT PRINCIPAL DEL WEBHOOK
// ============================================================================
app.post('/', async (req, res) => {
    // Responder 200 OK inmediatamente para cumplir con la API de Anarchy
    res.status(200).send('OK');

    const evento = req.body || {};
    const threadId = evento.threadId || evento.message?.threadId;
    const contenidoTexto = evento.message && evento.message.content;

    if (!contenidoTexto || !threadId) return;

    const texto = contenidoTexto.trim();

    // 🛑 FILTRO ABSOLUTO: Si el mensaje NO empieza con "!", se ignora al 100% (rol, texto libre, etc.)
    if (!texto.startsWith('!')) {
        return;
    }

    const args = texto.split(/\s+/);
    const comando = args[0].toLowerCase();
    const estado = obtenerEstadoThread(threadId);

    let respuesta = null;

    switch (comando) {
        case '!ayuda':
            respuesta = "🤖 **KATHERINE — CENTRO DE CONTROL**\n\n" +
                        "📖 **COMANDOS DISPONIBLES**\n" +
                        "🔊 `!leerchat` — Activar lectura.\n" +
                        "🔇 `!noleerchat` — Desactivar lectura.\n" +
                        "📊 `!estadochat` — Consultar estado.\n" +
                        "🏓 `!ping` — Probar estado y tiempo en vivo.\n\n" +
                        "🎵 **MÚSICA**\n" +
                        "▶️ `!play <canción>` — Reproducir.\n" +
                        "🔊 `!volumen <0-100>` — Ajustar volumen.";
            break;

        case '!ping': {
            const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
            respuesta = `🏓 **PONG!** Servidor en línea.\n⏱️ **Uptime:** ${uptimeSec}s\n⚡ Webhook conectado correctamente a Anarchy.`;
            break;
        }

        case !estadochat && '!estadochat': // Evaluado explícitamente abajo
        case '!estadochat':
            respuesta = `📊 **KATHERINE — ESTADO**\n🏠 **Thread ID:** ${threadId}\n🔊 **Lectura:** ${estado.lectura ? "🟢 ACTIVADA" : "🔴 DESACTIVADA"}`;
            break;

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

        default:
            // Si empieza con '!' pero no está en la lista anterior, NO hace nada (ignorado por completo)
            return;
    }

    if (respuesta) {
        console.log(`🎯 [COMANDO VÁLIDO EJECUTADO]: "${comando}" | Thread: ${threadId}`);
        await enviarRespuestaChat(threadId, respuesta);
    }
});

app.get('/', (req, res) => {
    res.status(200).json({ bot: 'Katherine', status: 'online' });
});

app.listen(PORT, '0.0.0.0', () => {
    console.log(`[KATHERINE SERVER] Servidor activo en el puerto ${PORT}`);
});
