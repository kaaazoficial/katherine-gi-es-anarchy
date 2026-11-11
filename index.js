
'use strict';

const express = require('express');
const crypto = require('crypto');

// ============================================================================
// CONFIGURACIÓN
// ============================================================================

const app = express();
const API_BASE = 'https://api.anarchyapps.org/bot-api/v1';

const BOT_TOKEN = process.env.BOT_TOKEN;
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const PORT = Number(process.env.PORT) || 3000;

const startTime = Date.now();

if (!BOT_TOKEN) {
    throw new Error('[FATAL] Falta BOT_TOKEN.');
}

if (!WEBHOOK_SECRET) {
    throw new Error('[FATAL] Falta WEBHOOK_SECRET.');
}

// ============================================================================
// ESTADO DEL BOT
// ============================================================================

const estadoBot = {
    lectura: false,
    volumen: 30,
    reproduciendo: false,
    cancionActual: null
};

// Las zonas horarias se guardan por usuario durante la ejecución.
const zonasHorarias = new Map();
const mensajesProcesados = new Set();

// ============================================================================
// MIDDLEWARE
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
    let total = Math.floor((Date.now() - startTime) / 1000);

    const dias = Math.floor(total / 86400);
    total %= 86400;

    const horas = Math.floor(total / 3600);
    total %= 3600;

    const minutos = Math.floor(total / 60);
    const segundos = total % 60;

    const partes = [];

    if (dias) partes.push(`${dias} día(s)`);
    if (horas) partes.push(`${horas} hora(s)`);
    if (minutos) partes.push(`${minutos} minuto(s)`);

    partes.push(`${segundos} segundo(s)`);

    return partes.join(', ');
}

function elegirAleatorio(lista) {
    return lista[Math.floor(Math.random() * lista.length)];
}

function mezclar(lista) {
    const copia = [...lista];

    for (let i = copia.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [copia[i], copia[j]] = [copia[j], copia[i]];
    }

    return copia;
}

function claveUsuario(autor) {
    return String(
        autor?.uid ||
        autor?.nickname ||
        'usuario-anonimo'
    );
}

