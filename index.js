
'use strict';

const express = require('express');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

// ============================================================================
// KATHERINE — CONFIGURACIÓN
// ============================================================================

const app = express();

const API_BASE = 'https://api.anarchyapps.org/bot-api/v1';

const GAME_API = 'https://genshin-db-api.vercel.app/api/v5';

const GITHUB_TREE =
    'https://api.github.com/repos/Genshin-Impact-Helper-Team/genshin-builds/git/trees/main?recursive=1';

const GITHUB_RAW =
    'https://raw.githubusercontent.com/Genshin-Impact-Helper-Team/genshin-builds/main/';

const BOT_TOKEN = process.env.BOT_TOKEN;
const COMMUNITY_BOT_TOKEN = process.env.COMMUNITY_BOT_TOKEN;

const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET;
const COMMUNITY_WEBHOOK_SECRET = process.env.COMMUNITY_WEBHOOK_SECRET;

const PORT = Number(process.env.PORT) || 3000;

const startTime = Date.now();

if (!BOT_TOKEN) {
    throw new Error('Falta BOT_TOKEN.');
}

if (!COMMUNITY_BOT_TOKEN) {
    console.warn('ACM no configurado. Las llamadas estarán desactivadas.');
}

if (!WEBHOOK_SECRET) {
    throw new Error('Falta WEBHOOK_SECRET.');
}

if (!COMMUNITY_WEBHOOK_SECRET) {
    console.warn('Falta COMMUNITY_WEBHOOK_SECRET. No se recibirán eventos firmados de ACM.');
}

// ============================================================================
// ESTADO GLOBAL
// ============================================================================

const estado = {
    developerBotUid: null,
    communityBotUid: null,

    conectado: false,

    lectura: false,

    salas: new Map(),

    zonas: new Map(),

    usuarios: new Map(),

    encuestas: new Map(),

    trivia: new Map(),

    juegos: new Map(),

    deseos: new Map(),

    puntos: new Map()
};

let eventQueue = Promise.resolve();
let messageQueue = Promise.resolve();

const processedMessages = new Set();

const lastGlobalMessage = new Map();
const lastChatMessage = new Map();

const apiCache = new Map();

let liveRoomsCache = null;
let liveRoomsCacheUntil = 0;
let liveRoomsLastRequest = 0;
let liveRoomsRequests = [];
let liveRoomsRequest = null;

let buildTreeCache = null;
let buildTreeCacheUntil = 0;
let buildTreeRequest = null;

const characterCache = new Map();

// ============================================================================
// MIDDLEWARE
// ============================================================================

app.use(express.json({
    limit: '2mb',

    verify: (req, res, buffer) => {
        req.rawBody = buffer.toString('utf8');
    }
}));

