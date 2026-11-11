
'use strict';

const express = require('express');
const crypto = require('crypto');
const GDB = require('genshin-db');

// ============================================================================
// KATHERINE — CONFIGURACIÓN
// ============================================================================

const app = express();

const API = 'https://api.anarchyapps.org/bot-api/v1';
const GAME_API = 'https://genshin-db-api.vercel.app/api/v5';

const TREE_URL = 'https://api.github.com/repos/Genshin-Impact-Helper-Team/genshin-builds/git/trees/main?recursive=1';

const RAW_URL = 'https://raw.githubusercontent.com/Genshin-Impact-Helper-Team/genshin-builds/main/';

const BOT_TOKEN = process.env.BOT_TOKEN;
const COMMUNITY_BOT_TOKEN = process.env.COMMUNITY_BOT_TOKEN;

const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET;
const COMMUNITY_WEBHOOK_SECRET = process.env.COMMUNITY_WEBHOOK_SECRET;

const PORT = Number(process.env.PORT) || 3000;
const GITHUB_TOKEN = process.env.GITHUB_TOKEN;

const START_TIME = Date.now();

if (!BOT_TOKEN) throw new Error('Falta BOT_TOKEN.');
if (!WEBHOOK_SECRET) throw new Error('Falta WEBHOOK_SECRET.');

if (!COMMUNITY_BOT_TOKEN) {
    console.warn('COMMUNITY_BOT_TOKEN ausente: llamadas desactivadas.');
}

if (!COMMUNITY_WEBHOOK_SECRET) {
    console.warn('COMMUNITY_WEBHOOK_SECRET ausente: eventos ACM desactivados.');
}

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
// ESTADO GLOBAL
// ============================================================================

const state = {
    developerUid: null,
    communityUid: null,

    calls: new Map(),
    zones: new Map(),
    points: new Map(),
    polls: new Map(),
    trivia: new Map(),
    numberGames: new Map(),
    wishes: new Map()
};

const processedMessages = new Set();
const cache = new Map();

let eventQueue = Promise.resolve();
let sendQueue = Promise.resolve();

const lastSent = new Map();

let treeCache = null;
let treeExpires = 0;
let treePromise = null;

let roomCache = null;
let roomCacheExpires = 0;
let roomPromise = null;
let lastRoomRequest = 0;

const roomRequests = [];

// ============================================================================
// UTILIDADES
// ============================================================================

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const normalize = value =>
    String(value ?? '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .trim();

const slugify = value =>
    normalize(value)
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '');

const random = list =>
    list[Math.floor(Math.random() * list.length)];

const shuffle = list => {
    const copy = [...list];

    for (let i = copy.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));

        [copy[i], copy[j]] = [copy[j], copy[i]];
    }

    return copy;
};

const uidOf = author =>
    String(author?.uid || author?.nickname || 'unknown');

const clean = text =>
    String(text ?? '')
        .replace(/[＊*`]/g, '')
        .replace(/\r/g, '')
        .trim();

const localize = value => {
    if (typeof value === 'string') return value;

    if (value && typeof value === 'object') {
        return value.es ||
            value.ES ||
            value.en ||
            value.EN ||
            Object.values(value).find(x => typeof x === 'string') ||
            '';
    }

    return '';
};

const splitText = (text, max = 1750) => {
    const lines = String(text).split('\n');

    const chunks = [];
    let current = '';

    for (const line of lines) {
        const next = current
            ? `${current}\n${line}`
            : line;

        if (next.length > max && current) {
            chunks.push(current);
            current = line;
        } else {
            current = next;
        }

        while (current.length > max) {
            chunks.push(current.slice(0, max));
            current = current.slice(max);
        }
    }

    if (current) chunks.push(current);

    return chunks;
};

const dateForZone = zone =>
    new Intl.DateTimeFormat('es', {
        timeZone: zone,
        weekday: 'long',
        year: 'numeric',
        month: 'long',
        day: 'numeric'
    }).format(new Date());

function validZone(zone) {
    try {
        new Intl.DateTimeFormat('es', {
            timeZone: zone
        }).format(new Date());

        return true;
    } catch {
        return false;
    }
}

function uptime() {
    let s = Math.floor((Date.now() - START_TIME) / 1000);

    const d = Math.floor(s / 86400);
    s %= 86400;

    const h = Math.floor(s / 3600);
    s %= 3600;

    const m = Math.floor(s / 60);
    s %= 60;

    return [
        d && `${d} día(s)`,
        h && `${h} hora(s)`,
        m && `${m} minuto(s)`,
        `${s} segundo(s)`
    ].filter(Boolean).join(', ');
}

function displayId(value) {
    if (value == null) return 'No especificado';

    if (typeof value === 'object') {
        return displayId(value.name || value.id || '');
    }

    const id = String(value)
        .replace(/\[\[(?:[^:\]]+:)?([^\]]+)\]\]/g, '$1');

    const names = {
        'atk%': 'ATQ%',
        'hp%': 'Vida%',
        'def%': 'DEF%',
        em: 'Maestría elemental',
        er: 'Recarga de energía',
        cr: 'Probabilidad CRIT',
        cd: 'Daño CRIT',
        na: 'Ataque normal',
        ca: 'Ataque cargado',
        skill: 'Habilidad elemental',
        burst: 'Habilidad definitiva',
        'pyro-dmg': 'Daño Pyro',
        'hydro-dmg': 'Daño Hydro',
        'electro-dmg': 'Daño Electro',
        'cryo-dmg': 'Daño Cryo',
        'dendro-dmg': 'Daño Dendro',
        'anemo-dmg': 'Daño Anemo',
        'geo-dmg': 'Daño Geo',
        'physical-dmg': 'Daño físico'
    };

    return names[id.toLowerCase()] ||
        id.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function guideText(value) {
    return localize(value)
        .replace(/\[\[(?:[^:\]]+:)?([^\]]+)\]\]/g, (_, id) => displayId(id))
        .replace(/[`*_]/g, '')
        .replace(/\{rot:[^}]+\}/g, '')
        .trim();
}

// ============================================================================
// ANARCHY API — CADA BOT UTILIZA SU TOKEN PROPIO
// ============================================================================

function tokenFor(source) {
    return source === 'community'
        ? COMMUNITY_BOT_TOKEN
        : BOT_TOKEN;
}

async function anarchy(
    endpoint,
    method = 'GET',
    body = null,
    source = 'developer',
    hide = false
) {
    const token = tokenFor(source);

    if (!token) {
        return {
            ok: false,
            status: 500,
            data: {
                error: `Falta token ${source}.`
            }
        };
    }

    try {
        const options = {
            method,

            headers: {
                Authorization: `Bot ${token}`,
                'Content-Type': 'application/json',
                Accept: 'application/json'
            },

            signal: AbortSignal.timeout(15000)
        };

        if (body !== null) {
            options.body = JSON.stringify(body);
        }

        const response = await fetch(`${API}${endpoint}`, options);

        const raw = await response.text();

        let data = {};

        try {
            data = raw ? JSON.parse(raw) : {};
        } catch {
            data = { raw };
        }

        console.log(
            `[ANARCHY] ${method} ${endpoint} -> ${response.status}`,
            hide ? '(respuesta protegida)' : data
        );

        return {
            ok: response.ok,
            status: response.status,
            data,
            retryAfter: response.headers.get('retry-after')
        };

    } catch (error) {
        console.error('[ANARCHY FETCH]', error.message);

        return {
            ok: false,
            status: 500,
            data: {
                error: error.message
            }
        };
    }
}

// ============================================================================
// CARGAR PERFILES DE AMBOS BOTS
// ============================================================================

async function loadProfiles() {
    const dev = await anarchy(
        '/me',
        'GET',
        null,
        'developer'
    );

    if (dev.ok) {
        state.developerUid = dev.data?.bot?.uid || null;

        console.log(
            '[BOT DESARROLLADOR]',
            dev.data?.bot,
            'Chats:',
            dev.data?.threadIds || []
        );
    } else {
        console.error(
            '[BOT DESARROLLADOR]',
            dev.status,
            dev.data
        );
    }

    if (COMMUNITY_BOT_TOKEN) {
        const community = await anarchy(
            '/me',
            'GET',
            null,
            'community'
        );

        if (community.ok) {
            state.communityUid = community.data?.bot?.uid || null;

            console.log(
                '[BOT COMUNIDAD]',
                community.data?.bot,
                'Chats:',
                community.data?.threadIds || []
            );
        } else {
            console.error(
                '[BOT COMUNIDAD]',
                community.status,
                community.data
            );
        }
    }
}

// ============================================================================
// ENVÍO DE MENSAJES
// ============================================================================

function sendMessage(
    threadId,
    text,
    source = 'developer',
    replyId = null
) {
    const id = String(threadId || '');
    const content = clean(text).slice(0, 2000);

    if (!id || !content) {
        return Promise.resolve({
            ok: false,
            status: 400,
            data: {
                error: 'Falta threadId o texto.'
            }
        });
    }

    const task = sendQueue.then(async () => {
        const key = `${source}:${id}`;

        const previous = lastSent.get(key) || 0;

        const wait = Math.max(
            0,
            1100 - (Date.now() - previous)
        );

        if (wait) await sleep(wait);

        const payload = {
            content
        };

        if (replyId) {
            payload.replyMessageId = String(replyId);
        }

        let result = await anarchy(
            `/threads/${encodeURIComponent(id)}/messages`,
            'POST',
            payload,
            source
        );

        if (result.status === 429) {
            const retry = Number(result.retryAfter);

            await sleep(
                Number.isFinite(retry) && retry > 0
                    ? retry * 1000
                    : 2500
            );

            result = await anarchy(
                `/threads/${encodeURIComponent(id)}/messages`,
                'POST',
                payload,
                source
            );
        }

        if (result.ok) {
            lastSent.set(key, Date.now());
        } else {
            console.error(
                '[SEND ERROR]',
                result.status,
                result.data
            );
        }

        return result;
    });

    sendQueue = task.catch(error => {
        console.error('[SEND QUEUE]', error);
    });

    return task;
}

async function sendLong(
    threadId,
    text,
    source = 'developer',
    replyId = null
) {
    const chunks = splitText(clean(text));

    const results = [];

    for (let i = 0; i < chunks.length; i++) {
        const result = await sendMessage(
            threadId,
            chunks[i],
            source,
            i === 0 ? replyId : null
        );

        results.push(result);

        if (!result.ok) break;
    }

    return results;
}

// ============================================================================
// GENSHIN-DB — BASE DE DATOS LOCAL Y API DE RESPALDO
// ============================================================================

const DB_OPTIONS = {
    dumpResult: true,
    matchNames: true,
    matchAltNames: true,
    matchAliases: true,
    queryLanguages: ['English', 'Spanish'],
    resultLanguage: 'Spanish'
};