function normalizarTexto(texto) {
    return String(texto || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .trim();
}

// ============================================================================
// SISTEMA DE ZONAS HORARIAS PERSONALES
// ============================================================================

function validarZonaHoraria(zona) {
    try {
        new Intl.DateTimeFormat('es-CL', {
            timeZone: zona
        }).format(new Date());

        return true;
    } catch {
        return false;
    }
}

function obtenerZonaUsuario(autor) {
    return zonasHorarias.get(claveUsuario(autor));
}

function horaUsuario(zona) {
    return new Intl.DateTimeFormat('es-CL', {
        timeZone: zona,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
        timeZoneName: 'short'
    }).format(new Date());
}

function fechaUsuario(zona) {
    return new Intl.DateTimeFormat('es-CL', {
        timeZone: zona,
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric'
    }).format(new Date());
}

// ============================================================================
// GENSHIN IMPACT — CURIOSIDADES
// ============================================================================

const curiosidadesGenshin = [

    'Teyvat está organizado alrededor de siete naciones, cada una vinculada a un elemento y a una figura Arconte.',

    'Mondstadt está asociada con Anemo y el ideal de la libertad.',

    'Liyue está asociada con Geo y sus historias giran alrededor de los contratos.',

    'Inazuma está asociada con Electro y su ideal central es la eternidad.',

    'Sumeru está asociada con Dendro y el conocimiento.',

    'Fontaine está asociada con Hydro y la justicia.',

    'Natlan está asociada con Pyro y la guerra.',

    'Cyno es el General Mahamatra y supervisa las normas de la Akademiya.',

    'Nahida también es conocida como la Pequeña Señorita Kusanali y es la Arconte Dendro.',

    'Dehya es una mercenaria Eremita de Sumeru conocida como la Melena Llameante.',

    'Kaeya está vinculado al linaje Alberich y a los secretos de Khaenri’ah.',

    'Dainsleif es conocido por el título de Guardarramas.',

    'La catástrofe de Khaenri’ah ocurrió aproximadamente quinientos años antes de los acontecimientos principales.',

    'La Espiral del Abismo es un desafío independiente de la exploración normal.',

    'Las Visiones permiten a ciertos personajes canalizar poderes elementales.',

    'La Invocación de los Siete es el juego de cartas coleccionables que existe dentro de Teyvat.',

    'Los Fatui tienen once Heraldos, cada uno identificado por un título.',

    'Los Adeptus de Liyue son seres extraordinarios vinculados a la protección de la región.',

    'La Orden del Abismo y los Fatui son organizaciones diferentes.',

    'El Viajero puede resonar con distintos elementos al interactuar con las Estatuas de los Siete.'
];

// ============================================================================
// GENSHIN IMPACT — ELEMENTOS Y ARMAS
// ============================================================================

const elementos = [
    'Anemo',
    'Geo',
    'Electro',
    'Dendro',
    'Hydro',
    'Pyro',
    'Cryo'
];

const tiposArma = [
    'Espada ligera',
    'Mandoble',
    'Lanza',
    'Arco',
    'Catalizador'
];

const naciones = [
    'Mondstadt',
    'Liyue',
    'Inazuma',
    'Sumeru',
    'Fontaine',
    'Natlan',
    'Snezhnaya'
];

// ============================================================================
// GENSHIN IMPACT — PERSONAJES
// ============================================================================

const personajes = [

    { nombre: 'Cyno', elemento: 'Electro', arma: 'Lanza', nacion: 'Sumeru', titulo: 'General Mahamatra' },

    { nombre: 'Nahida', elemento: 'Dendro', arma: 'Catalizador', nacion: 'Sumeru', titulo: 'Pequeña Señorita Kusanali' },

    { nombre: 'Dehya', elemento: 'Pyro', arma: 'Mandoble', nacion: 'Sumeru', titulo: 'Melena Llameante' },

    { nombre: 'Alhaitham', elemento: 'Dendro', arma: 'Espada ligera', nacion: 'Sumeru', titulo: 'Escriba de la Akademiya' },

    { nombre: 'Tighnari', elemento: 'Dendro', arma: 'Arco', nacion: 'Sumeru', titulo: 'Guardabosques' },

    { nombre: 'Nilou', elemento: 'Hydro', arma: 'Espada ligera', nacion: 'Sumeru', titulo: 'Bailarina del Teatro Zubayr' },

    { nombre: 'Kaeya', elemento: 'Cryo', arma: 'Espada ligera', nacion: 'Mondstadt', titulo: 'Capitán de Caballería' },

    { nombre: 'Fischl', elemento: 'Electro', arma: 'Arco', nacion: 'Mondstadt', titulo: 'Princesa de la Condenación' },

    { nombre: 'Diluc', elemento: 'Pyro', arma: 'Mandoble', nacion: 'Mondstadt', titulo: 'Héroe de la Noche Oscura' },

    { nombre: 'Venti', elemento: 'Anemo', arma: 'Arco', nacion: 'Mondstadt', titulo: 'Bardo de Mondstadt' },

    { nombre: 'Zhongli', elemento: 'Geo', arma: 'Lanza', nacion: 'Liyue', titulo: 'Consultor de la Funeraria Wangsheng' },

    { nombre: 'Hu Tao', elemento: 'Pyro', arma: 'Lanza', nacion: 'Liyue', titulo: 'Directora de la Funeraria Wangsheng' },

    { nombre: 'Xiao', elemento: 'Anemo', arma: 'Lanza', nacion: 'Liyue', titulo: 'Conquistador de Demonios' },

    { nombre: 'Ganyu', elemento: 'Cryo', arma: 'Arco', nacion: 'Liyue', titulo: 'Secretaria del Pabellón Yuehai' },

    { nombre: 'Raiden Shogun', elemento: 'Electro', arma: 'Lanza', nacion: 'Inazuma', titulo: 'Arconte Electro' },

    { nombre: 'Kaedehara Kazuha', elemento: 'Anemo', arma: 'Espada ligera', nacion: 'Inazuma', titulo: 'Ronin errante' },

    { nombre: 'Kamisato Ayaka', elemento: 'Cryo', arma: 'Espada ligera', nacion: 'Inazuma', titulo: 'Princesa Garza' },

    { nombre: 'Furina', elemento: 'Hydro', arma: 'Espada ligera', nacion: 'Fontaine', titulo: 'Figura teatral de Fontaine' },

    { nombre: 'Neuvillette', elemento: 'Hydro', arma: 'Catalizador', nacion: 'Fontaine', titulo: 'Juez Supremo de Fontaine' },

    { nombre: 'Navia', elemento: 'Geo', arma: 'Mandoble', nacion: 'Fontaine', titulo: 'Presidenta de Spina di Rosula' },

    { nombre: 'Arlecchino', elemento: 'Pyro', arma: 'Lanza', nacion: 'Fontaine', titulo: 'La Sota de los Once Heraldos' },

    { nombre: 'Mavuika', elemento: 'Pyro', arma: 'Mandoble', nacion: 'Natlan', titulo: 'Arconte Pyro' },

    { nombre: 'Kinich', elemento: 'Dendro', arma: 'Mandoble', nacion: 'Natlan', titulo: 'Guerrero de Natlan' },

    { nombre: 'Xianyun', elemento: 'Anemo', arma: 'Catalizador', nacion: 'Liyue', titulo: 'Retenedora de Nubes' },

    { nombre: 'Yelan', elemento: 'Hydro', arma: 'Arco', nacion: 'Liyue', titulo: 'Agente de inteligencia' }

];

function obtenerPersonaje(nombre) {
    const buscado = normalizarTexto(nombre);

    return personajes.find(
        p => normalizarTexto(p.nombre) === buscado
    );
}

function fichaPersonaje(p) {
    return (
        `${p.nombre}\n` +
        `Elemento: ${p.elemento}\n` +
        `Arma: ${p.arma}\n` +
        `Nación: ${p.nacion}\n` +
        `Título: ${p.titulo}`
    );
}

// ============================================================================
// GENSHIN IMPACT — REACCIONES ELEMENTALES
// ============================================================================

function obtenerReaccion(elemento1, elemento2) {

    const alias = {
        pyro: 'Pyro',
        fuego: 'Pyro',

        hydro: 'Hydro',
        agua: 'Hydro',

        electro: 'Electro',
        rayo: 'Electro',

        cryo: 'Cryo',
        hielo: 'Cryo',

        dendro: 'Dendro',
        naturaleza: 'Dendro',

        anemo: 'Anemo',
        viento: 'Anemo',

        geo: 'Geo',
        tierra: 'Geo'
    };

    const a = alias[normalizarTexto(elemento1)];
    const b = alias[normalizarTexto(elemento2)];

    if (!a || !b) return null;

    if (a === b) {
        return {
            a,
            b,
            nombre: 'No se produce una reacción entre elementos iguales.'
        };
    }

    const clave = [a, b].sort().join('|');

    const reacciones = {

        'Hydro|Pyro': 'Vaporizado',

        'Cryo|Pyro': 'Derretido',

        'Cryo|Hydro': 'Congelado',

        'Electro|Hydro': 'Electrocargado',

        'Electro|Pyro': 'Sobrecargado',

        'Cryo|Electro': 'Superconductor',

        'Dendro|Electro': 'Aceleración (Quicken)',

        'Dendro|Hydro': 'Florecimiento',

        'Dendro|Pyro': 'Quemadura',

        'Anemo|Cryo': 'Torbellino',

        'Anemo|Electro': 'Torbellino',

        'Anemo|Hydro': 'Torbellino',

        'Anemo|Pyro': 'Torbellino',

        'Cryo|Geo': 'Cristalización',

        'Electro|Geo': 'Cristalización',

        'Geo|Hydro': 'Cristalización',

        'Geo|Pyro': 'Cristalización'
    };

    return {
        a,
        b,
        nombre: reacciones[clave] ||
            'No existe una reacción elemental clásica directa entre estos elementos.'
    };
}

// ============================================================================
// GENSHIN IMPACT — ARTEFACTOS Y MISIONES
// ============================================================================

const artefactos = [
    'Bruja Carmesí en Llamas',
    'Sombra Verde Esmeralda',
    'Emblema del Destino',
    'Recuerdos del Bosque',
    'Sueños Áureos',
    'Ritual Antiguo de la Nobleza',
    'Nómada del Invierno',
    'Furia del Trueno',
    'Final del Gladiador',
    'Orquesta del Errante'
];

const encargosAventura = [
    'Investiga unas huellas extrañas cerca de una antigua ruina.',
    'Ayuda a un comerciante que perdió su mercancía en el camino.',
    'Derrota a un grupo de Hilichurls que bloquea una ruta comercial.',
    'Recoge ingredientes para preparar una receta de Teyvat.',
    'Explora una zona desconocida y registra cualquier hallazgo.',
    'Busca un cofre oculto en las cercanías de un dominio.',
    'Acompaña a un aventurero durante una expedición peligrosa.'
];

// ============================================================================
// CLIENTE HTTP ANARCHY
// ============================================================================

async function anarchyAPI(endpoint, method = 'GET', body = null) {

    try {

        const response = await fetch(
            `${API_BASE}${endpoint}`,
            {
                method,

                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'Authorization': `Bot ${BOT_TOKEN}`
                },

                body: body === null
                    ? undefined
                    : JSON.stringify(body),

                signal: AbortSignal.timeout(12000)
            }
        );

        const raw = await response.text();

        let data = {};

        try {
            data = raw ? JSON.parse(raw) : {};
        } catch {
            data = { raw };
        }

        console.log(
            `[API] ${method} ${endpoint} -> ${response.status}`,
            data
        );

        return {
            ok: response.ok,
            status: response.status,
            data
        };

    } catch (err) {

        console.error('[API ERROR]', err.message);

        return {
            ok: false,
            status: 500,
            data: { error: err.message }
        };
    }
}

