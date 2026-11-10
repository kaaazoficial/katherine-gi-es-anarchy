'use strict';

const express = require('express');
const crypto = require('crypto');

const app = express();

app.disable('x-powered-by');
app.set('trust proxy', 1);

// ============================================================
// CONFIGURACIÓN
// ============================================================

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

const API_TIMEOUT_MS = 15000;
const MAX_SEEN_EVENTS = 2000;

// ============================================================
// VALIDACIÓN
// ============================================================

function validarConfiguracion() {
  const errores = [];

  if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65535) {
    errores.push('PORT no es válido.');
  }

  if (!BOT_TOKEN) {
    errores.push('Falta BOT_TOKEN.');
  }

  if (!WEBHOOK_SECRET) {
    errores.push('Falta WEBHOOK_SECRET.');
  }

  if (!PREFIX_PATTERN.test(PREFIX)) {
    console.warn('[CONFIG] Se utilizará el prefijo !');
  }

  if (errores.length > 0) {
    console.error('[CONFIG] Errores:');

    errores.forEach(error => {
      console.error('- ' + error);
    });

    throw new Error('Configuración incompleta.');
  }

  console.log('[CONFIG] Configuración correcta.');
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
  apiAvailable: null,
  lastError: null
};

const threadStates = new Map();

const seenEvents = new Set();
const seenEventOrder = [];

let lastBotSendAt = 0;

const lastThreadSendAt = new Map();
const botSendHistory = [];
const threadSendHistory = new Map();

let sendQueue = Promise.resolve();

// ============================================================
// HEALTH CHECK
// ============================================================

app.get('/', (req, res) => {
  res.status(200).json({
    name: 'Anarchy Bot',
    status: 'online',
    platform: 'Anarchy',
    timestamp: new Date().toISOString()
  });
});

app.get('/health', (req, res) => {
  res.status(200).json({
    success: true,
    status: 'running',
    uptime: Math.floor(process.uptime()),
    startedAt: botStatus.startedAt,
    apiAvailable: botStatus.apiAvailable
  });
});

app.get('/healthz', (req, res) => {
  res.status(200).send('OK');
});

// ============================================================
// FUNCIONES AUXILIARES
// ============================================================

function delay(ms) {
  return new Promise(resolve => {
    setTimeout(resolve, Math.max(0, ms));
  });
}

function getThreadState(threadId) {
  const id = String(threadId || '').trim();

  if (!id) {
    throw new Error('threadId vacío.');
  }

  if (!threadStates.has(id)) {
    threadStates.set(id, {
      createdAt: new Date().toISOString(),
      commandCount: 0
    });
  }

  return threadStates.get(id);
}

function rememberEvent(key) {
  const eventKey = String(key || '').trim();

  if (!eventKey) return true;

  if (seenEvents.has(eventKey)) {
    return false;
  }

  seenEvents.add(eventKey);
  seenEventOrder.push(eventKey);

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

function pruneHistory(history, windowMs, now = Date.now()) {
  while (
    history.length &&
    now - history[0] >= windowMs
  ) {
    history.shift();
  }
}

// ============================================================
// ERRORES DE ANARCHY API
// ============================================================

class AnarchyApiError extends Error {
  constructor(status, data, retryAfter) {
    super(`Anarchy API respondió HTTP ${status}`);

    this.name = 'AnarchyApiError';
    this.status = status;
    this.data = data;
    this.retryAfter = retryAfter;
  }
}

// ============================================================
// CONEXIÓN CON ANARCHY API
// ============================================================

async function anarchyAPI(method, path, body) {
  if (!BOT_TOKEN) {
    throw new Error('BOT_TOKEN no configurado.');
  }

  if (
    typeof path !== 'string' ||
    !path.startsWith('/')
  ) {
    throw new Error('Ruta API inválida.');
  }

  const controller = new AbortController();

  const timeout = setTimeout(() => {
    controller.abort();
  }, API_TIMEOUT_MS);

  const headers = {
    Authorization: `Bot ${BOT_TOKEN}`,
    Accept: 'application/json'
  };

  const options = {
    method,
    headers,
    signal: controller.signal
  };

  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }

  try {
    const response = await fetch(
      `${API_BASE}${path}`,
      options
    );

    const responseText = await response.text();

    const data = parseResponseBody(responseText);

    if (!response.ok) {
      throw new AnarchyApiError(
        response.status,
        data,
        response.headers.get('retry-after')
      );
    }

    botStatus.apiAvailable = true;
    botStatus.lastError = null;

    return data;

  } catch (error) {
    botStatus.apiAvailable = false;
    botStatus.lastError = error.message;

    if (error.name === 'AbortError') {
      throw new Error(
        'Tiempo de espera agotado al conectar con Anarchy.'
      );
    }

    throw error;

  } finally {
    clearTimeout(timeout);
  }
}

// Envío de mensajes a través de la API
async function enviarMensaje(channelOrThreadId, content) {
  if (!channelOrThreadId || !content) return;
  
  return await anarchyAPI('POST', `/channels/${channelOrThreadId}/messages`, {
    content: String(content)
  });
}

// ============================================================
// VERIFICACIÓN HMAC GENÉRICA
// ============================================================

