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
// GESTIÓN DE ESTADOS POR HILO (THREAD) CON REGISTRO DE EVENTOS
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
            mensajesRecibidos: 0,
            mensajesEnviados: 0,
            ultimoError: 'Ninguno',
            ultimoComando: 'Ninguno'
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
// CLIENTE HTTP CON LOGS Y DIAGNÓSTICO PROFUNDO DE COMUNICACIÓN
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

// 🩺 DIAGNÓSTICO 1: Verificación de Autenticación y Estado Global con la API
async function ejecutarDiagnosticoGlobalAPI() {
    console.log('\n============================================================');
    console.log('🩺 [DIAGNÓSTICO 1 - API GLOBAL] Consultando estado del Bot (/me)...');
    console.log('============================================================');
    
    const resultado = await anarchyAPI('/me', 'GET');
    
    if (resultado.ok) {
        console.log('✅ [API GLOBAL OK] Conexión y Token validados con éxito.');
        console.log('📦 Info del Bot en Anarchy:', JSON.stringify(resultado.data, null, 2));
    } else {
        console.error('❌ [API GLOBAL ERROR] El token BOT_TOKEN no es válido o la API rechazó la credencial.');
    }
    console.log('============================================================\n');
}

// Sistema de envío con diagnóstico de comunicación en vivo y múltiples fallbacks
const enviarRespuestaChat = async (threadId, texto) => {
    const estado = obtenerEstadoThread(threadId);
    console.log(`\n📤 [INTENTO DE ENVÍO] Hacia Thread ID: ${threadId}`);

    // Intento 1: Ruta específica del thread
    let resApi = await anarchyAPI(`/threads/${encodeURIComponent(threadId)}/messages`, 'POST', { content: texto });

    // Intento 2: Fallback al endpoint general si da error de ruta/permiso (403 o 404)
    if (!resApi.ok && (resApi.status === 403 || resApi.status === 404)) {
        console.warn(`⚠️ [FALLBACK ACTIVADO] Falló la ruta del thread (${resApi.status}). Probando /messages general...`);
        resApi = await anarchyAPI('/messages', 'POST', {
            threadId: threadId,
            content: texto
        });
    }

    if (resApi.ok) {
        estado.mensajesEnviados++;
        estado.ultimoError = 'Ninguno';
        console.log(`🎉 [COMUNICACIÓN EXITOSA] Mensaje entregado correctamente en thread ${threadId}`);
    } else {
        estado.ultimoError = `Status ${resApi.status}: ${JSON.stringify(resApi.data)}`;
        console.error(`🚨 [FALLO DE COMUNICACIÓN]:`, estado.ultimoError);
    }

    return resApi;
};

// ============================================================================
// ENDPOINT PRINCIPAL DEL WEBHOOK
// ============================================================================
app.post('/', async (req, res) => {
    // 🛑 LOG UNIVERSAL DE ENTRADA: Registra CUALQUIER evento que llegue a Render
    console.log("\n========================================================");
    console.log("📥 [WEBHOOK RECIBIDO] Petición POST entrante en '/'");
    console.log("📦 BODY RECIBIDO:", JSON.stringify(req.body, null, 2));
    console.log("========================================================");

    // Responder 200 OK inmediatamente para cumplir con la API de Anarchy
    res.status(200).send('OK');

    const evento = req.body || {};
    const threadId = evento.threadId || evento.message?.threadId;
    const contenidoTexto = evento.message && evento.message.content;

    if (!contenidoTexto || !threadId) {
        console.log("⚠️ [AVISO] La petición llegó pero le falta 'threadId' o 'message.content'.");
        return;
    }

    const estado = obtenerEstadoThread(threadId);
    estado.mensajesRecibidos++;

    const texto = contenidoTexto.trim();

    // 🛑 FILTRO ESTRICTO: Solo procesar comandos que inicien con "!"
    if (!texto.startsWith('!')) {
        return;
    }

    const args = texto.split(/\s+/);
    const comando = args[0].toLowerCase();
    estado.ultimoComando = comando;
    const autor = evento.message?.author?.nickname || evento.message?.author?.name || 'Desconocido';

    console.log(`\n🎯 [COMANDO VÁLIDO DETECTADO] "${comando}" | Usuario: ${autor} | Thread: ${threadId}`);

    let respuesta = null;

    switch (comando) {
        case '!ayuda':
            respuesta = "🤖 **KATHERINE — CENTRO DE CONTROL**\n\n" +
                        "📖 **COMANDOS DISPONIBLES**\n" +
                        "🔊 `!leerchat` — Activar lectura.\n" +
                        "🔇 `!noleerchat` — Desactivar lectura.\n" +
                        "📊 `!estadochat` — Consultar estado del hilo.\n" +
                        "🩺 `!diagnostico` — Ejecutar ambos diagnósticos.\n" +
                        "🏓 `!ping` — Probar latencia.";
            break;

        case '!ping': {
            const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
            respuesta = `🏓 **PONG!** Servidor en línea.\n⏱️ **Uptime:** ${uptimeSec}s\n⚡ Webhook conectado.`;
            break;
        }

        case '!estadochat':
            respuesta = `📊 **KATHERINE — ESTADO DEL HILO**\n🏠 **Thread ID:** \`${threadId}\`\n🔊 **Lectura:** ${estado.lectura ? "🟢 ACTIVADA" : "🔴 DESACTIVADA"}\n📥 **Recibidos:** ${estado.mensajesRecibidos}\n📤 **Envíos exitosos:** ${estado.mensajesEnviados}`;
            break;

        // 🩺 DIAGNÓSTICO 2: Reporte detallado en tiempo real dentro del chat
        case '!diagnostico': {
            const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
            const mem = process.memoryUsage();
            
            // Disparamos también el Diagnóstico 1 en segundo plano hacia la consola de Render
            ejecutarDiagnosticoGlobalAPI();

            respuesta = "🩺 **KATHERINE — REPORTE DE DIAGNÓSTICO INTEGRAL**\n\n" +
                        "📊 **[DIAGNÓSTICO 2 - ESTADO LOCAL / HILO]**\n" +
                        `⏱️ **Uptime del Bot:** ${uptimeSec}s\n` +
                        `🧠 **Memoria RAM Usada:** ${Math.round(mem.heapUsed / 1024 / 1024)} MB\n` +
                        `🧵 **Hilos activos en caché:** ${threadStates.size}\n` +
                        `💬 **Mensajes capturados en este hilo:** ${estado.mensajesRecibidos}\n` +
                        `📤 **Envíos exitosos en este hilo:** ${estado.mensajesEnviados}\n` +
                        `🎯 **Último comando registrado:** \`${estado.ultimoComando}\`\n` +
                        `⚠️ **Último error de red:** \`${estado.ultimoError}\`\n\n` +
                        `🔍 **[DIAGNÓSTICO 1 - API]**\n` +
                        `Se acaba de disparar una consulta de verificación (\`/me\`) a los servidores de Anarchy. Revisa los logs de Render para ver la respuesta completa.`;
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
    ejecutarDiagnosticoGlobalAPI();
});

server.on('error', (err) => console.error('💥 [SERVER ERROR]:', err));
