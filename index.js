'use strict';

const express = require('express');
const crypto = require('crypto');

// ============================================================
// CONFIGURACIÓN PRINCIPAL
// ============================================================

const app = express();

app.disable('x-powered-by');
app.disable('etag');

const PORT = Number(process.env.PORT || 3000);

const API_BASE = 'https://api.anarchyapps.org/bot-api/v1';

const BOT_TOKEN = (
  process.env.BOT_TOKEN || ''
).trim();

const WEBHOOK_SECRET = (
  process.env.WEBHOOK_SECRET ||
  process.env.WEB_TOKEN ||
  process.env.SECRET ||
  ''
).trim();

const PREFIX = (
  process.env.COMMAND_PREFIX || '!'
).trim();

const PREFIX_PATTERN = /^[!$%&*+\-=~>;]{1,3}$/;

const COMMAND_PREFIX = PREFIX_PATTERN.test(PREFIX)
  ? PREFIX
  : '!';

// ============================================================
// VALIDACIÓN DE CONFIGURACIÓN
// ============================================================

const configErrors = [];

if (!BOT_TOKEN) {
  configErrors.push('Falta BOT_TOKEN.');
}

if (!WEBHOOK_SECRET) {
  configErrors.push('Falta WEBHOOK_SECRET.');
}

if (!PREFIX_PATTERN.test(PREFIX)) {
  console.warn(
    '[CONFIG] Prefijo inválido. Se utilizará !'
  );
}

if (configErrors.length) {
  console.error('\n[CONFIG] Errores de configuración:');

  configErrors.forEach(error => {
    console.error(`- ${error}`);
  });

  console.error(
    '\nConfigura las variables de entorno en Render.\n'
  );
}

// ============================================================
// MIDDLEWARE
// ============================================================

app.use(express.json({
  limit: '256kb',
  strict: true
}));

app.use((req, res, next) => {
  res.setHeader('Cache-Control', 'no-store');
  next();
});

// ============================================================
// ESTADO DEL BOT
// ============================================================

const botStatus = {
  startedAt: new Date().toISOString(),
  online: false,
  apiAvailable: null,
  lastError: null
};

// ============================================================
// HEALTH CHECK
// ============================================================

app.get('/health', (req, res) => {
  res.status(200).json({
    success: true,
    bot: 'Anarchy Entertainment Bot',
    status: botStatus.online ? 'online' : 'starting',
    uptime: Math.floor(process.uptime()),
    startedAt: botStatus.startedAt
  });
});

// ============================================================
// ESTADO LOCAL POR CHAT
// ============================================================

const threadStates = new Map();

// ============================================================
// PROTECCIÓN CONTRA EVENTOS DUPLICADOS
// ============================================================

const seenEvents = new Set();
const seenEventOrder = [];

const MAX_SEEN_EVENTS = 2000;

// ============================================================
// LIMITADORES DE MENSAJES
// ============================================================

let lastBotSendAt = 0;

const lastThreadSendAt = new Map();
const botSendHistory = [];
const threadSendHistory = new Map();

let sendQueue = Promise.resolve();

// ============================================================
// UTILIDADES
// ============================================================

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function getThreadState(threadId) {
  const id = String(threadId);

  if (!threadStates.has(id)) {
    threadStates.set(id, {
      createdAt: new Date().toISOString(),
      commandCount: 0
    });
  }

  return threadStates.get(id);
}

function rememberEvent(key) {
  if (!key) return true;

  if (seenEvents.has(key)) {
    return false;
  }

  seenEvents.add(key);
  seenEventOrder.push(key);

  if (seenEventOrder.length > MAX_SEEN_EVENTS) {
    const oldest = seenEventOrder.shift();
    seenEvents.delete(oldest);
  }

  return true;
}

function parseResponseBody(text) {
  i
