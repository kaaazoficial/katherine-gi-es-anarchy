
'use strict';

const express = require('express');
const crypto = require('crypto');

// ============================================================================
// CONFIGURACIÓN GENERAL
// ============================================================================

const app = express();

const BOT_TOKEN = process.env.BOT_TOKEN?.trim();
const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const PORT = Number(process.env.PORT) || 3000;

const API_BASE = 'https://api.anarchyapps.org/bot-api/v1';
const BOT_NAME = 'Katherine';

const REQUEST_TIMEOUT = 12000;
const MAX_BODY_SIZE = '2mb';
const MAX_THREAD_STATES = 5000;

const VERIFY_WEBHOOK_SIGNATURE =
    process.env.VERIFY_WEBHOOK_SIGNATURE === 'true';

const SIGNATURE_HEADER =
    (process.env.WEBHOOK_SIGNATURE_HEADER || 'x-webhook-signature')
        .toLowerCase();

const startTime = Date.now();

// ============================================================================
// VALIDACIÓN DE CONFIGURACIÓN
// ============================================================================

if (!BOT_TOKEN) {
    throw new Error(
        '[FATAL] Falta la variable de entorno obligatoria: BOT_TOKEN.'
    );
}

if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65535) {
    throw new Error('[FATAL] El puerto configurado no es válido.');
}

if (VERIFY_WEBHOOK_SIGNATURE && !SECRET) {
    throw new Error(
        '[FATAL] La verificación de firma está activada, pero falta WEBHOOK_SECRET.'
    );
}

// ============================================================================
// MANEJO DE ERRORES CRÍTICOS
// ============================================================================

process.on('uncaughtExceptionMonitor', (err) => {
    console.error('💥 [UNCAUGHT EXCEPTION]:', err);
});

process.on('unhandledRejection', (reason) => {
    console.error('💥 [UNHANDLED REJECTION]:', reason);
    process.exit(1);
});

// ============================================================================
// ESTADOS INDEPENDIENTES POR THREAD
// ============================================================================

const threadStates = new Map();

function obtenerEstadoThread(threadId) {
    const id = String(threadId);

    if (!threadStates.has(id)) {

        // Evitar crecimiento ilimitado de memoria.
        if (threadStates.size >= MAX_THREAD_STATES) {
            let oldestId = null;
            let oldestTime = Infinity;

            for (const [key, value] of threadStates) {
                if (value.lastActivity < oldestTime) {
                    oldestTime = value.lastActivity;
                    oldestId = key;
                }
            }

            if (oldestId !== null) {
                threadStates.delete(oldestId);
            }
        }

        threadStates.set(id, {
            lectura: false,
            conectado: false,
            volumen: 30,
            reproduciendo: false,
            cancionActual: null,
            lastActivity: Date.now()
        });
    }

    const estado = threadStates.get(id);
    estado.lastActivity = Date.now();

    return estado;
}

// ============================================================================
// MIDDLEWARE EXPRESS
// ============================================================================

app.disable('x-powered-by');

app.use(express.json({
    limit: MAX_BODY_SIZE,

    verify: (req, res, buffer) => {
        req.rawBody = buffer;
    }
}));

app.use(express.urlencoded({
    extended: false,
    limit: MAX_BODY_SIZE
}));

// ============================================================================
// SEGURIDAD: VERIFICACIÓN HMAC OPCIONAL
// ============================================================================

function verificarFirmaWebhook(req) {

    if (!VERIFY_WEBHOOK_SIGNATURE) {
        return true;
    }

    if (!req.rawBody || !SECRET) {
        return false;
    }

    const firmaRecibida = req.headers[SIGNATURE_HEADER];

    if (typeof firmaRecibida !== 'string') {
        return false;
    }

    const firmaLimpia = firmaRecibida
        .replace(/^sha256=/i, '')
        .trim();

    // Se espera una firma hexadecimal SHA-256 de 64 caracteres.
    if (!/^[a-f0-9]{64}$/i.test(firmaLimpia)) {
        return false;
    }

    const firmaEsperada = crypto
        .createHmac('sha256', SECRET)
        .update(req.rawBody)
        .digest();

    const firmaBuffer = Buffer.from(firmaLimpia, 'hex');

    if (firmaEsperada.length !== firmaBuffer.length) {
        return false;
    }

    return crypto.timingSafeEqual(
        firmaEsperada,
        firmaBuffer
    );
}

// ============================================================================
// CLIENTE HTTP ANARCHY API
// ============================================================================

