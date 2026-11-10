'use strict';

const express = require('express');
const crypto = require('crypto');

const app = express();
app.disable('x-powered-by');

// -----------------------------------------------------------------------------
// CONFIGURACIÓN
// En Render configura BOT_TOKEN y WEBHOOK_SECRET.
// WEB_TOKEN y SECRET se aceptan como nombres alternativos por compatibilidad.
// -----------------------------------------------------------------------------
const PORT = Number(process.env.PORT || 3000);
const API_BASE = 'https://api.anarchyapps.org/bot-api/v1';
const BOT_TOKEN = (process.env.BOT_TOKEN || '').trim();
const WEBHOOK_SECRET = (
  process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN || process.env.SECRET || ''
).trim();
const PREFIX = (process.env.COMMAND_PREFIX || '!').trim();

const PREFIX_PATTERN = /^[!$%&*+\-=~>;]{1,3}$/;
if (!PREFIX_PATTERN.test(PREFIX)) {
  console.warn(`⚠️ COMMAND_PREFIX "${PREFIX}" no es válido. Se usará "!".`);
}
const COMMAND_PREFIX = PREFIX_PATTERN.test(PREFIX) ? PREFIX : '!';

if (!BOT_TOKEN) {
  console.error('❌ Falta BOT_TOKEN en las variables de entorno de Render.');
}
if (!WEBHOOK_SECRET) {
  console.error('❌ Falta WEBHOOK_SECRET (o WEB_TOKEN/SECRET). El webhook rechazará eventos hasta configurarlo.');
}

// Estado local por chat. No sustituye los permisos que configura el Agente en ACM.
const threadStates = new Map();

// Evita procesar dos veces el mismo evento si Anarchy lo entrega duplicado.
const seenEvents = new Set();
const seenEventOrder = [];
const MAX_SEEN_EVENTS = 2000;

// Limitadores locales: 1 mensaje/segundo por bot y 1 mensaje/2 segundos por chat,
// además de los límites por hora indicados por Anarchy.
let lastBotSendAt = 0;
const lastThreadSendAt = new Map();
const botSendHistory = [];
const threadSendHistory = new Map();
let sendQueue = Promise.resolve();

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function getThreadState(threadId) {
  if (!threadStates.has(String(threadId))) {
    threadStates.set(String(threadId), {
      createdAt: new Date().toISOString(),
      commandCount: 0
    });
  }
  return threadStates.get(String(threadId));
}

function rememberEvent(key) {
  if (!key) return true;
  if (seenEvents.has(key)) return false;
  seenEvents.add(key);
  seenEventOrder.push(key);
  if (seenEventOrder.length > MAX_SEEN_EVENTS) {
    const oldest = seenEventOrder.shift();
    seenEvents.delete(oldest);
  }
  return true;
}

function parseResponseBody(text) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

class AnarchyApiError extends Error {
  constructor(status, data, retryAfter) {
    super(`Anarchy API respondió HTTP ${status}`);
    this.name = 'AnarchyApiError';
    this.status = status;
    this.data = data;
    this.retryAfter = retryAfter;
  }
}

async function anarchyAPI(method, path, body) {
  if (!BOT_TOKEN) throw new Error('BOT_TOKEN no está configurado.');

  const headers = {
    Authorization: `Bot ${BOT_TOKEN}`,
    Accept: 'application/json'
  };

  const options = { method, headers };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }

  const response = await fetch(`${API_BASE}${path}`, options);
  const responseText = await response.text();
  const data = parseResponseBody(responseText);

  if (!response.ok) {
    throw new AnarchyApiError(
      response.status,
      data,
      response.headers.get('retry-after')
    );
  }

  return data;
}

function pruneHistory(history, windowMs, now) {
  while (history.length && now - history[0] >= windowMs) {
    history.shift();
  }
}

