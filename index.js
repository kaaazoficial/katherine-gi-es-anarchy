
'use strict';

const express = require('express');
const crypto = require('crypto');

const app = express();

// ============================================================================
// CONFIGURACIÓN
// ============================================================================

const API_BASE = 'https://api.anarchyapps.org/bot-api/v1';

const BOT_TOKEN = process.env.BOT_TOKEN;
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;

// Token y secreto generados por el Agente en ACM.
// Son opcionales para mantener funcionando el bot de chat sin ACM.

const ACM_BOT_TOKEN = process.env.ACM_BOT_TOKEN;
const ACM_WEBHOOK_SECRET = process.env.ACM_WEBHOOK_SECRET;

const PORT = Number(process.env.PORT) || 3000;

const startTime = Date.now();

if (!BOT_TOKEN) {
    throw new Error('[FATAL] Falta BOT_TOKEN.');
}

if (!WEBHOOK_SECRET) {
    throw new Error('[FATAL] Falta WEBHOOK_SECRET.');
}

if (ACM_BOT_TOKEN && !ACM_WEBHOOK_SECRET) {
    console.warn('[CONFIG] Falta ACM_WEBHOOK_SECRET.');
}

if (!ACM_BOT_TOKEN && ACM_WEBHOOK_SECRET) {
    console.warn('[CONFIG] Falta ACM_BOT_TOKEN.');
}

// ============================================================================
// MANEJO DE ERRORES
// ============================================================================

process.on('uncaughtException', (err) => {
    console.error('💥 [EXCEPCIÓN NO CAPTURADA]:', err);
});

process.on('unhandledRejection', (reason) => {
    console.error('💥 [PROMESA RECHAZADA]:', reason);
});

// ============================================================================
// ESTADO GLOBAL
// ============================================================================

const estadoBot = {
    lectura: false,
    conectado: false,
    volumen: 30,
    reproduciendo: false,
    cancionActual: null
};

const salasActivas = new Map();

const mensajesProcesados = new Set();

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
// UTILIDADES
// ============================================================================