// ============================================================================
// DIAGNÓSTICO
// ============================================================================

async function consultarBotMe() {

    const resultado = await anarchyAPI('/me');

    if (resultado.ok) {

        console.log(
            '[BOT ME]',
            JSON.stringify(resultado.data, null, 2)
        );

        const threads = resultado.data?.threadIds || [];

        console.log(
            '[CHATS REGISTRADOS]',
            threads.length ? threads : 'Ninguno'
        );

    } else {

        console.error(
            '[BOT ME ERROR]',
            resultado.status,
            resultado.data
        );
    }
}

// ============================================================================
// COLA DE MENSAJES Y LÍMITES
// ============================================================================

let colaEnvios = Promise.resolve();

let ultimoEnvioGlobal = 0;

const ultimoEnvioChat = new Map();

const historialGlobal = [];

const historialChats = new Map();

function limpiarHistorial(lista, ventanaMs) {

    const limite = Date.now() - ventanaMs;

    while (lista.length && lista[0] < limite) {
        lista.shift();
    }
}

function enviarMensaje(threadId, texto, replyMessageId = null) {

    const id = String(threadId || '');

    const content = limpiarMensaje(texto);

    if (!id || !content) {
        return Promise.resolve({
            ok: false,
            status: 400,
            data: { error: 'Falta threadId o contenido.' }
        });
    }

    const tarea = colaEnvios.then(async () => {

        const ahora = Date.now();

        const esperaGlobal = Math.max(
            0,
            1000 - (ahora - ultimoEnvioGlobal)
        );

        const anteriorChat = ultimoEnvioChat.get(id) || 0;

        const esperaChat = Math.max(
            0,
            2000 - (ahora - anteriorChat)
        );

        await dormir(
            Math.max(esperaGlobal, esperaChat)
        );

        limpiarHistorial(
            historialGlobal,
            60 * 60 * 1000
        );

        const historialChat =
            historialChats.get(id) || [];

        limpiarHistorial(
            historialChat,
            10 * 60 * 1000
        );

        historialChats.set(id, historialChat);

        if (historialGlobal.length >= 300) {
            return {
                ok: false,
                status: 429,
                data: { error: 'Límite horario del bot alcanzado.' }
            };
        }

        if (historialChat.length >= 60) {
            return {
                ok: false,
                status: 429,
                data: { error: 'Límite de mensajes de este chat alcanzado.' }
            };
        }

        const payload = { content };

        if (replyMessageId) {
            payload.replyMessageId = String(replyMessageId);
        }

        const resultado = await anarchyAPI(
            `/threads/${encodeURIComponent(id)}/messages`,
            'POST',
            payload
        );

        if (resultado.ok) {

            ultimoEnvioGlobal = Date.now();

            ultimoEnvioChat.set(
                id,
                ultimoEnvioGlobal
            );

            historialGlobal.push(ultimoEnvioGlobal);

            historialChat.push(ultimoEnvioGlobal);
        }

        return resultado;
    });

    colaEnvios = tarea.catch(err => {
        console.error('[COLA ERROR]', err);
    });

    return tarea;
}