// ============================================================================
// UTILIDADES
// ============================================================================

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function cleanText(text) {
    return String(text ?? '')
        .replace(/[＊*`]/g, '')
        .replace(/\r/g, '')
        .trim()
        .slice(0, 2000);
}

function normalize(text) {
    return String(text ?? '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .trim();
}

function slugify(text) {
    return normalize(text)
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '');
}

function randomItem(array) {
    return array[Math.floor(Math.random() * array.length)];
}

function shuffle(array) {
    const result = [...array];

    for (let i = result.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));

        [result[i], result[j]] = [result[j], result[i]];
    }

    return result;
}

function getUserId(author) {
    return String(author?.uid || author?.nickname || 'anonymous');
}

function getUptime() {
    let seconds = Math.floor((Date.now() - startTime) / 1000);

    const days = Math.floor(seconds / 86400);
    seconds %= 86400;

    const hours = Math.floor(seconds / 3600);
    seconds %= 3600;

    const minutes = Math.floor(seconds / 60);
    seconds %= 60;

    const parts = [];

    if (days) parts.push(`${days} días`);
    if (hours) parts.push(`${hours} horas`);
    if (minutes) parts.push(`${minutes} minutos`);

    parts.push(`${seconds} segundos`);

    return parts.join(', ');
}

function localDate(timezone) {
    return new Intl.DateTimeFormat('es', {
        timeZone: timezone,
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric'
    }).format(new Date());
}

function validTimezone(timezone) {
    try {
        new Intl.DateTimeFormat('es', {
            timeZone: timezone
        }).format(new Date());

        return true;
    } catch {
        return false;
    }
}

function splitMessage(text, limit = 1800) {
    const lines = String(text).split('\n');

    const chunks = [];

    let current = '';

    for (const line of lines) {

        if (line.length > limit) {

            if (current) {
                chunks.push(current);
                current = '';
            }

            for (let i = 0; i < line.length; i += limit) {
                chunks.push(line.slice(i, i + limit));
            }

            continue;
        }

        const candidate = current
            ? `${current}\n${line}`
            : line;

        if (candidate.length > limit) {
            chunks.push(current);
            current = line;
        } else {
            current = candidate;
        }
    }

    if (current) {
        chunks.push(current);
    }

    return chunks;
}

// ============================================================================
// CURIOSIDADES — SISTEMA SIN REPETICIONES
// ============================================================================

const curiosities = [

    'Los Tesoros del Abismo y las fuerzas de la Orden del Abismo tienen objetivos distintos a los de los Fatui.',

    'Cyno es el General Mahamatra y tiene autoridad para perseguir infracciones dentro de la Akademiya.',

    'Nahida es conocida como la Pequeña Señorita Kusanali y su nombre de Arconte es Buer.',

    'Dehya recibe el título de Melena Llameante y pertenece a los Eremitas.',

    'Alhaitham ocupa el cargo de escriba de la Akademiya de Sumeru.',

    'Tighnari trabaja como guardabosques y conoce profundamente la flora de la selva de Sumeru.',

    'Nilou es una bailarina del Teatro Zubayr.',

    'Kaeya tiene una relación familiar con el linaje Alberich de Khaenri’ah.',

    'Dainsleif es conocido como el Guardarramas.',

    'La catástrofe de Khaenri’ah ocurrió aproximadamente quinientos años antes del comienzo de la aventura.',

    'Los Once Heraldos Fatui ocupan una posición de gran autoridad en Snezhnaya.',

    'La Espiral del Abismo es un desafío de combate separado de la exploración habitual.',

    'El Viajero puede resonar con diferentes elementos mediante las Estatuas de los Siete.',

    'La Invocación de los Siete es el juego de cartas coleccionables de Teyvat.',

    'Los Adeptus están profundamente vinculados a la historia y protección de Liyue.',

    'Dvalin es conocido como Stormterror y forma parte de los Cuatro Vientos de Mondstadt.',

    'Andrius es conocido como Lupus Boreas y está relacionado con los Cuatro Vientos.',

    'Los melusinos son una especie característica de Fontaine.',

    'Los Arcontes y los Dragones Soberanos tienen orígenes y funciones diferentes.',

    'La Akademiya ha sido durante generaciones uno de los centros de conocimiento más importantes de Teyvat.',

    'La Casa de la Hoguera está vinculada a los asuntos de los Fatui y al Heraldo conocido como la Sota.',

    'Arlecchino tiene el título de Padre dentro de la Casa de la Hoguera.',

    'La Funeraria Wangsheng es una institución histórica de Liyue dirigida por Hu Tao.',

    'El Abismo tiene una profunda conexión con los acontecimientos de Khaenri’ah.',

    'Enkanomiya es una región subterránea situada bajo la isla Watatsumi.',

    'Dragonspine es una zona montañosa de Mondstadt con una historia antigua y una mecánica de frío extremo.',

    'El Abismo y las Profundidades de Teyvat aparecen frecuentemente relacionados con secretos de la historia antigua.',

    'El Dendro representa uno de los elementos fundamentales del sistema de reacciones de Genshin Impact.',

    'El elemento Geo puede generar Cristalización al interactuar con Pyro, Hydro, Electro o Cryo.',

    'La reacción de Electrocargado puede mantener daño Electro en objetivos afectados por Hydro y Electro.',

    'El Reino de los Aranara está relacionado con los sueños y las leyendas de Sumeru.',

    'La Península de Galesong y el Valle Estelar forman parte de la geografía conocida de Mondstadt.',

    'Los Fatui mantienen una red de diplomáticos, soldados y agentes en distintas naciones.',

    'La Orden del Abismo posee miembros que pueden manipular energías y criaturas del Abismo.',

    'Fischl utiliza el nombre de Princesa de la Condenación y viaja acompañada de Oz.',

    'La historia de Inazuma incluye la separación entre Ei y la identidad pública de la Shogun Raiden.',

    'Furina desempeñó durante mucho tiempo un papel público central en Fontaine.',

    'Neuvillette es el Juez Supremo de Fontaine y está vinculado a la historia de los Dragones Soberanos.',

    'La historia de Natlan está estrechamente relacionada con sus seis tribus y sus tradiciones.',

    'El Gremio de Aventureros funciona como una organización presente en varias regiones de Teyvat.',

    'La reputación regional permite obtener recompensas y cosméticos mediante actividades de exploración y encargos.'
];

let curiosityBag = [];

let lastCuriosity = null;

function nextCuriosity() {

    if (!curiosityBag.length) {

        curiosityBag = shuffle(
            curiosities.map((_, index) => index)
        );

        if (
            curiosityBag.length > 1 &&
            curiosityBag[0] === lastCuriosity
        ) {
            [curiosityBag[0], curiosityBag[1]] =
                [curiosityBag[1], curiosityBag[0]];
        }
    }

    const index = curiosityBag.pop();

    lastCuriosity = index;

    return curiosities[index];
}

// ============================================================================
// GENSHIN IMPACT — EQUIPOS RECOMENDADOS
// ============================================================================

const TEAM_GUIDES = {

    'cyno': [
        'Cyno / Nahida / Furina / Baizhu — Quickbloom.',
        'Cyno / Nahida / Xingqiu / Baizhu — Hyperbloom.',
        'Cyno / Nahida / Yelan / Baizhu — Quickbloom.'
    ],

    'nahida': [
        'Nahida / Alhaitham / Yae Miko / Kuki Shinobu — Doble Dendro Aggravate.',
        'Nahida / Nilou / Kokomi / Collei — Florecimiento.',
        'Nahida / Cyno / Furina / Baizhu — Quickbloom.'
    ],

    'dehya': [
        'Dehya / Bennett / Xiangling / Kazuha — Pyro.',
        'Dehya / Furina / Bennett / Jean — Vaporizado y soporte.'
    ],

    'alhaitham': [
        'Alhaitham / Nahida / Yae Miko / Kuki Shinobu — Spread.',
        'Alhaitham / Nahida / Xingqiu / Kuki Shinobu — Hyperbloom.',
        'Alhaitham / Furina / Nahida / Baizhu — Quickbloom.'
    ],

    'tighnari': [
        'Tighnari / Nahida / Yae Miko / Zhongli — Spread.',
        'Tighnari / Fischl / Nahida / Baizhu — Aggravate.'
    ],

    'nilou': [
        'Nilou / Nahida / Kokomi / Collei — Florecimiento.',
        'Nilou / Nahida / Barbara / Yaoyao — Florecimiento.'
    ],

    'fischl': [
        'Fischl / Clorinde / Nahida / Kazuha — Aggravate.',
        'Fischl / Neuvillette / Kazuha / Baizhu — Electro y Hydro.'
    ],

    'kaeya': [
        'Kaeya / Shenhe / Kazuha / Kokomi — Congelamiento.',
        'Kaeya / Xiangling / Bennett / Chongyun — Derretido.'
    ],

    'hu tao': [
        'Hu Tao / Xingqiu / Yelan / Zhongli — Vaporizado.',
        'Hu Tao / Furina / Yelan / Xianyun — Vaporizado.'
    ],

    'xiao': [
        'Xiao / Faruzan / Furina / Xianyun — Anemo.',
        'Xiao / Faruzan / Bennett / Zhongli — DPS Anemo.'
    ],

    'ganyu': [
        'Ganyu / Mona / Diona / Venti — Congelamiento.',
        'Ganyu / Xiangling / Bennett / Zhongli — Derretido.'
    ],

    'raiden shogun': [
        'Raiden / Xiangling / Bennett / Xingqiu — Nacional.',
        'Raiden / Nahida / Yelan / Baizhu — Hyperbloom.'
    ],

    'kazuha': [
        'Kazuha / Bennett / Xiangling / Xingqiu — Vaporizado.',
        'Kazuha / Neuvillette / Furina / Charlotte — Hydro.'
    ],

    'kamisato ayaka': [
        'Ayaka / Shenhe / Kokomi / Kazuha — Congelamiento.',
        'Ayaka / Furina / Charlotte / Kazuha — Congelamiento.'
    ],

    'furina': [
        'Furina / Neuvillette / Kazuha / Xilonen — Hydro.',
        'Furina / Hu Tao / Yelan / Xianyun — Vaporizado.',
        'Furina / Xiao / Faruzan / Xianyun — Anemo.'
    ],

    'neuvillette': [
        'Neuvillette / Furina / Kazuha / Xilonen — Hydro.',
        'Neuvillette / Furina / Fischl / Baizhu — Electro-Hydro.'
    ],

    'navia': [
        'Navia / Furina / Bennett / Xianyun — Geo.',
        'Navia / Xiangling / Bennett / Zhongli — Cristalización.'
    ],

    'arlecchino': [
        'Arlecchino / Yelan / Bennett / Kazuha — Vaporizado.',
        'Arlecchino / Xingqiu / Bennett / Zhongli — Vaporizado.'
    ],

    'mavuika': [
        'Mavuika / Citlali / Xilonen / Bennett — DPS Pyro.',
        'Mavuika / Furina / Xilonen / Bennett — Vaporizado.'
    ],

    'kinich': [
        'Kinich / Emilie / Bennett / Xiangling — Quemadura.',
        'Kinich / Emilie / Furina / Bennett — Quemadura y Vaporizado.'
    ],

    'xianyun': [
        'Xianyun / Xiao / Faruzan / Furina — DPS en caída.',
        'Xianyun / Gaming / Furina / Bennett — Ataques en caída.'
    ],

    'yelan': [
        'Yelan / Hu Tao / Xingqiu / Zhongli — Vaporizado.',
        'Yelan / Arlecchino / Bennett / Kazuha — Vaporizado.',
        'Yelan / Nahida / Kuki Shinobu / Alhaitham — Hyperbloom.'
    ],

    'xingqiu': [
        'Xingqiu / Hu Tao / Yelan / Zhongli — Vaporizado.',
        'Xingqiu / Nahida / Kuki Shinobu / Alhaitham — Hyperbloom.'
    ],

    'xiangling': [
        'Xiangling / Bennett / Xingqiu / Kazuha — Nacional.',
        'Xiangling / Childe / Bennett / Kazuha — Internacional.'
    ],

    'bennett': [
        'Bennett / Xiangling / Xingqiu / Kazuha — Nacional.',
        'Bennett / Arlecchino / Yelan / Kazuha — Vaporizado.'
    ],

    'baizhu': [
        'Baizhu / Cyno / Nahida / Furina — Quickbloom.',
        'Baizhu / Neuvillette / Furina / Kazuha — Hydro.'
    ],

    'zhongli': [
        'Zhongli / Hu Tao / Xingqiu / Yelan — Vaporizado.',
        'Zhongli / Navia / Bennett / Xiangling — Cristalización.'
    ],

    'diluc': [
        'Diluc / Furina / Bennett / Xianyun — Vaporizado.',
        'Diluc / Xingqiu / Bennett / Kazuha — Vaporizado.'
    ],

    'venti': [
        'Venti / Ganyu / Mona / Diona — Congelamiento.',
        'Venti / Fischl / Xingqiu / Bennett — Reacciones.'
    ],

    'raiden': [
        'Raiden / Xiangling / Bennett / Xingqiu — Nacional.',
        'Raiden / Nahida / Yelan / Baizhu — Hyperbloom.'
    ],

    'kuki shinobu': [
        'Kuki / Nahida / Xingqiu / Alhaitham — Hyperbloom.',
        'Kuki / Nahida / Yelan / Dendro Traveler — Hyperbloom.'
    ]
};

// ============================================================================
// GENSHIN DB API — CACHÉ
// ============================================================================

async function fetchJson(url, cacheKey = null, ttl = 0, headers = {}) {

    if (cacheKey && apiCache.has(cacheKey)) {

        const item = apiCache.get(cacheKey);

        if (Date.now() < item.until) {
            return item.data;
        }
    }

    const response = await fetch(url, {
        method: 'GET',

        headers: {
            'Accept': 'application/json',
            'User-Agent': 'Katherine-Genshin-Bot/3.0',
            ...headers
        },

        signal: AbortSignal.timeout(15000)
    });

    if (!response.ok) {
        throw new Error(`HTTP ${response.status} consultando ${url}`);
    }

    const data = await response.json();

    if (cacheKey && ttl > 0) {

        apiCache.set(cacheKey, {
            data,
            until: Date.now() + ttl
        });
    }

    return data;
}

// ============================================================================
// CONSULTAR GENSHIN DB
// ============================================================================

async function gameSearch(folder, query) {

    const params = new URLSearchParams({

        query,

        dumpResult: 'true',

        matchNames: 'true',

        matchAltNames: 'true',

        matchAliases: 'true',

        queryLanguages: 'english,spanish',

        resultLanguage: 'english'
    });

    const cacheKey = `game:${folder}:${normalizar(query)}`;

    const data = await fetchJson(
        `${GAME_API}/${folder}?${params}`,
        cacheKey,
        6 * 60 * 60 * 1000
    );

    let result = data?.result ?? data?.data ?? data;

    if (
        result &&
        typeof result === 'object' &&
        result.result &&
        !result.name
    ) {
        result = result.result;
    }

    if (Array.isArray(result)) {
        result = result[0];
    }

    if (!result || typeof result !== 'object') {
        return {
            payload: data,
            result: null
        };
    }

    return {
        payload: data,
        result
    };
}

// ============================================================================
// GENSHIN BUILDS — CONSULTAR ÁRBOL DE GITHUB
// ============================================================================

async function getBuildTree() {

    if (
        buildTreeCache &&
        Date.now() < buildTreeCacheUntil
    ) {
        return buildTreeCache;
    }

    if (buildTreeRequest) {
        return buildTreeRequest;
    }

    buildTreeRequest = (async () => {

        const headers = {
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'Katherine-Genshin-Bot'
        };

        if (process.env.GITHUB_TOKEN) {
            headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
        }

        const response = await fetch(
            GITHUB_TREE,
            {
                headers,
                signal: AbortSignal.timeout(25000)
            }
        );

        if (!response.ok) {
            throw new Error(
                `GitHub respondió HTTP ${response.status}.`
            );
        }

        const data = await response.json();

        if (!Array.isArray(data.tree)) {
            throw new Error('GitHub no devolvió el índice de builds.');
        }

        if (data.truncated) {
            throw new Error('El índice de builds fue truncado por GitHub.');
        }

        buildTreeCache = data.tree
            .filter(item => item.type === 'blob')
            .map(item => item.path);

        buildTreeCacheUntil =
            Date.now() + 6 * 60 * 60 * 1000;

        console.log(
            `[GENSHIN BUILDS] Índice cargado: ${buildTreeCache.length} archivos.`
        );

        return buildTreeCache;
    })();

    try {
        return await buildTreeRequest;
    } finally {
        buildTreeRequest = null;
    }
}

// ============================================================================
// ALIASES DE PERSONAJES
// ============================================================================

const CHARACTER_ALIASES = {

    'kamisato ayaka': ['ayaka'],

    'kaedehara kazuha': ['kazuha'],

    'raiden shogun': ['raiden'],

    'sangonomiya kokomi': ['kokomi'],

    'arataki itto': ['itto'],

    'yae miko': ['yae'],

    'shikanoin heizou': ['heizou'],

    'kuki shinobu': ['kuki-shinobu'],

    'tartaglia': ['childe', 'tartaglia'],

    'wanderer': ['wanderer', 'scaramouche'],

    'faruzan': ['faruzan'],

    'traveler anemo': ['anemo-traveler', 'traveler-anemo'],

    'traveler geo': ['geo-traveler', 'traveler-geo'],

    'traveler electro': ['electro-traveler', 'traveler-electro'],

    'traveler dendro': ['dendro-traveler', 'traveler-dendro'],

    'traveler hydro': ['hydro-traveler', 'traveler-hydro'],

    'traveler pyro': ['pyro-traveler', 'traveler-pyro']
};

// ============================================================================
// BUSCAR BUILD DE UN PERSONAJE
// ============================================================================

function characterSlugCandidates(query, record, payload) {

    const candidates = new Set();

    const values = [
        payload?.filename,
        record?.filename,
        query,
        record?.name
    ];

    for (const value of values) {

        if (!value) continue;

        candidates.add(slugify(value));
    }

    const aliasKey = normalizar(query);

    for (const alias of CHARACTER_ALIASES[aliasKey] || []) {
        candidates.add(slugify(alias));
    }

    return [...candidates];
}

async function fetchRawJson(filePath) {

    const url = GITHUB_RAW +
        filePath.split('/').map(encodeURIComponent).join('/');

    try {

        return await fetchJson(
            url,
            `raw:${filePath}`,
            6 * 60 * 60 * 1000
        );

    } catch (err) {

        console.log(
            `[GENSHIN BUILDS] Archivo opcional no disponible: ${filePath}`
        );

        return null;
    }
}

function getBuildFolders(tree, root) {

    const prefix = `${root}/`;

    const folders = new Set();

    const supportedFiles = [
        'weapons.json',
        'artifacts-sets.json',
        'build-notes.json',
        'talents.json'
    ];

    for (const file of tree) {

        if (!file.startsWith(prefix)) continue;

        const remaining = file.slice(prefix.length);

        const parts = remaining.split('/');

        if (parts.length < 2) continue;

        if (!supportedFiles.includes(parts[parts.length - 1])) {
            continue;
        }

        folders.add(parts[0]);
    }

    return [...folders];
}

// ============================================================================
// FORMATEAR CONTENIDO DE BUILDS
// ============================================================================

const TRANSLATIONS = {

    'gilded-dreams': 'Sueños Áureos',
    'deepwood-memories': 'Recuerdos del Bosque',
    'emblem-of-severed-fate': 'Emblema del Destino',
    'thundering-fury': 'Furia del Trueno',
    'viridescent-venerer': 'Sombra Verde Esmeralda',
    'crimson-witch-of-flames': 'Bruja Carmesí en Llamas',
    'noblesse-oblige': 'Ritual Antiguo de la Nobleza',
    'gladiators-finale': 'Final del Gladiador',
    'wanderers-troupe': 'Orquesta del Errante',
    'blizzard-strayer': 'Nómada del Invierno',
    'tenacity-of-the-millelith': 'Tenacidad de la Geoarmada',
    'marechaussee-hunter': 'Cazador Fantasmal',
    'golden-troupe': 'Compañía Dorada',
    'nighttime-whispers-in-the-echoing-woods': 'Susurros Nocturnos',
    'flower-of-paradise-lost': 'Flor Olvidada del Paraíso',
    'desert-pavilion-chronicle': 'Crónica del Pabellón del Desierto',
    'ocean-hued-clam': 'Perla Oceánica',
    'husk-of-opulent-dreams': 'Cáscara de Sueños Opulentos',

    'staff-of-the-scarlet-sands': 'Báculo de las Arenas Escarlatas',
    'primordial-jade-winged-spear': 'Lanza Primordial de Jade',
    'staff-of-homa': 'Báculo de Homa',
    'deathmatch': 'Duelo a Muerte',
    'the-catch': 'La Captura',
    'engulfing-lightning': 'Luz del Segador',
    'wolf-fang': 'Colmillo de Lobo',
    'aqua-simulacra': 'Simulacro Acuático',
    'favonius-sword': 'Espada de Favonius',
    'freedom-sworn': 'Juramento por la Libertad',

    'atk%': 'ATQ%',
    'hp%': 'Vida%',
    'def%': 'DEF%',
    'em': 'Maestría Elemental',
    'er': 'Recarga de Energía',
    'cr': 'Probabilidad CRIT',
    'cd': 'Daño CRIT',
    'cr/cd': 'Probabilidad CRIT / Daño CRIT',
    'atk': 'ATQ',
    'hp': 'Vida'
};

function displayId(value) {

    if (value === null || value === undefined) {
        return 'No especificado';
    }

    if (typeof value === 'object') {

        if (value.name) {
            return displayId(value.name);
        }

        return JSON.stringify(value);
    }

    const original = String(value).trim();

    const key = original.toLowerCase();

    if (TRANSLATIONS[key]) {
        return TRANSLATIONS[key];
    }

    return original
        .replace(/\[\[(?:[^:\]]+:)?([^\]]+)\]\]/g, '$1')
        .replace(/-/g, ' ')
        .replace(/\b\w/g, letter => letter.toUpperCase());
}

function localized(value) {

    if (typeof value === 'string') {
        return value;
    }

    if (value && typeof value === 'object') {
        return value.es || value.en || value.fr ||
            Object.values(value).find(v => typeof v === 'string') ||
            '';
    }

    return '';
}

function cleanGuideNote(value) {

    return localized(value)
        .replace(/\[\[(?:[^:\]]+:)?([^\]]+)\]\]/g, (_, id) => displayId(id))
        .replace(/\*\*/g, '')
        .replace(/[`*_]/g, '')
        .replace(/\{rot:[^}]+\}/g, '')
        .trim();
}