function unwrap(value) {
    if (!value) return null;

    if (Array.isArray(value)) {
        return value[0] || null;
    }

    if (value.result !== undefined) {
        return value.result;
    }

    return value;
}

async function gameData(folder, query) {
    const key = `game:${folder}:${normalize(query)}`;

    const cached = cache.get(key);

    if (cached && cached.until > Date.now()) {
        return cached.value;
    }

    // Primera opción: base de datos local.
    try {
        const result = unwrap(
            GDB.searchFolder(folder, query, DB_OPTIONS)
        );

        if (result) {
            cache.set(key, {
                value: result,
                until: Date.now() + 12 * 60 * 60 * 1000
            });

            return result;
        }

    } catch (error) {
        console.warn(
            `[GENSHIN DB LOCAL] ${folder}/${query}: ${error.message}`
        );
    }

    // Segunda opción: API HTTP.
    try {
        const params = new URLSearchParams({
            query,
            dumpResult: 'true',
            matchNames: 'true',
            matchAltNames: 'true',
            matchAliases: 'true',
            queryLanguages: 'english,spanish',
            resultLanguage: 'spanish'
        });

        const response = await fetch(
            `${GAME_API}/${encodeURIComponent(folder)}?${params}`,
            {
                headers: {
                    Accept: 'application/json'
                },
                signal: AbortSignal.timeout(15000)
            }
        );

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const result = unwrap(await response.json());

        if (!result) return null;

        cache.set(key, {
            value: result,
            until: Date.now() + 6 * 60 * 60 * 1000
        });

        return result;

    } catch (error) {
        console.error(
            `[GENSHIN DB HTTP] ${folder}/${query}: ${error.message}`
        );

        return null;
    }
}

// ============================================================================
// GENSHIN BUILDS — ÍNDICE DEL REPOSITORIO
// ============================================================================

async function getBuildTree(force = false) {
    if (
        !force &&
        treeCache &&
        treeExpires > Date.now()
    ) {
        return treeCache;
    }

    if (treePromise) return treePromise;

    treePromise = (async () => {
        const headers = {
            Accept: 'application/vnd.github+json',
            'User-Agent': 'Katherine-Genshin-Bot',
            'X-GitHub-Api-Version': '2022-11-28'
        };

        if (GITHUB_TOKEN) {
            headers.Authorization = `Bearer ${GITHUB_TOKEN}`;
        }

        const response = await fetch(
            TREE_URL,
            {
                headers,
                signal: AbortSignal.timeout(25000)
            }
        );

        if (!response.ok) {
            throw new Error(`GitHub respondió HTTP ${response.status}.`);
        }

        const payload = await response.json();

        if (!Array.isArray(payload.tree) || payload.truncated) {
            throw new Error('GitHub devolvió un índice incompleto.');
        }

        treeCache = payload.tree
            .filter(x => x.type === 'blob')
            .map(x => x.path);

        treeExpires = Date.now() + 60 * 60 * 1000;

        console.log(
            `[BUILDS] Índice cargado: ${treeCache.length} archivos.`
        );

        return treeCache;
    })();

    try {
        return await treePromise;
    } finally {
        treePromise = null;
    }
}

const aliases = {
    raiden: 'raiden-shogun',
    ayaka: 'kamisato-ayaka',
    kazuha: 'kaedehara-kazuha',
    kokomi: 'sangonomiya-kokomi',
    itto: 'arataki-itto',
    yae: 'yae-miko',
    childe: 'tartaglia',
    scaramouche: 'wanderer',
    shinobu: 'kuki-shinobu',
    kuki: 'kuki-shinobu'
};

function slugCandidates(query, character) {
    const result = new Set();

    for (const value of [
        query,
        character?.name,
        character?.id,
        character?.filename
    ]) {
        if (typeof value !== 'string' || !value.trim()) continue;

        const slug = slugify(value);

        result.add(slug);

        if (aliases[slug]) {
            result.add(aliases[slug]);
        }
    }

    return [...result];
}

async function fetchBuildFile(file) {
    const key = `build:${file}`;

    const cached = cache.get(key);

    if (cached && cached.until > Date.now()) {
        return cached.value;
    }

    const url = RAW_URL +
        file.split('/').map(encodeURIComponent).join('/');

    try {
        const response = await fetch(
            url,
            {
                headers: {
                    Accept: 'application/json'
                },
                signal: AbortSignal.timeout(15000)
            }
        );

        if (!response.ok) return null;

        const json = await response.json();

        cache.set(key, {
            value: json,
            until: Date.now() + 6 * 60 * 60 * 1000
        });

        return json;

    } catch (error) {
        console.warn(
            `[BUILDS] No se pudo leer ${file}: ${error.message}`
        );

        return null;
    }
}

const BUILD_FILES = [
    'build-notes.json',
    'weapons.json',
    'artifacts-sets.json',
    'artifacts-mainstats.json',
    'artifacts-substats.json',
    'talents.json'
];

async function findCharacterBuilds(query, character) {
    const tree = await getBuildTree();

    const candidates = slugCandidates(
        query,
        character
    );

    const metadataPath = tree.find(file =>
        file.startsWith('src/content/') &&
        file.endsWith('/metadata.json') &&
        candidates.includes(file.split('/').at(-2))
    );

    if (!metadataPath) return null;

    const root = metadataPath.slice(0, -'/metadata.json'.length);
    const prefix = `${root}/`;

    const rootFiles = new Set();
    const folders = new Map();

    for (const file of tree) {
        if (!file.startsWith(prefix)) continue;

        const parts = file.slice(prefix.length).split('/');

        if (parts.length === 1) {
            rootFiles.add(parts[0]);
        } else if (
            parts.length === 2 &&
            BUILD_FILES.includes(parts[1])
        ) {
            if (!folders.has(parts[0])) {
                folders.set(parts[0], new Set());
            }

            folders.get(parts[0]).add(parts[1]);
        }
    }

    const metadata = await fetchBuildFile(metadataPath) || {};

    const shared = {};

    for (const filename of BUILD_FILES) {
        if (rootFiles.has(filename)) {
            shared[filename] = await fetchBuildFile(
                `${root}/${filename}`
            );
        }
    }

    const builds = [];

    for (const [folder, files] of folders.entries()) {
        const data = {};

        for (const filename of BUILD_FILES) {
            data[filename] = files.has(filename)
                ? await fetchBuildFile(`${root}/${folder}/${filename}`)
                : (shared[filename] || null);
        }

        const notes = data['build-notes.json'] || {};

        const updated =
            notes.last_updated ||
            metadata.last_updated ||
            '';

        if (String(updated).toUpperCase() === 'WIP') continue;

        builds.push({
            folder,
            name: guideText(notes.name) || displayId(folder),
            best: Boolean(notes.best),
            updated,
            data
        });
    }

    if (
        !builds.length &&
        BUILD_FILES.some(file => shared[file])
    ) {
        builds.push({
            folder: 'general',
            name: 'Build general',
            best: true,
            updated: metadata.last_updated || '',
            data: shared
        });
    }

    builds.sort((a, b) =>
        a.best !== b.best
            ? Number(b.best) - Number(a.best)
            : a.name.localeCompare(b.name)
    );

    return {
        root,
        metadata,
        builds
    };
}

// ============================================================================
// FORMATO DE BUILDS
// ============================================================================

function buildItem(item) {
    if (typeof item === 'string') return displayId(item);
    if (!item || typeof item !== 'object') return displayId(item);

    let value = displayId(item.name || item.id);

    if (item.refinement) value += ` R${item.refinement}`;
    if (item.pieces) value += ` (${item.pieces} piezas)`;

    const note = guideText(item.note);

    if (note) value += ` — ${note}`;

    return value;
}

function formatWeapons(data) {
    const list = data?.weapons?.weapons;

    if (!Array.isArray(list) || !list.length) {
        return 'No disponible en esta build.';
    }

    const ranked = list.map((group, index) => {
        const items = (group.items || [])
            .map(buildItem)
            .filter(Boolean);

        return items.length
            ? `${index + 1}. ${items[0]}${items.length > 1 ? `\n   Alternativas: ${items.slice(1).join(' / ')}` : ''}`
            : null;
    }).filter(Boolean);

    const conditional = (data.weapons.conditional || [])
        .map(buildItem)
        .filter(Boolean);

    return `ARMAS RECOMENDADAS\n${ranked.join('\n')}` +
        (conditional.length
            ? `\n\nARMAS CONDICIONALES\n${conditional.join('\n')}`
            : '');
}

function formatArtifacts(data) {
    const sets = data?.artifact_sets;

    if (!Array.isArray(sets) || !sets.length) {
        return 'No disponible en esta build.';
    }

    return sets.slice(0, 6).map((entry, index) => {
        const groups = (entry.groups || []).map(group => {
            if (Array.isArray(group.choices)) {
                return group.choices
                    .map(choice => (choice.items || []).map(buildItem).join(' + '))
                    .filter(Boolean)
                    .join(' O ');
            }

            const items = (group.items || [])
                .map(buildItem)
                .filter(Boolean)
                .join(' / ');

            return group.choose
                ? `Elegir entre: ${items}`
                : items;
        }).filter(Boolean);

        return `${index + 1}. ${groups.join(' + ')}`;
    }).join('\n');
}

function formatMainStats(data) {
    const stats = data?.main_stats;

    if (!stats) return 'No disponible en esta build.';

    const show = value =>
        Array.isArray(value)
            ? value.map(buildItem).join(' / ')
            : 'No especificado';

    return (
        `Reloj: ${show(stats.sands)}\n` +
        `Cáliz: ${show(stats.goblet)}\n` +
        `Corona: ${show(stats.circlet)}`
    );
}

function formatSubstats(data) {
    const list = data?.substats_priority;

    if (!Array.isArray(list) || !list.length) {
        return 'No disponible en esta build.';
    }

    return list.map((item, index) => {
        if (typeof item === 'string') {
            return `${index + 1}. ${displayId(item)}`;
        }

        const group = Array.isArray(item.items)
            ? item.items
            : [item];

        return `${index + 1}. ${item.approx ? '≈ ' : ''}${group.map(buildItem).join(' / ')}`;
    }).join('\n');
}

function formatTalents(data) {
    const list = data?.talents;

    if (!Array.isArray(list) || !list.length) {
        return 'No disponible en esta build.';
    }

    return list.map((group, index) =>
        `${index + 1}. ${(group.items || []).map(buildItem).join(' = ')}${group.approx ? ' (prioridad similar)' : ''}`
    ).join('\n');
}