// ============================================================================
// PROCESADOR DE COMANDOS
// ============================================================================

async function procesarMensaje(evento) {

    const threadId = String(evento.threadId || '');

    const mensaje = evento.message || {};

    const texto =
        typeof mensaje.content === 'string'
            ? mensaje.content.trim()
            : '';

    const messageId = mensaje.messageId || null;

    const autor = mensaje.author || {};

    const nickname = autor.nickname || 'Viajero';

    if (!threadId || !texto) return;

    // Evitar eventos duplicados.

    if (messageId) {

        const idMensaje = String(messageId);

        if (mensajesProcesados.has(idMensaje)) {
            return;
        }

        mensajesProcesados.add(idMensaje);

        if (mensajesProcesados.size > 5000) {
            mensajesProcesados.delete(
                mensajesProcesados.values().next().value
            );
        }
    }

    if (
        !texto.startsWith('!') &&
        !texto.startsWith('-')
    ) {
        return;
    }

    const partes = texto.split(/\s+/);

    const comando = partes[0].toLowerCase();

    const argumentos = partes.slice(1);

    let respuesta = null;

    console.log(
        `[COMANDO] ${comando} | ${nickname} | ${threadId}`
    );

    switch (comando) {

        // ================================================================
        // AYUDA
        // ================================================================

        case '!ayuda':
        case '-ayuda':
        case '!comandos':
        case '-comandos':

            respuesta =
                'KATHERINE — COMANDOS\n\n' +

                'GENSHIN IMPACT\n' +
                '!curiosidad — Curiosidad de Genshin.\n' +
                '!personaje [nombre] — Personaje aleatorio o ficha.\n' +
                '!equipo — Equipo aleatorio de cuatro personajes.\n' +
                '!elemento — Elemento aleatorio.\n' +
                '!vision — Visión aleatoria.\n' +
                '!arma — Tipo de arma aleatorio.\n' +
                '!nacion — Nación aleatoria.\n' +
                '!reaccion elemento elemento — Consultar reacción.\n' +
                '!artefacto — Set de artefactos aleatorio.\n' +
                '!comision — Encargo de aventura.\n' +
                '!expedicion — Proponer una expedición.\n' +
                '!lore — Dato del lore.\n\n' +

                'HORA Y FECHA\n' +
                '!zona America/Santiago — Guardar tu zona horaria.\n' +
                '!hora — Tu hora local.\n' +
                '!fecha — Tu fecha local.\n\n' +

                'JUEGOS Y UTILIDADES\n' +
                '!ping — Estado de conexión.\n' +
                '!uptime — Tiempo activo.\n' +
                '!dado — Lanzar un dado.\n' +
                '!moneda — Lanzar una moneda.\n' +
                '!8ball pregunta — Consultar la bola mágica.\n' +
                '!estadochat — Estado del bot.\n\n' +

                'LLAMADAS Y CHAT\n' +
                '!llamada! — Consultar acceso a llamadas.\n' +
                '!salas — Consultar disponibilidad de llamadas.\n' +
                '!leerchat / !noleerchat — Cambiar estado interno.\n' +
                '!play canción — Registrar solicitud musical.\n' +
                '!volumen 0-100 — Ajustar volumen interno.';

            break;

        // ================================================================
        // ZONA HORARIA
        // ================================================================

        case '!zona':
        case '-zona': {

            const key = claveUsuario(autor);

            const valor = argumentos.join(' ').trim();

            if (!valor) {

                const actual = zonasHorarias.get(key);

                respuesta = actual
                    ? `Tu zona horaria guardada es ${actual}. Puedes cambiarla con !zona America/Santiago o eliminarla con !zona borrar.`
                    : 'Todavía no configuraste tu zona horaria. Escríbela así: !zona America/Santiago. Ejemplos: America/Bogota, America/Lima, America/Mexico_City, Europe/Madrid.';

            } else if (normalizarTexto(valor) === 'borrar') {

                zonasHorarias.delete(key);

                respuesta =
                    'Tu zona horaria fue eliminada. Configúrala nuevamente con !zona America/Santiago.';

            } else if (!validarZonaHoraria(valor)) {

                respuesta =
                    'No reconozco esa zona horaria. Usa un identificador IANA, por ejemplo America/Santiago o Europe/Madrid.';

            } else {

                zonasHorarias.set(key, valor);

                respuesta =
                    `Listo, ${nickname}. Guardé tu zona horaria: ${valor}. Ahora !hora y !fecha mostrarán tu hora y fecha locales.`;
            }

            break;
        }

        // ================================================================
        // HORA LOCAL DEL USUARIO
        // ================================================================

        case '!hora':
        case '-hora': {

            const zona = obtenerZonaUsuario(autor);

            respuesta = zona
                ? `La hora local de ${nickname} es ${horaUsuario(zona)}. Zona horaria: ${zona}.`
                : `${nickname}, primero configura tu zona horaria con !zona America/Santiago. Anarchy no me entrega automáticamente la zona horaria de cada usuario.`;

            break;
        }

        // ================================================================
        // FECHA LOCAL DEL USUARIO
        // ================================================================

        case '!fecha':
        case '-fecha': {

            const zona = obtenerZonaUsuario(autor);

            respuesta = zona
                ? `La fecha local de ${nickname} es ${fechaUsuario(zona)}. Zona horaria: ${zona}.`
                : `${nickname}, primero configura tu zona horaria con !zona America/Santiago. Así podré mostrar tu fecha local correctamente.`;

            break;
        }

        // ================================================================
        // CURIOSIDADES GENSHIN
        // ================================================================

        case '!curiosidad':
        case '-curiosidad':

            respuesta =
                `¿Sabías que...?\n\n${elegirAleatorio(curiosidadesGenshin)}`;

            break;

        case '!lore':
        case '-lore':

            respuesta =
                `REGISTROS DE TEYVAT\n\n${elegirAleatorio(curiosidadesGenshin)}`;

            break;

        // ================================================================
        // PERSONAJES
        // ================================================================

        case '!personaje':
        case '-personaje': {

            if (argumentos.length) {

                const p = obtenerPersonaje(
                    argumentos.join(' ')
                );

                respuesta = p
                    ? fichaPersonaje(p)
                    : `No tengo una ficha para ${argumentos.join(' ')} en mi lista actual. Prueba con !personaje para elegir uno al azar.`;

            } else {

                respuesta =
                    `PERSONAJE ALEATORIO\n\n${fichaPersonaje(elegirAleatorio(personajes))}`;
            }

            break;
        }

        // ================================================================
        // EQUIPO ALEATORIO
        // ================================================================

        case '!equipo':
        case '-equipo': {

            const equipo = mezclar(personajes).slice(0, 4);

            respuesta =
                'EQUIPO ALEATORIO\n\n' +
                equipo.map((p, i) =>
                    `${i + 1}. ${p.nombre} — ${p.elemento} — ${p.arma}`
                ).join('\n') +
                '\n\nEquipo generado al azar; no representa una recomendación de meta.';

            break;
        }

        // ================================================================
        // ELEMENTO Y VISIÓN
        // ================================================================

        case '!elemento':
        case '-elemento':
        case '!vision':
        case '-vision':

            respuesta =
                `Tu elemento/Visión aleatoria es ${elegirAleatorio(elementos)}.`;

            break;

        // ================================================================
        // ARMAS
        // ================================================================

        case '!arma':
        case '-arma':

            respuesta =
                `Tu tipo de arma aleatorio es: ${elegirAleatorio(tiposArma)}.`;

            break;

        // ================================================================
        // NACIONES
        // ================================================================

        case '!nacion':
        case '-nacion':
        case '!region':
        case '-region':

            respuesta =
                `Tu destino en Teyvat será: ${elegirAleatorio(naciones)}.`;

            break;

        // ================================================================
        // REACCIONES ELEMENTALES
        // ================================================================

        case '!reaccion':
        case '-reaccion': {

            if (argumentos.length < 2) {

                respuesta =
                    'Indica dos elementos. Ejemplo: !reaccion pyro hydro. También puedes usar fuego, agua, rayo, hielo, viento o tierra.';

                break;
            }

            const reaccion = obtenerReaccion(
                argumentos[0],
                argumentos[1]
            );

            respuesta = reaccion
                ? `Reacción entre ${reaccion.a} y ${reaccion.b}: ${reaccion.nombre}.`
                : 'No reconocí esos elementos. Usa Anemo, Geo, Electro, Dendro, Hydro, Pyro o Cryo.';

            break;
        }

        // ================================================================
        // ARTEFACTOS
        // ================================================================

        case '!artefacto':
        case '-artefacto':

            respuesta =
                `Set de artefactos elegido para tu próxima expedición: ${elegirAleatorio(artefactos)}.`;

            break;

        // ================================================================
        // COMISIONES
        // ================================================================

        case '!comision':
        case '-comision':

            respuesta =
                'NUEVO ENCARGO DEL GREMIO\n\n' +
                elegirAleatorio(encargosAventura) +
                '\n\nEste es un encargo ficticio para diversión y roleplay.';

            break;

        // ================================================================
        // EXPEDICIONES
        // ================================================================

        case '!expedicion':
        case '-expedicion':

            respuesta =
                'EXPEDICIÓN ASIGNADA\n\n' +
                `Destino: ${elegirAleatorio(naciones)}.\n` +
                `Objetivo: ${elegirAleatorio(encargosAventura)}\n\n` +
                '¡Que los Siete guíen tu camino!';

            break;

        // ================================================================
        // PING
        // ================================================================

        case '!ping':
        case '-ping': {

            const inicio = Date.now();

            const comprobacion = await anarchyAPI('/me');

            const latencia = Date.now() - inicio;

            respuesta = comprobacion.ok
                ? `PONG, ${nickname}. Katherine está en línea.\nLatencia de API: ${latencia} ms.\nTiempo activa: ${tiempoActivo()}.`
                : `Katherine está activa, pero la API respondió con HTTP ${comprobacion.status}.`;

            break;
        }

        // ================================================================
        // UPTIME
        // ================================================================

        case '!uptime':
        case '-uptime':

            respuesta =
                `Katherine lleva activa ${tiempoActivo()}.`;

            break;

        // ================================================================
        // DADO
        // ================================================================

        case '!dado':
        case '-dado':

            respuesta =
                `🎲 ${nickname}, el dado cayó en ${Math.floor(Math.random() * 20) + 1} (d20).`;

            break;

        // ================================================================
        // MONEDA
        // ================================================================

        case '!moneda':
        case '-moneda':

            respuesta =
                `🪙 La moneda cayó en ${Math.random() < 0.5 ? 'cara' : 'sello'}.`;

            break;

        // ================================================================
        // BOLA MÁGICA
        // ================================================================

        case '!8ball':
        case '-8ball': {

            if (!argumentos.length) {

                respuesta =
                    'Hazme una pregunta. Ejemplo: !8ball ¿Conseguiré un personaje de cinco estrellas?';

            } else {

                const respuestas = [
                    'Los presagios son favorables.',
                    'El destino apunta a que sí.',
                    'No parece probable.',
                    'La respuesta aún no está escrita.',
                    'Consulta nuevamente después de tu próxima aventura.',
                    'Los Siete no han revelado esa respuesta.'
                ];

                respuesta =
                    `La bola mágica responde: ${elegirAleatorio(respuestas)}`;
            }

            break;
        }

        // ================================================================
        // ESTADO
        // ================================================================

        case '!estadochat':
        case '-estadochat':

            respuesta =
                'KATHERINE — ESTADO DEL SISTEMA\n\n' +
                `Lectura interna: ${estadoBot.lectura ? 'Activada' : 'Desactivada'}\n` +
                `Reproducción registrada: ${estadoBot.reproduciendo ? 'Sí' : 'No'}\n` +
                `Canción: ${estadoBot.cancionActual || 'Ninguna'}\n` +
                `Volumen interno: ${estadoBot.volumen}/100\n` +
                `Tiempo activa: ${tiempoActivo()}`;

            break;

        // ================================================================
        // LECTURA
        // ================================================================

        case '!leerchat':
        case '-leerchat':

            estadoBot.lectura = true;

            respuesta =
                'Indicador interno de lectura activado. La recepción de todos los mensajes depende del permiso correspondiente en Anarchy.';

            break;

        case '!noleerchat':
        case '-noleerchat':

            estadoBot.lectura = false;

            respuesta =
                'Indicador interno de lectura desactivado.';

            break;

        // ================================================================
        // MÚSICA
        // ================================================================

        case '!play':
        case '-play': {

            const cancion = argumentos.join(' ');

            if (!cancion) {

                respuesta =
                    'Indica una canción. Ejemplo: !play canción.';

            } else {

                estadoBot.reproduciendo = true;
                estadoBot.cancionActual = cancion;

                respuesta =
                    `Solicitud musical registrada: ${cancion}. La reproducción real requiere integrar un reproductor de audio.`;
            }

            break;
        }

        // ================================================================
        // VOLUMEN
        // ================================================================

        case '!volumen':
        case '-volumen': {

            const valor = Number(argumentos[0]);

            if (
                !Number.isInteger(valor) ||
                valor < 0 ||
                valor > 100
            ) {

                respuesta =
                    `Volumen interno actual: ${estadoBot.volumen}/100. Usa !volumen 0-100.`;

            } else {

                estadoBot.volumen = valor;

                respuesta =
                    `Volumen interno ajustado a ${valor}/100. Esto no controla un reproductor de audio todavía.`;
            }

            break;
        }

        // ================================================================
        // LLAMADAS
        // ================================================================

        case '!llamada!':

            respuesta =
                'Para entrar a una llamada, Anarchy exige el token del Bot de la comunidad creado en ACM. El BOT_TOKEN de Desarrolladores no autoriza las rutas live-rooms. No intentaré conectarme con una credencial incorrecta.';

            break;

        case '!salas':
        case '-salas':

            respuesta =
                'La consulta de salas activas requiere el token del Bot de la comunidad de ACM. Con el token normal de Desarrolladores no está disponible.';

            break;

        case '!salirllamada!':

            respuesta =
                'La salida de una llamada también requiere el token del Bot de la comunidad de ACM.';

            break;

        default:
            return;
    }

    // PUBLICAR RESPUESTA

    if (respuesta) {

        const resultado = await enviarMensaje(
            threadId,
            respuesta,
            messageId
        );

        if (!resultado.ok) {

            console.error(
                '[ERROR RESPUESTA]',
                resultado.status,
                resultado.data
            );
        }
    }
}