function dormir(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function limpiarMensaje(texto) {
    return String(texto ?? '')
        .replace(/[＊*`]/g, '')
        .replace(/\r/g, '')
        .trim()
        .slice(0, 2000);
}

function tiempoActivo() {
    let segundos = Math.floor((Date.now() - startTime) / 1000);

    const dias = Math.floor(segundos / 86400);
    segundos %= 86400;

    const horas = Math.floor(segundos / 3600);
    segundos %= 3600;

    const minutos = Math.floor(segundos / 60);
    segundos %= 60;

    const partes = [];

    if (dias) partes.push(`${dias} día(s)`);
    if (horas) partes.push(`${horas} hora(s)`);
    if (minutos) partes.push(`${minutos} minuto(s)`);

    partes.push(`${segundos} segundo(s)`);

    return partes.join(', ');
}

function obtenerHoraChile() {
    return new Intl.DateTimeFormat('es-CL', {
        timeZone: 'America/Santiago',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
    }).format(new Date());
}

function obtenerFechaChile() {
    return new Intl.DateTimeFormat('es-CL', {
        timeZone: 'America/Santiago',
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric'
    }).format(new Date());
}

function elegirAleatorio(lista) {
    return lista[Math.floor(Math.random() * lista.length)];
}

// ============================================================================
// CURIOSIDADES
// ============================================================================

const curiosidades = [
    'Los pulpos tienen tres corazones y su sangre contiene hemocianina, que le da un tono azulado.',
    'En Venus, un día sideral dura más que su año.',
    'Los tiburones existen desde antes que los árboles.',
    'La miel puede conservarse durante muchísimo tiempo si permanece bien sellada y sin humedad.',
    'Los wombats producen heces con forma de cubo.',
    'La Luna se aleja de la Tierra unos 3,8 centímetros por año.',
    'Un rayo puede calentar el aire a temperaturas superiores a las de la superficie del Sol.',
    'Los cuervos pueden reconocer rostros humanos.',
    'Los flamencos obtienen gran parte de su color rosado de su alimentación.',
    'La Antártida es el desierto más grande del planeta.',
    'Los koalas tienen huellas dactilares muy parecidas a las humanas.',
    'El corazón de una ballena azul puede pesar cientos de kilos.',
    'Saturno tiene una densidad media inferior a la del agua.',
    'Las mariposas prueban sustancias mediante receptores en sus patas.',
    'El sonido no puede propagarse en el vacío.',
    'Los caballitos de mar machos llevan los embriones en una bolsa incubadora.',
    'Las nutrias marinas suelen sujetarse entre sí mientras descansan.',
    'La luz del Sol tarda aproximadamente ocho minutos y veinte segundos en llegar a la Tierra.',
    'Los tardígrados pueden sobrevivir a condiciones extremas mediante criptobiosis.',
    'La cordillera de los Andes es la cordillera continental más larga del mundo.',
    'El planeta Urano gira prácticamente de lado respecto al plano de su órbita.',
    'Las abejas pueden comunicar la ubicación de alimento mediante una danza.',
    'El agua puede existir como sólido, líquido y gas simultáneamente en el punto triple.',
    'Los delfines utilizan silbidos característicos para identificarse.',
    'La Torre Eiffel puede crecer varios centímetros durante los días calurosos.',
    'Los cuervos pueden recordar a personas que representan una amenaza.',
    'La superficie de Marte tiene el volcán más grande conocido del sistema solar.',
    'Las estrellas de mar no tienen cerebro central.',
    'El bambú puede crecer a velocidades extraordinarias dependiendo de la especie.',
    'Los flamencos descansan frecuentemente sobre una sola pata.'
];

let ultimaCuriosidad = -1;

function obtenerCuriosidad() {
    let indice;

    do {
        indice = Math.floor(Math.random() * curiosidades.length);
    } while (
        curiosidades.length > 1 &&
        indice === ultimaCuriosidad
    );

    ultimaCuriosidad = indice;

    return curiosidades[indice];
}

// ============================================================================
// RESPUESTAS DE LA BOLA MÁGICA
// ============================================================================

const respuestas8Ball = [
    'Sí, definitivamente.',
    'Todo apunta a que sí.',
    'Es muy probable.',
    'Las señales son favorables.',
    'No puedo asegurarlo todavía.',
    'Pregúntame de nuevo más tarde.',
    'No parece probable.',
    'Mi respuesta es no.',
    'Las posibilidades son bajas.',
    'El destino todavía no está decidido.',
    'Confía en tu intuición.',
    'No cuentes con ello.'
];

// ============================================================================
// CLIENTE ANARCHY API
// ============================================================================

async function anarchyAPI(
    endpoint,
    method = 'GET',
    body = null,
    token = BOT_TOKEN
) {
    if (!token) {
        return {
            ok: false,
            status: 500,
            data: {
                error: 'No se configuró el token necesario.'
            }
        };
    }

    try {
        const url = `${API_BASE}${endpoint}`;

        console.log(`[API REQUEST] ${method} -> ${url}`);

        const options = {
            method,
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Authorization': `Bot ${token}`
            },
            signal: AbortSignal.timeout(15000)
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
            `[API RESPONSE] ${response.status}:`,
            JSON.stringify(data)
        );

        return {
            ok: response.ok,
            status: response.status,
            data,
            retryAfter: response.headers.get('retry-after')
        };

    } catch (err) {
        console.error(
            '❌ [ERROR FETCH ANARCHY]:',
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
    console.log('\n========== DIAGNÓSTICO ANARCHY ==========');

    const resultado = await anarchyAPI(
        '/me',
        'GET',
        null,
        BOT_TOKEN
    );

    if (resultado.ok) {
        console.log(
            '✅ DATOS DEL BOT:',
            JSON.stringify(resultado.data, null, 2)
        );

        const threads = resultado.data?.threadIds || [];

        console.log(
            '📌 Chats donde está agregado:',
            threads.length
                ? threads.join(', ')
                : 'Ninguno'
        );

    } else {
        console.error(
            '❌ Error consultando el bot:',
            resultado.status,
            resultado.data
        );
    }

    console.log(
        '[ACM] Token:',
        ACM_BOT_TOKEN ? 'CONFIGURADO' : 'NO CONFIGURADO'
    );

    console.log('=========================================\n');
}

// ============================================================================
// COLA DE MENSAJES Y LÍMITES DE ANARCHY
// ============================================================================

let colaEnvios = Promise.resolve();

let ultimoEnvioGlobal = 0;

const ultimoEnvioPorChat = new Map();

const enviosGlobales = [];

const enviosPorChat = new Map();

function podarMarcas(marcas, antiguedad) {
    const limite = Date.now() - antiguedad;

    while (
        marcas.length &&
        marcas[0] < limite
    ) {
        marcas.shift();
    }
}

function agregarACola(threadId, tarea) {
    const siguiente = colaEnvios.then(async () => {

        const ahora = Date.now();

        const esperaGlobal = Math.max(
            0,
            1050 - (ahora - ultimoEnvioGlobal)
        );

        const ultimoChat =
            ultimoEnvioPorChat.get(threadId) || 0;

        const esperaChat = Math.max(
            0,
            2050 - (ahora - ultimoChat)
        );

        await dormir(
            Math.max(esperaGlobal, esperaChat)
        );

        podarMarcas(
            enviosGlobales,
            60 * 60 * 1000
        );

        const marcasChat =
            enviosPorChat.get(threadId) || [];

        podarMarcas(
            marcasChat,
            10 * 60 * 1000
        );

        enviosPorChat.set(threadId, marcasChat);

        if (enviosGlobales.length >= 300) {
            return {
                ok: false,
                status: 429,
                data: {
                    error: 'Límite horario de mensajes alcanzado.'
                }
            };
        }

        if (marcasChat.length >= 60) {
            return {
                ok: false,
                status: 429,
                data: {
                    error: 'Límite de mensajes de este chat alcanzado.'
                }
            };
        }

        const resultado = await tarea();

        ultimoEnvioGlobal = Date.now();

        ultimoEnvioPorChat.set(
            threadId,
            ultimoEnvioGlobal
        );

        if (resultado.ok) {
            enviosGlobales.push(Date.now());
            marcasChat.push(Date.now());
        }

        return resultado;
    });

    colaEnvios = siguiente.catch(err => {
        console.error(
            '❌ Error en cola de mensajes:',
            err
        );
    });

    return siguiente;
}

// ============================================================================
// ENVIAR MENSAJES AL CHAT
// ============================================================================

async function enviarRespuestaChat(
    threadId,
    texto,
    replyMessageId = null,
    token = BOT_TOKEN
) {
    if (!threadId || !texto) {
        return {
            ok: false,
            status: 400,
            data: {
                error: 'Falta threadId o contenido.'
            }
        };
    }

    const contenido = limpiarMensaje(texto);

    if (!contenido) {
        return {
            ok: false,
            status: 400,
            data: {
                error: 'El mensaje quedó vacío.'
            }
        };
    }

    return agregarACola(
        String(threadId),
        async () => {

            const payload = {
                content: contenido
            };

            if (replyMessageId) {
                payload.replyMessageId = String(replyMessageId);
            }

            console.log(
                `[ENVIANDO MENSAJE] Thread: ${threadId}`
            );

            console.log(
                '[CONTENIDO]:',
                contenido
            );

            const resultado = await anarchyAPI(
                `/threads/${encodeURIComponent(threadId)}/messages`,
                'POST',
                payload,
                token
            );

            if (resultado.ok) {
                console.log(
                    '✅ [MENSAJE PUBLICADO]'
                );
            } else {
                console.error(
                    '❌ [ERROR PUBLICANDO MENSAJE]',
                    resultado.status,
                    JSON.stringify(resultado.data)
                );
            }

            return resultado;
        }
    );
}

// ============================================================================
// IDENTIFICAR EL TOKEN DEL WEBHOOK
// ============================================================================

function tokenParaEvento(origenWebhook) {
    if (
        origenWebhook === 'acm' &&
        ACM_BOT_TOKEN
    ) {
        return ACM_BOT_TOKEN;
    }

    return BOT_TOKEN;
}

// ============================================================================
// VALIDACIÓN HMAC
// ============================================================================

function validarFirmaWebhook(req) {
    const timestamp =
        req.headers['x-anarchy-bot-timestamp'];

    const signatureHeader =
        req.headers['x-anarchy-bot-signature'];

    const rawBody = req.rawBody;

    if (
        !timestamp ||
        !signatureHeader ||
        typeof rawBody !== 'string'
    ) {
        return {
            ok: false,
            error: 'Faltan encabezados de firma o cuerpo original.'
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

    const signature = String(signatureHeader).startsWith('v1=')
        ? String(signatureHeader).slice(3)
        : String(signatureHeader);

    if (!/^[a-f0-9]{64}$/i.test(signature)) {
        return {
            ok: false,
            error: 'Formato de firma incorrecto.'
        };
    }

    const secretos = [
        {
            secret: WEBHOOK_SECRET,
            source: 'chat'
        },
        {
            secret: ACM_WEBHOOK_SECRET,
            source: 'acm'
        }
    ].filter(item => item.secret);

    const recibida = Buffer.from(signature, 'hex');

    for (const item of secretos) {

        const esperada = crypto
            .createHmac('sha256', item.secret)
            .update(`${timestamp}.${rawBody}`)
            .digest();

        if (
            recibida.length === esperada.length &&
            crypto.timingSafeEqual(
                recibida,
                esperada
            )
        ) {
            return {
                ok: true,
                source: item.source
            };
        }
    }

    return {
        ok: false,
        error: 'La firma HMAC no coincide.'
    };
}

// ============================================================================
// SISTEMA DE LLAMADAS LIVEKIT
// ============================================================================

let ultimaConsultaSalas = 0;

let cacheSalas = [];

let cacheSalasHasta = 0;

// ---------------------------------------------------------------------------
// CONSULTAR SALAS ACTIVAS
// ---------------------------------------------------------------------------

async function obtenerSalasEnVivo(forzar = false) {

    if (!ACM_BOT_TOKEN) {
        return {
            ok: false,
            error: 'Falta el token generado en ACM para habilitar las llamadas.'
        };
    }

    if (
        !forzar &&
        Date.now() < cacheSalasHasta
    ) {
        return {
            ok: true,
            salas: cacheSalas
        };
    }

    const espera = Math.max(
        0,
        3000 - (Date.now() - ultimaConsultaSalas)
    );

    if (espera) {
        await dormir(espera);
    }

    ultimaConsultaSalas = Date.now();

    const resultado = await anarchyAPI(
        '/live-rooms',
        'GET',
        null,
        ACM_BOT_TOKEN
    );

    if (!resultado.ok) {
        return {
            ok: false,
            error: `Anarchy respondió HTTP ${resultado.status}: ${JSON.stringify(resultado.data)}`
        };
    }

    const salas = Array.isArray(
        resultado.data?.roomList
    )
        ? resultado.data.roomList
        : [];

    cacheSalas = salas;

    cacheSalasHasta = Date.now() + 2000;

    return {
        ok: true,
        salas
    };
}

// ---------------------------------------------------------------------------
// PUBLICAR AUDIO SILENCIOSO
// ---------------------------------------------------------------------------

async function publicarSilencioEnSala(estado, sdk) {

    const sampleRate = 16000;
    const channels = 1;
    const samplesPerFrame = 320;

    const silencio = new Int16Array(
        samplesPerFrame
    );

    const source = new sdk.AudioSource(
        sampleRate,
        channels
    );

    const track = sdk.LocalAudioTrack.createAudioTrack(
        'katherine-microphone',
        source
    );

    const options = new sdk.TrackPublishOptions();

    options.source = sdk.TrackSource.SOURCE_MICROPHONE;

    estado.source = source;
    estado.track = track;

    let publicada = false;

    while (
        !estado.stopping &&
        !publicada
    ) {
        try {
            await estado.room.localParticipant.publishTrack(
                track,
                options
            );

            publicada = true;

            console.log(
                `✅ [LIVEKIT] Micrófono silencioso publicado en ${estado.threadId}.`
            );

        } catch (err) {

            console.warn(
                '[LIVEKIT] Esperando permiso de micrófono:',
                err.message
            );

            await dormir(1500);
        }
    }

    if (!publicada) {
        return;
    }

    while (!estado.stopping) {

        try {
            const frame = new sdk.AudioFrame(
                silencio,
                sampleRate,
                channels,
                samplesPerFrame
            );

            await source.captureFrame(frame);

        } catch (err) {

            if (!estado.stopping) {
                console.error(
                    '[LIVEKIT] Error publicando silencio:',
                    err.message
                );
            }

            break;
        }

        await dormir(20);
    }
}

// ---------------------------------------------------------------------------
// ENTRAR A UNA LLAMADA
// ---------------------------------------------------------------------------

async function entrarLlamada(threadId) {

    if (!ACM_BOT_TOKEN) {
        return {
            ok: false,
            mensaje: 'No puedo entrar todavía. Falta el token generado desde ACM para el bot de comunidad.'
        };
    }

    const id = String(threadId);

    if (salasActivas.has(id)) {
        return {
            ok: true,
            mensaje: 'Katherine ya está conectada a la llamada de este chat.'
        };
    }

    if (salasActivas.size >= 3) {
        return {
            ok: false,
            mensaje: 'Ya estoy conectada al máximo de tres salas simultáneas.'
        };
    }

    const listado = await obtenerSalasEnVivo(true);

    if (!listado.ok) {
        return {
            ok: false,
            mensaje: `No pude consultar las llamadas. ${listado.error}`
        };
    }

    const sala = listado.salas.find(
        item => String(item.threadId) === id
    );

    if (!sala) {
        return {
            ok: false,
            mensaje: 'No encuentro una llamada activa en este chat. Inicia una llamada y vuelve a usar !llamada!.'
        };
    }

    if (sala.joined) {
        return {
            ok: false,
            mensaje: 'Anarchy indica que Katherine ya figura dentro de esta sala.'
        };
    }

    let room = null;

    let sdk = null;

    try {

        const joinResult = await anarchyAPI(
            `/live-rooms/${encodeURIComponent(id)}/join`,
            'POST',
            null,
            ACM_BOT_TOKEN
        );

        if (!joinResult.ok) {
            return {
                ok: false,
                mensaje: `Anarchy rechazó la entrada a la llamada. HTTP ${joinResult.status}: ${JSON.stringify(joinResult.data)}`
            };
        }

        const credentials = joinResult.data || {};

        if (
            !credentials.url ||
            !credentials.token
        ) {
            return {
                ok: false,
                mensaje: 'Anarchy no devolvió las credenciales necesarias de LiveKit.'
            };
        }

        sdk = await import('@livekit/rtc-node');

        room = new sdk.Room();

        const estado = {
            threadId: id,
            room,
            sdk,
            identity: credentials.identity || 'Katherine',
            stopping: false,
            source: null,
            track: null,
            audioTask: null
        };

        room.on(
            sdk.RoomEvent.Disconnected,
            () => {

                console.warn(
                    `[LIVEKIT] Katherine se desconectó de ${id}.`
                );

                estado.stopping = true;

                salasActivas.delete(id);

                if (estado.track) {
                    estado.track.close().catch(err => {
                        console.warn(
                            '[LIVEKIT] Error cerrando pista:',
                            err.message
                        );
                    });
                }
            }
        );

        await room.connect(
            credentials.url,
            credentials.token,
            {
                autoSubscribe: false,
                dynacast: true
            }
        );

        salasActivas.set(id, estado);

        estado.audioTask = publicarSilencioEnSala(
            estado,
            sdk
        ).catch(err => {
            console.error(
                '[LIVEKIT] Error manteniendo el micrófono:',
                err
            );
        });

        console.log(
            `✅ [LIVEKIT] Katherine conectada a ${id}. Identidad: ${estado.identity}`
        );

        return {
            ok: true,
            mensaje: 'Katherine está entrando a la llamada. Me conectaré como participante silenciosa.'
        };

    } catch (err) {

        console.error(
            '[LIVEKIT] Error de conexión:',
            err
        );

        if (room) {
            try {
                await room.disconnect();
            } catch (_) {}
        }

        await anarchyAPI(
            `/live-rooms/${encodeURIComponent(id)}/leave`,
            'POST',
            null,
            ACM_BOT_TOKEN
        );

        return {
            ok: false,
            mensaje: `No pude completar la conexión con LiveKit: ${err.message}`
        };
    }
}

// ---------------------------------------------------------------------------
// SALIR DE UNA LLAMADA
// ---------------------------------------------------------------------------

async function salirDeLlamada(threadId) {

    if (!ACM_BOT_TOKEN) {
        return {
            ok: false,
            mensaje: 'Falta el token de ACM para controlar las llamadas.'
        };
    }

    const id = String(threadId);

    const estado = salasActivas.get(id);

    const resultado = await anarchyAPI(
        `/live-rooms/${encodeURIComponent(id)}/leave`,
        'POST',
        null,
        ACM_BOT_TOKEN
    );

    if (estado) {

        estado.stopping = true;

        salasActivas.delete(id);

        try {
            if (estado.track) {
                await estado.track.close();
            }
        } catch (err) {
            console.warn(
                '[LIVEKIT] Error cerrando pista:',
                err.message
            );
        }

        try {
            await estado.room.disconnect();
        } catch (err) {
            console.warn(
                '[LIVEKIT] Error desconectando:',
                err.message
            );
        }
    }

    if (!resultado.ok && !estado) {
        return {
            ok: false,
            mensaje: `No pude confirmar la salida: ${JSON.stringify(resultado.data)}`
        };
    }

    return {
        ok: true,
        mensaje: 'Katherine ha salido de la llamada de este chat.'
    };
}

// ---------------------------------------------------------------------------
// CERRAR UNA SALA AL RECIBIR EVENTO
// ---------------------------------------------------------------------------

async function cerrarSalaPorEvento(threadId) {

    const id = String(threadId);

    const estado = salasActivas.get(id);

    if (!estado) return;

    estado.stopping = true;

    salasActivas.delete(id);

    try {
        if (estado.track) {
            await estado.track.close();
        }
    } catch (_) {}

    try {
        await estado.room.disconnect();
    } catch (_) {}

    console.log(
        `[LIVEKIT] Sesión de la sala ${id} cerrada.`
    );
}

// ============================================================================
// PROCESAMIENTO DE COMANDOS
// ============================================================================

async function procesarComando(evento, origenWebhook) {

    const threadId = String(evento.threadId || '');

    const mensaje = evento.message || {};

    const contenido =
        typeof mensaje.content === 'string'
            ? mensaje.content.trim()
            : '';

    const messageId = mensaje.messageId || null;

    const autor =
        mensaje.author?.nickname || 'Anónimo';

    if (!threadId || !contenido) {
        return;
    }

    // Evitar eventos duplicados.

    if (messageId) {

        const id = String(messageId);

        if (mensajesProcesados.has(id)) {
            console.log(
                `[WEBHOOK] Mensaje duplicado ignorado: ${id}`
            );
            return;
        }

        mensajesProcesados.add(id);

        if (mensajesProcesados.size > 5000) {
            mensajesProcesados.delete(
                mensajesProcesados.values().next().value
            );
        }
    }

    // Solo reconocer comandos con prefijo.

    if (
        !contenido.startsWith('!') &&
        !contenido.startsWith('-')
    ) {
        return;
    }

    const args = contenido.split(/\s+/);

    const comando = args[0].toLowerCase();

    const tokenRespuesta = tokenParaEvento(origenWebhook);

    console.log(
        `🎯 [COMANDO] ${comando} | Usuario: ${autor} | Thread: ${threadId}`
    );

    let respuesta = null;

    switch (comando) {

        // ====================================================================
        // AYUDA
        // ====================================================================

        case '!ayuda':
        case '-ayuda':
        case '!comandos':
        case '-comandos':

            respuesta =
                'KATHERINE — LISTA DE COMANDOS\n\n' +

                'GENERAL\n' +
                '!ayuda — Mostrar los comandos.\n' +
                '!ping — Comprobar conexión.\n' +
                '!uptime — Tiempo activa.\n' +
                '!estadochat — Estado de Katherine.\n' +
                '!hora — Hora de Chile.\n' +
                '!fecha — Fecha de Chile.\n\n' +

                'CURIOSIDADES Y JUEGOS\n' +
                '!curiosidad — Recibir una curiosidad aleatoria.\n' +
                '!dado — Lanzar un dado.\n' +
                '!moneda — Lanzar una moneda.\n' +
                '!8ball pregunta — Consultar la bola mágica.\n\n' +

                'LLAMADAS\n' +
                '!salas — Consultar salas activas.\n' +
                '!llamada! — Entrar a la llamada del chat.\n' +
                '!salirllamada! — Salir de la llamada.\n\n' +

                'CHAT Y MÚSICA\n' +
                '!leerchat — Activar indicador interno.\n' +
                '!noleerchat — Desactivar indicador interno.\n' +
                '!play canción — Registrar solicitud musical.\n' +
                '!volumen 0-100 — Ajustar volumen interno.';

            break;

        // ====================================================================
        // PING
        // ====================================================================

        case '!ping':
        case '-ping': {

            const inicio = Date.now();

            const comprobacion = await anarchyAPI(
                '/me',
                'GET',
                null,
                tokenRespuesta
            );

            const latencia = Date.now() - inicio;

            if (comprobacion.ok) {
                respuesta =
                    'PONG. Katherine está en línea.\n' +
                    `Latencia de API: ${latencia} ms.\n` +
                    `Servidor activo: ${tiempoActivo()}.`;
            } else {
                respuesta =
                    `Katherine está activa, pero la API respondió con HTTP ${comprobacion.status}.`;
            }

            break;
        }

        // ====================================================================
        // UPTIME
        // ====================================================================

        case '!uptime':
        case '-uptime':

            respuesta =
                `Katherine lleva activa ${tiempoActivo()}.`;

            break;

        // ====================================================================
        // HORA
        // ====================================================================

        case '!hora':
        case '-hora':

            respuesta =
                `La hora actual de Chile es ${obtenerHoraChile()}.`;

            break;

        // ====================================================================
        // FECHA
        // ====================================================================

        case '!fecha':
        case '-fecha':

            respuesta =
                `Hoy es ${obtenerFechaChile()}.`;

            break;

        // ====================================================================
        // CURIOSIDADES
        // ====================================================================

        case '!curiosidad':
        case '-curiosidad':

            respuesta =
                `¿Sabías que...? \n\n${obtenerCuriosidad()}`;

            break;

        // ====================================================================
        // DADO
        // ====================================================================

        case '!dado':
        case '-dado': {

            const resultado =
                Math.floor(Math.random() * 6) + 1;

            respuesta =
                `🎲 ${autor}, el dado cayó en ${resultado}.`;

            break;
        }

        // ====================================================================
        // MONEDA
        // ====================================================================

        case '!moneda':
        case '-moneda':

            respuesta =
                `🪙 La moneda cayó en ${Math.random() < 0.5 ? 'cara' : 'sello'}.`;

            break;

        // ====================================================================
        // BOLA MÁGICA
        // ====================================================================

        case '!8ball':
        case '-8ball': {

            const pregunta = args.slice(1).join(' ');

            respuesta = pregunta
                ? `🎱 ${elegirAleatorio(respuestas8Ball)}`
                : 'Escribe una pregunta después del comando. Ejemplo: !8ball ¿Tendré suerte hoy?';

            break;
        }

        // ====================================================================
        // ESTADO
        // ====================================================================

        case '!estadochat':
        case '-estadochat':

            respuesta =
                'KATHERINE — ESTADO\n\n' +
                `Lectura interna: ${estadoBot.lectura ? 'ACTIVADA' : 'DESACTIVADA'}\n` +
                `Llamada en este chat: ${salasActivas.has(threadId) ? 'CONECTADA' : 'DESCONECTADA'}\n` +
                `Salas conectadas: ${salasActivas.size}/3\n` +
                `Reproducción registrada: ${estadoBot.reproduciendo ? 'SÍ' : 'NO'}\n` +
                `Canción: ${estadoBot.cancionActual || 'Ninguna'}\n` +
                `Volumen interno: ${estadoBot.volumen}/100\n` +
                `Tiempo activa: ${tiempoActivo()}`;

            break;

        // ====================================================================
        // ACTIVAR LECTURA
        // ====================================================================

        case '!leerchat':
        case '-leerchat':

            estadoBot.lectura = true;

            respuesta =
                'Lectura interna activada. La recepción de mensajes también depende de los permisos de Anarchy.';

            break;

        // ====================================================================
        // DESACTIVAR LECTURA
        // ====================================================================

        case '!noleerchat':
        case '-noleerchat':

            estadoBot.lectura = false;

            respuesta =
                'Lectura interna desactivada.';

            break;

        // ====================================================================
        // VOLUMEN
        // ====================================================================

        case '!volumen':
        case '-volumen': {

            if (args.length < 2) {

                respuesta =
                    `El volumen interno actual es ${estadoBot.volumen}/100. Usa !volumen 0-100.`;

                break;
            }

            const volumen = Number(args[1]);

            if (
                !Number.isInteger(volumen) ||
                volumen < 0 ||
                volumen > 100
            ) {

                respuesta =
                    'Indica un número entero entre 0 y 100. Ejemplo: !volumen 75.';

            } else {

                estadoBot.volumen = volumen;

                respuesta =
                    `El volumen interno se ajustó a ${volumen}/100. Esto todavía no controla un reproductor de audio.`;
            }

            break;
        }

        // ====================================================================
        // PLAY
        // ====================================================================

        case '!play':
        case '-play': {

            const cancion = args.slice(1).join(' ');

            if (!cancion) {

                respuesta =
                    'Indica una canción. Ejemplo: !play Follow Me.';

            } else {

                estadoBot.reproduciendo = true;

                estadoBot.cancionActual = cancion;

                respuesta =
                    `Solicitud musical registrada: ${cancion}.\nLa reproducción de música necesita integrar un reproductor de audio.`;
            }

            break;
        }

        // ====================================================================
        // SALAS ACTIVAS
        // ====================================================================

        case '!salas':
        case '-salas': {

            const listado = await obtenerSalasEnVivo(true);

            if (!listado.ok) {

                respuesta =
                    `No pude consultar las salas. ${listado.error}`;

                break;
            }

            if (!listado.salas.length) {

                respuesta =
                    'No hay llamadas de voz o video activas en esta comunidad.';

                break;
            }

            const lineas = listado.salas
                .slice(0, 20)
                .map((sala, i) => {

                    const tipo =
                        sala.channelType === 'video'
                            ? 'Video'
                            : 'Voz';

                    return (
                        `${i + 1}. ${sala.title || 'Sala sin título'} — ${tipo} — ` +
                        `${sala.participantCount ?? 0} participante(s)` +
                        `${sala.joined ? ' — Katherine ya figura dentro' : ''}`
                    );
                });

            respuesta =
                `SALAS EN VIVO\n\n${lineas.join('\n')}\n\nUsa !llamada! desde el chat correspondiente para entrar.`;

            break;
        }

        // ====================================================================
        // ENTRAR A LLAMADA
        // ====================================================================

        case '!llamada!': {

            const resultado = await entrarLlamada(threadId);

            respuesta = resultado.mensaje;

            break;
        }

        // ====================================================================
        // SALIR DE LLAMADA
        // ====================================================================

        case '!salirllamada!': {

            const resultado = await salirDeLlamada(threadId);

            respuesta = resultado.mensaje;

            break;
        }

        // ====================================================================
        // COMANDO DESCONOCIDO
        // ====================================================================

        default:

            return;
    }

    // ========================================================================
    // PUBLICAR RESPUESTA
    // ========================================================================

    if (respuesta) {

        const resultado = await enviarRespuestaChat(
            threadId,
            respuesta,
            messageId,
            tokenRespuesta
        );

        if (!resultado.ok) {

            console.error(
                '❌ Katherine no pudo publicar la respuesta:',
                resultado.status,
                resultado.data
            );
        }
    }
}

// ============================================================================
// PROCESAMIENTO DE EVENTOS
// ============================================================================

async function procesarEvento(evento, origenWebhook) {

    console.log('\n-------------------------------------------------------');

    console.log(
        '⚡ [EVENTO ANARCHY]',
        evento.type,
        'Thread:',
        evento.threadId || 'sin thread'
    );

    if (evento.type === 'message') {

        await procesarComando(
            evento,
            origenWebhook
        );

        return;
    }

    if (evento.type === 'room.end') {

        await cerrarSalaPorEvento(
            evento.threadId
        );

        return;
    }

    if (evento.type === 'room.join') {

        console.log(
            `👤 Participante entró a la sala ${evento.threadId}.`
        );

        return;
    }

    if (evento.type === 'room.leave') {

        console.log(
            `👤 Participante salió de la sala ${evento.threadId}.`
        );

        return;
    }

    if (evento.type === 'room.speaker') {

        console.log(
            `🎙️ Cambio de rol de voz en la sala ${evento.threadId}.`
        );

        return;
    }

    if (evento.type === 'interaction') {

        console.log(
            '🔘 Interacción recibida:',
            evento.buttonId
        );

        return;
    }

    console.log(
        'ℹ️ Evento ignorado:',
        evento.type
    );
}

// ============================================================================
// WEBHOOK PRINCIPAL
// ============================================================================

app.post('/', (req, res) => {

    console.log('\n=======================================================');

    console.log(
        '⚡ [WEBHOOK] Petición recibida de Anarchy'
    );

    const validacion = validarFirmaWebhook(req);

    if (!validacion.ok) {

        console.warn(
            '⚠️ Webhook rechazado:',
            validacion.error
        );

        return res.status(401).send('Unauthorized');
    }

    console.log(
        '✅ Firma HMAC validada correctamente.'
    );

    // Respuesta inmediata a Anarchy.

    res.status(200).send('OK');

    // Procesar evento después de responder.

    const evento = req.body || {};

    procesarEvento(
        evento,
        validacion.source
    ).catch(err => {

        console.error(
            '💥 [ERROR PROCESANDO EVENTO]:',
            err
        );
    });
});

// ============================================================================
// HEALTH CHECK
// ============================================================================

app.get('/', (req, res) => {

    res.status(200).json({

        bot: 'Katherine',

        status: 'online',

        uptimeSeconds: Math.floor(
            (Date.now() - startTime) / 1000
        ),

        communityCallsEnabled: Boolean(
            ACM_BOT_TOKEN
        ),

        activeRooms: salasActivas.size,

        timestamp: new Date().toISOString()
    });
});

// ============================================================================
// APAGADO ORDENADO
// ============================================================================

let cerrando = false;

async function apagar(signal) {

    if (cerrando) return;

    cerrando = true;

    console.log(
        `🛑 [KATHERINE] Apagado solicitado: ${signal}`
    );

    const ids = [
        ...salasActivas.keys()
    ];

    for (const id of ids) {
        await salirDeLlamada(id);
    }

    process.exit(0);
}

process.on('SIGTERM', () => apagar('SIGTERM'));

process.on('SIGINT', () => apagar('SIGINT'));

// ============================================================================
// INICIO DEL SERVIDOR
// ============================================================================

app.listen(PORT, '0.0.0.0', () => {

    console.log('\n=======================================================');

    console.log(
        '       KATHERINE — ANARCHY BOT SYSTEM'
    );

    console.log('=======================================================');

    console.log(
        `[SERVER] Activo en puerto ${PORT}`
    );

    console.log(
        '[WEBHOOK] POST /'
    );

    console.log(
        '[HEALTH] GET /'
    );

    console.log(
        '[SECURITY] HMAC activado'
    );

    console.log(
        '[CHAT] BOT_TOKEN:',
        BOT_TOKEN ? 'CONFIGURADO' : 'AUSENTE'
    );

    console.log(
        '[ACM] Token:',
        ACM_BOT_TOKEN ? 'CONFIGURADO' : 'NO CONFIGURADO'
    );

    console.log(
        '[COMMANDS] !ayuda | !curiosidad | !llamada! | !salirllamada! | !salas'
    );

    console.log('=======================================================\n');

    consultarBotMe().catch(err => {

        console.error(
            '❌ Error en diagnóstico inicial:',
            err
        );
    });
});