function formatBuild(build, index, total) {
    const data = build.data;

    const notes = data['build-notes.json']?.notes || [];

    const notesText = Array.isArray(notes)
        ? notes.map(guideText).filter(Boolean).join('\n')
        : '';

    return [
        `BUILD ${index + 1}/${total}: ${build.name}${build.best ? ' (RECOMENDADA COMO PRINCIPAL EN LA GUÍA)' : ''}`,
        build.updated ? `Versión indicada: ${build.updated}` : '',
        '',
        formatWeapons(data['weapons.json']),
        '',
        'ARTEFACTOS',
        formatArtifacts(data['artifacts-sets.json']),
        '',
        'ESTADÍSTICAS PRINCIPALES',
        formatMainStats(data['artifacts-mainstats.json']),
        '',
        'SUBESTADÍSTICAS',
        formatSubstats(data['artifacts-substats.json']),
        '',
        'PRIORIDAD DE TALENTOS',
        formatTalents(data['talents.json']),
        notesText ? `\nNOTAS\n${notesText}` : ''
    ].filter(Boolean).join('\n');
}

// ============================================================================
// MATERIALES DE PERSONAJES
// ============================================================================

function extractMaterials(character) {
    const totals = new Map();

    const ignoredKeys = new Set([
        'name', 'description', 'desc',
        'icon', 'image', 'rarity', 'type'
    ]);

    function add(category, name, amount) {
        if (
            !name ||
            !Number.isFinite(Number(amount)) ||
            Number(amount) <= 0
        ) return;

        const key = `${category}|${name}`;

        totals.set(
            key,
            (totals.get(key) || 0) + Number(amount)
        );
    }

    function walk(node, category, depth = 0) {
        if (!node || depth > 12) return;

        if (Array.isArray(node)) {
            for (const child of node) {
                walk(child, category, depth + 1);
            }
            return;
        }

        if (typeof node !== 'object') return;

        // Acepta tanto [{name, count}] como {Material: cantidad}.
        const explicitName = localize(
            node.name ||
            node.itemName ||
            node.material ||
            node.item
        );

        const explicitAmount =
            node.count ??
            node.amount ??
            node.quantity;

        if (
            explicitName &&
            explicitAmount !== undefined
        ) {
            add(category, explicitName, explicitAmount);
            return;
        }

        for (const [key, value] of Object.entries(node)) {
            if (ignoredKeys.has(key)) continue;

            const nextCategory =
                /ascend|promote/i.test(key)
                    ? 'Ascensión'
                    : /talent|skill|^lvl/i.test(key)
                        ? 'Talentos'
                        : category;

            if (
                typeof value === 'number' ||
                (typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value))
            ) {
                add(nextCategory, key, value);
            } else {
                walk(value, nextCategory, depth + 1);
            }
        }
    }

    for (const [key, value] of Object.entries(character?.costs || {})) {
        const category =
            /ascend|promote/i.test(key)
                ? 'Ascensión'
                : /talent|skill|^lvl/i.test(key)
                    ? 'Talentos'
                    : 'Otros';

        walk(value, category);
    }

    const result = {
        Ascensión: [],
        Talentos: [],
        Otros: []
    };

    for (const [key, amount] of totals.entries()) {
        const separator = key.indexOf('|');
        const category = key.slice(0, separator);
        const name = key.slice(separator + 1);

        (result[category] || result.Otros).push(
            `${amount.toLocaleString('es-CL')} × ${name}`
        );
    }

    return result;
}

function formatMaterials(character) {
    const materials = extractMaterials(character);
    const sections = [];

    for (const category of ['Ascensión', 'Talentos', 'Otros']) {
        if (materials[category].length) {
            sections.push(
                `${category.toUpperCase()}\n${materials[category].map(x => `- ${x}`).join('\n')}`
            );
        }
    }

    return sections.length
        ? sections.join('\n\n')
        : 'La ficha no incluye una lista de materiales reconocible; no voy a inventarlos.';
}

// ============================================================================
// EQUIPOS RECOMENDADOS
// ============================================================================

const TEAM_GUIDES = {
    cyno: [
        'Cyno / Nahida / Furina / Baizhu — Quickbloom.',
        'Cyno / Nahida / Xingqiu / Baizhu — Hyperbloom.'
    ],
    nahida: [
        'Nahida / Nilou / Kokomi / Collei — Florecimiento.',
        'Nahida / Cyno / Furina / Baizhu — Quickbloom.',
        'Nahida / Alhaitham / Yae Miko / Kuki Shinobu — Aceleración.'
    ],
    dehya: [
        'Dehya / Bennett / Xiangling / Kazuha — Pyro.',
        'Dehya / Furina / Bennett / Jean — equipo con Furina.'
    ],
    alhaitham: [
        'Alhaitham / Nahida / Yae Miko / Kuki Shinobu — Aceleración.',
        'Alhaitham / Nahida / Xingqiu / Kuki Shinobu — Hyperbloom.'
    ],
    tighnari: [
        'Tighnari / Nahida / Yae Miko / Zhongli — Aceleración.',
        'Tighnari / Fischl / Nahida / Baizhu — Aceleración.'
    ],
    nilou: [
        'Nilou / Nahida / Kokomi / Collei — Florecimiento.',
        'Nilou / Nahida / Barbara / Yaoyao — Florecimiento.'
    ],
    'hu tao': [
        'Hu Tao / Xingqiu / Yelan / Zhongli — Vaporizado.',
        'Hu Tao / Furina / Yelan / Xianyun — Vaporizado.'
    ],
    xiao: [
        'Xiao / Faruzan / Furina / Xianyun — ataques en caída.',
        'Xiao / Faruzan / Bennett / Zhongli — Anemo.'
    ],
    'raiden shogun': [
        'Raiden / Xiangling / Bennett / Xingqiu — Nacional.',
        'Raiden / Nahida / Yelan / Baizhu — Hyperbloom.'
    ],
    furina: [
        'Furina / Neuvillette / Kazuha / Xilonen — Hydro.',
        'Furina / Hu Tao / Yelan / Xianyun — Vaporizado.'
    ],
    neuvillette: [
        'Neuvillette / Furina / Kazuha / Xilonen — Hydro.',
        'Neuvillette / Furina / Fischl / Baizhu — Hydro y Electro.'
    ],
    arlecchino: [
        'Arlecchino / Yelan / Bennett / Kazuha — Vaporizado.',
        'Arlecchino / Xingqiu / Bennett / Zhongli — Vaporizado.'
    ],
    mavuika: [
        'Mavuika / Citlali / Xilonen / Bennett — Pyro.',
        'Mavuika / Furina / Xilonen / Bennett — Vaporizado.'
    ],
    yelan: [
        'Yelan / Hu Tao / Xingqiu / Zhongli — Vaporizado.',
        'Yelan / Arlecchino / Bennett / Kazuha — Vaporizado.'
    ],
    fischl: [
        'Fischl / Clorinde / Nahida / Kazuha — Aceleración.',
        'Fischl / Tighnari / Nahida / Baizhu — Aceleración.'
    ],
    kaeya: [
        'Kaeya / Shenhe / Kazuha / Kokomi — Congelamiento.',
        'Kaeya / Xiangling / Bennett / Chongyun — Derretido.'
    ]
};

// ============================================================================
// FICHA COMPLETA DE PERSONAJE
// ============================================================================

async function characterReport(query, index = 0) {
    const character = await gameData('characters', query);

    if (!character) {
        throw new Error(`Genshin-DB no encontró ${query}.`);
    }

    let info = null;
    let buildLookupError = '';

    try {
        info = await findCharacterBuilds(query, character);
    } catch (error) {
        buildLookupError = error.message;
        console.error('[BUILD TREE]', error.message);
    }

    const builds = info?.builds || [];
    const name = character.name || query;
    const teams = TEAM_GUIDES[normalize(name)] || null;

    const selected = builds.length
        ? Math.max(0, Math.min(index, builds.length - 1))
        : 0;

    const build = builds.length
        ? formatBuild(builds[selected], selected, builds.length)
        : 'No encontré una build publicada para este personaje.';

    const sections = [
        `KATHERINE — FICHA DE ${name.toUpperCase()}`,
        `Rareza: ${character.rarity || 'No indicada'} estrellas`,
        `Elemento: ${character.elementType || 'No indicado'}`,
        `Arma: ${character.weaponType || info?.metadata?.weapon || 'No indicada'}`,
        `Región: ${character.region || 'No indicada'}`,
        character.description || character.desc || '',
        '',
        'MATERIALES DE PROGRESIÓN',
        formatMaterials(character),
        '',
        'EQUIPOS RECOMENDADOS',
        teams
            ? teams.map((x, i) => `${i + 1}. ${x}`).join('\n')
            : 'No tengo una composición curada para este personaje.',
        buildLookupError
            ? `No pude consultar el repositorio de builds: ${buildLookupError}`
            : '',
        '',
        build,
        builds.length
            ? `Hay ${builds.length} build(s). Usa !build ${name} 2 para consultar otra.`
            : '',
        '',
        'Fuentes: Genshin-DB y Genshin Builds. Las recomendaciones pueden cambiar con las actualizaciones.'
    ];

    return {
        character,
        builds,
        text: sections.filter(Boolean).join('\n')
    };
}

// ============================================================================
// COMANDOS DE DATOS GENSHIN
// ============================================================================

async function characterCommand(thread, args, source, replyId) {
    if (!args.length) {
        await sendMessage(
            thread,
            'Uso: !personaje nombre. Ejemplo: !personaje Cyno.',
            source,
            replyId
        );
        return;
    }

    const query = args.join(' ');

    await sendMessage(
        thread,
        `Consultando la ficha de ${query}...`,
        source
    );

    try {
        const report = await characterReport(query);
        await sendLong(thread, report.text, source);
    } catch (error) {
        console.error('[PERSONAJE]', error);

        await sendMessage(
            thread,
            `No pude completar la ficha de ${query}: ${error.message}`,
            source
        );
    }
}

async function buildCommand(thread, args, source, replyId) {
    if (!args.length) {
        await sendMessage(
            thread,
            'Uso: !build nombre [número]. Ejemplo: !build Cyno 2.',
            source,
            replyId
        );
        return;
    }

    let index = 0;
    const last = Number(args.at(-1));

    if (Number.isInteger(last) && last >= 1) {
        index = last - 1;
        args.pop();
    }

    const query = args.join(' ');

    try {
        const character = await gameData('characters', query);

        if (!character) {
            throw new Error(`No se encontró ${query}.`);
        }

        const info = await findCharacterBuilds(query, character);
        const builds = info?.builds || [];

        if (!builds.length) {
            await sendMessage(
                thread,
                `No encontré builds para ${query}. Usa !diagnosticogenshin ${query}.`,
                source,
                replyId
            );
            return;
        }

        if (!builds[index]) {
            await sendMessage(
                thread,
                `Encontré ${builds.length} build(s). Usa !build ${character.name || query} 1 hasta ${builds.length}.`,
                source,
                replyId
            );
            return;
        }

        await sendLong(
            thread,
            formatBuild(builds[index], index, builds.length),
            source,
            replyId
        );

    } catch (error) {
        console.error('[BUILD]', error);

        await sendMessage(
            thread,
            `Error consultando build: ${error.message}`,
            source,
            replyId
        );
    }
}