function verificarFirmaHmac(
  rawBody,
  signature,
  secret = WEBHOOK_SECRET
) {
  if (!secret || !signature || !Buffer.isBuffer(rawBody)) {
    return false;
  }

  const recibido = String(signature)
    .replace(/^sha256=/i, '')
    .trim();

  if (!/^[a-f0-9]{64}$/i.test(recibido)) {
    return false;
  }

  const esperado = crypto
    .createHmac('sha256', secret)
    .update(rawBody)
    .digest('hex');

  const recibidoBuffer = Buffer.from(recibido, 'hex');
  const esperadoBuffer = Buffer.from(esperado, 'hex');

  return (
    recibidoBuffer.length === esperadoBuffer.length &&
    crypto.timingSafeEqual(
      recibidoBuffer,
      esperadoBuffer
    )
  );
}

// ============================================================
// RUTAS Y COMANDOS DEL BOT
// ============================================================

app.post('/webhook', async (req, res) => {
  try {
    const event = req.body || {};
    const eventId = event.id || event.event_id || `${event.type}_${Date.now()}`;

    // Evitar eventos duplicados
    if (!rememberEvent(eventId)) {
      return res.status(200).json({ success: true, duplicate: true });
    }

    // Filtrar mensajes
    const message = event.data || event.message || event;
    const content = message.content || message.text || '';
    const channelId = message.channel_id || message.thread_id || event.channel_id;

    if (!content || !channelId) {
      return res.status(200).json({ success: true, ignored: true });
    }

    // Ignorar mensajes enviados por bots
    if (message.author && message.author.bot) {
      return res.status(200).json({ success: true, bot: true });
    }

    // Comprobar prefijo
    if (content.startsWith(COMMAND_PREFIX)) {
      const args = content.slice(COMMAND_PREFIX.length).trim().split(/\s+/);
      const command = args.shift().toLowerCase();

      const threadState = getThreadState(channelId);
      threadState.commandCount++;

      // --- COMANDOS ---
      if (command === 'ping') {
        await enviarMensaje(channelId, '¡Pong! 🏓 El bot de Anarchy está activo.');
      } else if (command === 'info') {
        await enviarMensaje(channelId, `🤖 **Katherine Bot**\nPlataforma: Anarchy\nPrefijo: \`${COMMAND_PREFIX}\`\nComandos ejecutados en este canal: ${threadState.commandCount}`);
      } else if (command === 'ayuda' || command === 'help') {
        await enviarMensaje(channelId, `📌 **Lista de Comandos:**\n- \`${COMMAND_PREFIX}ping\`: Comprueba si el bot responde.\n- \`${COMMAND_PREFIX}info\`: Muestra información básica del bot.\n- \`${COMMAND_PREFIX}ayuda\`: Muestra este menú.`);
      }
    }

    res.status(200).json({ success: true });
  } catch (error) {
    console.error('[WEBHOOK ERROR]', error.message);
    res.status(500).json({ success: false, error: error.message });
  }
});

// ============================================================
// MANEJO DE ERRORES
// ============================================================

app.use((err, req, res, next) => {
  console.error('[EXPRESS]', err.message);

  if (res.headersSent) return next(err);

  if (err.type === 'entity.too.large') {
    return res.status(413).json({
      success: false,
      error: 'Solicitud demasiado grande.'
    });
  }

  if (
    err instanceof SyntaxError &&
    err.status === 400 &&
    'body' in err
  ) {
    return res.status(400).json({
      success: false,
      error: 'JSON inválido.'
    });
  }

  return res.status(500).json({
    success: false,
    error: 'Error interno del servidor.'
  });
});

// ============================================================
// INICIO DEL SERVIDOR
// ============================================================

function iniciarServidor() {
  validarConfiguracion();

  const server = app.listen(
    PORT,
    '0.0.0.0',
    () => {
      console.log('');
      console.log('====================================');
      console.log('       ANARCHY BOT INICIADO');
      console.log('====================================');
      console.log(`[SERVER] Puerto: ${PORT}`);
      console.log('[SERVER] Estado: ONLINE');
      console.log(`[BOT] Prefijo: ${COMMAND_PREFIX}`);
      console.log(`[API] Base: ${API_BASE}`);
      console.log('[WEBHOOK] Secreto configurado: SÍ');
      console.log('====================================');
      console.log('');
    }
  );

  server.on('error', error => {
    console.error('[SERVER] Error:', error);
    process.exit(1);
  });

  let cerrando = false;

  function cerrarServidor(signal) {
    if (cerrando) return;

    cerrando = true;

    console.log(
      `[SERVER] Recibida señal ${signal}.`
    );

    const forceExit = setTimeout(() => {
      console.error('[SERVER] Cierre forzado.');
      process.exit(1);
    }, 10000);

    server.close(error => {
      clearTimeout(forceExit);

      if (error) {
        console.error('[SERVER] Error:', error);
        process.exit(1);
      }

      console.log('[SERVER] Servidor cerrado.');
      process.exit(0);
    });
  }

  process.once('SIGTERM', () => {
    cerrarServidor('SIGTERM');
  });

  process.once('SIGINT', () => {
    cerrarServidor('SIGINT');
  });

  return server;
}

// ============================================================
// EXPORTACIONES
// ============================================================

module.exports = {
  app,
  iniciarServidor,
  anarchyAPI,
  enviarMensaje,
  verificarFirmaHmac,
  getThreadState,
  rememberEvent,
  pruneHistory,
  delay,
  AnarchyApiError,
  COMMAND_PREFIX,
  threadStates,
  seenEvents,
  lastThreadSendAt,
  botSendHistory,
  threadSendHistory,
  sendQueue
};

// ============================================================
// EJECUCIÓN
// ============================================================

if (require.main === module) {
  try {
    iniciarServidor();
  } catch (error) {
    console.error(
      '[STARTUP] No se pudo iniciar:',
      error.message
    );

    process.exit(1);
  }
}
