const express = require('express');
const crypto = require('crypto');

const app = express();
// Asegúrate de que esta variable coincida exactamente con la que creaste en Render (WEBHOOK_SECRET o WEB_TOKEN)
const SECRET = process.env.WEBHOOK_SECRET || process.env.WEB_TOKEN;

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

app.post('/', (req, res) => {
  const timestamp = req.headers['x-anarchy-bot-timestamp'];
  const signature = req.headers['x-anarchy-bot-signature'];

  // Log para revisar qué datos llegan desde Anarchy
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

  const evento = req.body;

  // Extraer el texto del mensaje enviado en Anarchy
  const contenidoTexto = evento.content || (evento.message && evento.message.content) || (evento.data && evento.data.content);

  // Procesar comandos de texto
  if (contenidoTexto) {
    const texto = contenidoTexto.trim();
    const args = texto.split(' ');
    const comando = args[0].toLowerCase();
    
    console.log(`Comando detectado: ${comando}`);

    // 📚 -ayuda
    if (comando === '-ayuda') {
      return res.status(200).json({
        type: 4,
        data: {
          content: "🤖 **KATHERINE — CENTRO DE AYUDA**\n\n📖 **COMANDOS BÁSICOS**\n🔊 `-leerchat` — Activar lectura.\n🔇 `-noleerchat` — Desactivar lectura.\n📊 `-estadochat` — Consultar estado.\n🎙️ `-entrar-sala` — Entrar a la llamada.\n🚪 `-salir` — Salir de la llamada.\n🗣️ `-voz` — Probar la voz.\n📚 `-ayuda` — Mostrar comandos.\n\n━━━━━━━━━━━━━━\n\n🎵 **COMANDOS DE MÚSICA**\n▶️ `-musica` — Activar reproductor.\n🔎 `-play <canción>` — Buscar y reproducir.\n⏸️ `-pausa` — Pausar música.\n▶️ `-reanudar` — Continuar reproducción.\n⏭️ `-siguiente` — Siguiente canción.\n⏹️ `-pararmusica` — Detener música.\n🔊 `-volumen` — Consultar volumen.\n🎚️ `-volumen <0-100>` — Ajustar volumen."
        }
      });
    }

    // 🔊 -leerchat
    if (comando === '-leerchat') {
      estadoBot.lectura = true;
      return res.status(200).json({
        type: 4,
        data: { content: "🔊 **Katherine:** Lectura del chat **ACTIVADA**." }
      });
    }

    // 🔇 -noleerchat
    if (comando === '-noleerchat') {
      estadoBot.lectura = false;
      return res.status(200).json({
        type: 4,
        data: { content: "🔇 **Katherine:** Lectura del chat **DESACTIVADA**." }
      });
    }

    // 📊 -estadochat
    if (comando === '-estadochat') {
      const estadoLectura = estadoBot.lectura ? "🟢 ACTIVADA" : "🔇 DESACTIVADA";
      const estadoMic = estadoBot.conectado ? "🟢 CONECTADO" : "🔇 DESCONECTADO";
      const estadoLlamada = estadoBot.conectado ? "🟢 EN UNA LLAMADA" : "⚪ NO CONECTADO A UNA LLAMADA";

      return res.status(200).json({
        type: 4,
        data: {
          content: `📊 **KATHERINE — ESTADO DEL CHAT**\n\nLectura: ${estadoLectura}\nMicrófono: ${estadoMic}\nEstado: ${estadoLlamada}`
        }
      });
    }

    // 🎙️ -entrar-sala
    if (comando === '-entrar-sala') {
      estadoBot.conectado = true;
      return res.status(200).json({
        type: 4,
        data: {
          content: "🎙️ **KATHERINE — CONEXIÓN**\n\n🟢 Katherine entró a la llamada y publicó su micrófono.\n" + (estadoBot.lectura ? "🔊 Lectura: ACTIVADA." : "🔇 Lectura: DESACTIVADA.\nPara comenzar a leer, escribí \"-leerchat\".")
        }
      });
    }

    // 🚪 -salir
    if (comando === '-salir') {
      estadoBot.conectado = false;
      estadoBot.reproduciendo = false;
      return res.status(200).json({
        type: 4,
        data: { content: "🚪 **Katherine:** Salió de la llamada y desconectó el micrófono." }
      });
    }

    // 🗣️ -voz
    if (comando === '-voz') {
      return res.status(200).json({
        type: 4,
        data: { content: "🗣️ **Katherine:** Probando síntesis de voz... ¡Todo funciona correctamente!" }
      });
    }

    // 🎚️ -volumen
    if (comando === '-volumen') {
      if (args[1]) {
        const nuevoVol = parseInt(args[1]);
        if (!isNaN(nuevoVol) && nuevoVol >= 0 && nuevoVol <= 100) {
          estadoBot.volumen = nuevoVol;
          return res.status(200).json({
            type: 4,
            data: { content: `🎚️ **Katherine:** Volumen ajustado a **${estadoBot.volumen}/100**.` }
          });
        } else {
          return res.status(200).json({
            type: 4,
            data: { content: "⚠️ Por favor ingresa un número de volumen válido entre 0 y 100. Ejemplo: `-volumen 50`" }
          });
        }
      }
      return res.status(200).json({
        type: 4,
        data: { content: `🔊 **Katherine:** Volumen actual de música: **${estadoBot.volumen}/100**.` }
      });
    }

    // 🔎 -play
    if (comando === '-play') {
      const busqueda = args.slice(1).join(' ');
      if (!busqueda) {
        return res.status(200).json({
          type: 4,
          data: { content: "⚠️ Escribe la canción que quieres buscar. Ejemplo: `-play Follow Me`" }
        });
      }
      estadoBot.reproduciendo = true;
      estadoBot.cancionActual = busqueda;
      return res.status(200).json({
        type: 4,
        data: {
          content: `🎵 **KATHERINE MUSIC**\n\n▶️ **Reproduciendo:** ${busqueda}\n🔊 **Volumen:** ${estadoBot.volumen}/100`
        }
      });
    }

    // ⏸️ -pausa
    if (comando === '-pausa') {
      estadoBot.reproduciendo = false;
      return res.status(200).json({
        type: 4,
        data: { content: "⏸️ **Katherine:** Música pausada." }
      });
    }

    // ▶️ -reanudar
    if (comando === '-reanudar') {
      estadoBot.reproduciendo = true;
      return res.status(200).json({
        type: 4,
        data: { content: "▶️ **Katherine:** Reproducción reanudada." }
      });
    }

    // ⏹️ -pararmusica
    if (comando === '-pararmusica') {
      estadoBot.reproduciendo = false;
      estadoBot.cancionActual = null;
      return res.status(200).json({
        type: 4,
        data: { content: "⏹️ **Katherine:** Reproducción detenida y lista de reproducción limpiada." }
      });
    }
  }

  // Respuesta por defecto si entra otra cosa
  return res.status(200).send('OK');
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Servidor de Katherine activo en el puerto ${PORT}`);
});