async function enviarMensajeInterno(threadId, content, options = {}) {
  const id = String(threadId);
  const text = String(content || '').trim();
  if (!id) throw new Error('No se recibió threadId.');
  if (!text) throw new Error('No se puede enviar un mensaje vacío.');
  if (text.length > 2000) throw new Error('El mensaje supera los 2000 caracteres permitidos.');

  // Espera a que se cumplan los intervalos mínimos documentados.
  const now = Date.now();
  const botWait = Math.max(0, 1000 - (now - lastBotSendAt));
  const lastForThread = lastThreadSendAt.get(id) || 0;
  const threadWait = Math.max(0, 2000 - (now - lastForThread));
  await delay(Math.max(botWait, threadWait));

  const sendNow = Date.now();
  pruneHistory(botSendHistory, 60 * 60 * 1000, sendNow);
  const perThreadHistory = threadSendHistory.get(id) || [];
  pruneHistory(perThreadHistory, 10 * 60 * 1000, sendNow);

  if (botSendHistory.length >= 300) {
    throw new Error('Límite local: Katherine ya alcanzó 300 mensajes en la última hora.');
  }
  if (perThreadHistory.length >= 60) {
    throw new Error(`Límite local: este chat ya alcanzó 60 mensajes en los últimos 10 minutos.`);
  }

  const payload = { content: text };
  if (Array.isArray(options.buttons) && options.buttons.length) {
    payload.buttons = options.buttons.slice(0, 5);
  }
  if (options.replyMessageId) {
    payload.replyMessageId = String(options.replyMessageId);
  }

  // Esta es la única ruta de envío: la documentada por Anarchy.
  const result = await anarchyAPI(
    'POST',
    `/threads/${encodeURIComponent(id)}/messages`,
    payload
  );

  const sentAt = Date.now();
  lastBotSendAt = sentAt;
  lastThreadSendAt.set(id, sentAt);
  botSendHistory.push(sentAt);
  perThreadHistory.push(sentAt);
  threadSendHistory.set(id, perThreadHistory);

  console.log(`✅ [MENSAJE ENVIADO] Thread: ${id}`);
  return result;
}

// Cola global para que dos respuestas simultáneas no infrinjan el intervalo del bot.
function enviarMensaje(threadId, content, options = {}) {
  const task = sendQueue.then(() => enviarMensajeInterno(threadId, content, options));
  sendQueue = task.catch(() => {});
  return task;
}

async function obtenerMiPerfil() {
  return anarchyAPI('GET', '/me');
}

function obtenerThreadIds(perfil) {
  const ids = perfil?.threadIds ?? perfil?.bot?.threadIds ?? [];
  return Array.isArray(ids) ? ids.map(String) : [];
}

async function textoDiagnostico(threadId) {
  const perfil = await obtenerMiPerfil();
  const threadIds = obtenerThreadIds(perfil);
  const pertenece = threadIds.includes(String(threadId));
  const bot = perfil?.bot ?? perfil;
  const nombre = bot?.name || bot?.nickname || 'Katherine';
  const botId = bot?.botId || bot?.id || 'no informado por la API';

  console.log('🔎 [DIAGNÓSTICO /me]:', JSON.stringify({
    botName: nombre,
    botId,
    requestedThreadId: String(threadId),
    threadIds,
    botIsInThread: pertenece
  }, null, 2));

  if (pertenece) {
    return `✅ Diagnóstico de ${nombre}: token reconocido y el chat ${threadId} aparece en threadIds. Si el envío sigue dando 403, revisaremos la respuesta de la API y la pertenencia efectiva del bot.`;
  }

  return `⚠️ El token fue consultado, pero el chat ${threadId} NO aparece en threadIds. Revisa que hayas agregado a este mismo bot (botId: ${botId}) al chat público y que Render use su token actual.`;
}

function escaparRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function procesarComando(evento) {
  const mensaje = evento.message || {};
  const threadId = evento.threadId;
  const content = typeof mensaje.content === 'string' ? mensaje.content.trim() : '';
  const messageId = mensaje.messageId || null;

  if (!threadId || !content) return;

  // Anarchy documenta que un comando es el prefijo seguido inmediatamente de una letra.
  const commandRegex = new RegExp(`^${escaparRegex(COMMAND_PREFIX)}[a-z]`, 'i');
  if (!commandRegex.test(content)) return;

  const sinPrefijo = content.slice(COMMAND_PREFIX.length).trim();
  const partes = sinPrefijo.split(/\s+/);
  const comando = (partes.shift() || '').toLowerCase();
  const args = partes;
  const state = getThreadState(threadId);
  state.commandCount += 1;

  console.log(`🎯 [COMANDO] ${COMMAND_PREFIX}${comando} | Thread: ${threadId} | Usuario: ${mensaje.author?.nickname || 'desconocido'}`);

  let respuesta;

  switch (comando) {
    case 'ayuda':
    case 'help':
      respuesta = [
        '✦ **Katherine — Comandos**',
        `${COMMAND_PREFIX}ping — Comprueba si estoy activa.`,
        `${COMMAND_PREFIX}diagnostico — Comprueba el token y la presencia en este chat.`,
        `${COMMAND_PREFIX}estadochat — Muestra mi estado en este chat.`,
        `${COMMAND_PREFIX}leerchat — Explica el permiso de lectura completa.`,
        `${COMMAND_PREFIX}noleerchat — Explica dónde administrar ese permiso.`,
        `${COMMAND_PREFIX}play — Estado de la integración musical.`,
        `${COMMAND_PREFIX}volumen — Estado del control de volumen.`
      ].join('\n');
      break;

    case 'ping':
      respuesta = 'Pong! ✦ Katherine está activa y el webhook está funcionando.';
      break;

    case 'diagnostico':
      try {
        respuesta = await textoDiagnostico(threadId);
      } catch (error) {
        console.error('❌ [DIAGNÓSTICO /me]:', error.message, error.data || '');
        respuesta = `❌ No pude consultar GET /me: ${error.message}. Revisa el BOT_TOKEN y los logs de Render.`;
      }
      break;

    case 'estadochat':
      respuesta = `✦ Katherine está activa en este chat. Comandos procesados desde que inició el proceso: ${state.commandCount}.`;
      break;

    case 'leerchat':
      respuesta = 'Para que pueda recibir todos los mensajes del chat, el Agente debe activar el permiso de lectura completa en ACM. No puedo concederme ese permiso mediante un comando.';
      break;

    case 'noleerchat':
      respuesta = 'El permiso de lectura completa se administra desde ACM por el Agente de la comunidad. Este comando no modifica permisos.';
      break;

    case 'play':
      respuesta = 'La reproducción de música todavía no está conectada. Requiere la integración de Bot de comunidad y LiveKit, que utiliza una configuración distinta.';
      break;

    case 'volumen':
      respuesta = 'El control de volumen estará disponible cuando se integre el reproductor de audio.';
      break;

    default:
      return;
  }

  await enviarMensaje(threadId, respuesta, { replyMessageId: messageId });
}

async function procesarInteraccion(evento) {
  const threadId = evento.threadId;
  if (!threadId || !evento.buttonId) return;

  const userName = evento.user?.nickname || 'alguien';
  console.log(`🖱️ [INTERACTION] Botón: ${evento.buttonId} | Thread: ${threadId} | Usuario: ${userName}`);

  const respuesta = `Recibí tu selección: ${evento.buttonId}`;
  await enviarMensaje(threadId, respuesta, {
    replyMessageId: evento.messageId || undefined
  });
}

async function procesarEvento(evento) {
  if (!evento || typeof evento !== 'object') return;

  console.log('📩 [EVENTO ANARCHY]:', JSON.stringify({
    type: evento.type || null,
    botId: evento.botId || null,
    threadId: evento.threadId || null,
    ndcId: evento.ndcId ?? null,
    messageId: evento.message?.messageId || evento.messageId || null,
    author: evento.message?.author?.nickname || evento.user?.nickname || null
  }, null, 2));

  switch (evento.type) {
    case 'message':
      await procesarComando(evento);
      break;

    case 'interaction':
      await procesarInteraccion(evento);
      break;

    case 'room.join':
    case 'room.speaker':
    case 'room.leave':
    case 'room.end':
      console.log(`🎙️ [EVENTO DE SALA] ${evento.type} | Thread: ${evento.threadId || 'sin threadId'}`);
      break;

    default:
      console.log(`ℹ️ [EVENTO IGNORADO] Tipo no manejado: ${evento.type || 'sin tipo'}`);
  }
}

function verificarFirma(req) {
  if (!WEBHOOK_SECRET) {
    return { ok: false, status: 503, reason: 'Webhook secret no configurado.' };
  }

  const timestamp = req.get('X-Anarchy-Bot-Timestamp');
  const signature = req.get('X-Anarchy-Bot-Signature');
  const rawBody = req.body;

  if (!timestamp || !signature || !Buffer.isBuffer(rawBody)) {
    return { ok: false, status: 401, reason: 'Faltan headers de firma o cuerpo original.' };
  }

  const timestampNumber = Number(timestamp);
  if (!Number.isFinite(timestampNumber) || Math.abs(Date.now() / 1000 - timestampNumber) > 300) {
    return { ok: false, status: 401, reason: 'Timestamp inválido o vencido (máximo 5 minutos).' };
  }

  const match = /^v1=([a-f0-9]{64})$/i.exec(signature.trim());
  if (!match) {
    return { ok: false, status: 401, reason: 'Formato de firma inválido.' };
  }

  const expected = crypto
    .createHmac('sha256', WEBHOOK_SECRET)
    .update(`${timestamp}.`, 'utf8')
    .update(rawBody)
    .digest();

  const received = Buffer.from(match[1], 'hex');
  if (received.length !== expected.length || !crypto.timingSafeEqual(expected, received)) {
    return { ok: false, status: 401, reason: 'La firma HMAC no coincide.' };
  }

  return { ok: true };
}

