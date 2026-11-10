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

// Cliente HTTP mejorado con captura de error detallada
async function anarchyAPI(endpoint, method = 'GET', body = null) {
  try {
    const url = `https://api.theanarchy.app/api/v1${endpoint}`;
    console.log(`[API REQUEST] ${method} -> ${url}`);
    
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

    console.log(`[API RESPONSE] Status: ${response.status} | Body:`, JSON.stringify(data));
    return { ok: response.ok, status: response.status, data };
  } catch (err) {
    console.error(`❌ [ERROR CRÍTICO FETCH] No se pudo conectar a api.theanarchy.app:`, err);
    return { ok: false, status: 500, data: { error: err.message } };
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
  return resApi;
};

app.post('/', async (req, res) => {
  console.log("\n-------------------------------------------------------");
  console.log("⚡ [WEBHOOK] Petición recibida de Anarchy");

  const timestamp = req.headers['x-anarchy-bot-timestamp'];
  const signature = req.headers['x-anarchy-bot-signature'];

  if (SECRET && timestamp && signature) {
    try {
      const expected = 'v1=' + crypto
        .createHmac('sha256', SECRET)
        .update(`${timestamp}.${req.rawBody || ''}`)
        .digest('hex');

      const sigBuffer = Buffer.from(signature);
      const expBuffer = Buffer.from(expected);

      if (sigBuffer.length !== expBuffer.length || !crypto.timingSafeEqual(sigBuffer, expBuffer)) {
        console.warn(`⚠️ Advertencia HMAC: Firma no coincide (Puedes ignorarlo temporalmente).`);
      } else {
        console.log("✅ Firma HMAC validada correctamente.");
      }
    } catch (err) {
      console.error("❌ Error en HMAC:", err.message);
    }
  }

  res.status(200).send('OK');

  const evento = req.body || {};
  const ndcId = evento.ndcId;
  const threadId = evento.threadId;

  const contenidoTexto = 
    (typeof evento.content === 'string' && evento.content) ||
    (evento.message && typeof evento.message.content === 'string' && evento.message.content) ||
    (evento.data && typeof evento.data.content === 'string' && evento.data.content);

  if (!contenidoTexto || !ndcId || !threadId) return;

  const texto = contenidoTexto.trim();
  const args = texto.split(/\s+/);
  const comando = args[0].toLowerCase();

  console.log(`🎯 [COMANDO DETECTADO]: "${comando}" | Usuario: ${evento.message?.author?.nickname || 'Anónimo'}`);

  let respuesta = null;

  if (comando === '-ping') {
    const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
    respuesta = `🏓 **PONG!** Servidor en línea.\n⏱️ **Uptime:** ${uptimeSec}s\n⚡ Webhook funcionando correctamente.`;
  } else if (comando === '-ayuda') {
    respuesta = "🤖 **KATHERINE — CENTRO DE CONTROL**\n-ping: Ver estado\n-estadochat: Ver sala";
  } else if (comando === '-estadochat') {
    respuesta = `📊 **Sala ID:** ${threadId} | NDC: ${ndcId}`;
  }

  if (respuesta) {
    await enviarRespuestaChat(ndcId, threadId, respuesta);
  }
});

app.listen(PORT, () => {
  console.log(`[KATHERINE SERVER] Servidor activo en el puerto ${PORT}`);
});
