
'use strict';

const express = require('express');
const crypto = require('crypto');

const app = express();

// ===============================
// CONFIGURACIÓN
// ===============================

const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;
const PORT = Number(process.env.PORT) || 3000;

const API_URL = 'https://api.anarchyapps.org/bot-api/v1';
const startTime = Date.now();
const MAX_TIMESTAMP_AGE = 5 * 60; // 5 minutos

// Verificación de configuración
if (!SECRET) {
    throw new Error('Falta WEBHOOK_SECRET o WEB_TOKEN.');
}

if (!BOT_TOKEN) {
    throw new Error('Falta BOT_TOKEN.');
}

// Estados independientes por conversación
const estados = new Map();

function obtenerEstado(threadId) {
    if (!estados.has(threadId)) {
        estados.set(threadId, {
            lectura: false,
            conectado: false,
            volumen: 30,
            reproduciendo: false,
            cancionActual: null
        });
    }

    return estados.get(threadId);
}

// ===============================
// MIDDLEWARE
// ===============================

app.use(express.json({
    limit: '1mb',
    verify: (req, res, buf) => {
        req.rawBody = buf.toString('utf8');
    }
}));

app.use(express.urlencoded({
    extended: true,
    limit: '1mb'
}));

// ===============================
// SEGURIDAD HMAC
// ===============================

function verificarFirma(req) {
    const timestamp = req.headers['x-anarchy-bot-timestamp'];
    const signature = req.headers['x-anarchy-bot-signature'];

    if (!timestamp || !signature || !req.rawBody) {
        return false;
    }

    const timestampNumber = Number(timestamp);

    if (!Number.isFinite(timestampNumber)) {
        return false;
    }

    // Aceptar timestamps Unix en segundos o milisegundos.
    const timestampSeconds = timestampNumber > 1e12
        ? timestampNumber / 1000
        : timestampNumber;

    const ahora = Math.floor(Date.now() / 1000);

    if (Math.abs(ahora - timestampSeconds) > MAX_TIMESTAMP_AGE) {
        console.warn('[SEGURIDAD] Timestamp expirado.');
        return false;
    }

    const signatureClean = signature.startsWith('v1=')
        ? signature.slice(3)
        : signature;

    if (!/^[a-fA-F0-9]{64}$/.test(signatureClean)) {
        return false;
    }

    const expected = crypto
        .createHmac('sha256', SECRET)
        .update(`${timestamp}.${req.rawBody}`)
        .digest();

    const received = Buffer.from(signatureClean, 'hex');

    return crypto.timingSafeEqual(expected, received);
}

// ===============================
// CLIENTE API ANARCHY
// ===============================

async function anarchyAPI(endpoint, method = 'GET', body = null) {
    try {
        const url = `${API_URL}${endpoint}`;

        const options = {
            method,
            headers: {
                'Authorization': `Bot ${BOT_TOKEN}`,
                'Content-Type': 'application/json'
            },
            signal: AbortSignal.timeout(10000)
        };

        if (body !== null) {
            options.body = JSON.stringify(body);
        }

        console.log(`[API] ${method} ${endpoint}`);

        const response = await fetch(url, options);
        const responseText = await response.text();

        let data;

        try {
            data = responseText ? JSON.parse(responseText) : {};
        } catch {
            data = { raw: responseText };
        }

        if (!response.ok) {
            console.error('[API ERROR]', response.status, data);
        }

        return {
            ok: response.ok,
            status: response.status,
            data
        };

    } catch (error) {
        console.error('[API FETCH ERROR]', error.message);

        return {
            ok: false,
            status: 500,
            data: { error: error.message }
        };
    }
}

// ===============================
// VERIFICAR TOKEN DEL BOT
// ===============================

async function verificarTokenBot() {
    console.log('[KATHERINE] Verificando credenciales...');

    const resultado = await anarchyAPI('/me');

    if (resultado.ok) {
        console.log(
            'Token válido. Conectado como:',
            resultado.data.name ||
            resultado.data.botId ||
            'Katherine'
        );
    } else {
        console.error(
            'No se pudo verificar el token:',
            resultado.status
        );
    }
}

// ===============================
// ENVIAR MENSAJES
// ===============================

async function enviarRespuestaChat(threadId, texto) {
    const endpoint = `/threads/${encodeURIComponent(threadId)}/messages`;

    return await anarchyAPI(endpoint, 'POST', {
        content: texto
    });
}

// ===============================
// COMANDOS
// ===============================

