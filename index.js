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
// ESTADO GLOBAL DEL BOT
// ============================================================================
let estadoBot = {
    lectura: false,
    conectado: false,
    volumen: 30,
    reproduciendo: false,
    cancionActual: null
};

// ============================================================================
// MIDDLEWARE EXPRESS (Captura rawBody para validación HMAC)
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

// 🔍 DIAGNÓSTICO AL ARRANCAR: Consulta la info oficial del bot en la API
async function consultarBotMe() {
    console.log("\n=======================================================");
    console.log("🔍 [CONSULTANDO /me EN LA API DE ANARCHY]");
    const res = await anarchyAPI('/me', 'GET');
    console.log("📌 DATOS DE MI BOT EN LA API:", JSON.stringify(res.data, null, 2));
    console.log("=======================================================\n");
}

// Enviar respuesta al chat público
const enviarRespuestaChat = async (threadId, texto) => {
    const payload = { content: texto };
    console.log(`[ENVIANDO MENSAJE] Thread: ${threadId}`);
    const resApi = await anarchyAPI(`/threads/${encodeURIComponent(threadId)}/messages`, 'POST', payload);
    return resApi;
};

// ============================================================================
// ENDPOINT PRINCIPAL DEL WEBHOOK
// ============================================================================
app.post('/', async (req, res) => {
    console.log("\n-------------------------------------------------------");
    console.log("⚡ [WEBHOOK] Petición recibida de Anarchy");

    const timestamp = req.headers['x-anarchy-bot-timestamp'];
    const signature = req.headers['x-anarchy-bot-signature'];

    // Validación de firma HMAC (Regla 4)
    if (SECRET && timestamp && signature) {
        try {
            const expected = crypto
                .createHmac('sha256', SECRET)
                .update(`${timestamp}.${req.rawBody || ''}`)
                .digest('hex');

            const sigClean = signature.startsWith('v1=') ? signature.slice(3) : signature;
            const sigBuffer = Buffer.from(sigClean);
            const expBuffer = Buffer.from(expected);

            if (sigBuffer.length !== expBuffer.length || !crypto.timingSafeEqual(sigBuffer, expBuffer)) {
                console.warn(`⚠️ Advertencia HMAC: La firma no coincide.`);
            } else {
                console.log("✅ Firma HMAC validada correctamente.");
            }
        } catch (err) {
            console.error("❌ Error en bloque HMAC:", err.message);
        }
    }

    // Responder 200 OK de inmediato (Regla de los 5 segundos)
    res.status(200).send('OK');

    const evento = req.body || {};
    
    if (evento.type && evento.type !== 'message') {
        return;
    }

    const threadId = evento.threadId;
    const contenidoTexto = evento.message && evento.message.content;

    if (!contenidoTexto || !threadId) return;

    const texto = contenidoTexto.trim();
    
    // Filtro estricto: Solo comandos que inicien con "!" o "-"
    if (!texto.startsWith('!') && !texto.startsWith('-')) {
        return;
    }

    const args = texto.split(/\s+/);
    const comando = args[0].toLowerCase();
    const autor = evento.message?.author?.nickname || 'Anónimo';

    console.log(`🎯 [COMANDO DETECTADO]: "${comando}" | Usuario: ${autor} | Thread: ${threadId}`);

    let respuesta = null;

    switch (comando) {
        case '-ayuda':
        case '!ayuda':
            respuesta = "🤖 **KATHERINE — CENTRO DE CONTROL**\n\n" +
                        "📖 **COMANDOS BÁSICOS**\n" +
                        "🔊 `!leerchat` / `-leerchat` — Activar lectura.\n" +
                        "🔇 `!noleerchat` / `-noleerchat` — Desactivar lectura.\n" +
                        "📊 `!estadochat` / `-estadochat` — Consultar estado.\n" +
                        "🏓 `!ping` / `-ping` — Probar estado y tiempo en vivo.\n\n" +
                        "🎵 **MÚSICA**\n" +
                        "▶️ `!play <canción>` / `-play <canción>` — Reproducir.\n" +
                        "🔊 `!volumen <0-100>` / `-volumen <0-100>` — Ajustar volumen.";
            break;

        case '-ping':
        case '!ping': {
            const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
            respuesta = `🏓 **PONG!** Servidor en línea.\n⏱️ **Uptime:** ${uptimeSec}s\n⚡ Webhook conectado correctamente a Anarchy.`;
            break;
        }

        case '-estadochat':
        case '!estadochat':
            respuesta = `📊 **KATHERINE — ESTADO**\n🏠 **Thread ID:** \`${threadId}\`\n🔊 **Lectura:** ${estadoBot.lectura ? "🟢 ACTIVADA" : "🔴 DESACTIVADA"}\n🎙️ **Llamada:** ${estadoBot.conectado ? "🟢 CONECTADO" : "⚪ DESCONECTADO"}`;
            break;

        case '-leerchat':
        case '!leerchat':
            estadoBot.lectura = true;
            respuesta = "🔊 **Katherine:** Lectura del chat **ACTIVADA**.";
            break;

        case '-noleerchat':
        case '!noleerchat':
            estadoBot.lectura = false;
            respuesta = "🔇 **Katherine:** Lectura del chat **DESACTIVADA**.";
            break;

        case '-play':
        case '!play': {
            const busqueda = args.slice(1).join(' ');
            if (!busqueda) {
                respuesta = "⚠️ Especifica la canción. Ejemplo: `!play Follow Me`";
            } else {
                estadoBot.reproduciendo = true;
                estadoBot.cancionActual = busqueda;
                respuesta = `🎵 **KATHERINE MUSIC**\n\n▶️ **Reproduciendo:** ${busqueda}\n🎚️ **Volumen:** ${estadoBot.volumen}/100`;
            }
            break;
        }

        case '-volumen':
        case '!volumen': {
            const vol = Number(args[1]);
            if (isNaN(vol) || vol < 0 || vol > 100) {
                respuesta = `🎚️ Volumen actual: **${estadoBot.volumen}/100**. Usa \`!volumen <0-100>\`.`;
            } else {
                estadoBot.volumen = vol;
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

// Ruta web simple para verificar que el servidor está vivo
app.get('/', (req, res) => {
    res.status(200).json({ 
        bot: 'Katherine', 
        status: 'online', 
        uptimeSeconds: Math.floor((Date.now() - startTime) / 1000) 
    });
});

// ============================================================================
// INICIO DEL SERVIDOR Y CONSULTA AUTOMÁTICA DE DIAGNÓSTICO
// ============================================================================
app.listen(PORT, '0.0.0.0', () => {
    console.log(`[KATHERINE SERVER] Servidor activo en el puerto ${PORT}`);
    
    // 👈 ESTO ES LO QUE HACE QUE SALGA EN LOS LOGS AUTOMÁTICAMENTE AL ARRANCAR
    consultarBotMe(); 
});
