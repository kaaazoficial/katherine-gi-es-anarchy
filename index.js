
'use strict';

const express = require('express');
const crypto = require('crypto');

const app = express();

// =====================================
// CONFIGURACIÓN
// =====================================

const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;
const PORT = Number(process.env.PORT) || 3000;

const API_URL = 'https://api.anarchyapps.org/bot-api/v1';
const startTime = Date.now();
const MAX_TIMESTAMP_AGE = 300;

if (!SECRET) {
    throw new Error('Falta WEBHOOK_SECRET o WEB_TOKEN.');
}

if (!BOT_TOKEN) {
    throw new Error('Falta BOT_TOKEN.');
}

// =====================================
// ESTADOS INDEPENDIENTES
// =====================================

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

// =====================================
// MIDDLEWARE
// =====================================

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

// =====================================
// VALIDACIÓN HMAC CON DIAGNÓSTICO
// =====================================

function verificarFirma(req) {
    const timestamp = req.headers['x-anarchy-bot-timestamp'];
    const signature = req.headers['x-anarchy-bot-signature'];

    console.log('\n[DIAGNÓSTICO WEBHOOK]');
    console.log('Timestamp recibido:', timestamp ? 'SÍ' : 'NO');
    console.log('Firma recibida:', signature ? 'SÍ' : 'NO');
    console.log('RawBody disponible:', req.rawBody !== undefined);
    console.log('Longitud del cuerpo:', req.rawBody?.length ?? 0);
    console.log('Formato de firma:', signature?.split('=')[0] || 'NINGUNO');

    if (!timestamp || !signature || req.rawBody === undefined) {
        console.log('[MOTIVO] Faltan encabezados o cuerpo original.');
        return false;
    }

    const time = Number(timestamp);

    if (!Number.isFinite(time)) {
        console.log('[MOTIVO] Timestamp inválido.');
        return false;
    }

    const seconds = time > 1e12 ? time / 1000 : time;

    if (Math.abs(Date.now() / 1000 - seconds) > MAX_TIMESTAMP_AGE) {
        console.log('[MOTIVO] Timestamp fuera del margen de 5 minutos.');
        return false;
    }

    const expected = crypto
        .createHmac('sha256', SECRET)
        .update(`${timestamp}.${req.rawBody}`)
        .digest('hex');

    const received = signature.replace(/^v1=/, '');

    if (!/^[a-fA-F0-9]{64}$/.test(received)) {
        console.log('[MOTIVO] Formato de firma desconocido.');
        return false;
    }

    const expectedBuffer = Buffer.from(expected, 'hex');
    const receivedBuffer = Buffer.from(received, 'hex');

    const valid = crypto.timingSafeEqual(
        expectedBuffer,
        receivedBuffer
    );

    console.log(
        '[RESULTADO]',
        valid ? 'FIRMA VÁLIDA' : 'FIRMA INCORRECTA'
    );

    return valid;
}

// =====================================
// CLIENTE API ANARCHY
// =====================================

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

        console.log(`[API REQUEST] ${method} ${endpoint}`);

        const response = await fetch(url, options);
        const responseText = await response.text();

        let data;

        try {
            data = responseText ? JSON.parse(responseText) : {};
        } catch {
            data = { raw: responseText };
        }

        console.log(
            `[API RESPONSE] ${response.status}`,
            JSON.stringify(data)
        );

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

// =====================================
// VERIFICACIÓN DEL BOT
// =====================================

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
            '[TOKEN ERROR]',
            resultado.status,
            resultado.data
        );
    }
}

// =====================================
// ENVIAR MENSAJES
// =====================================

async function enviarRespuestaChat(threadId, texto) {
    const endpoint =
        `/threads/${encodeURIComponent(threadId)}/messages`;

    return await anarchyAPI(endpoint, 'POST', {
        content: texto
    });
}

// =====================================
// PROCESADOR DE COMANDOS
// =====================================