function rankedItems(groups, maximum = 5) {

    if (!Array.isArray(groups)) {
        return [];
    }

    return groups
        .slice(0, maximum)
        .map((group, index) => {

            const items = group.items || [];

            const names = items.map(item => {

                if (typeof item === 'string') {
                    return displayId(item);
                }

                let name = displayId(item.name);

                if (item.refinement) {
                    name += ` R${item.refinement}`;
                }

                if (item.note) {
                    name += ` (${cleanGuideNote(item.note)})`;
                }

                return name;
            });

            return `${index + 1}. ${names.join(' / ')}`;
        });
}

function formatWeapons(data) {

    const weapons = data?.weapons?.weapons;

    const result = rankedItems(weapons, 6);

    return result.length
        ? result.join('\n')
        : 'No hay clasificación disponible.';
}

function formatArtifacts(data) {

    const sets = data?.['artifacts-sets']?.artifact_sets;

    if (!Array.isArray(sets) || !sets.length) {
        return 'No hay clasificación disponible.';
    }

    return sets.slice(0, 3).map((entry, index) => {

        const groups = entry.groups || [];

        const options = groups.map(group => {

            const items = group.items || [];

            return items.map(item => {

                const nombre = displayId(item.name || item);

                const pieces = item.pieces
                    ? ` ${item.pieces} piezas`
                    : '';

                return `${nombre}${pieces}`;
            }).join(' + ');

        }).filter(Boolean);

        return `${index + 1}. ${options.join(' / ')}`;

    }).join('\n');
}

function formatMainStats(data) {

    const stats =
        data?.['artifacts-mainstats']?.main_stats;

    if (!stats) {
        return 'No disponible.';
    }

    const format = list => {

        if (!Array.isArray(list)) return 'No especificado';

        return list.slice(0, 3)
            .map(item => displayId(item))
            .join(' / ');
    };

    return (
        `Reloj: ${format(stats.sands)}\n` +
        `Cáliz: ${format(stats.goblet)}\n` +
        `Corona: ${format(stats.circlet)}`
    );
}

function formatSubStats(data) {

    const list =
        data?.['artifacts-substats']?.substats_priority;

    if (!Array.isArray(list)) {
        return 'No disponible.';
    }

    return list.slice(0, 6).map((item, index) => {

        if (typeof item === 'string') {
            return `${index + 1}. ${displayId(item)}`;
        }

        if (item.items) {
            return `${index + 1}. ${item.items.map(displayId).join(' / ')}`;
        }

        return `${index + 1}. ${displayId(item.name)}`;

    }).join('\n');
}

function formatTalents(data) {

    const talents = data?.talents?.talents;

    if (!Array.isArray(talents)) {
        return 'No disponible.';
    }

    return rankedItems(talents, 4);
}

function formatBuildNotes(notes) {

    if (!notes) return '';

    const values = (notes.notes || [])
        .map(cleanGuideNote)
        .filter(Boolean)
        .slice(0, 3);

    return values.join('\n');
}

// ============================================================================
// CARGAR GUÍAS DE PERSONAJES
// ============================================================================

async function getCharacterGuide(query) {

    const key = normalizar(query);

    if (characterCache.has(key)) {
        return characterCache.get(key);
    }

    const dbResult = await gameSearch(
        'characters',
        query
    );

    const character = dbResult.result;

    if (!character) {
        return null;
    }

    const name = character.name || query;

    const candidates = characterSlugCandidates(
        query,
        character,
        dbResult.payload
    );

    const tree = await getBuildTree();

    const metadataPath = tree.find(file => {

        if (!file.endsWith('/metadata.json')) {
            return false;
        }

        const folder = file
            .split('/')
            .slice(-2, -1)[0]
            .toLowerCase();

        return candidates.some(
            candidate => candidate.toLowerCase() === folder
        );
    });

    let root = null;

    let metadata = null;

    if (metadataPath) {

        root = metadataPath.replace(
            /\/metadata\.json$/,
            ''
        );

        metadata = await fetchRawJson(metadataPath);
    }

    let builds = [];

    if (root) {

        const folderNames = getBuildFolders(
            tree,
            root
        );

        const candidatesBuild = folderNames.slice(0, 12);

        builds = await Promise.all(
            candidatesBuild.map(async folder => {

                const notes = await fetchRawJson(
                    `${root}/${folder}/build-notes.json`
                );

                return {
                    folder,
                    notes
                };
            })
        );

        builds = builds.sort((a, b) => {

            if (a.notes?.best && !b.notes?.best) return -1;
            if (!a.notes?.best && b.notes?.best) return 1;

            return a.folder.localeCompare(b.folder);
        });

        builds = builds.slice(0, 3);

        builds = await Promise.all(
            builds.map(async build => {

                const files = {};

                const names = [
                    'weapons.json',
                    'artifacts-sets.json',
                    'artifacts-mainstats.json',
                    'artifacts-substats.json',
                    'talents.json'
                ];

                for (const filename of names) {

                    files[filename] = await fetchRawJson(
                        `${root}/${build.folder}/${filename}`
                    );

                    // Los archivos compartidos pueden estar en la carpeta raíz.
                    if (!files[filename]) {

                        files[filename] = await fetchRawJson(
                            `${root}/${filename}`
                        );
                    }
                }

                return {
                    ...build,
                    files
                };
            })
        );
    }

    const result = {
        name,
        character,
        metadata,
        builds,
        root,
        payload: dbResult.payload
    };

    characterCache.set(key, result);

    return result;
}

// ============================================================================
// MATERIALES DE ASCENSIÓN Y TALENTOS
// ============================================================================

function extractMaterials(character) {

    const roots = [];

    const directKeys = [
        'costs',
        'ascensionCosts',
        'ascensionMaterials',
        'talentMaterials',
        'materials'
    ];

    for (const key of directKeys) {

        if (character[key]) {
            roots.push({
                path: key,
                data: character[key]
            });
        }
    }

    const grouped = {
        ascension: new Map(),
        talent: new Map(),
        other: new Map()
    };

    function addMaterial(name, count, category) {

        if (!name) return;

        const group =
            category === 'ascension'
                ? grouped.ascension
                : category === 'talent'
                    ? grouped.talent
                    : grouped.other;

        const key = String(name);

        const numeric = Number(count);

        const value = Number.isFinite(numeric)
            ? numeric
            : 0;

        group.set(
            key,
            (group.get(key) || 0) + value
        );
    }

    function walk(node, route = [], depth = 0) {

        if (!node || depth > 12) return;

        if (Array.isArray(node)) {

            for (const item of node) {
                walk(item, route, depth + 1);
            }

            return;
        }

        if (typeof node !== 'object') return;

        const name =
            node.name ||
            node.materialName ||
            node.itemName ||
            node.material;

        const count =
            node.count ??
            node.amount ??
            node.quantity;

        if (
            name &&
            count !== undefined &&
            count !== null
        ) {

            const categoryPath = route
                .join('.')
                .toLowerCase();

            let category = 'other';

            if (/ascend|promote|character/.test(categoryPath)) {
                category = 'ascension';
            } else if (/talent|skill/.test(categoryPath)) {
                category = 'talent';
            }

            addMaterial(
                typeof name === 'object'
                    ? localized(name)
                    : name,
                count,
                category
            );

            return;
        }

        for (const [key, value] of Object.entries(node)) {

            if (['description', 'icon', 'image'].includes(key)) {
                continue;
            }

            walk(
                value,
                [...route, key],
                depth + 1
            );
        }
    }

    for (const item of roots) {
        walk(item.data, [item.path]);
    }

    function formatGroup(group) {

        return [...group.entries()]
            .filter(([name]) => name)
            .map(([name, count]) => {

                const amount = count > 0
                    ? `${count} × `
                    : '';

                return `${amount}${name}`;
            });
    }

    return {
        ascension: formatGroup(grouped.ascension),
        talent: formatGroup(grouped.talent),
        other: formatGroup(grouped.other)
    };
}

function formatMaterials(character) {

    const materials = extractMaterials(character);

    const lines = [];

    if (materials.ascension.length) {

        lines.push(
            'ASCENSIÓN',
            ...materials.ascension.slice(0, 20).map(x => `• ${x}`)
        );
    }

    if (materials.talent.length) {

        lines.push(
            'TALENTOS',
            ...materials.talent.slice(0, 20).map(x => `• ${x}`)
        );
    }

    if (materials.other.length) {

        lines.push(
            'OTROS MATERIALES',
            ...materials.other.slice(0, 10).map(x => `• ${x}`)
        );
    }

    if (!lines.length) {

        lines.push(
            'La fuente de datos no devolvió la tabla de costes para este personaje.',
            'Puedes consultar su progresión en https://genshindb.com/es/'
        );
    }

    return lines.join('\n');
}

// ============================================================================
// EQUIPOS
// ============================================================================

function getRecommendedTeams(name) {

    const key = normalize(name);

    const teams = TEAM_GUIDES[key];

    if (!teams) {
        return (
            'No tengo una composición verificada guardada para este personaje.\n' +
            'Puedes consultar guías de teoría de juego en https://keqingmains.com/'
        );
    }

    return (
        'EQUIPOS RECOMENDADOS\n\n' +
        teams.map((team, index) => `${index + 1}. ${team}`).join('\n')
    );
}

// ============================================================================
// GENERAR FICHA COMPLETA
// ============================================================================

function characterSummary(guide) {

    const character = guide.character;

    const element =
        character.elementType ||
        character.element ||
        character.vision ||
        'No especificado';

    const weapon =
        character.weaponType ||
        character.weapon ||
        'No especificado';

    const rarity =
        character.rarity ||
        character.stars ||
        '?';

    return (
        `${guide.name}\n` +
        `Rareza: ${rarity} estrellas\n` +
        `Elemento: ${element}\n` +
        `Arma: ${weapon}\n` +
        `Región: ${character.region || character.nation || 'No especificada'}`
    );
}

function characterBuildText(guide, buildIndex = 0) {

    if (!guide.builds.length) {

        return (
            'No encontré una build publicada para este personaje en la fuente consultada.\n' +
            'Fuente: https://genshin-impact-helper-team.github.io/genshin-builds/'
        );
    }

    const index = Math.max(
        0,
        Math.min(buildIndex, guide.builds.length - 1)
    );

    const build = guide.builds[index];

    const files = build.files;

    const notes = build.notes || {};

    const title =
        cleanGuideNote(notes.name) ||
        displayId(build.folder);

    const version =
        notes.last_updated ||
        guide.metadata?.last_updated ||
        'No indicada';

    const parts = [

        `BUILD: ${title}`,

        `Actualización de guía: ${version}`,

        '',

        'ARMAS RECOMENDADAS',

        formatWeapons({
            weapons: files['weapons.json']
        }),

        '',

        'ARTEFACTOS',

        formatArtifacts({
            'artifacts-sets.json':
                files['artifacts-sets.json']
        }),

        '',

        'ESTADÍSTICAS PRINCIPALES',

        formatMainStats({
            'artifacts-mainstats.json':
                files['artifacts-mainstats.json']
        }),

        '',

        'SUBESTADÍSTICAS',

        formatSubStats({
            'artifacts-substats.json':
                files['artifacts-substats.json']
        }),

        '',

        'PRIORIDAD DE TALENTOS',

        formatTalents({
            'talents.json':
                files['talents.json']
        })
    ];

    const notesText = formatBuildNotes(notes);

    if (notesText) {
        parts.push('', 'NOTAS DE LA BUILD', notesText);
    }

    parts.push(
        '',
        'Fuente de builds: https://genshin-impact-helper-team.github.io/genshin-builds/'
    );

    return parts.join('\n');
}