async function anarchyAPI(endpoint, method = 'GET', body = null) {

    const url = `${API_BASE}${endpoint}`;

    const options = {
        method,

        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Authorization': `Bot ${BOT_TOKEN}`
        },

        signal: AbortSignal.timeout(REQUEST_TIMEOUT)
    };

    if (body !== null) {
        options.body = JSON.stringify(body);
    }

    try {

        const response = await fetch(url, options);
        const responseText = await response.text();

        let data = {};

        if (responseText) {
            try {
                data = JSON.parse(responseText);
            } catch {
                data = {
                    raw: responseText.slice(0, 2000)
                };
            }
        }

        if (!response.ok) {
            console.error(
                `❌ [ANARCHY API] HTTP ${response.status}:`,
                data
            );
        }

        return {
            ok: response.ok,
            status: response.status,
            data
        };

    } catch (err) {

        const timeout = err.name === 'TimeoutError' ||
                        err.name === 'AbortError';

        console.error(
            timeout
                ? '⏱️ [ANARCHY API] Tiempo de espera agotado.'
                : '❌ [ANARCHY API] Error de conexión:',
            err.message
        );

        return {
            ok: false,
            status: 0,
            data: {
                error: err.message
            }
        };
    }
}

// ============================================================================
// SISTEMA DE ENVÍO DE MENSAJES
// ============================================================================

async function enviarRespuestaChat(threadId, texto) {

    const id = String(threadId);

    // Primer método: endpoint específico del hilo.
    let resultado = await anarchyAPI(
        `/threads/${encodeURIComponent(id)}/messages`,
        'POST',
        {
            content: texto
        }
    );

    // Método alternativo para casos de permisos o endpoint.
    if (
        !resultado.ok &&
        (resultado.status === 403 || resultado.status === 404)
    ) {

        resultado = await anarchyAPI(
            '/messages',
            'POST',
            {
                threadId: id,
                content: texto
            }
        );
    }

    return resultado;
}

// ============================================================================
// SISTEMA DE COMANDOS
// ============================================================================

const COMANDOS = {

    '!ayuda': () => {

        return (
            '🤖 **KATHERINE — CENTRO DE CONTROL**\n\n' +

            '📖 **COMANDOS GENERALES**\n' +
            '🔊 `!leerchat` — Activar lectura.\n' +
            '🔇 `!noleerchat` — Desactivar lectura.\n' +
            '📊 `!estadochat` — Consultar estado.\n' +
            '🏓 `!ping` — Comprobar conexión.\n' +
            '❔ `!ayuda` — Mostrar comandos.\n\n' +

            '🎵 **KATHERINE MUSIC**\n' +
            '▶️ `!play <canción>` — Seleccionar canción.\n' +
            '🔊 `!volumen <0-100>` — Ajustar volumen.'
        );
    },

    '!ping': () => {

        const uptimeSec = Math.floor(
            (Date.now() - startTime) / 1000
        );

        return (
            '🏓 **PONG!**\n\n' +
            '🟢 **Servidor:** En línea.\n' +
            `⏱️ **Uptime:** ${uptimeSec}s\n` +
            '⚡ **Webhook:** Recibido correctamente.'
        );
    },

    '!estadochat': (estado, threadId) => {

        return (
            '📊 **KATHERINE — ESTADO**\n\n' +
            `🏠 **Thread ID:** ${threadId}\n` +
            `🔊 **Lectura:** ${estado.lectura ? '🟢 ACTIVADA' : '🔴 DESACTIVADA'}\n` +
            `🎵 **Música:** ${estado.reproduciendo ? '🟢 ACTIVA' : '🔴 INACTIVA'}\n` +
            `🎚️ **Volumen:** ${estado.volumen}/100\n` +
            `🎼 **Canción:** ${estado.cancionActual || 'Ninguna'}`
        );
    },

    '!leerchat': (estado) => {

        estado.lectura = true;

        return '🔊 **Katherine:** Lectura del chat **ACTIVADA**.';
    },

    '!noleerchat': (estado) => {

        estado.lectura = false;

        return '🔇 **Katherine:** Lectura del chat **DESACTIVADA**.';
    },

    '!play': (estado, threadId, args) => {

        const busqueda = args.slice(1).join(' ').trim();

        if (!busqueda) {
            return '⚠️ Especifica una canción. Ejemplo: `!play Follow Me`';
        }

        estado.reproduciendo = true;
        estado.cancionActual = busqueda;

        return (
            '🎵 **KATHERINE MUSIC**\n\n' +
            `▶️ **Seleccionada:** ${busqueda}\n` +
            `🎚️ **Volumen:** ${estado.volumen}/100`
        );
    },

    '!volumen': (estado, threadId, args) => {

        const valor = args[1];

        if (
            valor === undefined ||
            !/^\d{1,3}$/.test(valor)
        ) {
            return (
                '⚠️ Introduce un volumen válido entre 0 y 100.\n' +
                'Ejemplo: `!volumen 50`'
            );
        }

        const volumen = Number(valor);

        if (volumen < 0 || volumen > 100) {
            return '⚠️ El volumen debe estar entre 0 y 100.';
        }

        estado.volumen = volumen;

        return (
            '🎚️ **KATHERINE MUSIC**\n\n' +
            `🔊 **Volumen establecido:** ${volumen}/100`
        );
    }
};

// ============================================================================
// PROCESAMIENTO DE EVENTOS
// ============================================================================

