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
// GESTIÓN DE ESTADOS INDEPENDIENTES POR HILO (THREAD)
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
        console.log(`[API REQUEST] ${method} -> ${url}`);

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

        console.log(`[API RESPONSE] Status: ${response.status} | Body:`, JSON.stringify(data));
        return { ok: response.ok, status: response.status, data };
    } catch (err) {
        console.error(`❌ [ERROR CRÍTICO FETCH]:`, err.message);
        return { ok: false, status: 500, data: { error: err.message } };
    }
}

// Sistema de envío con fallback automático
const enviarRespuestaChat = async (threadId, texto) => {
    console.log(`[ENVIANDO MENSAJE] Thread: ${threadId}`);
    
    // Intento 1: Ruta específica del thread
    let resApi = await anarchyAPI(`/threads/${encodeURIComponent(threadId)}/messages`, 'POST', { content: texto });

    // Intento 2: Ruta alternativa general si da error de ruta/permiso
    if (!resApi.ok && (resApi.status === 403 || resApi.status === 404)) {
        console.warn(`⚠️ [REINTENTO] Falló el envío en thread (${resApi.status}). Probando endpoint alternativo /messages...`);
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
    console.log("\n-------------------------------------------------------");
    console.log("⚡ [WEBHOOK] Petición recibida de Anarchy");

    // Registro detallado de todo el paquete recibido para depuración
    console.log("📦 PAYLOAD RECIBIDO:", JSON.stringify(req.body, null, 2));

    const timestamp = req.headers['x-anarchy-bot-timestamp'];
    const signature = req.headers['x-anarchy-bot-signature'];

    // Validación HMAC permisiva
    if (SECRET && timestamp && signature) {
        try {
            const expected = crypto
                .createHmac('sha256', SECRET)
                .update(`${timestamp}.${req.rawBody || ''}`)
                .digest('hex');

            const sigClean = signature.startsWith('v1=') ? signature.slice(3) : signature;
            const sigBuffer = Buffer.from(sigClean, 'hex');
            const expBuffer = Buffer.from(expected, 'hex');

            if (sigBuffer.length === expBuffer.length && crypto.timingSafeEqual(sigBuffer, expBuffer)) {
                console.log("✅ Firma HMAC validada correctamente.");
            } else {
                console.warn(`⚠️ Advertencia HMAC: La firma no coincide.`);
            }
        } catch (err) {
            console.error("❌ Error en bloque HMAC:", err.message);
        }
    }

    // Responder 200 OK inmediatamente
    res.status(200).send('OK');

    const evento = req.body || {};
    const threadId = evento.threadId || evento.message?.threadId;
    const contenidoTexto = evento.message && evento.message.content;

    if (!contenidoTexto || !threadId) return;

    const texto = contenidoTexto.trim();
    const args = texto.split(/\s+/);
    const comando = args[0].toLowerCase();
    const estado = obtenerEstadoThread(threadId);

    console.log(`🎯 [COMANDO DETECTADO]: "${comando}" | Usuario: ${evento.message?.author?.nickname || 'Anónimo'} | Thread: ${threadId}`);

    let respuesta = null;

    switch (comando) {
        case '-ayuda':
        case '!ayuda':
            respuesta = "🤖 **KATHERINE — CENTRO DE CONTROL**\n\n" +
                        "📖 **COMANDOS BÁSICOS**\n" +
                        "🔊 `-leerchat` / `!leerchat` — Activar lectura.\n" +
                        "🔇 `-noleerchat` / `!noleerchat` — Desactivar lectura.\n" +
                        "📊 `-estadochat` / `!estadochat` — Consultar estado.\n" +
                        "🏓 `-ping` / `!ping` — Probar estado y tiempo en vivo.\n" +
                        "🎙️ `-entrar-sala` — Entrar a la llamada.\n" +
                        "🚪 `-salir` — Desconectar de la llamada.\n\n" +
                        "🎵 **MÚSICA**\n" +
                        "▶️ `-play <canción>` — Reproducir.\n" +
                        "⏸️ `-pausa` — Pausar.\n" +
                        "🔊 `-volumen <0-100>` — Ajustar volumen.";
            break;

        case '-ping':
        case '!ping': {
            const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
            respuesta = `🏓 **PONG!** Servidor en línea.\n⏱️ **Uptime:** ${uptimeSec}s\n⚡ Webhook conectado correctamente a Anarchy.`;
            break;
        }

        case '-estadochat':
        case '!estadochat':
            respuesta = `📊 **KATHERINE — ESTADO**\n🏠 **Thread ID:** ${threadId}\n🔊 **Lectura:** ${estado.lectura ? "🟢 ACTIVADA" : "🔴 DESACTIVADA"}\n🎙️ **Llamada:** ${estado.conectado ? "🟢 CONECTADO" : "⚪ DESCONECTADO"}`;
            break;

        case '-leerchat':
        case '!leerchat':
            estado.lectura = true;
            respuesta = "🔊 **Katherine:** Lectura del chat **ACTIVADA**.";
            break;

        case '-noleerchat':
        case '!noleerchat':
            estado.lectura = false;
            respuesta = "🔇 **Katherine:** Lectura del chat **DESACTIVADA**.";
            break;

        case '-play':
        case '!play': {
            const busqueda = args.slice(1).join(' ');
            if (!busqueda) {
                respuesta = "⚠️ Especifica la canción. Ejemplo: `-play Follow Me`";
            } else {
                estado.reproduciendo = true;
                estado.cancionActual = busqueda;
                respuesta = `🎵 **KATHERINE MUSIC**\n\n▶️ **Reproduciendo:** ${busqueda}\n🎚️ **Volumen:** ${estado.volumen}/100`;
            }
            break;
        }
    }

    if (respuesta) {
        await enviarRespuestaChat(threadId, respuesta);
    }
});

app.get('/', (req, res) => {
    res.status(200).json({ bot: 'Katherine', status: 'online' });
});

// ============================================================================
// INICIO DEL SERVIDOR
// ============================================================================
app.listen(PORT, '0.0.0.0', () => {
    console.log(`[KATHERINE SERVER] Servidor activo en el puerto ${PORT}`);
});
