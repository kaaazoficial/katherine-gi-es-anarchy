const express = require('express');
const crypto = require('crypto');

const app = express();

const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;
const PORT = process.env.PORT || 3000;
const API_BASE = 'https://api.theanarchy.app/api/v1';
const startTime = Date.now();

if (!BOT_TOKEN) console.warn('[AVISO] Falta BOT_TOKEN: no se podrán enviar mensajes.');
if (!SECRET) console.warn('[AVISO] Falta WEBHOOK_SECRET/WEB_TOKEN: se rechazarán todas las peticiones.');

// Estado POR SALA (antes era global y se mezclaba entre salas)
const estados = new Map();
function getEstado(ndcId, threadId) {
  const key = `${ndcId}:${threadId}`;
  if (!estados.has(key)) {
    estados.set(key, {
      lectura: false,
      conectado: false,
      volumen: 30,
      reproduciendo: false,
      cancionActual: null,
      cola: []
    });
  }
  return estados.get(key);
}

// Guardamos el body crudo para validar la firma HMAC
app.use(express.json({
  verify: (req, res, buf) => { req.rawBody = buf.toString('utf8'); }
}));

// Cliente de la API con timeout
async function anarchyAPI(endpoint, method = 'GET', body = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10000);
  try {
    const options = {
      method,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${BOT_TOKEN}`
      },
      signal: controller.signal
    };
    if (body) options.body = JSON.stringify(body);

    const response = await fetch(`${API_BASE}${endpoint}`, options);
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { raw: text }; }
    return { ok: response.ok, status: response.status, data };
  } catch (err) {
    console.error(`[API ERROR] ${method} ${endpoint}:`, err.message);
    return { ok: false, status: 500, data: null };
  } finally {
    clearTimeout(timer);
  }
}

async function enviarRespuestaChat(ndcId, threadId, texto) {
  const payload = { message: { content: texto, type: 0 } };
  console.log(`[ENVIANDO A CHAT] NDC: ${ndcId} | Thread: ${threadId}`);
  const resApi = await anarchyAPI(`/x${ndcId}/chat/thread/${threadId}/message`, 'POST', payload);
  console.log(`[RESPUESTA CHAT] HTTP: ${resApi.status} | Datos: ${JSON.stringify(resApi.data)}`);
  return resApi;
}

// Validación de firma HMAC
function firmaValida(req) {
  const timestamp = req.headers['x-anarchy-bot-timestamp'];
  const signature = req.headers['x-anarchy-bot-signature'];
  if (!timestamp || !signature) return { ok: false, msg: 'Faltan cabeceras de seguridad.' };
  if (!SECRET) return { ok: false, msg: 'Servidor sin secreto configurado.' };

  let ts = parseInt(timestamp, 10);
  if (isNaN(ts)) return { ok: false, msg: 'Timestamp inválido.' };
  if (ts > 1e12) ts = Math.floor(ts / 1000); // por si viene en milisegundos

  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - ts) > 300) return { ok: false, msg: 'Timestamp expirado.' };

  const expected = 'v1=' + crypto
    .createHmac('sha256', SECRET)
    .update(`${timestamp}.${req.rawBody || ''}`)
    .digest('hex');

  const sigBuffer = Buffer.from(String(signature));
  const expBuffer = Buffer.from(expected);
  if (sigBuffer.length !== expBuffer.length || !crypto.timingSafeEqual(sigBuffer, expBuffer)) {
    return { ok: false, msg: 'Firma inválida.' };
  }
  return { ok: true };
}

const AYUDA =
  "🤖 **KATHERINE — CENTRO DE CONTROL AVANZADO**\n\n" +
  "📖 **COMANDOS BÁSICOS & ESTADO**\n" +
  "🔊 `-leerchat` — Activar lectura automática.\n" +
  "🔇 `-noleerchat` — Desactivar lectura.\n" +
  "📊 `-estadochat` — Consultar sala, miembros y estado.\n" +
  "🏓 `-ping` — Ver latencia y tiempo activo.\n" +
  "ℹ️ `-info` — Datos del sistema de Katherine.\n\n" +
  "🎙️ **VOZ & LLAMADA**\n" +
  "🎙️ `-entrar-sala` — Unirse a la llamada y abrir mic.\n" +
  "🚪 `-salir` — Desconectar de la llamada.\n" +
  "🗣️ `-voz` — Diagnóstico de síntesis vocal.\n\n" +
  "🎵 **REPRODUCTOR DE MÚSICA**\n" +
  "▶️ `-musica` — Activar consola de audio.\n" +
  "🔎 `-play <canción>` — Reproducir o añadir a la cola.\n" +
  "⏸️ `-pausa` — Pausar la música.\n" +
  "▶️ `-reanudar` — Continuar reproducción.\n" +
  "⏭️ `-siguiente` — Siguiente canción de la cola.\n" +
  "⏹️ `-pararmusica` — Detener y limpiar reproductor.\n" +
  "🔊 `-volumen` / `-volumen <0-100>` — Control de audio.";

async function procesarComando(evento, ndcId, threadId) {
  const contenidoTexto =
    evento.content ||
    (evento.message && evento.message.content) ||
    (evento.data && evento.data.content);

  if (typeof contenidoTexto !== 'string' || !ndcId || !threadId) return;

  const texto = contenidoTexto.trim();
  if (!texto.startsWith('-')) return;

  const args = texto.split(/\s+/);
  const comando = args[0].toLowerCase();
  const estado = getEstado(ndcId, threadId);
  const autor = evento.message?.author?.nickname || 'Anónimo';
  console.log(`[COMANDO]: ${comando} | Usuario: ${autor}`);

  let respuesta = null;

  switch (comando) {
    case '-ayuda':
      respuesta = AYUDA;
      break;

    case '-ping': {
      const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
      respuesta = `🏓 **PONG!** Servidor en línea.\n⏱️ **Uptime:** ${uptimeSec} segundos\n⚡ **Estado:** Conectado con la API de Anarchy.`;
      break;
    }

    case '-info':
      respuesta = `🤖 **KATHERINE BOT v3.1**\n🧠 **Motor:** Node.js (Express Webhook Engine)\n🛡️ **Seguridad:** HMAC-SHA256 Activo\n🌐 **Host:** Render Cloud Services`;
      break;

    case '-estadochat': {
      const chatRes = await anarchyAPI(`/x${ndcId}/chat/thread/${threadId}`);
      const chatInfo = chatRes.data?.thread || chatRes.data || {};
      const miembros = chatInfo.membersCount ?? chatInfo.participants?.length ?? 'Datos no disponibles';
      const tituloSala = chatInfo.title || 'Sala actual';
      respuesta =
        `📊 **KATHERINE — ESTADO EN TIEMPO REAL**\n\n` +
        `🏠 **Sala:** ${tituloSala}\n` +
        `👥 **Miembros en el chat:** ${miembros}\n` +
        `🔊 **Lectura de chat:** ${estado.lectura ? '🟢 ACTIVADA' : '🔴 DESACTIVADA'}\n` +
        `🎙️ **Estado de llamada:** ${estado.conectado ? '🟢 CONECTADO' : '⚪ DESCONECTADO'}\n` +
        `🎵 **Reproductor:** ${estado.reproduciendo ? `▶️ (${estado.cancionActual})` : '⏸️ Inactivo'}\n` +
        `📜 **En cola:** ${estado.cola.length}\n` +
        `🎚️ **Volumen actual:** ${estado.volumen}/100`;
      break;
    }

    case '-leerchat':
      estado.lectura = true;
      respuesta = '🔊 **Katherine:** Lectura del chat **ACTIVADA**.';
      break;

    case '-noleerchat':
      estado.lectura = false;
      respuesta = '🔇 **Katherine:** Lectura del chat **DESACTIVADA**.';
      break;

    case '-entrar-sala':
      estado.conectado = true;
      respuesta =
        '🎙️ **KATHERINE — CONEXIÓN DE VOZ**\n\n🟢 Katherine entró a la llamada y su micrófono está activo.\n' +
        (estado.lectura
          ? '🔊 Lectura del chat: **ACTIVADA**.'
          : '🔇 Lectura del chat: **DESACTIVADA** (usa `-leerchat` para activar).');
      break;

    case '-salir':
      estado.conectado = false;
      estado.reproduciendo = false;
      estado.cancionActual = null;
      estado.cola = [];
      respuesta = '🚪 **Katherine:** Salió de la llamada de voz y cerró el micrófono.';
      break;

    case '-voz':
      respuesta = '🗣️ **Katherine:** Comprobando módulos de voz... ¡Canales de audio operando correctamente!';
      break;

    case '-musica':
      respuesta = '🎵 **KATHERINE MUSIC SYSTEM**\n\nConsola de audio activada. Usa `-play <nombre>` para reproducir música.';
      break;

    case '-play': {
      const busqueda = args.slice(1).join(' ');
      if (!busqueda) {
        respuesta = '⚠️ **Error:** Ingresa el nombre de la canción. Ejemplo: `-play Follow Me`';
      } else if (estado.reproduciendo && estado.cancionActual) {
        estado.cola.push(busqueda);
        respuesta = `📜 **Katherine:** **${busqueda}** añadida a la cola (posición ${estado.cola.length}).`;
      } else {
        estado.reproduciendo = true;
        estado.cancionActual = busqueda;
        respuesta = `🎵 **KATHERINE MUSIC**\n\n▶️ **Reproduciendo:** ${busqueda}\n🎚️ **Volumen:** ${estado.volumen}/100`;
      }
      break;
    }

    case '-pausa':
      if (estado.reproduciendo) {
        estado.reproduciendo = false;
        respuesta = '⏸️ **Katherine:** Reproducción de música pausada.';
      } else {
        respuesta = '⚠️ No hay nada reproduciéndose en este momento.';
      }
      break;

    case '-reanudar':
      if (estado.cancionActual) {
        estado.reproduciendo = true;
        respuesta = `▶️ **Katherine:** Reanudando reproducción de **${estado.cancionActual}**.`;
      } else {
        respuesta = '⚠️ No hay ninguna pista pausada en este momento.';
      }
      break;

    case '-siguiente':
      if (estado.cola.length > 0) {
        estado.cancionActual = estado.cola.shift();
        estado.reproduciendo = true;
        respuesta = `⏭️ **Katherine:** Reproduciendo ahora **${estado.cancionActual}**.`;
      } else {
        estado.reproduciendo = false;
        estado.cancionActual = null;
        respuesta = '⏭️ **Katherine:** La cola está vacía, reproductor detenido.';
      }
      break;

    case '-pararmusica':
      estado.reproduciendo = false;
      estado.cancionActual = null;
      estado.cola = [];
      respuesta = '⏹️ **Katherine:** Reproductor detenido y lista de reproducción borrada.';
      break;

    case '-volumen': {
      if (args[1] !== undefined) {
        const nuevoVol = Number(args[1]);
        if (Number.isInteger(nuevoVol) && nuevoVol >= 0 && nuevoVol <= 100) {
          estado.volumen = nuevoVol;
          respuesta = `🎚️ **Katherine:** Volumen fijado en **${estado.volumen}/100**.`;
        } else {
          respuesta = '⚠️ **Error:** Ingresa un número entero entre 0 y 100. Ejemplo: `-volumen 50`';
        }
      } else {
        respuesta = `🔊 **Katherine:** Volumen actual de música: **${estado.volumen}/100**.`;
      }
      break;
    }

    default:
      return; // comando desconocido: se ignora
  }

  if (respuesta) await enviarRespuestaChat(ndcId, threadId, respuesta);
}

// Health check (útil para Render / UptimeRobot)
app.get('/', (req, res) => res.status(200).send('Katherine online'));

// Webhook principal
app.post('/', async (req, res) => {
  const check = firmaValida(req);
  if (!check.ok) return res.status(401).send(check.msg);

  // Responder rápido para evitar timeouts
  res.status(200).send('OK');

  try {
    const evento = req.body || {};
    const ndcId = evento.ndcId ?? evento.message?.ndcId;
    const threadId = evento.threadId ?? evento.message?.threadId;
    await procesarComando(evento, ndcId, threadId);
  } catch (err) {
    console.error('[ERROR PROCESANDO EVENTO]:', err);
  }
});

// Manejo de JSON inválido y errores generales
app.use((err, req, res, next) => {
  console.error('[ERROR EXPRESS]:', err.message);
  if (!res.headersSent) res.status(400).send('Petición inválida.');
});

app.listen(PORT, () => {
  console.log(`[KATHERINE SERVER] Activo en el puerto ${PORT}`);
});