async function procesarComando(evento) {
    const threadId = evento.threadId;
    const mensaje = evento.message;

    if (!threadId || typeof mensaje?.content !== 'string') {
        return;
    }

    const texto = mensaje.content.trim();

    if (!texto) return;

    const args = texto.split(/\s+/);
    const comando = args[0].toLowerCase();

    const estado = obtenerEstado(threadId);

    const usuario =
        mensaje.author?.nickname || 'Anónimo';

    console.log(
        `[COMANDO] ${comando} | Usuario: ${usuario} | Thread: ${threadId}`
    );

    let respuesta = null;

    switch (comando) {

        // -----------------------
        // AYUDA
        // -----------------------

        case '-ayuda':
        case '!ayuda':

            respuesta =
                '🤖 **KATHERINE — CENTRO DE CONTROL**\n\n' +
                '📖 **COMANDOS GENERALES**\n' +
                '🔊 `-leerchat` — Activar modo lectura.\n' +
                '🔇 `-noleerchat` — Desactivar modo lectura.\n' +
                '📊 `-estadochat` — Consultar estado.\n' +
                '🏓 `-ping` — Comprobar servidor.\n\n' +
                '🎵 **MÚSICA**\n' +
                'La integración de reproducción musical todavía no está configurada.';

            break;

        // -----------------------
        // PING
        // -----------------------

        case '-ping':
        case '!ping': {

            const uptime = Math.floor(
                (Date.now() - startTime) / 1000
            );

            respuesta =
                '🏓 **PONG!**\n\n' +
                '🟢 Servidor en línea.\n' +
                `⏱️ Uptime: ${uptime} segundos.\n` +
                '⚡ Webhook operativo.';

            break;
        }

        // -----------------------
        // ESTADO
        // -----------------------

        case '-estadochat':
        case '!estadochat':

            respuesta =
                '📊 **KATHERINE — ESTADO**\n\n' +
                `🏠 **Thread:** ${threadId}\n` +
                `🔊 **Lectura:** ${estado.lectura ? '🟢 ACTIVADA' : '🔴 DESACTIVADA'}\n` +
                `🎙️ **Llamada:** ${estado.conectado ? '🟢 CONECTADO' : '⚪ DESCONECTADO'}\n` +
                `🎵 **Música:** ${estado.reproduciendo ? '🟢 REPRODUCIENDO' : '⚪ DETENIDA'}\n` +
                `🎚️ **Volumen:** ${estado.volumen}/100`;

            break;

        // -----------------------
        // ACTIVAR LECTURA
        // -----------------------

        case '-leerchat':
        case '!leerchat':

            if (estado.lectura) {
                respuesta = '🔊 El modo lectura ya está activado.';
            } else {
                estado.lectura = true;
                respuesta = '🔊 **Katherine:** Modo lectura ACTIVADO.';
            }

            break;

        // -----------------------
        // DESACTIVAR LECTURA
        // -----------------------

        case '-noleerchat':
        case '!noleerchat':

            if (!estado.lectura) {
                respuesta = '🔇 El modo lectura ya está desactivado.';
            } else {
                estado.lectura = false;
                respuesta = '🔇 **Katherine:** Modo lectura DESACTIVADO.';
            }

            break;

        // -----------------------
        // VOLUMEN
        // -----------------------

        case '-volumen':
        case '!volumen': {

            if (!args[1]) {
                respuesta = `🎚️ Volumen actual: ${estado.volumen}/100`;
                break;
            }

            const volumen = Number(args[1]);

            if (
                !Number.isInteger(volumen) ||
                volumen < 0 ||
                volumen > 100
            ) {
                respuesta = '⚠️ Introduce un número entre 0 y 100.';
                break;
            }

            estado.volumen = volumen;

            respuesta =
                `🎚️ Volumen establecido en **${volumen}/100**.`;

            break;
        }

        // -----------------------
        // MÚSICA NO CONFIGURADA
        // -----------------------

        case '-play':
        case '!play':
        case '-pausa':
        case '!pausa':

            respuesta =
                '⚠️ **KATHERINE MUSIC**\n\n' +
                'El sistema de reproducción musical todavía no está integrado.\n' +
                'Se necesita configurar un servicio de audio para utilizar estos comandos.';

            break;

        // -----------------------
        // COMANDO DESCONOCIDO
        // -----------------------

        default:

            if (comando.startsWith('-') || comando.startsWith('!')) {
                respuesta = `❓ Comando desconocido: \`${comando}\`\nUtiliza \`-ayuda\` para consultar los comandos.`;
            }

            break;
    }

    if (respuesta) {
        const resultado = await enviarRespuestaChat(
            threadId,
            respuesta
        );

        if (!resultado.ok) {
            console.error(
                '[MENSAJE NO ENVIADO]',
                resultado.status,
                resultado.data
            );
        }
    }
}

// ===============================
// WEBHOOK PRINCIPAL
// ===============================

app.post('/', (req, res) => {

    console.log('\n========== WEBHOOK KATHERINE ==========');

    // Validar autenticidad antes de procesar.
    if (!verificarFirma(req)) {
        console.warn('[SEGURIDAD] Solicitud rechazada.');

        return res.status(401).send('Invalid signature');
    }

    console.log('[WEBHOOK] Firma validada correctamente.');

    // Responder inmediatamente a Anarchy.
    res.status(200).send('OK');

    // Procesar el evento después de responder.
    setImmediate(async () => {
        try {
            await procesarComando(req.body || {});
        } catch (error) {
            console.error(
                '[ERROR PROCESANDO EVENTO]',
                error
            );
        }
    });
});

// ===============================
// RUTA DE ESTADO
// ===============================

app.get('/', (req, res) => {
    res.status(200).json({
        bot: 'Katherine',
        status: 'online',
        uptime: Math.floor(
            (Date.now() - startTime) / 1000
        )
    });
});

// ===============================
// INICIAR SERVIDOR
// ===============================

const server = app.listen(PORT, '0.0.0.0', () => {

    console.log('================================');
    console.log('   KATHERINE BOT SERVER');
    console.log('================================');
    console.log(`Puerto: ${PORT}`);
    console.log('Estado: ONLINE');
    console.log('Webhook: ACTIVO');
    console.log('================================');

    verificarTokenBot();
});

// Manejo de errores del servidor
server.on('error', (error) => {
    console.error('[SERVER ERROR]', error);
});

process.on('unhandledRejection', (error) => {
    console.error('[UNHANDLED REJECTION]', error);
});