async function procesarComando(evento) {
    const threadId = evento.threadId;
    const mensaje = evento.message;

    if (!threadId || typeof mensaje?.content !== 'string') {
        console.log('[EVENTO] Mensaje sin threadId o contenido.');
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

        // AYUDA

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
                '▶️ `-play <canción>` — Pendiente de integración.\n' +
                '⏸️ `-pausa` — Pendiente de integración.\n' +
                '🎚️ `-volumen <0-100>` — Ajustar volumen.';

            break;

        // PING

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

        // ESTADO

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

        // ACTIVAR LECTURA

        case '-leerchat':
        case '!leerchat':

            if (estado.lectura) {
                respuesta = '🔊 El modo lectura ya está activado.';
            } else {
                estado.lectura = true;
                respuesta = '🔊 **Katherine:** Modo lectura ACTIVADO.';
            }

            break;

        // DESACTIVAR LECTURA

        case '-noleerchat':
        case '!noleerchat':

            if (!estado.lectura) {
                respuesta = '🔇 El modo lectura ya está desactivado.';
            } else {
                estado.lectura = false;
                respuesta = '🔇 **Katherine:** Modo lectura DESACTIVADO.';
            }

            break;

        // VOLUMEN

        case '-volumen':
        case '!volumen': {

            if (!args[1]) {
                respuesta =
                    `🎚️ Volumen actual: ${estado.volumen}/100`;
                break;
            }

            const volumen = Number(args[1]);

            if (
                !Number.isInteger(volumen) ||
                volumen < 0 ||
                volumen > 100
            ) {
                respuesta =
                    '⚠️ Introduce un número entre 0 y 100.';
                break;
            }

            estado.volumen = volumen;

            respuesta =
                `🎚️ Volumen establecido en **${volumen}/100**.`;

            break;
        }

        // REPRODUCCIÓN PENDIENTE

        case '-play':
        case '!play': {

            const cancion = args.slice(1).join(' ');

            if (!cancion) {
                respuesta =
                    '⚠️ Especifica una canción. Ejemplo: `-play Follow Me`';
            } else {
                respuesta =
                    '⚠️ El sistema de reproducción musical todavía no está integrado.';
            }

            break;
        }

        case '-pausa':
        case '!pausa':

            respuesta =
                '⚠️ El sistema de audio todavía no está integrado.';

            break;

        // COMANDO DESCONOCIDO

        default:

            if (comando.startsWith('-') || comando.startsWith('!')) {
                respuesta =
                    `❓ Comando desconocido: \`${comando}\`\n` +
                    'Utiliza `-ayuda` para consultar los comandos.';
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

// =====================================
// WEBHOOK PRINCIPAL
// =====================================

app.post('/', (req, res) => {

    console.log('\n========== WEBHOOK KATHERINE ==========');

    if (!verificarFirma(req)) {
        console.warn('[SEGURIDAD] Solicitud rechazada.');

        return res.status(401).send('Invalid signature');
    }

    console.log('[WEBHOOK] Firma validada correctamente.');

    // Respuesta inmediata
    res.status(200).send('OK');

    // Procesamiento asíncrono
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

// =====================================
// RUTA DE ESTADO
// =====================================

app.get('/', (req, res) => {
    res.status(200).json({
        bot: 'Katherine',
        status: 'online',
        uptime: Math.floor(
            (Date.now() - startTime) / 1000
        )
    });
});

// =====================================
// INICIO DEL SERVIDOR
// =====================================

const server = app.listen(PORT, '0.0.0.0', () => {

    console.log('================================');
    console.log('     KATHERINE BOT SERVER');
    console.log('================================');
    console.log(`Puerto: ${PORT}`);
    console.log('Estado: ONLINE');
    console.log('Webhook: ACTIVO');
    console.log('================================');

    verificarTokenBot();
});

server.on('error', (error) => {
    console.error('[SERVER ERROR]', error);
});

process.on('unhandledRejection', (error) => {
    console.error('[UNHANDLED REJECTION]', error);
});

