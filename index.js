const express = require('express');
const crypto = require('crypto');

const app = express();
const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;
const BOT_TOKEN = process.env.BOT_TOKEN;

// Estado del bot en memoria
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

// Función para enviar mensajes al chat de Anarchy mediante su API
async function enviarMensajeAnarchy(ndcId, threadId, texto) {
  try {
    const response = await fetch(`https://api.theanarchy.app/api/v1/x${ndcId}/chat/thread/${threadId}/message`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${BOT_TOKEN}`
      },
      body: JSON.stringify({ content: texto, type: 0 })
    });
    const data = await response.json();
    console.log("Respuesta de API al enviar mensaje:", data);
  } catch (error) {
    console.error("Error al enviar mensaje a Anarchy:", error);
  }
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

  // Procesar comandos de texto
  if (contenidoTexto && ndcId && threadId) {
    const texto = contenidoTexto.trim();
    const args = texto.split(' ');
    const comando = args[0].toLowerCase();
    
    console.log(`Comando detectado: ${comando}`);

    let respuestaTexto = null;

    if (comando === '-ayuda') {
      respuestaTexto = "🤖 **KATHERINE — CENTRO DE AYUDA**\n\n📖 **COMANDOS BÁSICOS**\n🔊 `-leerchat` — Activar lectura.\n🔇 `-noleerchat` — Desactivar lectura.\n📊 `-estadochat` — Consultar estado.\n🎙️ `-entrar-sala` — Entrar a la llamada.\n🚪 `-salir` — Salir de la llamada.\n🗣️ `-voz` — Probar la voz.\n📚 `-ayuda` — Mostrar comandos.\n\n━━━━━━━━━━━━━━\n\n🎵 **COMANDOS DE MÚSICA**\n▶️ `-musica` — Activar reproductor.\n🔎 `-play <canción>` — Buscar y reproducir.\n⏸️ `-pausa` — Pausar música.\n▶️ `-reanudar` — Continuar reproducción.\n⏭️ `-siguiente` — Siguiente canción.\n⏹️ `-pararmusica` — Detener música.\n🔊 `-volumen` — Consultar volumen.\n🎚️ `-volumen <0-100>` — Ajustar volumen.";
    } 
    else if (comando === '-leerchat') {
      estadoBot.lectura = true;
      respuestaTexto = "🔊 **Katherine:** Lectura del chat **ACTIVADA**.";
    } 
    else if (comando === '-noleerchat') {
      estadoBot.lectura = false;
      respuestaTexto = "🔇 **Katherine:** Lectura del chat **DESACTIVADA**.";
    } 
    else if (comando === '-estadochat') {
      const estadoLectura = estadoBot.lectura ? "🟢 ACTIVADA" : "🔇 DESACTIVADA";
      const estadoMic = estadoBot.conectado ? "🟢 CONECTADO" : "🔇 DESCONECTADO";
      const estadoLlamada = estadoBot.conectado ? "🟢 EN UNA LLAMADA" : "⚪ NO CONECTADO A UNA LLAMADA";
      respuestaTexto = `📊 **KATHERINE — ESTADO DEL CHAT**\n\nLectura: ${estadoLectura}\nMicrófono: ${estadoMic}\nEstado: ${estadoLlamada}`;
    } 
    else if (comando === '-entrar-sala') {
      estadoBot.conectado = true;
      respuestaTexto = "🎙️ **KATHERINE — CONEXIÓN**\n\n🟢 Katherine entró a la llamada y publicó su micrófono.\n" + (estadoBot.lectura ? "🔊 Lectura: ACTIVADA." : "🔇 Lectura: DESACTIVADA.\nPara comenzar a leer, escribí \"-leerchat\".");
    } 
    else if (comando === '-salir') {
      estadoBot.conectado = false;
      estadoBot.reproduciendo = false;
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
        const nuevoVol = parseInt(args[1]);
        if (!isNaN(nuevoVol) && nuevoVol >= 0 && nuevoVol <= 100) {
          estadoBot.volumen = nuevoVol;
          respuestaTexto = `🎚️ **Katherine:** Volumen ajustado a **${estadoBot.volumen}/100**.`;
        } else {
          respuestaTexto = "⚠️ Por favor ingresa un número de volumen válido entre 0 y 100. Ejemplo: `-volumen 50`";
        }
      } else {
        respuestaTexto = `🔊 **Katherine:** Volumen actual de música: **${estadoBot.volumen}/100**.`;
      }
    } 
    else if (comando === '-play') {
      const busqueda = args.slice(1).join(' ');
      if (!busqueda) {
        respuestaTexto = "⚠️ Escribe la canción que quieres buscar. Ejemplo: `-play Follow Me`";
      } else {
        estadoBot.reproduciendo = true;
        estadoBot.cancionActual = busqueda;
        respuestaTexto = `🎵 **KATHERINE MUSIC**\n\n▶️ **Reproduciendo:** ${busqueda}\n🔊 **Volumen:** ${estadoBot.volumen}/100`;
      }
    } 
    else if (comando === '-pausa') {
      estadoBot.reproduciendo = false;
      respuestaTexto = "⏸️ **Katherine:** Música pausada.";
    } 
    else if (comando === '-reanudar') {
      estadoBot.reproduciendo = true;
      respuestaTexto = "▶️ **Katherine:** Reproducción reanudada.";
    } 
    else if (comando === '-siguiente') {
      respuestaTexto = "⏭️ **Katherine:** Saltando a la siguiente canción...";
    }
    else if (comando === '-pararmusica') {
      estadoBot.reproduciendo = false;
      estadoBot.cancionActual = null;
      respuestaTexto = "⏹️ **Katherine:** Reproducción detenida y lista de reproducción limpiada.";
    }

    // Si hay una respuesta generada, la mandamos de vuelta a Anarchy por la API
    if (respuestaTexto) {
      await enviarMensajeAnarchy(ndcId, threadId, respuestaTexto);
    }
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Servidor de Katherine activo en el puerto ${PORT}`);
});