async function fullCharacterReport(name) {

    const guide = await getCharacterGuide(name);

    if (!guide) {
        return `No encontré información de ${name} en Genshin-DB. Revisa el nombre e inténtalo nuevamente.`;
    }

    const build = characterBuildText(guide);

    const materials = formatMaterials(
        guide.character
    );

    const teams = getRecommendedTeams(
        guide.name
    );

    return [
        'KATHERINE — ARCHIVO DE PERSONAJE',
        '',
        characterSummary(guide),
        '',
        build,
        '',
        'MATERIALES DE PROGRESIÓN',
        materials,
        '',
        teams
    ].join('\n');
}

// ============================================================================
// CACHÉ DE JUEGOS Y ENTRETENIMIENTO
// ============================================================================

const triviaQuestions = [

    {
        question: '¿Qué elemento utiliza Cyno?',
        options: ['Pyro', 'Electro', 'Dendro', 'Anemo'],
        answer: 1
    },

    {
        question: '¿Cómo se llama la Arconte Dendro?',
        options: ['Ei', 'Furina', 'Nahida', 'Mavuika'],
        answer: 2
    },

    {
        question: '¿Qué ciudad está vinculada a los contratos?',
        options: ['Mondstadt', 'Liyue', 'Inazuma', 'Fontaine'],
        answer: 1
    },

    {
        question: '¿Qué organización posee once Heraldos?',
        options: ['Caballeros de Favonius', 'Fatui', 'Adeptus', 'Gremio'],
        answer: 1
    },

    {
        question: '¿Cuál es el arma de Dehya?',
        options: ['Arco', 'Lanza', 'Mandoble', 'Catalizador'],
        answer: 2
    },

    {
        question: '¿Qué planeta es conocido como el planeta rojo?',
        options: ['Venus', 'Marte', 'Júpiter', 'Saturno'],
        answer: 1
    },

    {
        question: '¿Cuántos corazones tiene un pulpo?',
        options: ['Uno', 'Dos', 'Tres', 'Cuatro'],
        answer: 2
    },

    {
        question: '¿Qué gas es el más abundante en la atmósfera terrestre?',
        options: ['Oxígeno', 'Nitrógeno', 'Dióxido de carbono', 'Hidrógeno'],
        answer: 1
    },

    {
        question: '¿Quién dirige la Funeraria Wangsheng?',
        options: ['Yelan', 'Hu Tao', 'Ganyu', 'Ningguang'],
        answer: 1
    },

    {
        question: '¿Qué elemento representa la libertad?',
        options: ['Anemo', 'Geo', 'Electro', 'Hydro'],
        answer: 0
    },

    {
        question: '¿Cuál es el satélite natural de la Tierra?',
        options: ['Fobos', 'Luna', 'Europa', 'Titán'],
        answer: 1
    },

    {
        question: '¿Qué país tiene forma de bota?',
        options: ['España', 'Italia', 'Portugal', 'Grecia'],
        answer: 1
    },

    {
        question: '¿Cómo se llama la asistente de Cyno?',
        options: ['Collei', 'Candace', 'No existe una asistente oficial con ese cargo', 'Nilou'],
        answer: 2
    },

    {
        question: '¿Qué instrumento utiliza Venti?',
        options: ['Lira', 'Violín', 'Piano', 'Flauta traversa'],
        answer: 0
    }
];

const jokes = [
    '¿Por qué el Viajero nunca se pierde? Porque siempre tiene un punto de teletransporte pendiente.',
    'Cyno entró a una biblioteca. No contó ningún chiste: estaba en modo serio de investigación.',
    '¿Qué le dijo un slime a otro? ¡Nos vemos en la siguiente reacción!',
    '¿Por qué el Abismo no tiene vacaciones? Porque siempre está trabajando en las profundidades.',
    'Mi conexión con Teyvat es tan fuerte que hasta el lag quiere quedarse conmigo.',
    '¿Qué hace un Hilichurl cuando tiene frío? Se acerca al Pyro Regisvid.',
    '¿Por qué el jugador nunca tiene Mora? Porque cada mejora tiene un precio.',
    'El jefe dijo que hoy había que trabajar en equipo. Yo elegí cuatro personajes y me fui a la Espiral.'
];

const phrases = [
    'No necesitas recorrer todo Teyvat en un día. Una aventura también se construye paso a paso.',
    'Incluso el viajero más fuerte necesita descansar.',
    'Los caminos difíciles también conducen a lugares extraordinarios.',
    'No todas las victorias se consiguen luchando. Algunas se consiguen resistiendo.',
    'Que tu próximo destino sea más brillante que el anterior.',
    'Los recuerdos de cada aventura también son parte del tesoro.'
];

const challenges = [
    'Completa un dominio sin utilizar comida.',
    'Derrota un jefe usando un personaje que no sueles utilizar.',
    'Explora una zona que todavía no hayas completado al 100%.',
    'Gana una batalla utilizando un equipo de cuatro elementos diferentes.',
    'Completa un desafío sin usar teletransporte.',
    'Utiliza un personaje de cuatro estrellas durante toda una expedición.',
    'Consigue un logro que todavía no tengas.',
    'Visita una estatua de los Siete y recupera tus fuerzas.'
];

const standardFiveStars = [
    'Diluc',
    'Jean',
    'Mona',
    'Qiqi',
    'Keqing',
    'Tighnari',
    'Dehya'
];

const featuredFiveStars = [
    'Cyno',
    'Nahida',
    'Furina',
    'Neuvillette',
    'Arlecchino',
    'Mavuika',
    'Dehya'
];

function getScoreKey(uid) {
    return String(uid);
}

function addPoints(uid, amount) {

    const key = getScoreKey(uid);

    estado.puntos.set(
        key,
        (estado.puntos.get(key) || 0) + amount
    );
}

// ============================================================================
// DESEOS SIMULADOS
// ============================================================================

function getWishState(uid) {

    const key = String(uid);

    if (!estado.deseos.has(key)) {

        estado.deseos.set(key, {
            pity5: 0,
            pity4: 0,
            guaranteed: false,
            fiveStars: 0,
            fourStars: 0
        });
    }

    return estado.deseos.get(key);
}

function simulateWish(uid) {

    const data = getWishState(uid);

    data.pity5++;
    data.pity4++;

    let chanceFive = 0.006;

    if (data.pity5 >= 74) {
        chanceFive = Math.min(
            1,
            0.006 + (data.pity5 - 73) * 0.06
        );
    }

    const fiveStar =
        data.pity5 >= 90 ||
        Math.random() < chanceFive;

    if (fiveStar) {

        const featured =
            data.guaranteed || Math.random() < 0.5;

        const result = featured
            ? randomItem(featuredFiveStars)
            : randomItem(standardFiveStars);

        data.guaranteed = !featured;

        data.pity5 = 0;
        data.pity4 = 0;

        data.fiveStars++;

        return {
            stars: 5,
            name: result,
            featured
        };
    }

    const fourStar =
        data.pity4 >= 10 ||
        Math.random() < 0.051;

    if (fourStar) {

        data.pity4 = 0;

        data.fourStars++;

        return {
            stars: 4,
            name: randomItem([
                'Bennett',
                'Xingqiu',
                'Fischl',
                'Sucrose',
                'Diona',
                'Rosaria',
                'Kuki Shinobu',
                'Yaoyao'
            ])
        };
    }

    return {
        stars: 3,
        name: randomItem([
            'Espada del Alba',
            'Gran Espada Sangrienta',
            'Arco de Cuervo',
            'Cuentos de Cazadores de Dragones',
            'Lanza de Hierro Blanco'
        ])
    };
}

// ============================================================================
// ANARCHY API — TOKEN SEGÚN ORIGEN
// ============================================================================

function getToken(source) {

    if (source === 'community') {
        return COMMUNITY_BOT_TOKEN;
    }

    return BOT_TOKEN;
}