async function procesarEvento(evento) {

    const mensaje = evento.message;

    if (!mensaje || typeof mensaje.content !== 'string') {
        return;
    }

  
const threadId = evento.threadId || mensaje.threadId;

// DIAGNÓSTICO DE IDENTIFICADORES ANARCHY
console.log(
    '📩 [DATOS DEL CHAT]:',
    JSON.stringify({
        threadId: evento.threadId ?? null,
        messageThreadId: mensaje.threadId ?? null,
        messageId: mensaje.id ?? null,
        chatId: evento.chatId ?? null,
        conversationId: evento.conversationId ?? null
    }, null, 2)
);


    if (
        threadId === undefined ||
        threadId === null ||
        String(threadId).trim() === ''
    ) {
        return;
    }

    const texto = mensaje.content.trim();

    // Ignorar mensajes vacíos y mensajes que no sean comandos.
    if (!texto || !texto.startsWith('!')) {
        return;
    }

    const args = texto.split(/\s+/);
    const comando = args[0].toLowerCase();

    const ejecutar = COMANDOS[comando];

    // Ignorar comandos desconocidos.
    if (!ejecutar) {
        return;
    }

    const id = String(threadId);
    const estado = obtenerEstadoThread(id);

    try {

        const respuesta = ejecutar(
            estado,
            id,
            args
        );

        if (!respuesta) {
            return;
        }

        console.log(
            `🎯 [COMANDO] ${comando} | Thread: ${id}`
        );

        const resultado = await enviarRespuestaChat(
            id,
            respuesta
        );

        if (!resultado.ok) {
            console.error(
                `❌ [ENVÍO FALLIDO] ${comando} | Thread: ${id} | HTTP: ${resultado.status}`
            );
        }

    } catch (err) {

        console.error(
            `💥 [ERROR COMANDO ${comando}]:`,
            err
        );
    }
}

// ============================================================================
// ENDPOINT PRINCIPAL DEL WEBHOOK
// ============================================================================

app.post('/', (req, res) => {

    // Verificar firma antes de aceptar el evento,
    // únicamente cuando la protección esté configurada.
    if (!verificarFirmaWebhook(req)) {

        console.warn(
            '🛡️ [SEGURIDAD] Webhook rechazado: firma inválida.'
        );

        return res.status(401).send('Unauthorized');
    }

    // Confirmar recepción inmediatamente a Anarchy.
    res.status(200).send('OK');

    const evento = req.body || {};

    // Procesamiento independiente de la respuesta HTTP.
    void procesarEvento(evento).catch((err) => {

        console.error(
            '💥 [ERROR PROCESANDO WEBHOOK]:',
            err
        );
    });
});

// ============================================================================
// ENDPOINT DE ESTADO DEL SERVIDOR
// ============================================================================

app.get('/', (req, res) => {

    const uptimeSec = Math.floor(
        (Date.now() - startTime) / 1000
    );

    res.status(200).json({

        bot: BOT_NAME,
        status: 'online',
        uptime: uptimeSec,
        activeThreads: threadStates.size

    });
});

// ============================================================================
// MANEJO DE RUTAS INEXISTENTES
// ============================================================================

app.use((req, res) => {

    res.status(404).json({
        error: 'Endpoint no encontrado.',
        bot: BOT_NAME
    });
});

// ============================================================================
// MANEJO DE ERRORES EXPRESS
// ============================================================================

app.use((err, req, res, next) => {

    console.error(
        '❌ [EXPRESS ERROR]:',
        err.message
    );

    if (res.headersSent) {
        return next(err);
    }

    res.status(err.status || 500).json({
        error: 'Error interno del servidor.'
    });
});

// ============================================================================
// INICIO DEL SERVIDOR
// ============================================================================

const server = app.listen(PORT, '0.0.0.0', () => {

    console.log('');
    console.log('========================================');
    console.log('       KATHERINE BOT — ONLINE');
    console.log('========================================');
    console.log(`🤖 Bot: ${BOT_NAME}`);
    console.log(`🌐 Puerto: ${PORT}`);
    console.log('🟢 Estado: OPERATIVO');
    console.log(`🛡️ Firma HMAC: ${VERIFY_WEBHOOK_SIGNATURE ? 'ACTIVADA' : 'DESACTIVADA'}`);
    console.log('⚡ API: ANARCHY');
    console.log('========================================');
    console.log('');
});

// ============================================================================
// CIERRE CONTROLADO
// ============================================================================

function cerrarServidor(signal) {

    console.log(`\n⚠️ [SHUTDOWN] Señal recibida: ${signal}`);

    server.close(() => {

        console.log('🛑 [SHUTDOWN] Servidor cerrado correctamente.');

        process.exit(0);
    });

    setTimeout(() => {

        console.error(
            '⚠️ [SHUTDOWN] Tiempo agotado. Cerrando proceso.'
        );

        process.exit(1);

    }, 10000).unref();
}

process.on('SIGTERM', () => cerrarServidor('SIGTERM'));
process.on('SIGINT', () => cerrarServidor('SIGINT'));
