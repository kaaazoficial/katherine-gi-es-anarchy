const express = require('express');
const crypto = require('crypto');

const app = express();
const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;

app.use(express.json({
  verify: (req, res, buf) => {
    req.rawBody = buf.toString();
  }
}));

// Función para enviar mensajes al chat con la estructura correcta y logs de depuración
async function enviarMensajeAnarchy(ndcId, threadId, texto) {
  try {
    const url = `https://api.theanarchy.app/api/v1/x${ndcId}/chat/thread/${threadId}/message`;
    console.log(`Enviando mensaje a URL: ${url}`);

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${BOT_TOKEN}`
      },
      // Estructura envuelta en 'message' tal como maneja la plataforma
      body: JSON.stringify({
        message: {
          content: texto,
          type: 0
        }
      })
    });

    const responseText = await response.text();
    console.log(`Estado HTTP de respuesta API: ${response.status}`);
    console.log(`Cuerpo de respuesta API: ${responseText}`);
  } catch (error) {
    console.error("Error crítico al enviar mensaje a Anarchy:", error);
  }
}

// Función para obtener información real del chat (miembros, etc.)
async function obtenerInfoChat(ndcId, threadId) {
  try {
    const url = `https://api.theanarchy.app/api/v1/x${ndcId}/chat/thread/${threadId}`;
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${BOT_TOKEN}`
      }
    });
    
    if (response.ok) {
      const data = await response.json();
      // Retorna los miembros o el objeto de la sala si la API lo provee
      return data.thread || data;
    }
  } catch (error) {
    console.error("Error al obtener info del chat:", error);
  }
  return null;
}

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
    return res.status(401).send('Error al validar la firma.');
  }

  // Respondemos 200 OK inmediatamente a Anarchy para evitar timeouts
  res.status(200).send('OK');

  const evento = req.body;
  const ndcId = evento.ndcId;
  const threadId = evento.threadId;
  const contenidoTexto = evento.content || (evento.message && evento.message.content) || (evento.data && evento.data.content);

  if (contenidoTexto && ndcId && threadId) {
    const texto = contenidoTexto.trim();
    const args = texto.split(' ');
    const comando = args[0].toLowerCase();
    
    console.log(`Comando detectado: ${comando}`);

    let respuestaTexto = null;

    if (comando === '-ayuda') {
      respuestaTexto = "🤖 **KATHERINE — CENTRO DE AYUDA**\n\n📖 **COMANDOS BÁSICOS**\n🔊 `-leerchat` — Activar lectura.\n🔇 `-noleerchat` — Desactivar lectura.\n📊 `-estadochat` — Consultar estado y miembros.\n🎙️ `-entrar-sala` — Entrar a la llamada.\n🚪 `-salir` — Salir de la llamada.\n🗣️ `-voz` — Probar la voz.\n📚 `-ayuda` — Mostrar comandos.\n\n━━━━━━━━━━━━━━\n\n🎵 **COMANDOS DE MÚSICA**\n▶️ `-musica` — Activar reproductor.\n🔎 `-play <canción>` — Buscar y reproducir.\n⏸️ `-pausa` — Pausar música.\n▶️ `-reanudar` — Continuar reproducción.\n⏭️ `-siguiente` — Siguiente canción.\n⏹️ `-pararmusica` — Detener música.\n🔊 `-volumen` — Consultar volumen.\n🎚️ `-volumen <0-100>` — Ajustar volumen.";
    } 
    else if (comando === '-estadochat') {
      // Consultamos la API para traer datos reales del chat
      const chatInfo = await obtenerInfoChat(ndcId, threadId);
      const totalMiembros = chatInfo?.membersCount || chatInfo?.participants?.length || "No disponible";
      const tituloSala = chatInfo?.title || "Taberna / Sala actual";

      respuestaTexto = `📊 **KATHERINE — ESTADO DEL CHAT**\n\n🏠 **Sala:** ${tituloSala}\n👥 **Miembros en el chat:** ${totalMiembros}\n🟢 **Estado del bot:** Conectado y escuchando webhooks.`;
    } 
    else if (comando === '-entrar-sala') {
      respuestaTexto = "🎙️ **KATHERINE — CONEXIÓN**\n\n🟢 Katherine entró a la llamada y publicó su micrófono.";
    } 
    else if (comando === '-salir') {
      respuestaTexto = "🚪 **Katherine:** Salió de la llamada y desconectó el micrófono.";
    } 
    else if (comando === '-voz') {
      respuestaTexto = "🗣️ **Katherine:** Probando síntesis de voz... ¡Todo funciona correctamente!";
    } 
    else if (comando === '-musica') {
      respuestaTexto = "🎵 **KATHERINE MUSIC**\n\nReproductor activado. Usa `-play <canción>` para empezar a reproducir.";
    }
    else if (comando === '-volumen') {
      if (args[1]) {
        respuestaTexto = `🎚️ **Katherine:** Volumen ajustado a **${args[1]}/100**.`;
      } else {
        respuestaTexto = `🔊 **Katherine:** Volumen actual de música: **30/100**.`;
      }
    } 
    else if (comando === '-play') {
      const busqueda = args.slice(1).join(' ');
      if (!busqueda) {
        respuestaTexto = "⚠️ Escribe la canción que quieres buscar. Ejemplo: `-play Follow Me`";
      } else {
        respuestaTexto = `🎵 **KATHERINE MUSIC**\n\n▶️ **Reproduciendo:** ${busqueda}`;
      }
    } 
    else if (comando === '-pausa') {
      respuestaTexto = "⏸️ **Katherine:** Música pausada.";
    } 
    else if (comando === '-reanudar') {
      respuestaTexto = "▶️ **Katherine:** Reproducción reanudada.";
    } 
    else if (comando === '-siguiente') {
      respuestaTexto = "⏭️ **Katherine:** Saltando a la siguiente canción...";
    }
    else if (comando === '-pararmusica') {
      respuestaTexto = "⏹️ **Katherine:** Reproducción detenida.";
    }

    // Si hay texto de respuesta, lo enviamos de vuelta al chat
    if (respuestaTexto) {
      await enviarMensajeAnarchy(ndcId, threadId, respuestaTexto);
    }
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Servidor de Katherine activo en el puerto ${PORT}`);
});