async function anarchyAPI(
    endpoint,
    method = 'GET',
    body = null,
    source = 'developer',
    sensitive = false
) {

    const token = getToken(source);

    if (!token) {

        return {
            ok: false,
            status: 500,
            data: {
                error: `Falta token para el origen ${source}.`
            }
        };
    }

    try {

        const response = await fetch(
            `${API_BASE}${endpoint}`,
            {
                method,

                headers: {
                    'Authorization': `Bot ${token}`,
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },

                body: body === null
                    ? undefined
                    : JSON.stringify(body),

                signal: AbortSignal.timeout(15000)
            }
        );

        const raw = await response.text();

        let data = {};

        try {
            data = raw ? JSON.parse(raw) : {};
        } catch {
            data = { raw };
        }

        if (sensitive) {
            console.log(
                `[ANARCHY API] ${method} ${endpoint} -> ${response.status} (respuesta protegida)`
            );
        } else {
            console.log(
                `[ANARCHY API] ${method} ${endpoint} -> ${response.status}`,
                data
            );
        }

        return {
            ok: response.ok,
            status: response.status,
            data,
            retryAfter: response.headers.get('retry-after')
        };

    } catch (err) {

        console.error(
            '[ANARCHY API ERROR]',
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
// DIAGNÓSTICO DE AMBOS BOTS
// ============================================================================

async function loadBotProfiles() {

    console.log('[KATHERINE] Consultando perfil de Desarrolladores.');

    const developer = await anarchyAPI(
        '/me',
        'GET',
        null,
        'developer'
    );

    if (developer.ok) {

        const bot = developer.data?.bot || {};

        estado.developerBotUid = bot.uid || null;

        console.log('[DEVELOPER BOT]', {
            name: bot.name,
            uid: bot.uid,
            botId: bot.botId,
            threadIds: developer.data?.threadIds || []
        });

    } else {

        console.error(
            '[DEVELOPER BOT ERROR]',
            developer.status,
            developer.data
        );
    }

    if (COMMUNITY_BOT_TOKEN) {

        console.log('[KATHERINE] Consultando perfil de comunidad.');

        const community = await anarchyAPI(
            '/me',
            'GET',
            null,
            'community'
        );

        if (community.ok) {

            const bot = community.data?.bot || {};

            estado.communityBotUid = bot.uid || null;

            console.log('[COMMUNITY BOT]', {
                name: bot.name,
                uid: bot.uid,
                threadIds: community.data?.threadIds || []
            });

        } else {

            console.error(
                '[COMMUNITY BOT ERROR]',
                community.status,
                community.data
            );
        }
    }

    console.log(
        '[KATHERINE] Perfiles consultados.'
    );
}

// ============================================================================
// ENVÍO DE MENSAJES
// ============================================================================

function sendMessage(
    threadId,
    text,
    source = 'developer',
    replyMessageId = null,
    buttons = null
) {

    const id = String(threadId || '');

    const content = cleanText(text);

    if (!id || !content) {

        return Promise.resolve({
            ok: false,
            status: 400,
            data: {
                error: 'Falta threadId o contenido.'
            }
        });
    }

    const task = messageQueue.then(async () => {

        const now = Date.now();

        const globalLast =
            lastGlobalMessage.get(source) || 0;

        const chatKey = `${source}:${id}`;

        const chatLast =
            lastChatMessage.get(chatKey) || 0;

        const globalWait = Math.max(
            0,
            1050 - (now - globalLast)
        );

        const chatWait = Math.max(
            0,
            2050 - (now - chatLast)
        );

        await sleep(
            Math.max(globalWait, chatWait)
        );

        const payload = {
            content
        };

        if (replyMessageId) {
            payload.replyMessageId = String(replyMessageId);
        }

        if (Array.isArray(buttons) && buttons.length) {
            payload.buttons = buttons.slice(0, 5);
        }

        let result = await anarchyAPI(
            `/threads/${encodeURIComponent(id)}/messages`,
            'POST',
            payload,
            source
        );

        if (result.status === 429) {

            const retry = Number(result.retryAfter);

            const wait = Number.isFinite(retry) && retry > 0
                ? retry * 1000
                : 3000;

            console.warn(
                `[RATE LIMIT] Esperando ${wait} ms.`
            );

            await sleep(wait);

            result = await anarchyAPI(
                `/threads/${encodeURIComponent(id)}/messages`,
                'POST',
                payload,
                source
            );
        }

        if (result.ok) {

            const sent = Date.now();

            lastGlobalMessage.set(source, sent);

            lastChatMessage.set(chatKey, sent);
        }

        return result;
    });

    messageQueue = task.catch(err => {
        console.error('[MESSAGE QUEUE ERROR]', err);
    });

    return task;
}

async function sendLongMessage(
    threadId,
    text,
    source = 'developer',
    replyMessageId = null
) {

    const chunks = splitMessage(
        cleanText(text),
        1800
    );

    const results = [];

    for (let i = 0; i < chunks.length; i++) {

        const result = await sendMessage(
            threadId,
            chunks[i],
            source,
            i === 0 ? replyMessageId : null
        );

        results.push(result);

        if (!result.ok) {
            break;
        }
    }

    return results;
}

// ============================================================================
// LIVE-ROOMS — LIMITES DE ANARCHY
// ============================================================================

async function getLiveRooms(force = false) {

    if (!COMMUNITY_BOT_TOKEN) {

        return {
            ok: false,
            error: 'Falta COMMUNITY_BOT_TOKEN.'
        };
    }

    if (
        !force &&
        liveRoomsCache &&
        Date.now() < liveRoomsCacheUntil
    ) {

        return {
            ok: true,
            rooms: liveRoomsCache
        };
    }

    if (liveRoomsRequest) {
        return liveRoomsRequest;
    }

    liveRoomsRequest = (async () => {

        const wait = Math.max(
            0,
            3000 - (Date.now() - liveRoomsLastRequest)
        );

        if (wait) {
            await sleep(wait);
        }

        const now = Date.now();

        while (
            liveRoomsRequests.length &&
            liveRoomsRequests[0] < now - 3600000
        ) {
            liveRoomsRequests.shift();
        }

        if (liveRoomsRequests.length >= 300) {

            return {
                ok: false,
                error: 'Se alcanzó el máximo de 300 consultas de salas por hora.'
            };
        }

        liveRoomsLastRequest = Date.now();

        liveRoomsRequests.push(
            liveRoomsLastRequest
        );

        const result = await anarchyAPI(
            '/live-rooms',
            'GET',
            null,
            'community'
        );

        if (!result.ok) {

            return {
                ok: false,
                error: `HTTP ${result.status}: ${JSON.stringify(result.data)}`
            };
        }

        const rooms = Array.isArray(result.data?.roomList)
            ? result.data.roomList
            : [];

        liveRoomsCache = rooms;

        liveRoomsCacheUntil =
            Date.now() + 2500;

        return {
            ok: true,
            rooms
        };

    })();

    try {
        return await liveRoomsRequest;
    } finally {
        liveRoomsRequest = null;
    }
}

// ============================================================================
// LIVEKIT — LIMPIEZA
// ============================================================================

async function closeCall(threadId, callLeaveApi = false) {

    const id = String(threadId);

    const call = estado.salas.get(id);

    if (!call) return;

    call.stopping = true;

    estado.salas.delete(id);

    estado.conectado = estado.salas.size > 0;

    try {

        if (call.track) {
            await call.track.close();
        } else if (call.source) {
            await call.source.close();
        }

    } catch (err) {

        console.warn(
            '[LIVEKIT] Error cerrando audio:',
            err.message
        );
    }

    try {

        if (call.room?.isConnected) {
            await call.room.disconnect();
        }

    } catch (err) {

        console.warn(
            '[LIVEKIT] Error desconectando:',
            err.message
        );
    }

    if (callLeaveApi && COMMUNITY_BOT_TOKEN) {

        await anarchyAPI(
            `/live-rooms/${encodeURIComponent(id)}/leave`,
            'POST',
            null,
            'community'
        );
    }

    console.log(
        `[LIVEKIT] Katherine salió de ${id}.`
    );
}

// ============================================================================
// LIVEKIT — PUBLICAR MICRÓFONO SILENCIOSO
// ============================================================================

async function publishSilentMicrophone(call, sdk) {

    const sampleRate = 48000;
    const channels = 1;
    const samplesPerFrame = 960;

    const source = new sdk.AudioSource(
        sampleRate,
        channels
    );

    const track = sdk.LocalAudioTrack.createAudioTrack(
        'Katherine-Microphone',
        source
    );

    const options = new sdk.TrackPublishOptions();

    options.source = sdk.TrackSource.SOURCE_MICROPHONE;

    call.source = source;
    call.track = track;

    let attempts = 0;
    let published = false;

    while (
        !call.stopping &&
        call.room.isConnected &&
        !published
    ) {

        try {

            await call.room.localParticipant.publishTrack(
                track,
                options
            );

            published = true;

            call.published = true;

            console.log(
                `[LIVEKIT] Micrófono habilitado en ${call.threadId}.`
            );

        } catch (err) {

            attempts++;

            if (attempts === 1 || attempts % 5 === 0) {

                console.log(
                    `[LIVEKIT] Esperando autorización de micrófono. Intento ${attempts}: ${err.message}`
                );
            }

            // Anarchy habilita la publicación unos segundos después
            // de detectar la entrada del bot a la sala.

            await sleep(1200);
        }
    }

    if (!published || call.stopping) {
        return;
    }

    const silentBuffer = new Int16Array(
        samplesPerFrame
    );

    while (
        !call.stopping &&
        call.room.isConnected
    ) {

        try {

            const frame = new sdk.AudioFrame(
                silentBuffer,
                sampleRate,
                channels,
                samplesPerFrame
            );

            await source.captureFrame(frame);

        } catch (err) {

            if (!call.stopping) {

                console.error(
                    '[LIVEKIT] Error enviando audio:',
                    err.message
                );
            }

            break;
        }

        await sleep(20);
    }
}

// ============================================================================
// LIVEKIT — ENTRAR A UNA LLAMADA
// ============================================================================

async function joinCall(threadId) {

    const id = String(threadId);

    if (!COMMUNITY_BOT_TOKEN) {

        return {
            ok: false,
            message: 'No está configurado COMMUNITY_BOT_TOKEN. No puedo consultar ni entrar a las llamadas.'
        };
    }

    if (estado.salas.has(id)) {

        return {
            ok: true,
            message: 'Katherine ya está conectada a esta llamada.'
        };
    }

    if (estado.salas.size >= 3) {

        return {
            ok: false,
            message: 'Katherine ya está conectada al máximo de tres salas simultáneas.'
        };
    }

    try {

        // Importar LiveKit antes de solicitar el token de 60 segundos.
        const sdk = await import('@livekit/rtc-node');

        const listing = await getLiveRooms();

        if (!listing.ok) {

            return {
                ok: false,
                message: `No pude consultar las salas: ${listing.error}`
            };
        }

        const liveRoom = listing.rooms.find(
            room => String(room.threadId) === id
        );

        if (!liveRoom) {

            return {
                ok: false,
                message: 'No hay una llamada activa en este chat. Inicia una llamada y vuelve a utilizar !llamada!.'
            };
        }

        if (liveRoom.joined) {

            return {
                ok: false,
                message: 'Anarchy indica que Katherine ya figura dentro de esta llamada. Revisa si Render acaba de reiniciarse.'
            };
        }

        // Solicitar el token temporal de LiveKit.

        const joinResult = await anarchyAPI(
            `/live-rooms/${encodeURIComponent(id)}/join`,
            'POST',
            null,
            'community',
            true
        );

        if (!joinResult.ok) {

            return {
                ok: false,
                message: `Anarchy rechazó la entrada: HTTP ${joinResult.status}. ${JSON.stringify(joinResult.data)}`
            };
        }

        const credentials = joinResult.data || {};

        if (
            !credentials.url ||
            !credentials.token ||
            !credentials.identity
        ) {

            return {
                ok: false,
                message: 'Anarchy no devolvió url, token e identity.'
            };
        }

        const room = new sdk.Room();

        const call = {
            threadId: id,
            room,
            sdk,

            identity: credentials.identity,

            stopping: false,
            published: false,

            source: null,
            track: null
        };

        room.on(
            sdk.RoomEvent.Disconnected,
            () => {

                console.warn(
                    `[LIVEKIT] Desconexión inesperada en ${id}.`
                );

                if (estado.salas.get(id) === call) {

                    call.stopping = true;

                    estado.salas.delete(id);

                    estado.conectado =
                        estado.salas.size > 0;
                }
            }
        );

        // Conectar inmediatamente. El permiso dura 60 segundos.

        await room.connect(
            credentials.url,
            credentials.token,
            {
                autoSubscribe: false,
                dynacast: true
            }
        );

        estado.salas.set(id, call);

        estado.conectado = true;

        console.log(
            `[LIVEKIT] Conectado a ${id} como ${credentials.identity}.`
        );

        // La publicación queda en segundo plano.
        // No se bloquea la respuesta del comando.

        call.audioTask = publishSilentMicrophone(
            call,
            sdk
        ).catch(err => {

            console.error(
                '[LIVEKIT] Error en publicación:',
                err
            );
        });

        return {
            ok: true,
            message: 'Katherine se ha unido a la llamada. Estoy habilitando mi micrófono.'
        };

    } catch (err) {

        console.error(
            '[LIVEKIT JOIN ERROR]',
            err
        );

        await anarchyAPI(
            `/live-rooms/${encodeURIComponent(id)}/leave`,
            'POST',
            null,
            'community'
        );

        return {
            ok: false,
            message: `No pude conectar Katherine a LiveKit: ${err.message}`
        };
    }
}

// ============================================================================
// LIVEKIT — SALIR
// ============================================================================

async function leaveCall(threadId) {

    const id = String(threadId);

    if (!COMMUNITY_BOT_TOKEN) {

        return {
            ok: false,
            message: 'Falta COMMUNITY_BOT_TOKEN.'
        };
    }

    const result = await anarchyAPI(
        `/live-rooms/${encodeURIComponent(id)}/leave`,
        'POST',
        null,
        'community'
    );

    await closeCall(id, false);

    if (!result.ok) {

        return {
            ok: false,
            message: `La desconexión local se realizó, pero Anarchy respondió HTTP ${result.status}.`
        };
    }

    return {
        ok: true,
        message: 'Katherine ha salido de la llamada.'
    };
}

// ============================================================================
// ENCUESTAS INTERACTIVAS
// ============================================================================

function createPoll(threadId, question, options) {

    const pollId = crypto.randomBytes(5).toString('hex');

    const buttons = options.map((option, index) => ({

        id: `poll_${pollId}_${index}`,

        label: option.slice(0, 40)
    }));

    const poll = {
        threadId: String(threadId),
        question,
        options: [...options],
        votes: Array(options.length).fill(0),
        voters: new Set(),
        expiresAt: Date.now() + 30 * 60 * 1000
    };

    buttons.forEach((button, index) => {

        estado.encuestas.set(
            `${threadId}:${button.id}`,
            {
                poll,
                index
            }
        );
    });

    return buttons;
}

async function processInteraction(event, source) {

    const threadId = String(event.threadId || '');

    const buttonId = String(event.buttonId || '');

    const key = `${threadId}:${buttonId}`;

    const record = estado.encuestas.get(key);

    if (!record) return;

    const { poll, index } = record;

    if (Date.now() > poll.expiresAt) {

        for (const option of poll.options) {
            void option;
        }

        return;
    }

    const user = event.user || {};

    const uid = getUserId(user);

    if (poll.voters.has(uid)) {

        await sendMessage(
            threadId,
            'Ya registraste tu voto en esta encuesta.',
            source
        );

        return;
    }

    poll.voters.add(uid);

    poll.votes[index]++;

    const total = poll.votes.reduce(
        (sum, value) => sum + value,
        0
    );

    const results = poll.options.map(
        (option, i) =>
            `${option}: ${poll.votes[i]} voto(s)`
    );

    await sendLongMessage(
        threadId,

        `ENCUESTA: ${poll.question}\n\n` +
        `${user.nickname || 'Un viajero'} votó por: ${poll.options[index]}.\n\n` +
        `RESULTADOS (${total} votos)\n` +
        results.join('\n'),

        source
    );
}

// ============================================================================
// TRIVIA Y PUNTOS
// ============================================================================

function startTrivia(threadId) {

    const question = randomItem(triviaQuestions);

    estado.trivia.set(String(threadId), {
        question,
        attempts: 0,
        expiresAt: Date.now() + 120000
    });

    return (
        `TRIVIA DE KATHERINE\n\n` +
        `${question.question}\n\n` +
        question.options.map(
            (option, index) => `${index + 1}. ${option}`
        ).join('\n') +
        '\n\nResponde con !responder 1, 2, 3 o 4.'
    );
}

function answerTrivia(threadId, uid, answer) {

    const trivia = estado.trivia.get(String(threadId));

    if (!trivia) {
        return 'No hay una trivia activa en este chat. Usa !trivia.';
    }

    if (Date.now() > trivia.expiresAt) {

        estado.trivia.delete(String(threadId));

        return 'La trivia ha expirado. Inicia otra con !trivia.';
    }

    const selected = Number(answer);

    if (
        !Number.isInteger(selected) ||
        selected < 1 ||
        selected > trivia.question.options.length
    ) {

        return 'Debes responder con un número válido.';
    }

    trivia.attempts++;

    if (selected - 1 === trivia.question.answer) {

        estado.trivia.delete(String(threadId));

        addPoints(uid, 10);

        return (
            '¡Respuesta correcta!\n' +
            '+10 puntos para ti.\n' +
            `Respuesta: ${trivia.question.options[trivia.question.answer]}`
        );
    }

    if (trivia.attempts >= 3) {

        estado.trivia.delete(String(threadId));

        return (
            'Se terminaron los intentos.\n' +
            `La respuesta correcta era: ${trivia.question.options[trivia.question.answer]}.`
        );
    }

    return 'No es correcto. Inténtalo nuevamente.';
}

// ============================================================================
// JUEGO DE ADIVINAR
// ============================================================================

function startGuessGame(threadId) {

    const secret = Math.floor(Math.random() * 100) + 1;

    estado.juegos.set(String(threadId), {
        secret,
        attempts: 0,
        expiresAt: Date.now() + 300000
    });

    return (
        'He elegido un número entre 1 y 100.\n' +
        'Tienes siete intentos para descubrirlo.\n' +
        'Utiliza !intento número.'
    );
}

function guessNumber(threadId, uid, number) {

    const game = estado.juegos.get(String(threadId));

    if (!game) {
        return 'No hay una partida activa. Escribe !adivina.';
    }

    if (Date.now() > game.expiresAt) {

        estado.juegos.delete(String(threadId));

        return 'La partida expiró. Vuelve a iniciar con !adivina.';
    }

    const guess = Number(number);

    if (
        !Number.isInteger(guess) ||
        guess < 1 ||
        guess > 100
    ) {
        return 'Escribe un número entero entre 1 y 100.';
    }

    game.attempts++;

    if (guess === game.secret) {

        estado.juegos.delete(String(threadId));

        addPoints(uid, 15);

        return (
            `¡Correcto! El número era ${game.secret}.\n` +
            'Has ganado 15 puntos.'
        );
    }

    if (game.attempts >= 7) {

        estado.juegos.delete(String(threadId));

        return `Terminó la partida. El número era ${game.secret}.`;
    }

    return (
        `${guess < game.secret ? 'El número es mayor.' : 'El número es menor.'}\n` +
        `Intentos restantes: ${7 - game.attempts}.`
    );
}

// ============================================================================
// PERFIL Y DESEOS
// ============================================================================

function getUserDisplayProfile(uid) {

    const wishes = getWishState(uid);

    return (
        `Tus estadísticas recreativas:\n\n` +
        `Deseos simulados de 5 estrellas: ${wishes.fiveStars}\n` +
        `Deseos simulados de 4 estrellas: ${wishes.fourStars}\n` +
        `Contador actual de 5 estrellas: ${wishes.pity5}\n` +
        `Contador actual de 4 estrellas: ${wishes.pity4}\n` +
        `Garantizado en la próxima de 5 estrellas: ${wishes.guaranteed ? 'Sí' : 'No'}\n` +
        `Puntos de Katherine: ${estado.puntos.get(uid) || 0}`
    );
}

// ============================================================================
// COMANDOS DE GENSHIN
// ============================================================================

async function commandCharacter(threadId, args, source, replyId) {

    if (!args.length) {

        await sendMessage(
            threadId,
            'Uso: !personaje nombre\nEjemplo: !personaje Cyno\nTambién puedes utilizar !build, !materiales y !equipos.',
            source,
            replyId
        );

        return;
    }

    const name = args.join(' ');

    await sendMessage(
        threadId,
        `Estoy consultando los registros de Teyvat para ${name}...`,
        source
    );

    try {

        const report = await fullCharacterReport(name);

        await sendLongMessage(
            threadId,
            report,
            source
        );

    } catch (err) {

        console.error(
            '[CHARACTER ERROR]',
            err
        );

        await sendMessage(
            threadId,
            'No pude consultar la ficha. La base de datos o el repositorio de builds no está disponible en este momento.',
            source
        );
    }
}

async function commandBuild(threadId, args, source, replyId) {

    if (!args.length) {

        await sendMessage(
            threadId,
            'Uso: !build nombre del personaje\nEjemplo: !build Cyno\nPara ver una build alternativa: !build Cyno 2.',
            source,
            replyId
        );

        return;
    }

    let buildIndex = 0;

    const possibleIndex = Number(args[args.length - 1]);

    if (
        Number.isInteger(possibleIndex) &&
        possibleIndex >= 1
    ) {

        buildIndex = possibleIndex - 1;
        args.pop();
    }

    const name = args.join(' ');

    try {

        const guide = await getCharacterGuide(name);

        if (!guide) {
            await sendMessage(
                threadId,
                `No encontré una ficha para ${name}.`,
                source,
                replyId
            );

            return;
        }

        const text = [
            characterSummary(guide),
            '',
            characterBuildText(guide, buildIndex)
        ].join('\n');

        await sendLongMessage(
            threadId,
            text,
            source,
            replyId
        );

    } catch (err) {

        console.error('[BUILD ERROR]', err);

        await sendMessage(
            threadId,
            'No pude acceder a los datos de builds. Inténtalo nuevamente más tarde.',
            source
        );
    }
}

async function commandMaterials(threadId, args, source, replyId) {

    if (!args.length) {

        await sendMessage(
            threadId,
            'Uso: !materiales nombre del personaje\nEjemplo: !materiales Nahida.',
            source,
            replyId
        );

        return;
    }

    const name = args.join(' ');

    try {

        const result = await gameSearch(
            'characters',
            name
        );

        if (!result.result) {

            await sendMessage(
                threadId,
                `No encontré los materiales de ${name}.`,
                source,
                replyId
            );

            return;
        }

        const materialText = formatMaterials(
            result.result
        );

        await sendLongMessage(
            threadId,

            `MATERIALES DE ${result.result.name || name}\n\n${materialText}`,

            source,
            replyId
        );

    } catch (err) {

        console.error('[MATERIALS ERROR]', err);

        await sendMessage(
            threadId,
            'No pude consultar la base de materiales.',
            source
        );
    }
}

async function commandTeams(threadId, args, source, replyId) {

    if (!args.length) {

        await sendMessage(
            threadId,
            'Uso: !equipos nombre del personaje\nEjemplo: !equipos Cyno.',
            source,
            replyId
        );

        return;
    }

    const name = args.join(' ');

    try {

        const result = await gameSearch(
            'characters',
            name
        );

        const canonicalName =
            result.result?.name || name;

        await sendMessage(
            threadId,
            `${canonicalName}\n\n${getRecommendedTeams(canonicalName)}`,
            source,
            replyId
        );

    } catch (err) {

        console.error('[TEAMS ERROR]', err);

        await sendMessage(
            threadId,
            'No pude obtener la ficha del personaje.',
            source
        );
    }
}

async function commandGameItem(threadId, folder, args, source, replyId) {

    if (!args.length) {

        await sendMessage(
            threadId,
            folder === 'weapons'
                ? 'Uso: !arma nombre del arma.'
                : 'Uso: !artefacto nombre del set.',
            source,
            replyId
        );

        return;
    }

    const query = args.join(' ');

    try {

        const result = await gameSearch(
            folder,
            query
        );

        const item = result.result;

        if (!item) {

            await sendMessage(
                threadId,
                `No encontré ${query}.`,
                source,
                replyId
            );

            return;
        }

        const description =
            item.description ||
            item.effect ||
            item.lore ||
            'No hay descripción disponible.';

        const parts = [
            item.name || query,
            `Rareza: ${item.rarity || 'No especificada'}`
        ];

        if (item.weaponType) {
            parts.push(`Tipo de arma: ${item.weaponType}`);
        }

        if (item.baseAttack) {
            parts.push(`ATQ base: ${item.baseAttack}`);
        }

        if (item.subStatText) {
            parts.push(`Estadística secundaria: ${item.subStatText}`);
        }

        if (item['2pc']) {
            parts.push(`Efecto de 2 piezas: ${item['2pc']}`);
        }

        if (item['4pc']) {
            parts.push(`Efecto de 4 piezas: ${item['4pc']}`);
        }

        parts.push('', `Descripción: ${description}`);

        await sendLongMessage(
            threadId,
            parts.join('\n'),
            source,
            replyId
        );

    } catch (err) {

        console.error('[ITEM ERROR]', err);

        await sendMessage(
            threadId,
            'No pude consultar ese objeto.',
            source
        );
    }
}

// ============================================================================
// COMANDOS PRINCIPALES
// ============================================================================

async function processCommand(event, source) {

    const threadId = String(event.threadId || '');

    const message = event.message || {};

    const author = message.author || {};

    const nickname = author.nickname || 'Viajero';

    const uid = getUserId(author);

    const content = String(
        message.content || ''
    ).trim();

    const messageId = message.messageId || null;

    if (!threadId || !content) return;

    // Evitar que el bot responda a sí mismo.

    if (
        author.uid &&
        (
            String(author.uid) === String(estado.developerBotUid) ||
            String(author.uid) === String(estado.communityBotUid)
        )
    ) {
        return;
    }

    // Evitar duplicados.

    if (messageId) {

        const id = String(messageId);

        if (processedMessages.has(id)) {
            return;
        }

        processedMessages.add(id);

        if (processedMessages.size > 5000) {

            processedMessages.delete(
                processedMessages.values().next().value
            );
        }
    }

    if (
        !content.startsWith('!') &&
        !content.startsWith('-')
    ) {
        return;
    }

    const parts = content.split(/\s+/);

    const command = parts[0].toLowerCase();

    const args = parts.slice(1);

    console.log(
        `[KATHERINE COMMAND] ${command} | ${nickname} | ${threadId} | ${source}`
    );

    let response = null;

    // ========================================================================
    // AYUDA
    // ========================================================================

    switch (command) {

        case '!ayuda':
        case '!comandos':
        case '-ayuda':

            response =
                'KATHERINE — CENTRO DE COMANDOS\n\n' +

                'GENSHIN IMPACT\n' +
                '!personaje [nombre] — Ficha, build y materiales.\n' +
                '!build [nombre] — Armas, artefactos y talentos.\n' +
                '!materiales [nombre] — Materiales de progresión.\n' +
                '!equipos [nombre] — Equipos recomendados.\n' +
                '!curiosidad — Dato aleatorio sin repeticiones.\n' +
                '!artefacto [nombre] — Consultar un set.\n' +
                '!arma [nombre] — Consultar un arma.\n' +
                '!reaccion pyro hydro — Reacción elemental.\n' +
                '!equipo — Cuatro personajes aleatorios.\n' +
                '!jefe — Elegir un jefe.\n' +
                '!comision — Encargo de aventura.\n' +
                '!expedicion — Generar una expedición.\n' +
                '!deseo — Simular una invocación.\n' +
                '!pity — Consultar contador simulado.\n' +
                '!resetpity — Reiniciar simulación.\n\n' +

                'ENTRETENIMIENTO\n' +
                '!encuesta pregunta | opción 1 | opción 2 — Crear votación.\n' +
                '!trivia — Pregunta de conocimientos.\n' +
                '!responder número — Responder trivia.\n' +
                '!ranking — Tabla de puntos.\n' +
                '!puntos — Consultar tus puntos.\n' +
                '!adivina — Juego de números.\n' +
                '!intento número — Intentar adivinar.\n' +
                '!elige A | B | C — Elegir al azar.\n' +
                '!duelo persona | persona — Duelo ficticio.\n' +
                '!ship persona | persona — Compatibilidad ficticia.\n' +
                '!8ball pregunta — Bola mágica.\n' +
                '!dado [caras] — Lanzar dado.\n' +
                '!moneda — Cara o sello.\n' +
                '!chiste — Contar un chiste.\n' +
                '!frase — Frase aleatoria.\n' +
                '!reto — Desafío aleatorio.\n' +
                '!suerte — Consultar tu fortuna.\n\n' +

                'UTILIDADES\n' +
                '!ping — Comprobar conexión.\n' +
                '!uptime — Tiempo activa.\n' +
                '!estadochat — Estado del bot.\n' +
                '!fecha — Fecha personal.\n' +
                '!zona America/Santiago — Configurar zona horaria.\n\n' +

                'LLAMADAS\n' +
                '!salas — Consultar salas activas.\n' +
                '!llamada! — Entrar a la llamada.\n' +
                '!salirllamada! — Salir de la llamada.';

            break;

        // ====================================================================
        // PERSONAJES Y BUILDS
        // ====================================================================

        case '!personaje':

            await commandCharacter(
                threadId,
                args,
                source,
                messageId
            );

            return;

        case '!build':

            await commandBuild(
                threadId,
                args,
                source,
                messageId
            );

            return;

        case '!materiales':

            await commandMaterials(
                threadId,
                args,
                source,
                messageId
            );

            return;

        case '!equipos':

            await commandTeams(
                threadId,
                args,
                source,
                messageId
            );

            return;

        case '!arma':

            await commandGameItem(
                threadId,
                'weapons',
                args,
                source,
                messageId
            );

            return;

        case '!artefacto':

            await commandGameItem(
                threadId,
                'artifacts',
                args,
                source,
                messageId
            );

            return;

        // ====================================================================
        // CURIOSIDADES
        // ====================================================================

        case '!curiosidad':

            response =
                `¿Sabías que...?\n\n${nextCuriosity()}`;

            break;

        // ====================================================================
        // EQUIPO ALEATORIO
        // ====================================================================

        case '!equipo': {

            const characters = [
                'Cyno',
                'Nahida',
                'Dehya',
                'Alhaitham',
                'Tighnari',
                'Nilou',
                'Kaeya',
                'Fischl',
                'Diluc',
                'Venti',
                'Zhongli',
                'Hu Tao',
                'Xiao',
                'Ganyu',
                'Raiden Shogun',
                'Kazuha',
                'Ayaka',
                'Furina',
                'Neuvillette',
                'Navia',
                'Arlecchino',
                'Mavuika',
                'Kinich',
                'Xianyun',
                'Yelan'
            ];

            response =
                'EQUIPO ALEATORIO\n\n' +
                shuffle(characters)
                    .slice(0, 4)
                    .map((name, index) => `${index + 1}. ${name}`)
                    .join('\n') +
                '\n\nComposición recreativa.';

            break;
        }

        // ====================================================================
        // REACCIONES ELEMENTALES
        // ====================================================================

        case '!reaccion': {

            if (args.length < 2) {

                response =
                    'Uso: !reaccion pyro hydro\nElementos disponibles: Pyro, Hydro, Cryo, Electro, Dendro, Anemo y Geo.';

                break;
            }

            const aliases = {
                fuego: 'Pyro',
                agua: 'Hydro',
                hielo: 'Cryo',
                rayo: 'Electro',
                naturaleza: 'Dendro',
                viento: 'Anemo',
                tierra: 'Geo'
            };

            let a = aliases[normalize(args[0])] || args[0];
            let b = aliases[normalize(args[1])] || args[1];

            a = a.charAt(0).toUpperCase() + a.slice(1).toLowerCase();
            b = b.charAt(0).toUpperCase() + b.slice(1).toLowerCase();

            const reactions = {
                'Hydro|Pyro': 'Vaporizado',
                'Cryo|Pyro': 'Derretido',
                'Cryo|Hydro': 'Congelado',
                'Electro|Hydro': 'Electrocargado',
                'Electro|Pyro': 'Sobrecargado',
                'Cryo|Electro': 'Superconductor',
                'Dendro|Electro': 'Aceleración',
                'Dendro|Hydro': 'Florecimiento',
                'Dendro|Pyro': 'Quemadura'
            };

            if (!elements.includes(a) || !elements.includes(b)) {
                response = 'Uno de los elementos no es válido.';
                break;
            }

            if (a === b) {
                response = 'No se produce una reacción entre elementos iguales.';
                break;
            }

            const key = [a, b].sort().join('|');

            if (a === 'Anemo' || b === 'Anemo') {

                const other = a === 'Anemo' ? b : a;

                response = ['Pyro', 'Hydro', 'Cryo', 'Electro'].includes(other)
                    ? 'Reacción: Torbellino.'
                    : 'No hay una reacción clásica directa de Torbellino.';
                
                break;
            }

            if (a === 'Geo' || b === 'Geo') {

                const other = a === 'Geo' ? b : a;

                response = ['Pyro', 'Hydro', 'Cryo', 'Electro'].includes(other)
                    ? 'Reacción: Cristalización.'
                    : 'No se produce Cristalización con ese elemento.';

                break;
            }

            response =
                `Reacción entre ${a} y ${b}: ${reactions[key] || 'No existe una reacción clásica directa.'}`;

            break;
        }

        // ====================================================================
        // JEFES
        // ====================================================================

        case '!jefe':
        case '!boss': {

            const bosses = [
                'Andrius',
                'Dvalin',
                'Azhdaha',
                'Tartaglia',
                'La Signora',
                'Shouki no Kami',
                'Narval Devorador',
                'Aparejador',
                'Hipostasis Electro',
                'Hipostasis Cryo',
                'Hipostasis Pyro',
                'Dragarto de las Profundidades'
            ];

            response =
                `JEFE SELECCIONADO\n\nTu próximo desafío será: ${randomItem(bosses)}.`;

            break;
        }

        // ====================================================================
        // COMISIONES Y EXPEDICIONES
        // ====================================================================

        case '!comision': {

            const missions = [
                'Investiga unas huellas misteriosas cerca de una ruina.',
                'Ayuda a un comerciante que perdió su mercancía.',
                'Derrota a los Hilichurls que bloquean una ruta.',
                'Busca un cofre oculto cerca de un dominio.',
                'Explora una región desconocida.',
                'Recoge ingredientes para una receta tradicional.',
                'Acompaña a un aventurero durante una expedición.',
                'Investiga un extraño fenómeno elemental.'
            ];

            response =
                `ENCARGO DEL GREMIO DE AVENTUREROS\n\n${randomItem(missions)}`;

            break;
        }

        case '!expedicion': {

            const destinations = [
                'Mondstadt',
                'Liyue',
                'Inazuma',
                'Sumeru',
                'Fontaine',
                'Natlan',
                'Nod-Krai'
            ];

            response =
                'NUEVA EXPEDICIÓN\n\n' +
                `Destino: ${randomItem(destinations)}.\n` +
                'Objetivo: investigar una zona desconocida y registrar los hallazgos.\n\n' +
                '¡Que los Siete guíen tu camino!';

            break;
        }

        // ====================================================================
        // WISH SIMULATOR
        // ====================================================================

        case '!deseo': {

            const result = simulateWish(uid);

            response =
                `RESULTADO DE LA INVOCACIÓN\n\n` +
                `${'⭐'.repeat(result.stars)}\n` +
                `${result.name}\n\n` +
                `Deseos desde el último 5 estrellas: ${getWishState(uid).pity5}\n` +
                'Simulación recreativa; no afecta tu cuenta del juego.';

            break;
        }

        case '!pity':

            response =
                'CONTADOR DE DESEOS SIMULADOS\n\n' +
                getUserDisplayProfile(uid);

            break;

        case '!resetpity':

            estado.deseos.set(uid, {
                pity5: 0,
                pity4: 0,
                guaranteed: false,
                fiveStars: 0,
                fourStars: 0
            });

            response =
                'Tu contador de deseos de Katherine fue reiniciado. Esto no modifica tu cuenta de Genshin Impact.';

            break;

        // ====================================================================
        // FECHA PERSONAL (NO EXISTE COMANDO DE HORA)
        // ====================================================================

        case '!zona': {

            const value = args.join(' ').trim();

            if (!value) {

                const current = estado.zonas.get(uid);

                response = current
                    ? `Tu zona horaria registrada es ${current}.`
                    : 'Configura tu zona horaria con !zona America/Santiago. Ejemplos: America/Bogota, America/Lima, America/Mexico_City o Europe/Madrid.';

            } else if (normalize(value) === 'borrar') {

                estado.zonas.delete(uid);

                response = 'Tu zona horaria fue eliminada.';

            } else if (!validTimezone(value)) {

                response = 'La zona horaria no es válida. Utiliza un identificador IANA.';

            } else {

                estado.zonas.set(uid, value);

                response =
                    `Listo, ${nickname}. Registré tu zona horaria: ${value}.`;
            }

            break;
        }

        case '!fecha': {

            const zone = estado.zonas.get(uid);

            response = zone
                ? `La fecha de ${nickname} es ${localDate(zone)}.`
                : `${nickname}, primero configura tu zona horaria con !zona America/Santiago.`;

            break;
        }

        // ====================================================================
        // PING Y ESTADO
        // ====================================================================

        case '!ping': {

            const start = Date.now();

            const result = await anarchyAPI(
                '/me',
                'GET',
                null,
                source
            );

            const latency = Date.now() - start;

            response = result.ok
                ? `PONG, ${nickname}.\nKatherine está en línea.\nLatencia API: ${latency} ms.`
                : `El servidor está en línea, pero Anarchy respondió HTTP ${result.status}.`;

            break;
        }

        case '!uptime':

            response =
                `Katherine lleva activa ${getUptime()}.`;

            break;

        case '!estadochat':

            response =
                'KATHERINE — ESTADO\n\n' +
                `Servidor: EN LÍNEA\n` +
                `Llamadas conectadas: ${estado.salas.size}/3\n` +
                `Credencial de desarrollador: CONFIGURADA\n` +
                `Credencial de comunidad: ${COMMUNITY_BOT_TOKEN ? 'CONFIGURADA' : 'NO CONFIGURADA'}\n` +
                `Tiempo activa: ${getUptime()}`;

            break;

        // ====================================================================
        // DADO
        // ====================================================================

        case '!dado': {

            const faces = args.length
                ? Number(args[0])
                : 6;

            if (
                !Number.isInteger(faces) ||
                faces < 2 ||
                faces > 100
            ) {

                response =
                    'El dado debe tener entre 2 y 100 caras.';

            } else {

                const value = Math.floor(
                    Math.random() * faces
                ) + 1;

                response =
                    `🎲 ${nickname}, tu resultado es ${value}/${faces}.`;
            }

            break;
        }

        // ====================================================================
        // MONEDA
        // ====================================================================

        case '!moneda':

            response =
                `🪙 La moneda cayó en ${Math.random() < 0.5 ? 'CARA' : 'SELLO'}.`;

            break;

        // ====================================================================
        // ELIGE
        // ====================================================================

        case '!elige': {

            const options = args
                .join(' ')
                .split('|')
                .map(x => x.trim())
                .filter(Boolean);

            if (options.length < 2) {

                response =
                    'Uso: !elige opción 1 | opción 2 | opción 3.';

            } else {

                response =
                    `Katherine ha elegido: ${randomItem(options)}.`;
            }

            break;
        }

        // ====================================================================
        // BOLA MÁGICA
        // ====================================================================

        case '!8ball': {

            if (!args.length) {

                response =
                    'Haz una pregunta después del comando.';

            } else {

                response = randomItem([
                    'Sí, definitivamente.',
                    'Todo apunta a que sí.',
                    'Es muy probable.',
                    'Las señales son favorables.',
                    'No puedo asegurarlo.',
                    'Vuelve a preguntarme después.',
                    'No parece probable.',
                    'Mi respuesta es no.',
                    'El destino todavía no está decidido.'
                ]);
            }

            break;
        }

        // ====================================================================
        // SHIP
        // ====================================================================

        case '!ship': {

            const names = args
                .join(' ')
                .split('|')
                .map(x => x.trim())
                .filter(Boolean);

            if (names.length !== 2) {

                response =
                    'Uso: !ship personaje 1 | personaje 2.';

            } else {

                const seed = crypto
                    .createHash('sha256')
                    .update(`${names[0]}|${names[1]}`)
                    .digest();

                const percentage =
                    seed.readUInt32BE(0) % 101;

                response =
                    `COMPATIBILIDAD\n\n` +
                    `${names[0]} + ${names[1]}\n` +
                    `${percentage}% de compatibilidad.\n\n` +
                    (percentage >= 80
                        ? 'Una pareja destinada a encontrarse.'
                        : percentage >= 50
                            ? 'Podría funcionar con algo de esfuerzo.'
                            : 'Las estrellas no parecen estar alineadas.') +
                    '\n\nResultado ficticio, solo por entretenimiento.';
            }

            break;
        }

        // ====================================================================
        // DUELO
        // ====================================================================

        case '!duelo': {

            const fighters = args
                .join(' ')
                .split('|')
                .map(x => x.trim())
                .filter(Boolean);

            if (fighters.length !== 2) {

                response =
                    'Uso: !duelo participante 1 | participante 2.';

            } else {

                response =
                    `DUELO\n\n${fighters[0]} VS ${fighters[1]}\n\n` +
                    `🏆 Ganador: ${randomItem(fighters)}.\n\n` +
                    'El resultado fue elegido aleatoriamente.';
            }

            break;
        }

        // ====================================================================
        // ENCUESTA
        // ====================================================================

        case '!encuesta': {

            const pieces = args
                .join(' ')
                .split('|')
                .map(x => x.trim())
                .filter(Boolean);

            if (pieces.length < 3 || pieces.length > 6) {

                response =
                    'Uso: !encuesta ¿Qué prefieres? | Opción A | Opción B\nPuedes agregar hasta cinco opciones.';

            } else {

                const question = pieces[0];

                const options = pieces.slice(1);

                const buttons = createPoll(
                    threadId,
                    question,
                    options
                );

                const result = await sendMessage(
                    threadId,
                    `ENCUESTA\n\n${question}\n\nVota utilizando uno de los botones.`,
                    source,
                    messageId,
                    buttons
                );

                if (!result.ok) {

                    response =
                        'No pude publicar la encuesta.';

                } else {

                    console.log(
                        `[POLL] Encuesta creada en ${threadId}.`
                    );
                }
            }

            break;
        }

        // ====================================================================
        // TRIVIA
        // ====================================================================

        case '!trivia':

            response = startTrivia(threadId);

            break;

        case '!responder':

            response = answerTrivia(
                threadId,
                uid,
                args[0]
            );

            break;

        case '!puntos':

            response =
                `${nickname}, tienes ${estado.puntos.get(uid) || 0} puntos.`;

            break;

        case '!ranking': {

            const ranking = [...estado.puntos.entries()]
                .sort((a, b) => b[1] - a[1])
                .slice(0, 10);

            if (!ranking.length) {

                response = 'Todavía no hay puntos registrados.';

            } else {

                response =
                    'RANKING DE KATHERINE\n\n' +
                    ranking.map(
                        ([id, points], index) =>
                            `${index + 1}. ${id} — ${points} puntos`
                    ).join('\n');
            }

            break;
        }

        // ====================================================================
        // ADIVINAR NÚMERO
        // ====================================================================

        case '!adivina':

            response = startGuessGame(threadId);

            break;

        case '!intento':

            response = guessNumber(
                threadId,
                uid,
                args[0]
            );

            break;

        // ====================================================================
        // SUERTE
        // ====================================================================

        case '!suerte':

            response =
                `${nickname}, tu fortuna de hoy: ${randomItem([
                    'Las oportunidades están cerca.',
                    'Hoy es un buen día para comenzar algo nuevo.',
                    'Una sorpresa podría aparecer en tu camino.',
                    'La paciencia será tu mejor aliada.',
                    'Los pequeños avances también cuentan.',
                    'Tu próxima aventura podría ser la mejor.',
                    'No subestimes una buena oportunidad.'
                ])}`;

            break;

        // ====================================================================
        // CHISTES
        // ====================================================================

        case '!chiste':

            response = randomItem(jokes);

            break;

        // ====================================================================
        // FRASES
        // ====================================================================

        case '!frase':

            response = randomItem(phrases);

            break;

        // ====================================================================
        // RETOS
        // ====================================================================

        case '!reto':

            response =
                `RETO PARA ${nickname.toUpperCase()}\n\n${randomItem(challenges)}`;

            break;

        // ====================================================================
        // LLAMADAS
        // ====================================================================

        case '!llamada!': {

            const result = await joinCall(threadId);

            response = result.message;

            break;
        }

        case '!salirllamada!': {

            const result = await leaveCall(threadId);

            response = result.message;

            break;
        }

        case '!salas': {

            const result = await getLiveRooms();

            if (!result.ok) {

                response =
                    `No pude consultar las salas: ${result.error}`;

                break;
            }

            if (!result.rooms.length) {

                response =
                    'No hay llamadas activas en este momento.';

                break;
            }

            response =
                'LLAMADAS ACTIVAS\n\n' +
                result.rooms.slice(0, 15).map(
                    (room, index) => {

                        const type =
                            room.channelType === 'video'
                                ? 'VIDEO'
                                : 'VOZ';

                        return (
                            `${index + 1}. ${room.title || 'Sala sin título'}\n` +
                            `ID: ${room.threadId}\n` +
                            `Tipo: ${type}\n` +
                            `Participantes: ${room.participantCount ?? 0}\n` +
                            `Katherine: ${room.joined ? 'Dentro' : 'Fuera'}`
                        );
                    }
                ).join('\n\n');

            break;
        }

        default:
            return;
    }

    // Publicar respuesta.
    // Las respuestas largas se dividen automáticamente.

    if (response) {

        await sendLongMessage(
            threadId,
            response,
            source,
            messageId
        );
    }
}

// ============================================================================
// EVENTOS DE LA SALA
// ============================================================================

async function processRoomEvent(event) {

    const threadId = String(event.threadId || '');

    if (!threadId) return;

    const user = event.user || {};

    const uid = String(user.uid || '');

    const nickname = user.nickname || 'Aventurero';

    const isCommunityBot =
        (
            uid &&
            estado.communityBotUid &&
            uid === String(estado.communityBotUid)
        ) ||
        [...estado.salas.values()].some(
            call => call.identity === uid
        );

    switch (event.type) {

        case 'room.join':

            console.log(
                `[ROOM JOIN] ${nickname} entró a ${threadId}.`
            );

            if (!isCommunityBot) {

                await sendMessage(
                    threadId,
                    '¡Ad Astra Abbysosque! Bienvenido al chat.',
                    'community'
                );
            }

            break;

        case 'room.leave':

            console.log(
                `[ROOM LEAVE] ${nickname} salió de ${threadId}.`
            );

            if (isCommunityBot) {

                await closeCall(threadId, false);

            } else {

                await sendMessage(
                    threadId,
                    'Buen viaje, el Gremio de Aventureros espera tu regreso',
                    'community'
                );
            }

            break;

        case 'room.speaker':

            console.log(
                `[ROOM SPEAKER] ${nickname}: ${event.speaker ? 'Puede hablar' : 'Está escuchando'}.`
            );

            break;

        case 'room.end':

            console.log(
                `[ROOM END] Llamada finalizada: ${threadId}.`
            );

            await closeCall(threadId, false);

            break;
    }
}

// ============================================================================
// WEBHOOK — VALIDACIÓN DE DOS SECRETOS
// ============================================================================

function validateWebhook(req) {

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
            error: 'Faltan los encabezados de autenticación.'
        };
    }

    const timestampNumber = Number(timestamp);

    if (
        !Number.isFinite(timestampNumber) ||
        Math.abs(Date.now() / 1000 - timestampNumber) > 300
    ) {
        return {
            ok: false,
            error: 'El timestamp está vencido o es inválido.'
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

    const secrets = [
        {
            name: 'developer',
            secret: WEBHOOK_SECRET
        },
        {
            name: 'community',
            secret: COMMUNITY_WEBHOOK_SECRET
        }
    ].filter(item => item.secret);

    const received = Buffer.from(
        signature,
        'hex'
    );

    for (const item of secrets) {

        const expected = crypto
            .createHmac('sha256', item.secret)
            .update(`${timestamp}.${req.rawBody}`)
            .digest();

        if (
            received.length === expected.length &&
            crypto.timingSafeEqual(received, expected)
        ) {

            return {
                ok: true,
                source: item.name
            };
        }
    }

    return {
        ok: false,
        error: 'La firma no coincide con ninguno de los secretos.'
    };
}

// ============================================================================
// WEBHOOK PRINCIPAL
// ============================================================================

app.post('/', (req, res) => {

    console.log('[WEBHOOK] Evento recibido.');

    const validation = validateWebhook(req);

    if (!validation.ok) {

        console.warn(
            '[WEBHOOK RECHAZADO]',
            validation.error
        );

        return res.status(401).send('Unauthorized');
    }

    console.log(
        `[WEBHOOK] Firma válida. Origen: ${validation.source}`
    );

    // Responder a Anarchy inmediatamente.
    res.status(200).send('OK');

    const event = req.body || {};

    // Mantener el orden de eventos.
    const task = eventQueue.then(async () => {

        if (event.type === 'message') {

            await processCommand(
                event,
                validation.source
            );

        } else if (event.type === 'interaction') {

            await processInteraction(
                event,
                validation.source
            );

        } else if (
            event.type === 'room.join' ||
            event.type === 'room.leave' ||
            event.type === 'room.speaker' ||
            event.type === 'room.end'
        ) {

            if (validation.source !== 'community') {

                console.warn(
                    '[WEBHOOK] Evento de sala recibido con secreto de desarrollador.'
                );

                return;
            }

            await processRoomEvent(event);

        } else {

            console.log(
                '[WEBHOOK] Evento no procesado:',
                event.type
            );
        }
    });

    eventQueue = task.catch(err => {

        console.error(
            '[EVENT QUEUE ERROR]',
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

        activeCalls: estado.salas.size,

        communityEnabled: Boolean(
            COMMUNITY_BOT_TOKEN &&
            COMMUNITY_WEBHOOK_SECRET
        ),

        characterBuildDatabase: 'Genshin Builds',

        characterData: 'Genshin-DB',

        timestamp: new Date().toISOString()
    });
});

// ============================================================================
// CIERRE ORDENADO
// ============================================================================

let shuttingDown = false;

async function shutdown(signal) {

    if (shuttingDown) return;

    shuttingDown = true;

    console.log(
        `[KATHERINE] Apagando por ${signal}.`
    );

    const rooms = [...estado.salas.keys()];

    for (const threadId of rooms) {

        try {

            await anarchyAPI(
                `/live-rooms/${encodeURIComponent(threadId)}/leave`,
                'POST',
                null,
                'community'
            );

            await closeCall(threadId, false);

        } catch (err) {

            console.error(
                `[KATHERINE] Error cerrando ${threadId}:`,
                err.message
            );
        }
    }

    process.exit(0);
}

process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));

// ============================================================================
// INICIO
// ============================================================================

app.listen(PORT, '0.0.0.0', () => {

    console.log('');
    console.log('=======================================================');
    console.log('        KATHERINE — GENSHIN COMMUNITY BOT');
    console.log('=======================================================');

    console.log(`[SERVER] Puerto: ${PORT}`);

    console.log('[WEBHOOK] POST /');

    console.log('[HEALTH] GET /');

    console.log('[DEVELOPER TOKEN]', BOT_TOKEN ? 'CONFIGURADO' : 'AUSENTE');

    console.log('[COMMUNITY TOKEN]', COMMUNITY_BOT_TOKEN ? 'CONFIGURADO' : 'AUSENTE');

    console.log('[DEVELOPER SECRET]', WEBHOOK_SECRET ? 'CONFIGURADO' : 'AUSENTE');

    console.log('[COMMUNITY SECRET]', COMMUNITY_WEBHOOK_SECRET ? 'CONFIGURADO' : 'AUSENTE');

    console.log('[GENSHIN BUILDS] API DINÁMICA');

    console.log('[GENSHIN DB] API DINÁMICA');

    console.log('[LIVEKIT] LISTO');

    console.log('[CURIOSIDADES] ROTACIÓN SIN REPETICIONES');

    console.log('=======================================================');
    console.log('');

    loadBotProfiles().catch(err => {

        console.error(
            '[BOT PROFILE ERROR]',
            err
        );
    });
});