async function materialsCommand(thread, args, source, replyId) {
    if (!args.length) {
        await sendMessage(
            thread,
            'Uso: !materiales nombre del personaje.',
            source,
            replyId
        );
        return;
    }

    const character = await gameData(
        'characters',
        args.join(' ')
    );

    if (!character) {
        await sendMessage(
            thread,
            'No encontré ese personaje.',
            source,
            replyId
        );
        return;
    }

    await sendLong(
        thread,
        `MATERIALES DE ${character.name}\n\n${formatMaterials(character)}`,
        source,
        replyId
    );
}

async function teamsCommand(thread, args, source, replyId) {
    if (!args.length) {
        await sendMessage(
            thread,
            'Uso: !equipos nombre del personaje.',
            source,
            replyId
        );
        return;
    }

    const character = await gameData(
        'characters',
        args.join(' ')
    );

    if (!character) {
        await sendMessage(
            thread,
            'No encontré ese personaje.',
            source,
            replyId
        );
        return;
    }

    const list = TEAM_GUIDES[
        normalize(character.name || args.join(' '))
    ];

    await sendMessage(
        thread,
        list
            ? `EQUIPOS PARA ${character.name}\n\n${list.map((x, i) => `${i + 1}. ${x}`).join('\n')}\n\nComposiciones orientativas.`
            : `No tengo equipos curados para ${character.name}.`,
        source,
        replyId
    );
}

async function objectCommand(thread, folder, args, source, replyId) {
    const examples = {
        weapons: '!arma Báculo de Homa',
        artifacts: '!artefacto Sueños Áureos',
        domains: '!dominio nombre',
        enemies: '!enemigo nombre',
        foods: '!comida nombre',
        materials: '!material nombre',
        achievements: '!logro nombre'
    };

    if (!args.length) {
        await sendMessage(
            thread,
            `Uso: ${examples[folder]}`,
            source,
            replyId
        );
        return;
    }

    const query = args.join(' ');

    const item = await gameData(folder, query);

    if (!item) {
        await sendMessage(
            thread,
            `No encontré "${query}" en ${folder}.`,
            source,
            replyId
        );
        return;
    }

    const fields = [
        'description', 'desc', 'effectName', 'effect',
        'passiveName', 'passiveDesc', 'lore', 'story',
        'weaponType', 'rarity', 'baseAttack', 'subStatText',
        'subStat', 'location', 'region', 'domainType',
        'enemyType', 'foodType', 'ingredients', 'recipe',
        'source', 'drop'
    ];

    const lines = [item.name || query];

    for (const field of fields) {
        const value = item[field];

        if (value == null || value === '') continue;

        if (Array.isArray(value)) {
            const text = value.map(localize).filter(Boolean).join(', ');
            if (text) lines.push(`${field}: ${text}`);
        } else if (typeof value === 'object') {
            const text = localize(value);
            if (text) lines.push(`${field}: ${text}`);
        } else {
            lines.push(`${field}: ${value}`);
        }
    }

    await sendLong(
        thread,
        lines.join('\n'),
        source,
        replyId
    );
}

async function talentCommand(thread, args, source, replyId) {
    if (!args.length) {
        await sendMessage(
            thread,
            'Uso: !talento nombre del personaje.',
            source,
            replyId
        );
        return;
    }

    const character = await gameData(
        'characters',
        args.join(' ')
    );

    if (!character) {
        await sendMessage(
            thread,
            'No encontré ese personaje.',
            source,
            replyId
        );
        return;
    }

    const fields = [
        ['Ataque normal', character.combat1],
        ['Habilidad elemental', character.combat2],
        ['Habilidad definitiva', character.combat3],
        ['Pasiva 1', character.passive1],
        ['Pasiva 2', character.passive2],
        ['Pasiva 3', character.passive3]
    ];

    const text = fields
        .filter(([, value]) => value)
        .map(([label, value]) =>
            `${label}: ${value.name || label}\n${value.description || value.desc || ''}`
        )
        .join('\n\n');

    await sendLong(
        thread,
        `TALENTOS DE ${character.name}\n\n${text || 'No hay datos disponibles.'}`,
        source,
        replyId
    );
}

async function constellationCommand(thread, args, source, replyId) {
    if (!args.length) {
        await sendMessage(
            thread,
            'Uso: !constelacion nombre del personaje.',
            source,
            replyId
        );
        return;
    }

    const character = await gameData(
        'characters',
        args.join(' ')
    );

    if (!character) {
        await sendMessage(
            thread,
            'No encontré ese personaje.',
            source,
            replyId
        );
        return;
    }

    const lines = [];

    for (let i = 1; i <= 6; i++) {
        const item = character[`constellation${i}`];

        if (item) {
            lines.push(
                `${i}. ${item.name || `Constelación ${i}`}\n${item.description || item.desc || ''}`
            );
        }
    }

    await sendLong(
        thread,
        `CONSTELACIONES DE ${character.name}\n\n${lines.join('\n\n') || 'No hay datos disponibles.'}`,
        source,
        replyId
    );
}

async function genshinDiagnostic(thread, args, source, replyId) {
    if (!args.length) {
        await sendMessage(
            thread,
            'Uso: !diagnosticogenshin Cyno.',
            source,
            replyId
        );
        return;
    }

    const query = args.join(' ');

    let local = null;
    let localError = '';

    try {
        local = unwrap(
            GDB.searchFolder('characters', query, DB_OPTIONS)
        );
    } catch (error) {
        localError = error.message;
    }

    const character = local || await gameData('characters', query);

    let builds = null;
    let buildError = '';

    if (character) {
        try {
            builds = await findCharacterBuilds(query, character);
        } catch (error) {
            buildError = error.message;
        }
    }

    await sendMessage(
        thread,
        [
            'DIAGNÓSTICO GENSHIN',
            `Búsqueda: ${query}`,
            `Genshin-DB local: ${local ? 'OK' : `No encontrado ${localError}`}`,
            `API de respaldo: ${character ? 'OK' : 'No encontrado'}`,
            `Nombre devuelto: ${character?.name || 'Ninguno'}`,
            `Repositorio Builds: ${builds ? 'OK' : buildError || 'No encontrado'}`,
            `Carpeta: ${builds?.root || 'No encontrada'}`,
            `Builds: ${builds?.builds?.map(x => x.folder).join(', ') || 'Ninguna'}`
        ].join('\n'),
        source,
        replyId
    );
}

// ============================================================================
// CURIOSIDADES GENSHIN — SIN REPETIR HASTA AGOTAR LA LISTA
// ============================================================================

const curiosities = [
    'Cyno es el General Mahamatra de la Akademiya de Sumeru.',
    'Nahida también es conocida como la Pequeña Señorita Kusanali.',
    'Dehya recibe el título de Melena Llameante y pertenece a los Eremitas.',
    'Alhaitham ocupa el cargo de escriba de la Akademiya.',
    'Tighnari trabaja como guardabosques en el Bosque Avidya.',
    'Nilou es una bailarina del Teatro Zubayr.',
    'Kaeya pertenece al linaje Alberich, vinculado a Khaenri’ah.',
    'Dainsleif es conocido como el Guardarramas.',
    'La catástrofe de Khaenri’ah ocurrió aproximadamente quinientos años antes de la aventura principal.',
    'Los Fatui tienen once Heraldos.',
    'La Orden del Abismo y los Fatui son organizaciones distintas.',
    'La Invocación de los Siete es el juego de cartas coleccionables de Teyvat.',
    'Dvalin es conocido como Stormterror y forma parte de los Cuatro Vientos.',
    'Andrius también recibe el nombre de Lupus Boreas.',
    'Enkanomiya es una región subterránea vinculada a Watatsumi.',
    'Dragonspine tiene una mecánica especial de frío extremo.',
    'Arlecchino recibe el título de Padre dentro de la Casa de la Hoguera.',
    'Hu Tao dirige la Funeraria Wangsheng.',
    'Neuvillette es el Juez Supremo de Fontaine.',
    'Los melusinos son una especie característica de Fontaine.',
    'Los Adeptus están estrechamente relacionados con la protección de Liyue.',
    'Las Visiones permiten a ciertos individuos canalizar poderes elementales.',
    'La Espiral del Abismo es un desafío de combate independiente de la exploración normal.',
    'El Viajero puede resonar con distintos elementos mediante las Estatuas de los Siete.',
    'La Akademiya fue uno de los centros de conocimiento más importantes de Teyvat.',
    'Oz es el compañero de Fischl.',
    'La historia de Inazuma está profundamente relacionada con el concepto de eternidad.',
    'Los Dragones Soberanos tienen una conexión antigua con los elementos de Teyvat.',
    'La historia de Natlan está ligada a sus tribus y tradiciones.',
    'El Gremio de Aventureros tiene presencia en varias naciones.',
    'Los Aranara están relacionados con los sueños y las leyendas de Sumeru.',
    'La Funeraria Wangsheng tiene una larga historia dentro de Liyue.',
    'La Casa de la Hoguera está relacionada con los Fatui.',
    'La historia de Fontaine está vinculada a la justicia y al poder Hydro.',
    'Los Cuatro Vientos son figuras importantes en la historia de Mondstadt.'
];

let curiosityBag = [];
let lastCuriosity = -1;

function nextCuriosity() {
    if (!curiosityBag.length) {
        curiosityBag = shuffle(curiosities.map((_, i) => i));

        if (
            curiosityBag.length > 1 &&
            curiosityBag[0] === lastCuriosity
        ) {
            [curiosityBag[0], curiosityBag[1]] =
                [curiosityBag[1], curiosityBag[0]];
        }
    }

    lastCuriosity = curiosityBag.pop();

    return curiosities[lastCuriosity];
}

// ============================================================================
// INTERACCIONES SOCIALES
// ============================================================================