// ============================================================================
// EVENTOS DE SALAS
// ============================================================================

async function procesarEventoSala(evento) {

    const threadId = String(evento.threadId || '');

    if (!threadId) return;

    if (evento.type === 'room.join') {

        console.log(
            `[SALA] Entró ${evento.user?.nickname || 'un usuario'} a la sala ${threadId}.`
        );

        await enviarMensaje(
            threadId,
            '¡Ad Astra Abbysosque! Bienvenido al chat.'
        );

        return;
    }

    if (evento.type === 'room.leave') {

        console.log(
            `[SALA] Salió ${evento.user?.nickname || 'un usuario'} de la sala ${threadId}.`
        );

        await enviarMensaje(
            threadId,
            'Buen viaje, el Gremio de Aventureros espera tu regreso'
        );

        return;
    }

    if (evento.type === 'room.end') {

        console.log(
            `[SALA] La llamada del chat ${threadId} terminó.`
        );
    }
}

// ============================================================================
// VALIDACIÓN HMAC
// ============================================================================

function validarWebhook(req) {

    const timestamp =
        req.headers['x-anarchy-bot-timestamp'];

    const signatureHeader =
        req.headers['x-anarchy-bot-signature'];

    if (
        !timestamp ||
        !signatureHeader ||
        typeof req.rawBody !== 'string'
    ) {
        return {
            ok: false,
            error: 'Faltan encabezados o cuerpo original.'
        };
    }

    const segundos = Number(timestamp);

    if (
        !Number.isFinite(segundos) ||
        Math.abs(Date.now() / 1000 - segundos) > 300
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
            error: 'Formato de firma inválido.'
        };
    }

    const expected = crypto
        .createHmac('sha256', WEBHOOK_SECRET)
        .update(`${timestamp}.${req.rawBody}`)
        .digest();

    const received = Buffer.from(signature, 'hex');

    if (
        received.length !== expected.length ||
        !crypto.timingSafeEqual(received, expected)
    ) {
        return {
            ok: false,
            error: 'La firma HMAC no coincide.'
        };
    }

    return { ok: true };
}

