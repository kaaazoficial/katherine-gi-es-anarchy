const express = require('express');
const crypto = require('crypto');

const app = express();
const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;
const PORT = process.env.PORT || 3000;
const startTime = Date.now();

let estadoBot = {
  lectura: false,
  conectado: false,
  volumen: 30,
  reproduciendo: false,
  cancionActual: null
};

app.use(express.json({
  verify: (req, res, buf) => {
    req.rawBody = buf.toString();
  }
}));

app.use(express.urlencoded({ extended: true }));

// Cliente HTTP oficial para la API de Anarchy (Basado en la documentación)
async function anarchyAPI(endpoint, method = 'GET', body = null) {
  try {
    const url = `https://api.anarchyapps.org/bot-api/v1${endpoint}`;
    console.log(`[API REQUEST] ${method} -> ${url}`);
    
    const options = {
      method,
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bot ${BOT_TOKEN}` // <-- Corregido según la documentación oficial ("Bot {token}")
      }
    };
    if (body) options.body = JSON.stringify(body);

    const response = await fetch(url, options);
    const text = await response.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { raw: text }; }

    console.log(`[API RESPONSE] Status: ${response.status} | Body:`, JSON.stringify(data));
    return { ok: response.ok, status: response.status, data };
  } catch (err) {
    console.error(`❌ [ERROR CRÍTICO FETCH]:`, err);
    return { ok: false, status: 500, data: { error: err.message } };
  }
}

// Enviar respuesta al chat (Estructura oficial de Anarchy Bot API)
const enviarRespuestaChat = async (threadId, texto) => {
  const payload = {
    content: texto
  };
  console.log(`[ENVIANDO MENSAJE] Thread: ${threadId}`);
  const resApi = await anarchyAPI(`/threads/${threadId}/messages`, 'POST', payload);
  return resApi;
};

// Endpoint principal del Webhook
app.post('/', async (req, res) => {
  console.log("\n-------------------------------------------------------");
  console.log("⚡ [WEBHOOK] Petición recibida de Anarchy");

  const timestamp = req.headers['x-anarchy-bot-timestamp'];
  const signature = req.headers['x-anarchy-bot-signature'];

  // Validación de firma HMAC (Sección 4 de la documentación)
  if (SECRET && timestamp && signature) {
    try {
      const expected = crypto
        .createHmac('sha256', SECRET)
        .update(`${timestamp}.${req.rawBody || ''}`)
        .digest('hex');

      const sigClean = signature.startsWith('v1=') ? signature.slice(3) : signature;
      const sigBuffer = Buffer.from(sigClean);
      const expBuffer = Buffer.from(expected);

      if (sigBuffer.length !== expBuffer.length || !crypto.timingSafeEqual(sigBuffer, expBuffer)) {
        console.warn(`⚠️ Advertencia HMAC: La firma no coincide.`);
      } else {
        console.log("✅ Firma HMAC validada correctamente.");
      }
    } catch (err) {
      console.error("❌ Error en bloque HMAC:", err.message);
    }
  }

  // Responder 200 OK inmediatamente para cumplir con la regla de los 5 segundos
  res.status(200).send('OK');

  const evento = req.body || {};
  const threadId = evento.threadId;
  const contenidoTexto = evento.message && evento.message.content;

  if (!contenidoTexto || !threadId) return;

  const texto = contenidoTexto.trim();
  const args = texto.split(/\s+/);
  const comando = args[0].toLowerCase();

  console.log(`🎯 [COMANDO DETECTADO]: "${comando}" | Usuario: ${evento.message?.author?.nickname || 'Anónimo'}`);

  let respuesta = null;

  switch (comando) {
    case '-ayuda':
    case '!ayuda':
      respuesta = "🤖 **KATHERINE — CENTRO DE CONTROL**\n\n" +
                  "📖 **COMANDOS BÁSICOS**\n" +
                  "🔊 `-leerchat` — Activar lectura.\n" +
                  "🔇 `-noleerchat` — Desactivar lectura.\n" +
                  "📊 `-estadochat` — Consultar estado.\n" +
                  "🏓 `-ping` — Probar estado y tiempo en vivo.\n" +
                  "🎙️ `-entrar-sala` — Entrar a la llamada.\n" +
                  "🚪 `-salir` — Desconectar de la llamada.\n\n" +
                  "🎵 **MÚSICA**\n" +
                  "▶️ `-play <canción>` — Reproducir.\n" +
                  "⏸️ `-pausa` — Pausar.\n" +
                  "🔊 `-volumen <0-100>` — Ajustar volumen.";
      break;

    case '-ping':
    case '!ping': {
      const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
      respuesta = `🏓 **PONG!** Servidor en línea.\n⏱️ **Uptime:** ${uptimeSec}s\n⚡ Webhook conectado correctamente a Anarchy.`;
      break;
    }

    case '-estadochat':
    case '!estadochat':
      respuesta = `📊 **KATHERINE — ESTADO**\n🏠 **Thread ID:** ${threadId}\n🔊 **Lectura:** ${estadoBot.lectura ? "🟢 ACTIVADA" : "🔴 DESACTIVADA"}\n🎙️ **Llamada:** ${estadoBot.conectado ? "🟢 CONECTADO" : "⚪ DESCONECTADO"}`;
      break;

    case '-leerchat':
    case '!leerchat':
      estadoBot.lectura = true;
      respuesta = "🔊 **Katherine:** Lectura del chat **ACTIVADA**.";
      break;

    case '-noleerchat':
    case '!noleerchat':
      estadoBot.lectura = false;
      respuesta = "🔇 **Katherine:** Lectura del chat **DESACTIVADA**.";
      break;

    case '-play':
    case '!play': {
      const busqueda = args.slice(1).join(' ');
      if (!busqueda) {
        respuesta = "⚠️ Especifica la canción. Ejemplo: `-play Follow Me`";
      } else {
        estadoBot.reproduciendo = true;
        estadoBot.cancionActual = busqueda;
        respuesta = `🎵 **KATHERINE MUSIC**\n\n▶️ **Reproduciendo:** ${busqueda}\n🎚️ **Volumen:** ${estadoBot.volumen}/100`;
      }
      break;
    }
  }

  if (respuesta) {
    await enviarRespuestaChat(threadId, respuesta);
  }
});

app.listen(PORT, () => {
  console.log(`[KATHERINE SERVER] Servidor activo en el puerto ${PORT}`);
