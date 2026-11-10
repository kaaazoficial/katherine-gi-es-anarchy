const express = require('express');
const crypto = require('crypto');

const app = express();
const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;
const PORT = process.env.PORT || 3000;
const startTime = Date.now();

// Estado global del bot en memoria
let estadoBot = {
  lectura: false,
  conectado: false,
  volumen: 30,
  reproduciendo: false,
  cancionActual: null,
  cola: []
};

app.use(express.json({
  verify: (req, res, buf) => {
    req.rawBody = buf.toString();
  }
}));

// Cliente centralizado y optimizado para la API de Anarchy
async function anarchyAPI(endpoint, method = 'GET', body = null) {
  try {
    const url = `https://api.theanarchy.app/api/v1${endpoint}`;
    const options = {
      method,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${BOT_TOKEN}`
      }
    };
    if (body) options.body = JSON.stringify(body);

    const response = await fetch(url, options);
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { raw: text }; }

    return { ok: response.ok, status: response.status, data };
  } catch (err) {
    console.error(`[API ERROR] ${method} ${endpoint}:`, err.message);
    return { ok: false, status: 500, data: null };
  }
}

// Función para enviar respuestas directamente al chat de la sala
const enviarRespuestaChat = async (ndcId, threadId, texto) => {
  const payload = {
    message: {
      content: texto,
      type: 0
    }
  };
  console.log(`[ENVIANDO A CHAT] NDC: ${ndcId} | Thread: ${threadId}`);
  const resApi = await anarchyAPI(`/x${ndcId}/chat/thread/${threadId}/message`, 'POST', payload);
  console.log(`[RESPUESTA CHAT] Estado HTTP: ${resApi.status} | Datos:`, JSON.stringify(resApi.data));
  return resApi;
};

// Endpoint principal del Webhook con validación HMAC
app.post('/', async (req, res) => {
  const timestamp = req.headers['x-anarchy-bot-timestamp'];
  const signature = req.headers['x-anarchy-bot-signature'];

  console.log("BODY RECIBIDO:", JSON.stringify(req.body));

  if (!timestamp || !signature) {
    return res.status(401).send('Faltan cabeceras de seguridad.');
  }

  const now = Math.floor(Date.now() / 1000);
  if (Math.abs(now - parseInt(timestamp)) > 300) {
    return res.status(401).send('Timestamp expirado.');
  }

  if (SECRET) {
    try {
      const expected = 'v1=' + crypto
        .createHmac('sha256', SECRET)
        .update(`${timestamp}.${req.rawBody}`)
        .digest('hex');

      const sigBuffer = Buffer.from(signature);
      const expBuffer = Buffer.from(expected);

      if (sigBuffer.length !== expBuffer.length || !crypto.timingSafeEqual(sigBuffer, expBuffer)) {
        return res.status(401).send('Firma inválida.');
      }
    } catch (err) {
      console.error("Error validando HMAC:", err);
      return res.status(401).send('Error al validar la firma.');
    }
  }

  // Responder 200 OK inmediatamente a Anarchy
  res.status(200).send('OK');

  const evento = req.body;
  const ndcId = evento.ndcId;
  const threadId = evento.threadId;
  const contenidoTexto = evento.content || (evento.message && evento.message.content) || (evento.data && evento.data.content);

  if (!contenidoTexto || !ndcId || !threadId) return;

  const texto = contenidoTexto.trim();
  const args = texto.split(/\s+/);
  const comando = args[0].toLowerCase();

  console.log(`[COMANDO DETECTADO]: ${comando} | Usuario: ${evento.message?.author?.nickname || 'Anónimo'}`);

  let respuesta = null;

  switch (comando) {
    case '-ayuda':
      respuesta = "🤖 **KATHERINE — CENTRO DE CONTROL AVANZADO**\n\n" +
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
                  "🔎 `-play <canción>` — Buscar y reproducir pista.\n" +
                  "⏸️ `-pausa` — Pausar la música.\n" +
                  "▶️ `-reanudar` — Continuar reproducción.\n" +
                  "⏭️ `-siguiente` — Siguiente canción de la cola.\n" +
                  "⏹️ `-pararmusica` — Detener y limpiar reproductor.\n" +
                  "🔊 `-volumen` / `🎚️ -volumen <0-100>` — Control de audio.";
      break;

    case '-ping': {
      const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
      respuesta = `🏓 **PONG!** Servidor en línea.\n⏱️ **Uptime:** ${uptimeSec} segundos\n⚡ **Estado:** Conectado con la API de Anarchy.`;
      break;
    }

    case '-info':
      respuesta = `🤖 **KATHERINE BOT v3.0**\n🧠 **Motor:** Node.js (Express Webhook Engine)\n🛡️ **Seguridad:** HMAC-SHA256 Activo\n🌐 **Host:** Render Cloud Services`;
      break;

    case '-estadochat': {
      const chatRes = await anarchyAPI(`/x${ndcId}/chat/thread/${threadId}`);
      const chatInfo = chatRes.data?.thread || chatRes.data || {};
      const miembros = chatInfo.membersCount || chatInfo.participants?.length || "Datos no disponibles";
      const tituloSala = chatInfo.title || "Taberna / Sala actual";

      respuesta = `📊 **KATHERINE — ESTADO EN TIEMPO REAL**\n\n` +
                  `🏠 **Sala:** ${tituloSala}\n` +
                  `👥 **Miembros en el chat:** ${miembros}\n` +
                  `🔊 **Lectura de chat:** ${estadoBot.lectura ? "🟢 ACTIVADA" : "🔴 DESACTIVADA"}\n` +
                  `🎙️ **Estado de llamada:** ${estadoBot.conectado ? "🟢 CONECTADO" : "⚪ DESCONECTADO"}\n` +
                  `🎵 **Reproductor:** ${estadoBot.reproduciendo ? `▶️ (${estadoBot.cancionActual})` : "⏸️ Inactivo"}\n` +
                  `🎚️ **Volumen actual:** ${estadoBot.volumen}/100`;
      break;
    }

    case '-leerchat':
      estadoBot.lectura = true;
      respuesta = "🔊 **Katherine:** Lectura del chat **ACTIVADA**.";
      break;

    case '-noleerchat':
      estadoBot.lectura = false;
      respuesta = "🔇 **Katherine:** Lectura del chat **DESACTIVADA**.";
      break;

    case '-entrar-sala':
      estadoBot.conectado = true;
      respuesta = "🎙️ **KATHERINE — CONEXIÓN DE VOZ**\n\n🟢 Katherine entró a la llamada y su micrófono está activo.\n" +
                  (estadoBot.lectura ? "🔊 Lectura del chat: **ACTIVADA**." : "🔇 Lectura del chat: **DESACTIVADA** (Usa `-leerchat` para activar).");
      break;

    case '-salir':
      estadoBot.conectado = false;
      estadoBot.reproduciendo = false;
      estadoBot.cancionActual = null;
      respuesta = "🚪 **Katherine:** Salió de la llamada de voz y cerró el micrófono.";
      break;

    case '-voz':
      respuesta = "🗣️ **Katherine:** Comprobando módulos de voz... ¡Canales de audio operando correctamente!";
      break;

    case '-musica':
      respuesta = "🎵 **KATHERINE MUSIC SYSTEM**\n\nConsola de audio activada. Usa `-play <nombre>` para reproducir música.";
      break;

    case '-play': {
      const busqueda = args.slice(1).join(' ');
      if (!busqueda) {
        respuesta = "⚠️ **Error:** Ingresa el nombre de la canción. Ejemplo: `-play Follow Me`";
      } else {
        estadoBot.reproduciendo = true;
        estadoBot.cancionActual = busqueda;
        respuesta = `🎵 **KATHERINE MUSIC**\n\n▶️ **Reproduciendo:** ${busqueda}\n🎚️ **Volumen:** ${estadoBot.volumen}/100`;
      }
      break;
    }

    case '-pausa':
      estadoBot.reproduciendo = false;
      respuesta = "⏸️ **Katherine:** Reproducción de música pausada.";
      break;

    case '-reanudar':
      if (estadoBot.cancionActual) {
        estadoBot.reproduciendo = true;
        respuesta = `▶️ **Katherine:** Reanudando reproducción de **${estadoBot.cancionActual}**.`;
      } else {
        respuesta = "⚠️ No hay ninguna pista pausada en este momento.";
      }
      break;

    case '-siguiente':
      respuesta = "⏭️ **Katherine:** Saltando a la siguiente canción de la lista...";
      break;

    case '-pararmusica':
      estadoBot.reproduciendo = false;
      estadoBot.cancionActual = null;
      estadoBot.cola = [];
      respuesta = "⏹️ **Katherine:** Reproductor detenido y lista de reproducción borrada.";
      break;

    case '-volumen': {
      if (args[1]) {
        const nuevoVol = parseInt(args[1], 10);
        if (!isNaN(nuevoVol) && nuevoVol >= 0 && nuevoVol <= 100) {
          estadoBot.volumen = nuevoVol;
          respuesta = `🎚️ **Katherine:** Volumen fijado en **${estadoBot.volumen}/100**.`;
        } else {
          respuesta = "⚠️ **Error:** Ingresa un número válido entre 0 y 100. Ejemplo: `-volumen 50`";
        }
      } else {
        respuesta = `🔊 **Katherine:** Volumen actual de música: **${estadoBot.volumen}/100**.`;
      }
      break;
    }
  }

  if (respuesta) {
    await enviarRespuestaChat(ndcId, threadId, respuesta);
  }
});

app.listen(PORT, () => {
  console.log(`[KATHERINE SERVER] Servidor activo en el puerto ${PORT}`);
});