const socialActions = {
    abrazo: ['abraza cariñosamente a', 'corre hacia y abraza a', 'rodea con sus brazos a'],
    abrazar: ['abraza cariñosamente a', 'corre hacia y abraza a', 'rodea con sus brazos a'],
    beso: ['le da un beso a', 'deja un beso en la mejilla de', 'se acerca y besa a'],
    besar: ['le da un beso a', 'deja un beso en la mejilla de', 'se acerca y besa a'],
    patada: ['le da una patada de broma a', 'lanza una patadita teatral hacia', 'intenta sorprender con una patada a'],
    golpe: ['le da un golpecito de broma a', 'le da un pequeño golpe teatral a'],
    cachetada: ['le da una cachetada teatral a', 'le da una bofetada de telenovela a'],
    acariciar: ['acaricia suavemente a', 'le acomoda el cabello a'],
    mimar: ['mima cariñosamente a', 'llena de cariño a'],
    morder: ['le da un mordisquito de broma a', 'intenta morder juguetonamente a'],
    consolar: ['consola a'],
    felicitar: ['felicita a', 'aplaude a'],
    saludar: ['saluda alegremente a', 'levanta la mano para saludar a'],
    proteger: ['protege a'],
    bailar: ['invita a bailar a', 'comienza a bailar junto a'],
    cosquillas: ['le hace cosquillas a', 'intenta hacer reír con cosquillas a'],
    chocar: ['choca los cinco con', 'celebra chocando la mano de'],
    highfive: ['choca los cinco con', 'celebra chocando la mano de'],
    ayudar: ['ayuda a', 'se ofrece a ayudar a'],
    regalar: ['le entrega una pequeña sorpresa a', 'regala un obsequio a'],
    acompañar: ['acompaña a', 'decide viajar junto a'],
    asustar: ['intenta asustar a', 'aparece de repente frente a'],
    curar: ['cura las heridas de', 'atiende a'],
    cargar: ['carga en brazos a', 'levanta con cuidado a'],
    empujar: ['empuja suavemente a', 'da un pequeño empujón a'],
    regañar: ['regaña a', 'llama la atención de'],
    admirar: ['admira a', 'observa con admiración a'],
    apoyar: ['muestra su apoyo a', 'anima a'],
    dormir: ['se queda dormida junto a', 'se acomoda para descansar junto a'],
    ignorar: ['decide ignorar dramáticamente a', 'finge no ver a'],
    mirar: ['mira fijamente a', 'observa en silencio a'],
    invitar: ['invita a una aventura a', 'propone una expedición a']
};

function getTarget(args, message) {
    for (const list of [
        message?.mentions,
        message?.mentionedUsers,
        message?.mentionList
    ]) {
        if (Array.isArray(list) && list.length) {
            const item = list[0];

            if (typeof item === 'string') {
                return item.startsWith('@') ? item : `@${item}`;
            }

            const name = item.nickname || item.name || item.uid;

            if (name) {
                return `@${String(name).replace(/^@/, '')}`;
            }
        }
    }

    const raw = args.join(' ').trim();
    const match = raw.match(/@[^\s,;]+/);

    if (match) return match[0];

    return raw
        ? `@${raw.split(/[\s,;]+/)[0].replace(/^@/, '')}`
        : null;
}

async function socialCommand(thread, command, args, event, source, replyId) {
    const target = getTarget(args, event.message);
    const actor = event.message?.author?.nickname || 'Viajero';

    if (!target) {
        await sendMessage(
            thread,
            `Uso: !${command} @usuario\nEjemplo: !${command} @zzzinn3r`,
            source,
            replyId
        );
        return;
    }

    const endings = {
        abrazo: 'Un abrazo siempre viene bien.',
        abrazar: 'Un abrazo siempre viene bien.',
        beso: 'Qué momento tan bonito.',
        besar: 'Qué momento tan bonito.',
        patada: '¡Que comience el combate!',
        golpe: 'Eso fue inesperado.',
        cachetada: 'Drama en Teyvat.',
        acariciar: 'Un gesto de cariño.',
        mimar: 'Se merece todo el cariño.',
        morder: '¡Auch! Eso sí fue inesperado.',
        consolar: 'Nadie tiene que afrontar una mala aventura en soledad.',
        felicitar: '¡Bien hecho!',
        saludar: '¡Hola, viajero!',
        proteger: 'No permitiré que le hagan daño.',
        bailar: '¡Que suene la música!',
        cosquillas: '¡No puede dejar de reír!',
        chocar: '¡Buen trabajo en equipo!',
        highfive: '¡Buen trabajo en equipo!',
        ayudar: 'Para eso están los compañeros.',
        regalar: 'Un regalo inesperado.',
        acompañar: 'Nunca hay que viajar solo.',
        asustar: '¡Sorpresa!',
        curar: '¡De vuelta a la aventura!',
        cargar: 'Qué compañerismo.',
        empujar: '¡Cuidado!',
        regañar: 'A portarse bien.',
        admirar: 'Una persona digna de admiración.',
        apoyar: 'Siempre habrá alguien de tu lado.',
        dormir: 'Hora de descansar.',
        ignorar: 'El silencio también es una respuesta.',
        mirar: 'El silencio se vuelve incómodo...',
        invitar: 'Prepara tu equipo.'
    };

    let sentence;

    if (command === 'consolar') {
        sentence = `${actor} se acerca a ${target} y le ofrece un abrazo reconfortante.`;
    } else if (command === 'proteger') {
        sentence = `${actor} se coloca delante de ${target} para protegerle.`;
    } else {
        sentence = `${actor} ${random(socialActions[command])} ${target}.`;
    }

    await sendMessage(
        thread,
        `${sentence}\n${endings[command] || '¡Una nueva interacción!'}`,
        source,
        replyId
    );
}

// ============================================================================
// ENTRETENIMIENTO
// ============================================================================

const triviaQuestions = [
    { q: '¿Qué elemento utiliza Cyno?', a: ['Pyro', 'Electro', 'Dendro', 'Anemo'], c: 1 },
    { q: '¿Quién es la Arconte Dendro?', a: ['Ei', 'Nahida', 'Furina', 'Mavuika'], c: 1 },
    { q: '¿Quién dirige la Funeraria Wangsheng?', a: ['Ganyu', 'Yelan', 'Hu Tao', 'Keqing'], c: 2 },
    { q: '¿Cuántos corazones tiene un pulpo?', a: ['Uno', 'Dos', 'Tres', 'Cuatro'], c: 2 },
    { q: '¿Qué planeta se conoce como el planeta rojo?', a: ['Venus', 'Marte', 'Saturno', 'Júpiter'], c: 1 },
    { q: '¿Qué organización tiene once Heraldos?', a: ['Fatui', 'Adeptus', 'Caballeros', 'Gremio'], c: 0 },
    { q: '¿Cuál es el arma de Dehya?', a: ['Arco', 'Catalizador', 'Mandoble', 'Lanza'], c: 2 }
];

const jokes = [
    '¿Por qué Cyno nunca pierde una discusión? Porque siempre tiene una sentencia preparada.',
    '¿Qué le dijo un slime a otro? Nos vemos en la próxima reacción.',
    '¿Por qué el jugador nunca tiene Mora? Porque todo se le va en artefactos.',
    '¿Qué hace un Hilichurl cuando tiene frío? Busca un Pyro.',
    'El Abismo pidió refuerzos, pero todos estaban gastando resina.',
    '¿Por qué el Viajero no se pierde? Porque siempre tiene un punto de teletransporte cerca.',
    'El artefacto tenía cuatro substats buenos. Al subirlo, todos fueron a DEF%.',
    'No es mala suerte en el gachapón. Es una inversión emocional a largo plazo.'
];

const fortunes = [
    'Hoy te esperan buenas noticias.',
    'La paciencia será tu mejor aliada.',
    'Una oportunidad inesperada aparecerá.',
    'No abandones tu objetivo.',
    'Los pequeños avances también cuentan.',
    'Hoy es un buen día para comenzar algo nuevo.'
];

const challenges = [
    'Completa un dominio con personajes que nunca utilizas.',
    'Derrota un jefe sin consumir comida.',
    'Consigue un logro que todavía no tengas.',
    'Explora una zona que aún no hayas completado.',
    'Completa un combate con cuatro elementos diferentes.',
    'Utiliza solo personajes de cuatro estrellas durante una expedición.',
    'Derrota un jefe sin usar tu personaje principal.'
];

function wishState(uid) {
    if (!state.wishes.has(uid)) {
        state.wishes.set(uid, {
            pity5: 0,
            pity4: 0,
            guaranteed: false
        });
    }

    return state.wishes.get(uid);
}

function simulateWish(uid) {
    const wish = wishState(uid);

    wish.pity5++;
    wish.pity4++;

    let chance = 0.006;

    if (wish.pity5 >= 74) {
        chance = Math.min(
            1,
            0.006 + (wish.pity5 - 73) * 0.06
        );
    }

    if (wish.pity5 >= 90 || Math.random() < chance) {
        const featured =
            wish.guaranteed || Math.random() < 0.5;

        const name = featured
            ? random(['Cyno', 'Nahida', 'Furina', 'Neuvillette', 'Arlecchino', 'Mavuika'])
            : random(['Diluc', 'Jean', 'Mona', 'Qiqi', 'Keqing', 'Tighnari', 'Dehya']);

        wish.guaranteed = !featured;
        wish.pity5 = 0;
        wish.pity4 = 0;

        return {
            stars: 5,
            name
        };
    }

    if (wish.pity4 >= 10 || Math.random() < 0.051) {
        wish.pity4 = 0;

        return {
            stars: 4,
            name: random(['Bennett', 'Xingqiu', 'Fischl', 'Sucrose', 'Diona', 'Rosaria', 'Kuki Shinobu'])
        };
    }

    return {
        stars: 3,
        name: random(['Espada del Alba', 'Arco de Cuervo', 'Gran Espada Sangrienta', 'Lanza de Hierro Blanco'])
    };
}

function startTrivia(thread) {
    const question = random(triviaQuestions);

    state.trivia.set(String(thread), {
        question,
        attempts: 0,
        expires: Date.now() + 120000
    });

    return `TRIVIA KATHERINE\n\n${question.q}\n\n${question.a.map((x, i) => `${i + 1}. ${x}`).join('\n')}\n\nResponde con !responder 1, 2, 3 o 4.`;
}

function answerTrivia(thread, uid, number) {
    const trivia = state.trivia.get(String(thread));

    if (!trivia) return 'No hay una trivia activa. Usa !trivia.';

    if (Date.now() > trivia.expires) {
        state.trivia.delete(String(thread));
        return 'La trivia expiró.';
    }

    const answer = Number(number);

    if (!Number.isInteger(answer) || answer < 1 || answer > 4) {
        return 'Responde con un número del 1 al 4.';
    }

    trivia.attempts++;

    if (answer - 1 === trivia.question.c) {
        state.trivia.delete(String(thread));
        state.points.set(uid, (state.points.get(uid) || 0) + 10);
        return '¡Respuesta correcta! Has ganado 10 puntos.';
    }

    if (trivia.attempts >= 3) {
        state.trivia.delete(String(thread));
        return `Se acabaron los intentos. La respuesta correcta era ${trivia.question.a[trivia.question.c]}.`;
    }

    return `No es correcto. Te quedan ${3 - trivia.attempts} intento(s).`;
}

function startNumberGame(thread) {
    state.numberGames.set(String(thread), {
        number: Math.floor(Math.random() * 100) + 1,
        attempts: 0,
        expires: Date.now() + 300000
    });

    return 'He elegido un número entre 1 y 100. Tienes siete intentos. Usa !intento número.';
}

