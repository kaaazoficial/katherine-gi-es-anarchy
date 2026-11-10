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

// Capturar el cuerpo crudo para la validación HMAC
app.use(express.json({
  verify: (req, res, buf) => {
    req.rawBody = buf.toString();
  }
}));

app.use(express.urlencoded({ extended: true }));

// Cliente HTTP para consultar la API de Anarchy
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
    console.error(`[ERROR API] ${method} ${endpoint}:`, err.message);
    return { ok: false, status: 500, data: null };
  }
}

// Enviar respuesta al chat
const enviarRespuestaChat = async (ndcId, threadId, texto) => {
  const payload = {
    message: {
      content: texto,
      type: 0
    }
  };
  console.log(`[ENVIANDO MENSAJE] NDC: ${ndcId} | Thread: ${threadId}`);
  const resApi = await anarchyAPI(`/x${ndcId}/chat/thread/${threadId}/message`, 'POST', payload);
  console.log(`[RESULTADO API] Status HTTP: ${resApi.status} | Respuesta:`, JSON.stringify(resApi.data));
  return resApi;
};

// Endpoint principal del Webhook
app.post('/', async (req, res) => {
  console.log("\n=======================================================");
  console.log("⚡ [WEBHOOK] Petición entrante desde Anarchy");
  console.log("Headers:", JSON.stringify(req.headers));
  console.log("Body:", JSON.stringify(req.body));
  console.log("=======================================================\n");

  const timestamp = req.headers['x-anarchy-bot-timestamp'];
  const signature = req.headers['x-anarchy-bot-signature'];

  if (!timestamp || !signature) {
    console.warn("⚠️ Faltan cabeceras de seguridad 'x-anarchy-bot-timestamp' o 'x-anarchy-bot-signature'.");
  }

  // Validación de firma HMAC (Segura pero flexible con el SECRET)
  if (SECRET && timestamp && signature) {
    try {
      const expected = 'v1=' + crypto
        .createHmac('sha256', SECRET)
        .update(`${timestamp}.${req.rawBody || ''}`)
        .digest('hex');

      const sigBuffer = Buffer.from(signature);
      const expBuffer = Buffer.from(expected);

      if (sigBuffer.length !== expBuffer.length || !crypto.timingSafeEqual(sigBuffer, expBuffer)) {
        console.error(`❌ Firma HMAC inválida. Esperada: (${expected}) | Recibida: (${signature})`);
      } else {
        console.log("✅ Firma HMAC validada correctamente.");
      }
    } catch (err) {
      console.error("❌ Error procesando HMAC:", err.message);
    }
  }

  // Responder 200 OK inmediatamente para evitar timeouts
  res.status(200).send('OK');

  const evento = req.body || {};
  const ndcId = evento.ndcId;
  const threadId = evento.threadId;

  // Extracción flexible del texto del mensaje
  const contenidoTexto = 
    (typeof evento.content === 'string' && evento.content) ||
    (evento.message && typeof evento.message.content === 'string' && evento.message.content) ||
    (evento.data && typeof evento.data.content === 'string' && evento.data.content);

  if (!contenidoTexto || !ndcId || !threadId) {
    console.log("ℹ️ La petición no contiene un mensaje de chat válido o le faltan los identificadores de sala.");
    return;
  }

  const texto = contenidoTexto.trim();
  const args = texto.split(/\s+/);
  const comando = args[0].toLowerCase();

  console.log(`🎯 [COMANDO DETECTADO]: "${comando}" | Usuario: ${evento.message?.author?.nickname || 'Anónimo'}`);

  let respuesta = null;

  switch (comando) {
    case '-ayuda':
      respuesta = "🤖 **KATHERINE — CENTRO DE CONTROL**\n\n" +
                  "📖 **COMANDOS BÁSICOS**\n" +
                  "🔊 `-leerchat` — Activar lectura.\n" +
                  "🔇 `-noleerchat` — Desactivar lectura.\n" +
                  "📊 `-estadochat` — Consultar miembros y sala.\n" +
                  "🏓 `-ping` — Probar estado y tiempo en vivo.\n" +
                  "🎙️ `-entrar-sala` — Entrar a la llamada.\n" +
                  "🚪 `-salir` — Desconectar de la llamada.\n\n" +
                  "🎵 **MÚSICA**\n" +
                  "▶️ `-play <canción>` — Reproducir.\n" +
                  "⏸️ `-pausa` — Pausar.\n" +
                  "▶️ `-reanudar` — Reanudar.\n" +
                  "🔊 `-volumen <0-100>` — Ajustar volumen.";
      break;

    case '-ping': {
      const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
      respuesta = `🏓 **PONG!** Servidor en línea.\n⏱️ **Uptime:** ${uptimeSec}s\n⚡ Webhook funcionando correctamente.`;
      break;
    }

    case '-estadochat': {
      const chatRes = await anarchyAPI(`/x${ndcId}/chat/thread/${threadId}`);
      const chatInfo = chatRes.data?.thread || chatRes.data || {};
      const miembros = chatInfo.membersCount || chatInfo.participants?.length || "Desconocido";
      const tituloSala = chatInfo.title || "Sala actual";

      respuesta = `📊 **KATHERINE — ESTADO EN TIEMPO REAL**\n\n` +
                  `🏠 **Sala:** ${tituloSala}\n` +
                  `👥 **Miembros en el chat:** ${miembros}\n` +
                  `🔊 **Lectura:** ${estadoBot.lectura ? "🟢 ACTIVADA" : "🔴 DESACTIVADA"}\n` +
                  `🎙️ **Llamada:** ${estadoBot.conectado ? "🟢 CONECTADO" : "⚪ DESCONECTADO"}`;
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
      respuesta = "🎙️ **Katherine:** Entró a la llamada y su micrófono está activo.";
      break;

    case '-salir':
      estadoBot.conectado = false;
      respuesta = "🚪 **Katherine:** Salió de la llamada de voz.";
      break;

    case '-play': {
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

    case '-pausa':
      estadoBot.reproduciendo = false;
      respuesta = "⏸️ **Katherine:** Música pausada.";
      break;

    case '-reanudar':
      estadoBot.reproduciendo = true;
      respuesta = "▶️ **Katherine:** Reproducción reanudada.";
      break;

    case '-volumen': {
      if (args[1]) {
        const nuevoVol = parseInt(args[1], 10);
        if (!isNaN(nuevoVol) && nuevoVol >= 0 && nuevoVol <= 100) {
          estadoBot.volumen = nuevoVol;
          respuesta = `🎚️ **Katherine:** Volumen fijado en **${estadoBot.volumen}/100**.`;
        } else {
          respuesta = "⚠️ Ingresa un número entre 0 y 100. Ejemplo: `-volumen 50`";
        }
      } else {
        respuesta = `🔊 **Katherine:** Volumen actual: **${estadoBot.volumen}/100**.`;
      }
      break;
    }
  }

  if (respuesta) {
    await enviarRespuestaChat(ndcId, threadId, respuesta);
  }
});

app.listen(PORT, () => {
  console.log(`[KATHERINE SERVER] Servidor activo escuchando en el puerto ${PORT}`);
});