function webhookHandler(req, res) {
  const firma = verificarFirma(req);
  if (!firma.ok) {
    console.warn(`⛔ [WEBHOOK RECHAZADO] ${firma.reason}`);
    return res.status(firma.status).json({ ok: false, error: firma.reason });
  }

  let evento;
  try {
    evento = JSON.parse(req.body.toString('utf8'));
  } catch {
    return res.status(400).json({ ok: false, error: 'JSON inválido.' });
  }

  const eventKey = evento.type === 'message'
    ? `message:${evento.threadId}:${evento.message?.messageId}`
    : evento.type === 'interaction'
      ? `interaction:${evento.threadId}:${evento.messageId}:${evento.buttonId}:${evento.user?.uid}`
      : `${evento.type}:${evento.threadId}:${evento.user?.uid || ''}`;

  if (!rememberEvent(eventKey)) {
    return res.status(200).json({ ok: true, duplicate: true });
  }

  // Confirmamos la recepción inmediatamente. El procesamiento continúa en segundo plano
  // dentro del mismo proceso, sin bloquear la respuesta HTTP del webhook.
  res.status(200).json({ ok: true });

  setImmediate(() => {
    procesarEvento(evento).catch(error => {
      console.error('❌ [ERROR PROCESANDO EVENTO]:', error.message);
      if (error instanceof AnarchyApiError) {
        console.error('❌ [ANARCHY API]:', JSON.stringify({
          status: error.status,
          data: error.data,
          retryAfter: error.retryAfter
        }, null, 2));
      }
    });
  });
}

// El cuerpo se lee como Buffer para calcular HMAC sobre los bytes originales.
const rawJsonBody = express.raw({ type: 'application/json', limit: '1mb' });
app.post('/', rawJsonBody, webhookHandler);
app.post('/webhook', rawJsonBody, webhookHandler);

// Endpoint de salud para Render.
app.get('/', (_req, res) => {
  res.json({
    bot: 'Katherine',
    status: 'online',
    uptime: Math.floor(process.uptime())
  });
});

app.get('/health', (_req, res) => {
  res.status(200).json({ ok: true, bot: 'Katherine' });
});

app.use((error, _req, res, _next) => {
  console.error('❌ [ERROR EXPRESS]:', error.message);
  res.status(400).json({ ok: false, error: 'No se pudo procesar la solicitud.' });
});

const server = app.listen(PORT, () => {
  console.log(`✅ Katherine está escuchando en el puerto ${PORT}`);
  console.log(`🔗 Webhook: POST / o POST /webhook`);
  console.log(`🔐 Firma HMAC: ${WEBHOOK_SECRET ? 'configurada' : 'NO configurada'}`);

  // Comprueba qué bot reconoce el token y qué chats aparecen en threadIds.
  obtenerMiPerfil()
    .then(perfil => {
      const bot = perfil?.bot ?? perfil;
      console.log('🔎 [ANARCHY /me]:', JSON.stringify({
        botName: bot?.name || bot?.nickname || 'no informado',
        botId: bot?.botId || bot?.id || 'no informado',
        threadIds: obtenerThreadIds(perfil)
      }, null, 2));
    })
    .catch(error => {
      console.error('❌ [ANARCHY /me] No se pudo verificar el token:', error.message);
      if (error.data) console.error(JSON.stringify(error.data, null, 2));
    });
});

function cerrarServidor(signal) {
  console.log(`🛑 Recibida señal ${signal}. Cerrando Katherine...`);
  server.close(() => {
    console.log('✅ Servidor cerrado.');
    process.exit(0);
  });
  setTimeout(() => process.exit(1), 10000).unref();
}

process.on('SIGTERM', () => cerrarServidor('SIGTERM'));
process.on('SIGINT', () => cerrarServidor('SIGINT'));
process.on('unhandledRejection', error => {
  console.error('❌ [UNHANDLED REJECTION]:', error);
});
