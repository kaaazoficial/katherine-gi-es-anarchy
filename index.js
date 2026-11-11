
'use strict';

const express = require('express');
const crypto = require('crypto');

const app = express();

// ============================================================================
// CONFIGURACIÓN
// ============================================================================

const API_BASE = 'https://api.anarchyapps.org/bot-api/v1';

const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;

const PORT = Number(process.env.PORT) || 3000;
const startTime = Date.now();

if (!BOT_TOKEN) {
    throw new Error('[FATAL ERROR] Falta BOT_TOKEN.');
}

if (!SECRET) {
    throw new Error('[FATAL ERROR] Falta WEBHOOK_SECRET.');
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
// MIDDLEWARE EXPRESS
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
// CLIENTE HTTP ANARCHY API
// ============================================================================

async function anarchyAPI(endpoint, method = 'GET', body = null) {

    try {
        const url = `${API_BASE}${endpoint}`;

        console.log(`[API REQUEST] ${method} -> ${url}`);

        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bot ${BOT_TOKEN}`,
                'Accept': 'application/json'
            },
            signal: AbortSignal.timeout(12000)
        };

        if (body !== null) {
            options.body = JSON.stringify(body);
        }

        const response = await fetch(url, options);

        const responseText = await response.text();

        let data = {};

        try {
            data = responseText
                ? JSON.parse(responseText)
                : {};
        } catch {
            data = {
                raw: responseText
            };
        }

        console.log(
            `[API RESPONSE] Status: ${response.status} | Body:`,
            JSON.stringify(data)
        );

        if (response.status === 429) {
            console.warn(
                '⚠️ Límite de solicitudes alcanzado. Retry-After:',
                response.headers.get('retry-after')
            );
        }

        return {
            ok: response.ok,
            status: response.status,
            data
        };

    } catch (err) {

        console.error(
            '❌ [ERROR CRÍTICO FETCH]:',
            err.message
        );

        return {
            ok: false,
            status: 500,
            data: {
                error: err.message
            }
        };
    }
}

// ============================================================================
// DIAGNÓSTICO DEL BOT
// ============================================================================

async function consultarBotMe() {

    console.log('\n=======================================================');
    console.log('🔍 [CONSULTANDO /me EN ANARCHY]');
    
    const res = await anarchyAPI('/me', 'GET');

    if (!res.ok) {
        console.error(
            '❌ No se pudo consultar el bot:',
            res.status,
            res.data
        );
    } else {
        console.log(
            '✅ DATOS DEL BOT:',
            JSON.stringify(res.data, null, 2)
        );
    }

    console.log('=======================================================\n');
}

// ============================================================================
// SISTEMA DE COLA PARA RESPETAR LOS LÍMITES DE ANARCHY
// ============================================================================

let colaEnvios = Promise.resolve();

let ultimoEnvioGlobal = 0;

const ultimoEnvioPorChat = new Map();

function dormir(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function agregarACola(threadId, tarea) {

    const siguiente = colaEnvios.then(async () => {

        const ahora = Date.now();

        const esperaGlobal = Math.max(
            0,
            1000 - (ahora - ultimoEnvioGlobal)
        );

        const ultimaVezChat =
            ultimoEnvioPorChat.get(threadId) || 0;

        const esperaChat = Math.max(
            0,
            2000 - (ahora - ultimaVezChat)
        );

        const espera = Math.max(
            esperaGlobal,
            esperaChat
        );

        if (espera > 0) {
            await dormir(espera);
        }

        const inicio = Date.now();

        ultimoEnvioGlobal = inicio;
        ultimoEnvioPorChat.set(threadId, inicio);

        return await tarea();
    });

    colaEnvios = siguiente.catch(err => {
        console.error('❌ Error en cola de mensajes:', err);
    });

    return siguiente;
}

// ============================================================================
// ENVIAR RESPUESTAS AL CHAT
// ============================================================================

const enviarRespuestaChat = async (
    threadId,
    texto,
    replyMessageId = null
) => {

    if (!threadId || !texto) {

        console.error(
            '❌ Faltan datos para enviar el mensaje.'
        );

        return {
            ok: false,
            status: 400
        };
    }

    return agregarACola(String(threadId), async () => {

        const payload = {
            content: String(texto).slice(0, 2000)
        };

        if (replyMessageId) {
            payload.replyMessageId = String(replyMessageId);
        }

        console.log(
            `[ENVIANDO MENSAJE] Thread: ${threadId}`
        );

        console.log(
            '[CONTENIDO]:',
            payload.content
        );

        const resApi = await anarchyAPI(
            `/threads/${encodeURIComponent(threadId)}/messages`,
            'POST',
            payload
        );

        if (resApi.ok) {

            console.log(
                '✅ [MENSAJE PUBLICADO CORRECTAMENTE]'
            );

        } else {

            console.error(
                '❌ [ERROR PUBLICANDO MENSAJE]',
                resApi.status,
                JSON.stringify(resApi.data)
            );

        }

        return resApi;
    });
};

// ============================================================================
// CONTROL DE MENSAJES YA PROCESADOS
// ============================================================================

const mensajesProcesados = new Set();

function mensajeYaProcesado(messageId) {

    if (!messageId) {
        return false;
    }

    const id = String(messageId);

    if (mensajesProcesados.has(id)) {
        return true;
    }

    mensajesProcesados.add(id);

    // Evita que el conjunto crezca indefinidamente.
    if (mensajesProcesados.size > 5000) {

        const primerId = mensajesProcesados.values().next().value;

        mensajesProcesados.delete(primerId);
    }

    return false;
}

// ============================================================================
// PROCESADOR DE EVENTOS Y COMANDOS
// ============================================================================

async function procesarEvento(evento) {

    console.log('\n-------------------------------------------------------');
    console.log('⚡ [PROCESANDO EVENTO ANARCHY]');

    console.log(
        '[TIPO DE EVENTO]:',
        evento.type
    );

    // Solo procesamos mensajes.
    if (evento.type !== 'message') {

        console.log(
            'ℹ️ Evento ignorado:',
            evento.type
        );

        return;
    }

    const threadId = String(evento.threadId || '');

    const mensaje = evento.message || {};

    const contenidoTexto =
        typeof mensaje.content === 'string'
            ? mensaje.content
            : '';

    const messageId = mensaje.messageId || null;

    const autor =
        mensaje.author?.nickname || 'Anónimo';

    if (!threadId || !contenidoTexto.trim()) {

        console.warn(
            '⚠️ Mensaje sin threadId o contenido.'
        );

        return;
    }

    if (mensajeYaProcesado(messageId)) {

        console.warn(
            '⚠️ Mensaje duplicado ignorado:',
            messageId
        );

        return;
    }

    const texto = contenidoTexto.trim();

    // Solo aceptar comandos que comiencen con ! o -.
    if (
        !texto.startsWith('!') &&
        !texto.startsWith('-')
    ) {

        return;
    }

    const args = texto.split(/\s+/);

    const comando = args[0].toLowerCase();

    console.log(
        `🎯 [COMANDO DETECTADO]: ${comando}`
    );

    console.log(
        `👤 [USUARIO]: ${autor}`
    );

    console.log(
        `🏠 [THREAD]: ${threadId}`
    );

    let respuesta = null;

    // ========================================================================
    // SISTEMA DE COMANDOS
    // ========================================================================

    switch (comando) {

        // --------------------------------------------------------------------
        // AYUDA
        // --------------------------------------------------------------------

        case '!ayuda':
        case '-ayuda':

            respuesta =
                '🤖 **KATHERINE — CENTRO DE CONTROL**\n\n' +

                '📖 **COMANDOS BÁSICOS**\n' +
                '🔊 `!leerchat` / `-leerchat` — Activar lectura interna.\n' +
                '🔇 `!noleerchat` / `-noleerchat` — Desactivar lectura interna.\n' +
                '📊 `!estadochat` / `-estadochat` — Consultar estado.\n' +
                '🏓 `!ping` / `-ping` — Comprobar conexión.\n\n' +

                '🎵 **MÚSICA**\n' +
                '▶️ `!play <canción>` / `-play <canción>` — Registrar canción.\n' +
                '🎚️ `!volumen <0-100>` / `-volumen <0-100>` — Ajustar volumen interno.\n\n' +

                '💡 **INFORMACIÓN**\n' +
                'Utiliza `!` como prefijo predeterminado.';

            break;

        // --------------------------------------------------------------------
        // PING
        // --------------------------------------------------------------------

        case '!ping':
        case '-ping': {

            const uptimeSec = Math.floor(
                (Date.now() - startTime) / 1000
            );

            respuesta =
                '🏓 **PONG! KATHERINE ESTÁ EN LÍNEA.**\n\n' +
                `⏱️ **Tiempo activa:** ${uptimeSec} segundos.\n` +
                '⚡ **Servidor:** Operativo.\n' +
                '📡 **Webhook:** Recibiendo eventos.\n' +
                '🤖 **API:** Conectividad comprobada por el servidor.';

            break;
        }

        // --------------------------------------------------------------------
        // ESTADO DEL CHAT
        // --------------------------------------------------------------------

        case '!estadochat':
        case '-estadochat':

            respuesta =
                '📊 **KATHERINE — ESTADO DEL SISTEMA**\n\n' +
                `🏠 **Thread ID:** ${threadId}\n` +
                `🔊 **Lectura interna:** ${estadoBot.lectura ? '🟢 ACTIVADA' : '🔴 DESACTIVADA'}\n` +
                `🎙️ **Llamada:** ${estadoBot.conectado ? '🟢 CONECTADO' : '⚪ DESCONECTADO'}\n` +
                `🎵 **Reproducción registrada:** ${estadoBot.reproduciendo ? '🟢 SÍ' : '⚪ NO'}\n` +
                `🎼 **Canción:** ${estadoBot.cancionActual || 'Ninguna'}\n` +
                `🔉 **Volumen interno:** ${estadoBot.volumen}/100`;

            break;

        // --------------------------------------------------------------------
        // ACTIVAR LECTURA
        // --------------------------------------------------------------------

        case '!leerchat':
        case '-leerchat':

            estadoBot.lectura = true;

            respuesta =
                '🔊 **KATHERINE:** Modo de lectura interno **ACTIVADO**.\n\n' +
                'La recepción de mensajes también depende de los permisos y la configuración de Anarchy.';

            break;

        // --------------------------------------------------------------------
        // DESACTIVAR LECTURA
        // --------------------------------------------------------------------

        case '!noleerchat':
        case '-noleerchat':

            estadoBot.lectura = false;

            respuesta =
                '🔇 **KATHERINE:** Modo de lectura interno **DESACTIVADO**.';

            break;

        // --------------------------------------------------------------------
        // REPRODUCCIÓN
        // --------------------------------------------------------------------

        case '!play':
        case '-play': {

            const busqueda = args.slice(1).join(' ');

            if (!busqueda) {

                respuesta =
                    '⚠️ **Especifica una canción.**\n\n' +
                    'Ejemplo: `!play Follow Me`';

            } else {

                estadoBot.reproduciendo = true;

                estadoBot.cancionActual = busqueda;

                respuesta =
                    '🎵 **KATHERINE MUSIC**\n\n' +
                    `📌 **Solicitud registrada:** ${busqueda}\n` +
                    `🎚️ **Volumen interno:** ${estadoBot.volumen}/100\n\n` +
                    '⚠️ El módulo de reproducción de audio todavía necesita su integración con LiveKit.';

            }

            break;
        }

        // --------------------------------------------------------------------
        // VOLUMEN
        // --------------------------------------------------------------------

        case '!volumen':
        case '-volumen': {

            const vol = Number(args[1]);

            if (
                args.length < 2 ||
                !Number.isFinite(vol) ||
                vol < 0 ||
                vol > 100
            ) {

                respuesta =
                    `🎚️ **Volumen interno actual:** ${estadoBot.volumen}/100\n\n` +
                    'Utiliza `!volumen <0-100>`.';

            } else {

                estadoBot.volumen = vol;

                respuesta =
                    `🎚️ **Volumen interno actualizado:** ${vol}/100.\n\n` +
                    'El control del volumen de audio requiere conectar el reproductor.';

            }

            break;
        }

        // --------------------------------------------------------------------
        // COMANDO DESCONOCIDO
        // --------------------------------------------------------------------

        default:

            console.log(
                `ℹ️ Comando no reconocido: ${comando}`
            );

            return;
    }

    // ========================================================================
    // PUBLICAR RESPUESTA
    // ========================================================================

    if (respuesta) {

        console.log(
            '📨 [KATHERINE] Preparando respuesta...'
        );

        const resultado = await enviarRespuestaChat(
            threadId,
            respuesta,
            messageId
        );

        if (!resultado.ok) {

            console.error(
                '❌ Katherine no pudo publicar su respuesta.',
                resultado.status,
                resultado.data
            );

        }
    }

    console.log(
        '-------------------------------------------------------\n'
    );
}

// ============================================================================
// VALIDACIÓN DE FIRMA HMAC
// ============================================================================

function validarFirmaWebhook(req) {

    const timestamp =
        req.headers['x-anarchy-bot-timestamp'];

    const signature =
        req.headers['x-anarchy-bot-signature'];

    const rawBody = req.rawBody;

    if (
        !timestamp ||
        !signature ||
        typeof rawBody !== 'string'
    ) {

        return {
            ok: false,
            error: 'Faltan encabezados o cuerpo original.'
        };
    }

    const timestampNumber = Number(timestamp);

    if (
        !Number.isFinite(timestampNumber) ||
        Math.abs(Date.now() / 1000 - timestampNumber) > 300
    ) {

        return {
            ok: false,
            error: 'Timestamp inválido o vencido.'
        };
    }

    const firmaRecibida = String(signature).startsWith('v1=')
        ? String(signature).slice(3)
        : String(signature);

    if (!/^[a-f0-9]{64}$/i.test(firmaRecibida)) {

        return {
            ok: false,
            error: 'Formato de firma incorrecto.'
        };
    }

    const firmaEsperada = crypto
        .createHmac('sha256', SECRET)
        .update(`${timestamp}.${rawBody}`)
        .digest();

    const firmaBuffer = Buffer.from(
        firmaRecibida,
        'hex'
    );

    if (
        firmaBuffer.length !== firmaEsperada.length ||
        !crypto.timingSafeEqual(
            firmaBuffer,
            firmaEsperada
        )
    ) {

        return {
            ok: false,
            error: 'La firma HMAC no coincide.'
        };
    }

    return {
        ok: true
    };
}

// ============================================================================
// WEBHOOK PRINCIPAL DE ANARCHY
// ============================================================================

app.post('/', (req, res) => {

    console.log('\n=======================================================');
    console.log('⚡ [WEBHOOK] PETICIÓN RECIBIDA DE ANARCHY');

    // ------------------------------------------------------------------------
    // VALIDAR FIRMA
    // ------------------------------------------------------------------------

    const validacion = validarFirmaWebhook(req);

    if (!validacion.ok) {

        console.error(
            '❌ [WEBHOOK RECHAZADO]:',
            validacion.error
        );

        return res.status(401).send('Unauthorized');
    }

    console.log(
        '✅ Firma HMAC validada correctamente.'
    );

    // ------------------------------------------------------------------------
    // RESPONDER RÁPIDAMENTE A ANARCHY
    // ------------------------------------------------------------------------

    res.status(200).send('OK');

    // ------------------------------------------------------------------------
    // PROCESAR EL EVENTO DESPUÉS DE RESPONDER
    // ------------------------------------------------------------------------

    const evento = req.body || {};

    procesarEvento(evento).catch(err => {

        console.error(
            '💥 [ERROR PROCESANDO EVENTO]:',
            err
        );

    });
});

// ============================================================================
// RUTA DE ESTADO DEL SERVIDOR
// ============================================================================

app.get('/', (req, res) => {

    res.status(200).json({

        bot: 'Katherine',

        status: 'online',

        uptimeSeconds: Math.floor(
            (Date.now() - startTime) / 1000
        ),

        timestamp: new Date().toISOString()

    });
});

// ============================================================================
// INICIO DEL SERVIDOR
// ============================================================================

app.listen(PORT, '0.0.0.0', () => {

    console.log('\n');
    console.log('=======================================================');
    console.log('       KATHERINE — ANARCHY BOT SYSTEM');
    console.log('=======================================================');

    console.log(
        `[KATHERINE SERVER] Servidor activo en el puerto ${PORT}`
    );

    console.log(
        '[WEBHOOK] Endpoint: POST /'
    );

    console.log(
        '[HEALTH] Endpoint: GET /'
    );

    console.log(
        '[API] Anarchy API conectada mediante BOT_TOKEN'
    );

    console.log(
        '[SECURITY] Validación HMAC activada'
    );

    console.log(
        '[COMMANDS] !ayuda | !ping | !estadochat | !play | !volumen'
    );

    console.log('=======================================================\n');

    // Diagnóstico automático al iniciar.
    consultarBotMe().catch(err => {

        console.error(
            '❌ Error en diagnóstico inicial:',
            err
        );

    });

});