function tryNumber(thread, uid, value) {
    const game = state.numberGames.get(String(thread));

    if (!game) return 'No hay una partida activa. Usa !adivina.';

    if (Date.now() > game.expires) {
        state.numberGames.delete(String(thread));
        return 'La partida expiró.';
    }

    const number = Number(value);

    if (!Number.isInteger(number) || number < 1 || number > 100) {
        return 'Escribe un entero del 1 al 100.';
    }

    game.attempts++;

    if (number === game.number) {
        state.numberGames.delete(String(thread));
        state.points.set(uid, (state.points.get(uid) || 0) + 15);
        return `¡Correcto! El número era ${number}. Ganaste 15 puntos.`;
    }

    if (game.attempts >= 7) {
        state.numberGames.delete(String(thread));
        return `Se terminaron los intentos. El número era ${game.number}.`;
    }

    return `${number < game.number ? 'El número es mayor.' : 'El número es menor.'}\nIntentos restantes: ${7 - game.attempts}.`;
}

// ============================================================================
// LIVE-ROOMS — LÍMITES DE ANARCHY
// ============================================================================

async function getLiveRooms(force = false) {
    if (!COMMUNITY_BOT_TOKEN) {
        return {
            ok: false,
            error: 'Falta COMMUNITY_BOT_TOKEN.'
        };
    }

    if (!force && roomCache && Date.now() < roomCacheExpires) {
        return {
            ok: true,
            rooms: roomCache
        };
    }

    if (roomPromise) return roomPromise;

    roomPromise = (async () => {
        const wait = Math.max(
            0,
            3000 - (Date.now() - lastRoomRequest)
        );

        if (wait) await sleep(wait);

        const now = Date.now();

        while (
            roomRequests.length &&
            roomRequests[0] < now - 3600000
        ) {
            roomRequests.shift();
        }

        if (roomRequests.length >= 300) {
            return {
                ok: false,
                error: 'Límite de 300 consultas por hora alcanzado.'
            };
        }

        lastRoomRequest = Date.now();
        roomRequests.push(lastRoomRequest);

        const result = await anarchy(
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

        roomCache = Array.isArray(result.data?.roomList)
            ? result.data.roomList
            : [];

        roomCacheExpires = Date.now() + 2500;

        return {
            ok: true,
            rooms: roomCache
        };
    })();

    try {
        return await roomPromise;
    } finally {
        roomPromise = null;
    }
}

// ============================================================================
// LIVEKIT
// ============================================================================

async function closeLocalCall(thread) {
    const id = String(thread);
    const call = state.calls.get(id);

    if (!call) return;

    call.stopping = true;
    state.calls.delete(id);

    try {
        if (call.track) {
            await call.track.close();
        } else if (call.source) {
            await call.source.close();
        }
    } catch (error) {
        console.warn('[LIVEKIT TRACK CLOSE]', error.message);
    }

    try {
        if (call.room?.isConnected) {
            await call.room.disconnect();
        }
    } catch (error) {
        console.warn('[LIVEKIT DISCONNECT]', error.message);
    }
}

async function publishSilentMic(call, sdk) {
    const rate = 48000;
    const channels = 1;
    const samples = 480;

    const source = new sdk.AudioSource(rate, channels);

    const track = sdk.LocalAudioTrack.createAudioTrack(
        'Katherine-Microphone',
        source
    );

    const options = new sdk.TrackPublishOptions();

    options.source = sdk.TrackSource.SOURCE_MICROPHONE;

    call.source = source;
    call.track = track;

    let attempts = 0;

    while (
        !call.stopping &&
        call.room.isConnected &&
        !call.published
    ) {
        try {
            await call.room.localParticipant.publishTrack(
                track,
                options
            );

            call.published = true;

            console.log(
                `[LIVEKIT] Micrófono publicado en ${call.thread}.`
            );

        } catch (error) {
            attempts++;

            if (attempts === 1 || attempts % 5 === 0) {
                console.log(
                    `[LIVEKIT] Esperando permiso, intento ${attempts}: ${error.message}`
                );
            }

            await sleep(1200);
        }
    }

    if (!call.published || call.stopping) return;

    const silence = new Int16Array(samples);

    while (
        !call.stopping &&
        call.room.isConnected
    ) {
        try {
            await source.captureFrame(
                new sdk.AudioFrame(
                    silence,
                    rate,
                    channels,
                    samples
                )
            );
        } catch (error) {
            if (!call.stopping) {
                console.error('[LIVEKIT AUDIO]', error.message);
            }
            break;
        }

        await sleep(10);
    }
}

async function joinCall(thread) {
    const id = String(thread);

    if (!COMMUNITY_BOT_TOKEN) {
        return {
            ok: false,
            message: 'Falta COMMUNITY_BOT_TOKEN.'
        };
    }

    if (state.calls.has(id)) {
        return {
            ok: true,
            message: 'Katherine ya está conectada a esta llamada.'
        };
    }

    if (state.calls.size >= 3) {
        return {
            ok: false,
            message: 'Katherine ya está conectada al máximo de tres salas.'
        };
    }

    let room = null;

    try {
        // Carga el SDK antes de solicitar el token de LiveKit de 60 segundos.
        const sdk = await import('@livekit/rtc-node');

        const listing = await getLiveRooms();

        if (!listing.ok) {
            return {
                ok: false,
                message: `No pude consultar las salas: ${listing.error}`
            };
        }

        const info = listing.rooms.find(
            item => String(item.threadId) === id
        );

        if (!info) {
            return {
                ok: false,
                message: 'No hay una llamada activa en este chat. Iníciala y vuelve a usar !llamada!.'
            };
        }

        if (info.joined) {
            return {
                ok: false,
                message: 'Anarchy indica que Katherine ya figura dentro de esta sala. Comprueba si Render se reinició.'
            };
        }

        const grant = await anarchy(
            `/live-rooms/${encodeURIComponent(id)}/join`,
            'POST',
            null,
            'community',
            true
        );

        if (!grant.ok) {
            return {
                ok: false,
                message: `Anarchy rechazó el ingreso: HTTP ${grant.status}: ${JSON.stringify(grant.data)}`
            };
        }

        const credentials = grant.data || {};

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

        room = new sdk.Room();

        const call = {
            thread: id,
            room,
            identity: credentials.identity,
            source: null,
            track: null,
            stopping: false,
            published: false
        };

        room.on(
            sdk.RoomEvent.Disconnected,
            () => {
                console.warn(`[LIVEKIT] Desconexión de ${id}.`);

                call.stopping = true;

                if (state.calls.get(id) === call) {
                    state.calls.delete(id);
                }
            }
        );

        // El token dura 60 segundos, conectar enseguida.
        await room.connect(
            credentials.url,
            credentials.token,
            {
                autoSubscribe: false,
                dynacast: true
            }
        );

        state.calls.set(id, call);

        console.log(
            `[LIVEKIT] Katherine conectada a ${id} como ${credentials.identity}.`
        );

        call.audioTask = publishSilentMic(
            call,
            sdk
        ).catch(error => {
            console.error('[LIVEKIT PUBLISH]', error);
        });

        return {
            ok: true,
            message: 'Katherine se unió a la llamada. Estoy habilitando el micrófono silencioso.'
        };

    } catch (error) {
        console.error('[LIVEKIT JOIN]', error);

        if (room) {
            try {
                if (room.isConnected) {
                    await room.disconnect();
                }
            } catch (_) {
                // La desconexión ya fue solicitada.
            }
        }

        await anarchy(
            `/live-rooms/${encodeURIComponent(id)}/leave`,
            'POST',
            null,
            'community'
        );

        return {
            ok: false,
            message: `No pude conectar con LiveKit: ${error.message}`
        };
    }
}

async function leaveCall(thread) {
    const id = String(thread);

    if (!COMMUNITY_BOT_TOKEN) {
        return {
            ok: false,
            message: 'Falta COMMUNITY_BOT_TOKEN.'
        };
    }

    const result = await anarchy(
        `/live-rooms/${encodeURIComponent(id)}/leave`,
        'POST',
        null,
        'community'
    );

    await closeLocalCall(id);

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
// EVENTOS DE LLAMADA
// ============================================================================

async function processRoomEvent(event) {
    const thread = String(event.threadId || '');

    if (!thread) return;

    const uid = String(event.user?.uid || '');
    const nickname = event.user?.nickname || 'Aventurero';

    const isBot = Boolean(
        uid && (
            uid === String(state.communityUid || '') ||
            [...state.calls.values()].some(
                call => String(call.identity) === uid
            )
        )
    );

    switch (event.type) {
        case 'room.join':
            console.log(`[ROOM JOIN] ${nickname} entró a ${thread}.`);

            if (!isBot) {
                await sendMessage(
                    thread,
                    '¡Ad Astra Abbysosque! Bienvenido al chat.',
                    'community'
                );
            }
            break;

        case 'room.leave':
            console.log(`[ROOM LEAVE] ${nickname} salió de ${thread}.`);

            if (isBot) {
                await closeLocalCall(thread);
            } else {
                await sendMessage(
                    thread,
                    'Buen viaje, el Gremio de Aventureros espera tu regreso',
                    'community'
                );
            }
            break;

        case 'room.speaker':
            console.log(
                `[ROOM SPEAKER] ${nickname}: ${event.speaker ? 'puede hablar' : 'está escuchando'}.`
            );
            break;

        case 'room.end':
            console.log(`[ROOM END] ${thread}`);
            await closeLocalCall(thread);
            break;
    }
}

// ============================================================================
// PROCESADOR PRINCIPAL DE COMANDOS
// ============================================================================

async function processCommand(event, source) {
    const thread = String(event.threadId || '');

    const message = event.message || {};
    const author = message.author || {};

    const nickname = author.nickname || 'Viajero';
    const uid = uidOf(author);
    const messageId = message.messageId || null;
    const content = String(message.content || '').trim();

    if (!thread || !content) return;

    // No responder a los mensajes del propio bot.
    if (
        author.uid &&
        [state.developerUid, state.communityUid]
            .filter(Boolean)
            .map(String)
            .includes(String(author.uid))
    ) {
        return;
    }

    if (messageId) {
        const key = `${source}:${messageId}`;

        if (processedMessages.has(key)) return;

        processedMessages.add(key);

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

    const command = parts[0]
        .toLowerCase()
        .replace(/^-/, '!');

    const args = parts.slice(1);

    console.log(
        `[COMANDO] ${command} | ${nickname} | ${thread} | ${source}`
    );

    // Acciones sociales.
    if (socialActions[command.slice(1)]) {
        await socialCommand(
            thread,
            command.slice(1),
            args,
            event,
            source,
            messageId
        );
        return;
    }

    let response = null;

    switch (command) {
        case '!ayuda':
        case '!comandos':
            response =
                'KATHERINE — COMANDOS\n\n' +
                'GENSHIN IMPACT\n' +
                '!personaje nombre — Ficha, build, materiales y equipos.\n' +
                '!build nombre [número] — Build detallada.\n' +
                '!materiales nombre — Materiales de progresión.\n' +
                '!equipos nombre — Equipos recomendados.\n' +
                '!arma nombre / !artefacto nombre.\n' +
                '!dominio nombre / !enemigo nombre.\n' +
                '!comida nombre / !material nombre / !logro nombre.\n' +
                '!talento nombre / !constelacion nombre.\n' +
                '!reaccion electro dendro — Reacción elemental.\n' +
                '!curiosidad — Dato sin repetición hasta agotar el ciclo.\n' +
                '!diagnosticogenshin nombre — Diagnóstico de fuentes.\n' +
                '!deseo / !pity / !resetpity — Gachapón simulado.\n' +
                '!equipo / !jefe / !comision / !expedicion / !mora.\n\n' +
                'INTERACCIONES\n' +
                '!abrazo @usuario / !beso @usuario / !patada @usuario / !golpe @usuario.\n' +
                '!cachetada / !acariciar / !mimar / !morder / !consolar.\n' +
                '!felicitar / !saludar / !proteger / !bailar / !cosquillas.\n' +
                '!chocar / !ayudar / !regalar / !acompañar / !asustar.\n' +
                '!curar / !cargar / !empujar / !regañar / !admirar / !apoyar.\n\n' +
                'ENTRETENIMIENTO\n' +
                '!trivia / !responder número.\n' +
                '!adivina / !intento número.\n' +
                '!encuesta pregunta | A | B / !votar número.\n' +
                '!elige A | B | C / !duelo A | B / !ship A | B.\n' +
                '!dado [caras] / !moneda / !8ball pregunta.\n' +
                '!chiste / !frase / !reto / !suerte / !puntos / !ranking.\n\n' +
                'UTILIDADES\n' +
                '!fecha — Fecha personal.\n' +
                '!zona America/Santiago — Configurar zona horaria.\n' +
                '!ping / !uptime / !estadochat.\n\n' +
                'LLAMADAS\n' +
                '!salas / !llamada! / !salirllamada!';
            break;

        case '!personaje':
            await characterCommand(thread, args, source, messageId);
            return;

        case '!build':
            await buildCommand(thread, args, source, messageId);
            return;

        case '!materiales':
            await materialsCommand(thread, args, source, messageId);
            return;

        case '!equipos':
            await teamsCommand(thread, args, source, messageId);
            return;

        case '!arma':
            await objectCommand(thread, 'weapons', args, source, messageId);
            return;

        case '!artefacto':
            await objectCommand(thread, 'artifacts', args, source, messageId);
            return;

        case '!dominio':
            await objectCommand(thread, 'domains', args, source, messageId);
            return;

        case '!enemigo':
            await objectCommand(thread, 'enemies', args, source, messageId);
            return;

        case '!comida':
            await objectCommand(thread, 'foods', args, source, messageId);
            return;

        case '!material':
            await objectCommand(thread, 'materials', args, source, messageId);
            return;

        case '!logro':
            await objectCommand(thread, 'achievements', args, source, messageId);
            return;

        case '!talento':
            await talentCommand(thread, args, source, messageId);
            return;

        case '!constelacion':
            await constellationCommand(thread, args, source, messageId);
            return;

        case '!diagnosticogenshin':
            await genshinDiagnostic(thread, args, source, messageId);
            return;

        case '!zona': {
            const zone = args.join(' ').trim();

            if (!zone) {
                response = state.zones.has(uid)
                    ? `Tu zona horaria es ${state.zones.get(uid)}.`
                    : 'Configura tu zona horaria con !zona America/Santiago. Ejemplos: America/Bogota, America/Lima, America/Mexico_City, Europe/Madrid.';
            } else if (normalize(zone) === 'borrar') {
                state.zones.delete(uid);
                response = 'Tu zona horaria fue eliminada.';
            } else if (!validZone(zone)) {
                response = 'Zona horaria inválida. Usa un identificador IANA, por ejemplo America/Santiago.';
            } else {
                state.zones.set(uid, zone);
                response = `Zona horaria guardada: ${zone}.`;
            }
            break;
        }

        case '!fecha': {
            const zone = state.zones.get(uid);

            response = zone
                ? `La fecha local de ${nickname} es ${dateForZone(zone)}.`
                : `${nickname}, primero configura tu zona horaria con !zona America/Santiago.`;

            break;
        }

        case '!curiosidad':
            response = `¿Sabías que...?\n\n${nextCuriosity()}`;
            break;

        case '!equipo': {
            const roster = [
                'Cyno', 'Nahida', 'Dehya', 'Alhaitham', 'Tighnari',
                'Nilou', 'Kaeya', 'Fischl', 'Diluc', 'Venti',
                'Zhongli', 'Hu Tao', 'Xiao', 'Ganyu', 'Raiden Shogun',
                'Kazuha', 'Kamisato Ayaka', 'Furina', 'Neuvillette',
                'Navia', 'Arlecchino', 'Mavuika', 'Kinich', 'Xianyun', 'Yelan'
            ];

            response =
                'EQUIPO ALEATORIO\n\n' +
                shuffle(roster)
                    .slice(0, 4)
                    .map((x, i) => `${i + 1}. ${x}`)
                    .join('\n') +
                '\n\nEquipo recreativo, no es una recomendación de meta.';

            break;
        }

        case '!jefe':
            response =
                `Tu próximo desafío será enfrentarte a ${random([
                    'Dvalin', 'Andrius', 'Azhdaha', 'Tartaglia',
                    'La Signora', 'Shouki no Kami', 'Narval Devorador',
                    'Hipostasis Electro', 'Hipostasis Pyro',
                    'Hipostasis Cryo', 'Dragarto de las Profundidades'
                ])}.`;
            break;

        case '!comision':
            response =
                'ENCARGO DEL GREMIO\n\n' +
                random([
                    'Investiga unas huellas misteriosas cerca de una ruina.',
                    'Ayuda a un comerciante que perdió su mercancía.',
                    'Derrota a los Hilichurls que bloquean una ruta.',
                    'Busca un cofre oculto cerca de un dominio.',
                    'Explora una región desconocida.',
                    'Recoge ingredientes para una receta tradicional.',
                    'Investiga un extraño fenómeno elemental.'
                ]);
            break;

        case '!expedicion':
            response =
                `NUEVA EXPEDICIÓN\n\nDestino: ${random([
                    'Mondstadt', 'Liyue', 'Inazuma',
                    'Sumeru', 'Fontaine', 'Natlan'
                ])}.\nObjetivo: explorar una zona desconocida y registrar tus hallazgos.\n\n¡Que los Siete guíen tu camino!`;
            break;

        case '!mora':
            response =
                `El Gremio te ha otorgado ${(Math.floor(Math.random() * 100) + 1) * 100} Mora ficticia.`;
            break;

        case '!reaccion': {
            if (args.length < 2) {
                response = 'Uso: !reaccion electro dendro. Elementos: Pyro, Hydro, Cryo, Electro, Dendro, Anemo y Geo.';
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

            const convert = x =>
                aliases[normalize(x)] ||
                x.charAt(0).toUpperCase() + x.slice(1).toLowerCase();

            const a = convert(args[0]);
            const b = convert(args[1]);

            const valid = [
                'Pyro', 'Hydro', 'Cryo',
                'Electro', 'Dendro', 'Anemo', 'Geo'
            ];

            if (!valid.includes(a) || !valid.includes(b)) {
                response = 'Elemento no reconocido.';
                break;
            }

            if (a === b) {
                response = 'No hay reacción entre elementos iguales.';
                break;
            }

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

            if (a === 'Anemo' || b === 'Anemo') {
                response = ['Pyro', 'Hydro', 'Cryo', 'Electro'].includes(
                    a === 'Anemo' ? b : a
                )
                    ? 'Reacción: Torbellino.'
                    : 'No se produce Torbellino con ese elemento.';
            } else if (a === 'Geo' || b === 'Geo') {
                response = ['Pyro', 'Hydro', 'Cryo', 'Electro'].includes(
                    a === 'Geo' ? b : a
                )
                    ? 'Reacción: Cristalización.'
                    : 'No se produce Cristalización con ese elemento.';
            } else {
                response =
                    `Reacción entre ${a} y ${b}: ${reactions[[a, b].sort().join('|')] || 'No existe reacción directa clásica.'}`;
            }

            break;
        }

        case '!deseo': {
            const wish = simulateWish(uid);

            response =
                `RESULTADO DE DESEO SIMULADO\n\n` +
                `${'⭐'.repeat(wish.stars)}\n` +
                `${wish.name}\n\n` +
                `Pity actual: ${state.wishes.get(uid).pity5}\n` +
                'No afecta tu cuenta de Genshin.';

            break;
        }

        case '!pity': {
            const wish = wishState(uid);

            response =
                `DESEOS SIMULADOS\n\n` +
                `5 estrellas: ${wish.pity5}\n` +
                `4 estrellas: ${wish.pity4}\n` +
                `Garantizado: ${wish.guaranteed ? 'Sí' : 'No'}`;

            break;
        }

        case '!resetpity':
            state.wishes.set(uid, {
                pity5: 0,
                pity4: 0,
                guaranteed: false
            });
            response = 'El contador simulado fue reiniciado. No modifica tu cuenta.';
            break;

        case '!ping': {
            const start = Date.now();
            const result = await anarchy('/me', 'GET', null, source);

            response = result.ok
                ? `PONG, ${nickname}.\nKatherine está en línea.\nLatencia API: ${Date.now() - start} ms.`
                : `Servidor activo, pero Anarchy respondió HTTP ${result.status}.`;

            break;
        }

        case '!uptime':
            response = `Katherine lleva activa ${uptime()}.`;
            break;

        case '!estadochat':
            response =
                `KATHERINE — ESTADO\n\n` +
                `Servidor: EN LÍNEA\n` +
                `Bot desarrollador: CONFIGURADO\n` +
                `Bot comunidad: ${COMMUNITY_BOT_TOKEN ? 'CONFIGURADO' : 'NO CONFIGURADO'}\n` +
                `Llamadas: ${state.calls.size}/3\n` +
                `Genshin-DB: ACTIVADO\n` +
                `Tiempo activa: ${uptime()}`;
            break;

        case '!trivia':
            response = startTrivia(thread);
            break;

        case '!responder':
            response = answerTrivia(thread, uid, args[0]);
            break;

        case '!adivina':
            response = startNumberGame(thread);
            break;

        case '!intento':
            response = tryNumber(thread, uid, args[0]);
            break;

        case '!puntos':
            response = `${nickname}, tienes ${state.points.get(uid) || 0} puntos.`;
            break;

        case '!ranking': {
            const rows = [...state.points.entries()]
                .sort((a, b) => b[1] - a[1])
                .slice(0, 10);

            response = rows.length
                ? 'RANKING KATHERINE\n\n' +
                  rows.map(([user, points], i) =>
                      `${i + 1}. ${user} — ${points} puntos`
                  ).join('\n')
                : 'Todavía no hay puntos registrados.';

            break;
        }

        case '!encuesta': {
            const pieces = args.join(' ')
                .split('|')
                .map(x => x.trim())
                .filter(Boolean);

            if (pieces.length < 3 || pieces.length > 6) {
                response = 'Uso: !encuesta ¿Qué prefieres? | Opción A | Opción B. Máximo cinco opciones.';
            } else {
                state.polls.set(thread, {
                    question: pieces[0],
                    options: pieces.slice(1),
                    votes: new Map(),
                    expires: Date.now() + 30 * 60 * 1000
                });

                response =
                    `ENCUESTA CREADA\n\n${pieces[0]}\n\n` +
                    pieces.slice(1).map((x, i) => `${i + 1}. ${x}`).join('\n') +
                    '\n\nVota con !votar número. Dura 30 minutos.';
            }

            break;
        }

        case '!votar': {
            const poll = state.polls.get(thread);

            if (!poll) {
                response = 'No hay encuesta activa.';
            } else if (Date.now() > poll.expires) {
                state.polls.delete(thread);
                response = 'La encuesta expiró.';
            } else {
                const n = Number(args[0]);

                if (!Number.isInteger(n) || n < 1 || n > poll.options.length) {
                    response = `Elige un número del 1 al ${poll.options.length}.`;
                } else {
                    poll.votes.set(uid, n - 1);

                    const counts = poll.options.map((option, i) =>
                        `${i + 1}. ${option}: ${[...poll.votes.values()].filter(v => v === i).length} voto(s)`
                    );

                    response =
                        `Voto registrado.\n\n${counts.join('\n')}\nTotal: ${poll.votes.size}.`;
                }
            }

            break;
        }

        case '!elige': {
            const choices = args.join(' ')
                .split('|')
                .map(x => x.trim())
                .filter(Boolean);

            response = choices.length >= 2
                ? `Katherine elige: ${random(choices)}.`
                : 'Uso: !elige A | B | C.';

            break;
        }

        case '!duelo': {
            const choices = args.join(' ')
                .split('|')
                .map(x => x.trim())
                .filter(Boolean);

            response = choices.length === 2
                ? `DUELO\n\n${choices[0]} VS ${choices[1]}\n\nGanador: ${random(choices)}.\nResultado aleatorio.`
                : 'Uso: !duelo A | B.';

            break;
        }

        case '!ship': {
            const names = args.join(' ')
                .split('|')
                .map(x => x.trim())
                .filter(Boolean);

            if (names.length !== 2) {
                response = 'Uso: !ship A | B.';
            } else {
                const hash = crypto.createHash('sha256')
                    .update(`${names[0]}|${names[1]}`)
                    .digest();

                const score = hash.readUInt32BE(0) % 101;

                response =
                    `COMPATIBILIDAD\n\n${names[0]} + ${names[1]}\n${score}%\n\n` +
                    `${score >= 80 ? 'Las estrellas están alineadas.' : score >= 50 ? 'Podría funcionar.' : 'Una relación complicada.'}\n` +
                    'Resultado ficticio.';
            }

            break;
        }

        case '!dado': {
            const faces = args.length ? Number(args[0]) : 6;

            response = Number.isInteger(faces) && faces >= 2 && faces <= 1000
                ? `🎲 ${nickname}: ${Math.floor(Math.random() * faces) + 1}/${faces}.`
                : 'El dado debe tener entre 2 y 1000 caras.';

            break;
        }

        case '!moneda':
            response = `🪙 La moneda cayó en ${Math.random() < 0.5 ? 'CARA' : 'SELLO'}.`;
            break;

        case '!8ball':
            response = args.length
                ? random([
                    'Sí, definitivamente.',
                    'Todo apunta a que sí.',
                    'Es muy probable.',
                    'No puedo asegurarlo.',
                    'Vuelve a preguntarme después.',
                    'No parece probable.',
                    'Mi respuesta es no.',
                    'El destino todavía no está decidido.'
                ])
                : 'Escribe una pregunta después de !8ball.';
            break;

        case '!chiste':
            response = random(jokes);
            break;

        case '!frase':
            response = random([
                'No necesitas recorrer todo Teyvat en un día.',
                'Incluso el viajero más fuerte necesita descansar.',
                'Los caminos difíciles también conducen a grandes destinos.',
                'Cada aventura deja un recuerdo.',
                'Los pequeños avances también son victorias.',
                'Que tu próximo destino sea mejor que el anterior.'
            ]);
            break;

        case '!reto':
            response = `RETO PARA ${nickname.toUpperCase()}\n\n${random(challenges)}`;
            break;

        case '!suerte':
            response = random(fortunes);
            break;

        case '!salas': {
            const listing = await getLiveRooms(true);

            if (!listing.ok) {
                response = `No pude consultar las salas: ${listing.error}`;
            } else if (!listing.rooms.length) {
                response = 'No hay llamadas activas.';
            } else {
                response =
                    'SALAS ACTIVAS\n\n' +
                    listing.rooms.slice(0, 15).map((room, i) =>
                        `${i + 1}. ${room.title || 'Sala sin título'}\n` +
                        `ID: ${room.threadId}\n` +
                        `Tipo: ${room.channelType}\n` +
                        `Participantes: ${room.participantCount ?? 0}\n` +
                        `Katherine: ${room.joined ? 'Dentro' : 'Fuera'}`
                    ).join('\n\n');
            }

            break;
        }

        case '!llamada!':
            response = (await joinCall(thread)).message;
            break;

        case '!salirllamada!':
            response = (await leaveCall(thread)).message;
            break;

        default:
            return;
    }

    if (response) {
        await sendLong(thread, response, source, messageId);
    }
}

// ============================================================================
// VALIDACIÓN HMAC — DOS SECRETOS
// ============================================================================

function validateWebhook(req) {
    const timestamp = req.headers['x-anarchy-bot-timestamp'];
    const header = req.headers['x-anarchy-bot-signature'];

    if (!timestamp || !header || typeof req.rawBody !== 'string') {
        return {
            ok: false,
            error: 'Faltan encabezados o cuerpo original.'
        };
    }

    const seconds = Number(timestamp);

    if (
        !Number.isFinite(seconds) ||
        Math.abs(Date.now() / 1000 - seconds) > 300
    ) {
        return {
            ok: false,
            error: 'Timestamp inválido o vencido.'
        };
    }

    const signature = String(header).startsWith('v1=')
        ? String(header).slice(3)
        : String(header);

    if (!/^[a-f0-9]{64}$/i.test(signature)) {
        return {
            ok: false,
            error: 'Formato de firma incorrecto.'
        };
    }

    const received = Buffer.from(signature, 'hex');

    const secrets = [
        { source: 'developer', secret: WEBHOOK_SECRET },
        { source: 'community', secret: COMMUNITY_WEBHOOK_SECRET }
    ].filter(x => x.secret);

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
                source: item.source
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
        console.warn('[WEBHOOK RECHAZADO]', validation.error);
        return res.status(401).send('Unauthorized');
    }

    console.log(`[WEBHOOK] Firma válida: ${validation.source}.`);

    // Confirmar la recepción antes de procesar el evento.
    res.status(200).send('OK');

    const event = req.body || {};

    const task = eventQueue.then(async () => {
        if (event.type === 'message') {
            await processCommand(event, validation.source);
        } else if (event.type === 'interaction') {
            console.log(
                '[INTERACTION]',
                event.buttonId,
                event.user?.nickname
            );
        } else if ([
            'room.join',
            'room.leave',
            'room.speaker',
            'room.end'
        ].includes(event.type)) {
            if (validation.source !== 'community') {
                console.warn(
                    '[WEBHOOK] Evento de sala firmado con secreto de desarrollador; ignorado.'
                );
                return;
            }

            await processRoomEvent(event);
        } else {
            console.log(
                '[WEBHOOK] Evento no manejado:',
                event.type
            );
        }
    });

    eventQueue = task.catch(error => {
        console.error('[EVENT QUEUE]', error);
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
            (Date.now() - START_TIME) / 1000
        ),
        activeCalls: state.calls.size,
        developerToken: Boolean(BOT_TOKEN),
        communityToken: Boolean(COMMUNITY_BOT_TOKEN),
        developerWebhook: Boolean(WEBHOOK_SECRET),
        communityWebhook: Boolean(COMMUNITY_WEBHOOK_SECRET),
        genshinDb: true,
        timestamp: new Date().toISOString()
    });
});

// ============================================================================
// APAGADO ORDENADO
// ============================================================================

let shuttingDown = false;

async function shutdown(signal) {
    if (shuttingDown) return;

    shuttingDown = true;

    console.log(`[KATHERINE] Apagado: ${signal}.`);

    for (const thread of [...state.calls.keys()]) {
        try {
            await anarchy(
                `/live-rooms/${encodeURIComponent(thread)}/leave`,
                'POST',
                null,
                'community'
            );

            await closeLocalCall(thread);
        } catch (error) {
            console.error(`[SHUTDOWN] ${thread}:`, error.message);
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
    console.log('       KATHERINE — GENSHIN COMMUNITY BOT');
    console.log('=======================================================');
    console.log(`[SERVER] Puerto: ${PORT}`);
    console.log('[WEBHOOK] POST /');
    console.log('[HEALTH] GET /');
    console.log(`[DEVELOPER TOKEN] ${BOT_TOKEN ? 'CONFIGURADO' : 'AUSENTE'}`);
    console.log(`[COMMUNITY TOKEN] ${COMMUNITY_BOT_TOKEN ? 'CONFIGURADO' : 'AUSENTE'}`);
    console.log(`[DEVELOPER SECRET] ${WEBHOOK_SECRET ? 'CONFIGURADO' : 'AUSENTE'}`);
    console.log(`[COMMUNITY SECRET] ${COMMUNITY_WEBHOOK_SECRET ? 'CONFIGURADO' : 'AUSENTE'}`);
    console.log('[GENSHIN DB] ACTIVADO');
    console.log('[GENSHIN BUILDS] GITHUB');
    console.log('[LIVEKIT] ' + (COMMUNITY_BOT_TOKEN ? 'ACTIVADO' : 'DESACTIVADO'));
    console.log('[SOCIAL ACTIONS] ACTIVADAS');
    console.log('=======================================================');
    console.log('');

    loadProfiles().catch(error => {
        console.error('[PROFILE ERROR]', error);
    });
})