// ============================================================================
// WEBHOOK PRINCIPAL
// ============================================================================

app.post('/', (req, res) => {

    console.log('\n[WEBHOOK] Petición recibida de Anarchy.');

    const validacion = validarWebhook(req);

    if (!validacion.ok) {

        console.warn(
            '[WEBHOOK RECHAZADO]',
            validacion.error
        );

        return res.status(401).send('Unauthorized');
    }

    console.log('[WEBHOOK] Firma validada.');

    // Responder inmediatamente a Anarchy.

    res.status(200).send('OK');

    const evento = req.body || {};

    Promise.resolve().then(async () => {

        if (evento.type === 'message') {

            await procesarMensaje(evento);

        } else if (
            evento.type === 'room.join' ||
            evento.type === 'room.leave' ||
            evento.type === 'room.end'
        ) {

            await procesarEventoSala(evento);

        } else if (evento.type === 'interaction') {

            console.log(
                '[INTERACTION]',
                evento.buttonId,
                evento.user?.nickname
            );

        } else {

            console.log(
                '[EVENTO IGNORADO]',
                evento.type
            );
        }

    }).catch(err => {

        console.error(
            '[ERROR PROCESANDO EVENTO]',
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

        timezoneUsersConfigured: zonasHorarias.size,

        timestamp: new Date().toISOString()
    });
});

// ============================================================================
// INICIO DEL SERVIDOR
// ============================================================================

app.listen(PORT, '0.0.0.0', () => {

    console.log('=======================================================');

    console.log('KATHERINE — ANARCHY BOT');

    console.log(`Servidor activo en puerto ${PORT}`);

    console.log('Webhook: POST /');

    console.log('Health check: GET /');

    console.log('Autenticación HMAC: activada');

    console.log('Bot token: configurado');

    console.log('Curiosidades: Genshin Impact');

    console.log('Comando de zona horaria: !zona');

    console.log('=======================================================');

    consultarBotMe().catch(err => {
        console.error('[DIAGNÓSTICO]', err);
    });
});

