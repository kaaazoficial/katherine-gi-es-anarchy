const express = require("express");
const { spawn } = require("child_process");
const ffmpegPath = require("ffmpeg-static");
const https = require("https");
const http = require("http");

const app = express();
app.use(express.json());

let leerChat = false;
let salaActiva = null;
let livekitRoom = null;
let audioSource = null;
let audioTrack = null;
let conectandoSala = false;

let pruebaMusicaActiva = false;

// REPRODUCTOR
let reproductorActivo = false;
let musicaPausada = false;
let detenerMusica = false;
let saltarMusica = false;
let reproduciendoMusica = false;
let volumenMusica = 30;
let colaCanciones = [];
let trabajadorMusicaActivo = false;
let canalMusica = null;

const mensajesProcesados = new Set();

let colaLectura = Promise.resolve();
let colaMensajes = Promise.resolve();
let colaReproduccion = Promise.resolve();
let ultimaSolicitudTTS = 0;

const ESPERA_TTS = 6200;
const FRECUENCIA_LIVEKIT = 48000;
const CANALES_LIVEKIT = 1;
const MUESTRAS_BLOQUE = 480;
const CHAT_ID = "599";

const JAMENDO_CLIENT_ID = "1a74043c";
const JAMENDO_TRACK_ID = "1917276";

// SERVIDOR
app.get("/", (req, res) => {
  res.send("Rbsn está funcionando correctamente.");
});

// DESCARGAR ARCHIVOS USANDO IPV4
function descargarIPv4(direccion, intento = 1) {
  return new Promise((resolve, reject) => {
    let url;

    try {
      url = new URL(direccion);
    } catch (error) {
      return reject(
        new Error("La dirección del audio no es válida.")
      );
    }

    if (
      url.protocol !== "https:" &&
      url.protocol !== "http:"
    ) {
      return reject(
        new Error("Protocolo de descarga no permitido.")
      );
    }

    const cliente =
      url.protocol === "https:" ? https : http;

    const opciones = {
      hostname: url.hostname,
      port: url.port || undefined,
      path: url.pathname + url.search,
      method: "GET",
      family: 4,
      autoSelectFamily: false,
      headers: {
        "User-Agent": "Rbsn-Music/1.0",
        "Accept": "*/*"
      }
    };

    let terminado = false;

    const finalizarError = error => {
      if (terminado) return;

      terminado = true;
      reject(error);
    };

    const solicitud = cliente.request(
      opciones,
      respuesta => {
        const codigo = respuesta.statusCode || 0;

        if (
          codigo >= 300 &&
          codigo < 400 &&
          respuesta.headers.location
        ) {
          respuesta.resume();

          if (intento >= 6) {
            return finalizarError(
              new Error(
                "Demasiadas redirecciones al descargar música."
              )
            );
          }

          const nuevaDireccion = new URL(
            respuesta.headers.location,
            url
          ).toString();

          terminado = true;

          descargarIPv4(
            nuevaDireccion,
            intento + 1
          ).then(resolve).catch(reject);

          return;
        }

        if (codigo !== 200) {
          respuesta.resume();

          return finalizarError(
            new Error(
              "El servidor de música respondió HTTP " + codigo
            )
          );
        }

        const partes = [];
        let total = 0;

        const LIMITE_AUDIO = 40 * 1024 * 1024;

        respuesta.on("data", parte => {
          total += parte.length;

          if (total > LIMITE_AUDIO) {
            solicitud.destroy(
              new Error(
                "El archivo supera el límite permitido."
              )
            );

            return;
          }

          partes.push(parte);
        });

        respuesta.on("end", () => {
          if (terminado) return;

          terminado = true;

          resolve(Buffer.concat(partes));
        });

        respuesta.on("error", finalizarError);
      }
    );

    solicitud.setTimeout(30000, () => {
      solicitud.destroy(
        new Error(
          "Tiempo agotado al descargar audio por IPv4."
        )
      );
    });

    solicitud.on("error", finalizarError);

    solicitud.end();
  });
}

// REINTENTAR DESCARGA
async function descargarAudioJamendo(direccion) {
  let ultimoError = null;

  for (let intento = 1; intento <= 3; intento++) {
    try {
      console.log(
        `Descarga Jamendo IPv4: intento ${intento}/3`
      );

      const audio = await descargarIPv4(direccion);

      if (!audio || !audio.length) {
        throw new Error(
          "El archivo descargado está vacío."
        );
      }

      console.log(
        "Audio descargado correctamente:",
        audio.length,
        "bytes"
      );

      return audio;

    } catch (error) {
      ultimoError = error;

      console.error(
        `Error de descarga ${intento}/3:`,
        error.message,
        error.code || ""
      );

      if (intento < 3) {
        await new Promise(resolve =>
          setTimeout(resolve, intento * 2000)
        );
      }
    }
  }

  throw new Error(
    "No se pudo descargar la música después de 3 intentos: " +
    (ultimoError?.message || "Error desconocido.")
  );
}

// ENVIAR MENSAJES A ANARCHY
async function enviarMensaje(threadId, contenido) {
  colaMensajes = colaMensajes.then(async () => {
    const respuesta = await fetch(
      `https://api.anarchyapps.org/bot-api/v1/threads/${threadId}/messages`,
      {
        method: "POST",
        headers: {
          "Authorization": "Bot " + process.env.BOT_TOKEN,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          content: contenido
        })
      }
    );

    const texto = await respuesta.text();

    if (!respuesta.ok) {
      throw new Error(
        "Error al enviar mensaje: " +
        respuesta.status + " " + texto
      );
    }

    console.log("Mensaje enviado a Anarchy.");

    return texto;
  });

  return colaMensajes;
}

// RESPUESTA SEGURA
function responder(threadId, contenido) {
  enviarMensaje(threadId, contenido).catch(error => {
    console.error("ERROR AL RESPONDER:", error);
  });
}

// AYUDA
const MENSAJE_AYUDA = `🤖 RBSN — CENTRO DE AYUDA

📖 COMANDOS BÁSICOS

🔊 "$leerchat" — Activar lectura.
🔇 "$noleerchat" — Desactivar lectura.
📊 "$estadochat" — Consultar estado.
🎙️ "$entrar-sala" — Entrar a la llamada.
🚪 "$salir" — Salir de la llamada.
🗣️ "$voz" — Probar la voz.
📚 "$ayuda" — Mostrar comandos.

━━━━━━━━━━━━━━

🎵 COMANDOS DE MÚSICA

▶️ "$musica" — Activar reproductor.
🔎 "$play <canción>" — Buscar y reproducir.
⏸️ "$pausa" — Pausar música.
▶️ "$reanudar" — Continuar reproducción.
⏭️ "$siguiente" — Siguiente canción.
⏹️ "$pararmusica" — Detener música.
🔊 "$volumen" — Consultar volumen.
🎚️ "$volumen <0-100>" — Ajustar volumen.
🧪 "$pruebamusica" — Prueba de audio.

━━━━━━━━━━━━━━

🎧 RBSN — Tu compañero de voz
🔊 Volumen inicial de música: 30/100`;

// GENERAR AUDIO CON SHARVARD
async function generarAudio(texto) {
  const espera = Math.max(
    0,
    ESPERA_TTS - (Date.now() - ultimaSolicitudTTS)
  );

  if (espera > 0) {
    await new Promise(resolve =>
      setTimeout(resolve, espera)
    );
  }

  ultimaSolicitudTTS = Date.now();

  const solicitud = await fetch(
    "https://api.tts.ai/v1/tts/",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model: "piper",
        voice: "es_ES-sharvard-medium",
        text: texto,
        language: "es",
        format: "wav"
      })
    }
  );

  const datos = await solicitud.json();

  if (!solicitud.ok || !datos.uuid) {
    throw new Error(
      "No se pudo generar la voz: " +
      JSON.stringify(datos)
    );
  }

  let resultado = null;

  for (let i = 0; i < 30; i++) {
    await new Promise(resolve =>
      setTimeout(resolve, 1000)
    );

    const consulta = await fetch(
      "https://api.tts.ai/v1/speech/results/?uuid=" +
      encodeURIComponent(datos.uuid)
    );

    resultado = await consulta.json();

    if (resultado.status === "completed") break;

    if (resultado.status === "failed") {
      throw new Error(
        "Falló la generación de voz."
      );
    }
  }

  if (
    !resultado ||
    resultado.status !== "completed" ||
    !resultado.result_url
  ) {
    throw new Error(
      "La generación de voz tardó demasiado."
    );
  }

  const descarga = await fetch(resultado.result_url);

  if (!descarga.ok) {
    throw new Error(
      "No se pudo descargar el audio."
    );
  }

  return Buffer.from(await descarga.arrayBuffer());
}

// CONVERTIR WAV A PCM
function convertirWav(audio) {
  if (
    audio.toString("ascii", 0, 4) !== "RIFF" ||
    audio.toString("ascii", 8, 12) !== "WAVE"
  ) {
    throw new Error(
      "El archivo recibido no es WAV."
    );
  }

  let posicion = 12;
  let formato = null;
  let datosAudio = null;

  while (posicion + 8 <= audio.length) {
    const nombre = audio.toString(
      "ascii",
      posicion,
      posicion + 4
    );

    const longitud = audio.readUInt32LE(
      posicion + 4
    );

    const inicio = posicion + 8;

    if (inicio + longitud > audio.length) {
      throw new Error("El WAV está incompleto.");
    }

    if (nombre === "fmt ") {
      if (longitud < 16) {
        throw new Error(
          "El encabezado WAV es inválido."
        );
      }

      formato = {
        tipo: audio.readUInt16LE(inicio),
        canales: audio.readUInt16LE(inicio + 2),
        frecuencia: audio.readUInt32LE(inicio + 4),
        bits: audio.readUInt16LE(inicio + 14)
      };
    }

    if (nombre === "data") {
      datosAudio = {
        inicio,
        longitud
      };
    }

    posicion = inicio + longitud + (longitud % 2);
  }

  if (!formato || !datosAudio) {
    throw new Error(
      "El WAV no contiene los datos necesarios."
    );
  }

  if (formato.tipo !== 1 || formato.bits !== 16) {
    throw new Error(
      "El WAV no está en PCM de 16 bits."
    );
  }

  if (!formato.frecuencia || !formato.canales) {
    throw new Error(
      "Frecuencia o canales WAV inválidos."
    );
  }

  const cantidad = Math.floor(
    datosAudio.longitud / 2
  );

  const muestras = new Int16Array(cantidad);

  for (let i = 0; i < cantidad; i++) {
    muestras[i] = audio.readInt16LE(
      datosAudio.inicio + i * 2
    );
  }

  return {
    muestras,
    frecuencia: formato.frecuencia,
    canales: formato.canales
  };
}

// CONVERTIR A MONO Y 48000 HZ
function prepararAudio(wav) {
  const muestrasMono = [];

  for (
    let i = 0;
    i + wav.canales <= wav.muestras.length;
    i += wav.canales
  ) {
    let suma = 0;

    for (
      let canal = 0;
      canal < wav.canales;
      canal++
    ) {
      suma += wav.muestras[i + canal];
    }

    muestrasMono.push(suma / wav.canales);
  }

  const proporcion =
    wav.frecuencia / FRECUENCIA_LIVEKIT;

  const cantidadNueva = Math.max(
    1,
    Math.floor(muestrasMono.length / proporcion)
  );

  const resultado = new Int16Array(cantidadNueva);

  for (let i = 0; i < cantidadNueva; i++) {
    const posicion = i * proporcion;

    const anterior = Math.min(
      Math.floor(posicion),
      muestrasMono.length - 1
    );

    const siguiente = Math.min(
      anterior + 1,
      muestrasMono.length - 1
    );

    const fraccion = posicion - anterior;

    const valor =
      muestrasMono[anterior] * (1 - fraccion) +
      muestrasMono[siguiente] * fraccion;

    resultado[i] = Math.max(
      -32768,
      Math.min(32767, Math.round(valor))
    );
  }

  return resultado;
}

// COLA ÚNICA PARA EVITAR MEZCLAR VOZ Y MÚSICA
function ejecutarEnColaReproduccion(funcion) {
  const tarea = colaReproduccion.then(funcion);

  colaReproduccion = tarea.catch(error => {
    console.error(
      "ERROR EN LA COLA DE AUDIO:",
      error
    );
  });

  return tarea;
}

// TRANSMITIR MUESTRAS POR LIVEKIT
async function transmitirMuestras(muestras, modo) {
  if (!livekitRoom || !audioSource) {
    return 0;
  }

  const { AudioFrame } =
    await import("@livekit/rtc-node");

  let procesadas = 0;

  for (
    let inicio = 0;
    inicio < muestras.length;
    inicio += MUESTRAS_BLOQUE
  ) {
    if (!livekitRoom || !audioSource) break;

    if (modo === "voz" && !leerChat) break;

    if (
      modo === "prueba" &&
      !pruebaMusicaActiva
    ) {
      break;
    }

    if (
      modo === "musica" &&
      (
        !reproductorActivo ||
        detenerMusica ||
        saltarMusica ||
        musicaPausada
      )
    ) {
      break;
    }

    const fragmento = muestras.subarray(
      inicio,
      Math.min(
        inicio + MUESTRAS_BLOQUE,
        muestras.length
      )
    );

    const bloque = new Int16Array(fragmento.length);

    bloque.set(fragmento);

    // CONTROL DE VOLUMEN MUSICAL
    if (modo === "musica") {
      const factor = volumenMusica / 100;

      for (let i = 0; i < bloque.length; i++) {
        bloque[i] = Math.max(
          -32768,
          Math.min(
            32767,
            Math.round(bloque[i] * factor)
          )
        );
      }
    }

    const frame = new AudioFrame(
      bloque,
      FRECUENCIA_LIVEKIT,
      CANALES_LIVEKIT,
      bloque.length
    );

    await audioSource.captureFrame(frame);

    procesadas += fragmento.length;
  }

  await audioSource.waitForPlayout();

  return procesadas;
}

// REPRODUCIR WAV
async function reproducirAudio(audio) {
  return ejecutarEnColaReproduccion(async () => {
    if (
      !livekitRoom ||
      !audioSource ||
      !leerChat
    ) {
      return;
    }

    const wav = convertirWav(audio);
    const muestras = prepararAudio(wav);

    await transmitirMuestras(muestras, "voz");
  });
}

// DECODIFICAR MP3 CON FFMPEG
function decodificarMP3(audio) {
  return new Promise((resolve, reject) => {
    if (!ffmpegPath) {
      return reject(
        new Error("FFmpeg no está disponible.")
      );
    }

    const proceso = spawn(ffmpegPath, [
      "-hide_banner",
      "-loglevel",
      "error",
      "-i",
      "pipe:0",
      "-f",
      "s16le",
      "-acodec",
      "pcm_s16le",
      "-ac",
      "1",
      "-ar",
      "48000",
      "pipe:1"
    ]);

    const partes = [];
    const errores = [];

    proceso.stdout.on("data", parte => {
      partes.push(parte);
    });

    proceso.stderr.on("data", parte => {
      errores.push(parte);
    });

    proceso.on("error", reject);

    proceso.on("close", codigo => {
      if (codigo !== 0) {
        return reject(
          new Error(
            "FFmpeg no pudo convertir el audio: " +
            Buffer.concat(errores).toString()
          )
        );
      }

      resolve(Buffer.concat(partes));
    });

    proceso.stdin.on("error", reject);

    proceso.stdin.end(audio);
  });
}

// CONVERTIR BUFFER PCM A MUESTRAS
function convertirPCM(buffer) {
  const cantidad = Math.floor(buffer.length / 2);

  const muestras = new Int16Array(cantidad);

  for (let i = 0; i < cantidad; i++) {
    muestras[i] = buffer.readInt16LE(i * 2);
  }

  return muestras;
}

// BUSCAR CANCIÓN DE PRUEBA
async function buscarCancionPrueba() {
  const url = new URL(
    "https://api.jamendo.com/v3.0/tracks/"
  );

  url.searchParams.set(
    "client_id",
    JAMENDO_CLIENT_ID
  );

  url.searchParams.set("format", "json");
  url.searchParams.set("limit", "1");

  url.searchParams.set(
    "id",
    JAMENDO_TRACK_ID
  );

  url.searchParams.set("audioformat", "mp32");

  url.searchParams.set(
    "include",
    "licenses"
  );

  console.log("Consultando API de Jamendo.");

  const respuesta = await fetch(url);
  const datos = await respuesta.json();

  if (
    !respuesta.ok ||
    !datos.results ||
    !datos.results.length
  ) {
    throw new Error(
      "Jamendo no encontró la canción: " +
      JSON.stringify(datos)
    );
  }

  const cancion = datos.results[0];

  if (!cancion.audio) {
    throw new Error(
      "Jamendo no proporcionó el audio."
    );
  }

  if (
    !cancion.license_ccurl ||
    !cancion.license_ccurl.includes("by-sa/3.0")
  ) {
    throw new Error(
      "La licencia de la canción no coincide con la esperada."
    );
  }

  console.log(
    "Canción encontrada:",
    cancion.name
  );

  return cancion;
}

// BUSCAR CANCIONES EN JAMENDO
async function buscarCancionesJamendo(termino) {
  const url = new URL(
    "https://api.jamendo.com/v3.0/tracks/"
  );

  url.searchParams.set(
    "client_id",
    JAMENDO_CLIENT_ID
  );

  url.searchParams.set("format", "json");
  url.searchParams.set("limit", "20");
  url.searchParams.set("search", termino);
  url.searchParams.set("audioformat", "mp32");
  url.searchParams.set("include", "licenses");
  url.searchParams.set("order", "popularity_total");

  console.log(
    "Buscando música en Jamendo:",
    termino
  );

  const respuesta = await fetch(url);
  const datos = await respuesta.json();

  if (!respuesta.ok) {
    throw new Error(
      "Jamendo respondió con un error: " +
      JSON.stringify(datos)
    );
  }

  if (!datos.results || !datos.results.length) {
    throw new Error(
      "No encontré canciones con ese nombre."
    );
  }

  const disponibles = datos.results.filter(cancion =>
    cancion.audio &&
    cancion.license_ccurl &&
    cancion.license_ccurl.includes("by-sa/3.0") &&
    cancion.audiodownload_allowed !== false
  );

  if (!disponibles.length) {
    throw new Error(
      "Encontré canciones, pero ninguna cumple los requisitos de licencia y descarga."
    );
  }

  return disponibles;
}

// AGREGAR CANCIÓN A LA COLA
function agregarCancionCola(cancion) {
  colaCanciones.push(cancion);

  console.log(
    "Canción agregada a la cola:",
    cancion.name
  );
}

// REPRODUCIR UNA CANCIÓN COMPLETA
async function reproducirCancion(cancion, threadId) {
  console.log(
    "Preparando canción:",
    cancion.name
  );

  responder(
    threadId,
    `🎵 RBSN MUSIC

▶️ Reproduciendo: ${cancion.name}
🎤 Artista: ${cancion.artist_name}
🎧 Fuente: Jamendo
📜 Licencia: CC BY-SA 3.0
🔗 https://www.jamendo.com/track/${cancion.id}`
  );

  const mp3 = await descargarAudioJamendo(
    cancion.audio
  );

  console.log("Decodificando canción.");

  const pcm = await decodificarMP3(mp3);

  const muestras = convertirPCM(pcm);

  if (!muestras.length) {
    throw new Error(
      "La canción no contiene audio."
    );
  }

  console.log(
    "Duración aproximada:",
    Math.round(muestras.length / FRECUENCIA_LIVEKIT),
    "segundos"
  );

  let posicion = 0;

  // TRANSMISIÓN POR FRAGMENTOS
  // PERMITE INTERCALAR LA LECTURA DEL CHAT
  while (posicion < muestras.length) {
    if (
      !reproductorActivo ||
      detenerMusica ||
      saltarMusica ||
      !livekitRoom ||
      !audioSource
    ) {
      break;
    }

    // PAUSA
    if (musicaPausada) {
      await new Promise(resolve =>
        setTimeout(resolve, 200)
      );

      continue;
    }

    const fragmento = muestras.subarray(
      posicion,
      Math.min(
        posicion + FRECUENCIA_LIVEKIT,
        muestras.length
      )
    );

    const procesadas = await ejecutarEnColaReproduccion(
      async () => {
        return transmitirMuestras(
          fragmento,
          "musica"
        );
      }
    );

    if (procesadas > 0) {
      posicion += procesadas;
    }

    if (
      detenerMusica ||
      saltarMusica ||
      !reproductorActivo
    ) {
      break;
    }

    if (procesadas === 0) {
      await new Promise(resolve =>
        setTimeout(resolve, 100)
      );
    }
  }

  if (saltarMusica) {
    console.log("Canción saltada.");
  } else if (detenerMusica) {
    console.log("Reproducción detenida.");
  } else if (posicion >= muestras.length) {
    console.log("Canción finalizada.");
  }
}

// PROCESAR COLA MUSICAL
async function procesarColaMusica() {
  if (trabajadorMusicaActivo) return;

  trabajadorMusicaActivo = true;

  try {
    while (
      reproductorActivo &&
      colaCanciones.length > 0
    ) {
      if (!livekitRoom || !audioSource) {
        throw new Error(
          "Rbsn perdió la conexión con la llamada."
        );
      }

      const cancion = colaCanciones.shift();

      canalMusica = cancion;

      saltarMusica = false;
      detenerMusica = false;
      reproduciendoMusica = true;

      try {
        await reproducirCancion(
          cancion,
          CHAT_ID
        );

      } catch (error) {
        console.error(
          "ERROR REPRODUCIENDO CANCIÓN:",
          error
        );

        responder(
          CHAT_ID,
          `🔴 RBSN MUSIC

No pude reproducir:
${cancion.name}

Motivo: ${error.message}`
        );
      }

      reproduciendoMusica = false;
      canalMusica = null;

      if (detenerMusica) break;

      if (colaCanciones.length > 0) {
        responder(
          CHAT_ID,
          "⏭️ RBSN MUSIC — Preparando la siguiente canción."
        );
      }
    }

  } catch (error) {
    console.error(
      "ERROR EN EL REPRODUCTOR:",
      error
    );

    responder(
      CHAT_ID,
      "🔴 RBSN MUSIC — " + error.message
    );

  } finally {
    reproduciendoMusica = false;
    trabajadorMusicaActivo = false;
    canalMusica = null;

    if (detenerMusica) {
      colaCanciones = [];
    }

    if (
      reproductorActivo &&
      colaCanciones.length > 0
    ) {
      procesarColaMusica();
    }
  }
}

// ACTIVAR REPRODUCTOR
function activarMusica(threadId) {
  if (!livekitRoom || !audioTrack) {
    responder(
      threadId,
      'Primero escribí "$entrar-sala" para conectar a Rbsn.'
    );

    return;
  }

  reproductorActivo = true;
  detenerMusica = false;

  responder(
    threadId,
    `🎵 RBSN MUSIC

🟢 Reproductor activado.
🔊 Volumen: ${volumenMusica}/100

Usá "$play canción" para buscar música.`
  );

  if (colaCanciones.length > 0) {
    procesarColaMusica();
  }
}

// BUSCAR Y REPRODUCIR
async function buscarYReproducir(termino, threadId) {
  if (!livekitRoom || !audioTrack) {
    responder(
      threadId,
      'Primero escribí "$entrar-sala" para conectar a Rbsn.'
    );

    return;
  }

  if (!termino || termino.length < 2) {
    responder(
      threadId,
      'Escribí el nombre de una canción. Ejemplo: "$play lofi".'
    );

    return;
  }

  try {
    reproductorActivo = true;
    detenerMusica = false;

    responder(
      threadId,
      `🔎 RBSN MUSIC

Buscando "${termino}" en Jamendo...`
    );

    const canciones = await buscarCancionesJamendo(
      termino
    );

    const cancion = canciones[0];

    agregarCancionCola(cancion);

    responder(
      threadId,
      `✅ RBSN MUSIC

Encontré una canción:

🎵 ${cancion.name}
🎤 ${cancion.artist_name}

📥 Agregada a la cola.
🎶 Posición: ${colaCanciones.length + (reproduciendoMusica ? 1 : 0)}`
    );

    procesarColaMusica();

  } catch (error) {
    console.error(
      "ERROR BUSCANDO MÚSICA:",
      error
    );

    responder(
      threadId,
      `🔴 RBSN MUSIC

No pude buscar esa canción.

Motivo: ${error.message}`
    );
  }
}

// PAUSAR
function pausarMusica(threadId) {
  if (!reproduciendoMusica) {
    responder(
      threadId,
      "⏸️ No hay ninguna canción reproduciéndose."
    );

    return;
  }

  musicaPausada = true;

  responder(
    threadId,
    "⏸️ RBSN MUSIC — Música pausada."
  );
}

// REANUDAR
function reanudarMusica(threadId) {
  if (!reproductorActivo || !reproduciendoMusica) {
    responder(
      threadId,
      "▶️ No hay ninguna canción pausada."
    );

    return;
  }

  if (!musicaPausada) {
    responder(
      threadId,
      "🎵 La música ya está reproduciéndose."
    );

    return;
  }

  musicaPausada = false;

  responder(
    threadId,
    "▶️ RBSN MUSIC — Música reanudada."
  );
}

// SIGUIENTE CANCIÓN
function siguienteMusica(threadId) {
  if (!reproduciendoMusica) {
    responder(
      threadId,
      "⏭️ No hay ninguna canción reproduciéndose."
    );

    return;
  }

  saltarMusica = true;
  musicaPausada = false;

  responder(
    threadId,
    "⏭️ RBSN MUSIC — Pasando a la siguiente canción."
  );
}

// DETENER REPRODUCTOR
function detenerReproductor(threadId) {
  reproductorActivo = false;
  musicaPausada = false;
  detenerMusica = true;
  saltarMusica = false;

  colaCanciones = [];

  if (
    audioSource &&
    typeof audioSource.clearQueue === "function"
  ) {
    audioSource.clearQueue();
  }

  responder(
    threadId,
    `⏹️ RBSN MUSIC

Reproductor detenido.
🗑️ Cola de canciones vaciada.`
  );
}

// CONTROLAR VOLUMEN
function controlarVolumen(valor, threadId) {
  if (valor === undefined || valor === "") {
    responder(
      threadId,
      `🔊 RBSN MUSIC

Volumen actual: ${volumenMusica}/100`
    );

    return;
  }

  const numero = Number(valor);

  if (
    !Number.isInteger(numero) ||
    numero < 0 ||
    numero > 100
  ) {
    responder(
      threadId,
      "⚠️ El volumen debe ser un número entero entre 0 y 100."
    );

    return;
  }

  volumenMusica = numero;

  responder(
    threadId,
    `🔊 RBSN MUSIC

Volumen ajustado a ${volumenMusica}/100.`
  );
}

// PRUEBA MUSICAL
async function probarMusica(threadId) {
  if (
    !livekitRoom ||
    !audioTrack ||
    !audioSource
  ) {
    throw new Error(
      'Primero conectá a Rbsn con "$entrar-sala".'
    );
  }

  if (pruebaMusicaActiva) {
    throw new Error(
      "Ya hay una prueba musical en marcha."
    );
  }

  pruebaMusicaActiva = true;

  try {
    console.log(
      "Buscando canción de prueba en Jamendo."
    );

    const cancion = await buscarCancionPrueba();

    responder(
      threadId,
      `🎵 RBSN MUSIC

▶️ Prueba de reproducción: ${cancion.name}
🎤 Artista: ${cancion.artist_name}
🎧 Fuente: Jamendo
📜 Licencia: CC BY-SA 3.0
🔗 https://www.jamendo.com/track/${cancion.id}

🔊 Reproduciendo una prueba de 15 segundos...`
    );

    const mp3 = await descargarAudioJamendo(
      cancion.audio
    );

    const pcm = await decodificarMP3(mp3);

    const muestras = convertirPCM(pcm);

    const duracionPrueba =
      15 * FRECUENCIA_LIVEKIT;

    const fragmento = muestras.subarray(
      0,
      Math.min(
        duracionPrueba,
        muestras.length
      )
    );

    if (!fragmento.length) {
      throw new Error(
        "No se pudieron obtener muestras de audio."
      );
    }

    await ejecutarEnColaReproduccion(async () => {
      await transmitirMuestras(
        fragmento,
        "prueba"
      );
    });

    responder(
      threadId,
      `✅ RBSN MUSIC

La prueba de música terminó.

🎵 ${cancion.name}
🎤 ${cancion.artist_name}`
    );

  } finally {
    pruebaMusicaActiva = false;
  }
}

// DIVIDIR TEXTOS LARGOS
function dividirTexto(texto) {
  const partes = [];
  let restante = texto;

  while (restante.length > 450) {
    let corte = restante.lastIndexOf(" ", 450);

    if (corte < 100) corte = 450;

    partes.push(
      restante.slice(0, corte).trim()
    );

    restante = restante.slice(corte).trim();
  }

  if (restante) {
    partes.push(restante);
  }

  return partes;
}

// COLA DE LECTURA
function agregarLectura(texto) {
  colaLectura = colaLectura.then(async () => {
    if (
      !leerChat ||
      !livekitRoom ||
      !audioSource
    ) {
      return;
    }

    console.log("Rbsn va a leer:", texto);

    for (const parte of dividirTexto(texto)) {
      if (!leerChat || !livekitRoom) break;

      const audio = await generarAudio(parte);

      await reproducirAudio(audio);
    }
  }).catch(error => {
    console.error(
      "ERROR AL LEER MENSAJE:",
      error
    );
  });
}

// ENTRAR A LA SALA
async function entrarSala() {
  if (livekitRoom) {
    return "Rbsn ya está conectado a la llamada.";
  }

  if (conectandoSala) {
    return "Rbsn ya está intentando entrar a la llamada. Esperá unos segundos.";
  }

  conectandoSala = true;

  let room = null;
  let track = null;

  try {
    const respuesta = await fetch(
      `https://api.anarchyapps.org/bot-api/v1/live-rooms/${CHAT_ID}/join`,
      {
        method: "POST",
        headers: {
          "Authorization":
            "Bot " + process.env.COMMUNITY_BOT_TOKEN
        }
      }
    );

    const datos = await respuesta.json();

    if (
      !respuesta.ok ||
      !datos.url ||
      !datos.token
    ) {
      throw new Error(JSON.stringify(datos));
    }

    const livekit =
      await import("@livekit/rtc-node");

    room = new livekit.Room();

    await room.connect(
      datos.url,
      datos.token,
      {
        autoSubscribe: false,
        dynacast: true
      }
    );

    const source = new livekit.AudioSource(
      FRECUENCIA_LIVEKIT,
      CANALES_LIVEKIT
    );

    track = livekit.LocalAudioTrack.createAudioTrack(
      "Rbsn-Microfono",
      source
    );

    const opciones =
      new livekit.TrackPublishOptions();

    opciones.source =
      livekit.TrackSource.SOURCE_MICROPHONE;

    let publicado = false;
    let ultimoError = null;

    for (
      let intento = 1;
      intento <= 12;
      intento++
    ) {
      try {
        await room.localParticipant.publishTrack(
          track,
          opciones
        );

        publicado = true;
        break;

      } catch (error) {
        ultimoError = error;

        console.log(
          "Esperando permiso:",
          intento
        );

        await new Promise(resolve =>
          setTimeout(resolve, 1000)
        );
      }
    }

    if (!publicado) {
      throw new Error(
        ultimoError?.message ||
        "No se pudo publicar el micrófono."
      );
    }

    livekitRoom = room;
    audioSource = source;
    audioTrack = track;
    salaActiva = CHAT_ID;

    console.log(
      "RBSN CONECTADO Y MICRÓFONO PUBLICADO."
    );

    return "Rbsn entró a la llamada y publicó su micrófono.";

  } catch (error) {
    console.error(
      "ERROR AL ENTRAR:",
      error
    );

    if (track) {
      try {
        await track.close();
      } catch (_) {}
    }

    if (room) {
      try {
        await room.disconnect();
      } catch (_) {}
    }

    try {
      await fetch(
        `https://api.anarchyapps.org/bot-api/v1/live-rooms/${CHAT_ID}/leave`,
        {
          method: "POST",
          headers: {
            "Authorization":
              "Bot " + process.env.COMMUNITY_BOT_TOKEN
          }
        }
      );
    } catch (_) {}

    throw error;

  } finally {
    conectandoSala = false;
  }
}

// SALIR DE LA SALA
async function salirSala() {
  leerChat = false;
  pruebaMusicaActiva = false;

  reproductorActivo = false;
  musicaPausada = false;
  detenerMusica = true;
  saltarMusica = false;
  colaCanciones = [];

  if (audioSource) {
    if (
      typeof audioSource.clearQueue === "function"
    ) {
      audioSource.clearQueue();
    }
  }

  if (audioTrack) {
    try {
      await audioTrack.close();
    } catch (error) {
      console.error(error);
    }

    audioTrack = null;
  }

  if (livekitRoom) {
    try {
      await livekitRoom.disconnect();
    } catch (error) {
      console.error(error);
    }

    livekitRoom = null;
  }

  audioSource = null;

  if (salaActiva) {
    try {
      await fetch(
        `https://api.anarchyapps.org/bot-api/v1/live-rooms/${salaActiva}/leave`,
        {
          method: "POST",
          headers: {
            "Authorization":
              "Bot " + process.env.COMMUNITY_BOT_TOKEN
          }
        }
      );
    } catch (error) {
      console.error(error);
    }
  }

  salaActiva = null;

  return "Rbsn salió de la llamada y la lectura quedó desactivada.";
}

// CONSULTAR ESTADO
function obtenerEstado() {
  if (
    leerChat &&
    livekitRoom &&
    audioTrack
  ) {
    return `📊 RBSN — ESTADO DEL CHAT

🟢 Lectura: ACTIVADA.
🎙️ Micrófono: CONECTADO.
✅ Estado: LISTO Y PREPARADO PARA LEER.`;
  }

  if (leerChat && !livekitRoom) {
    return `📊 RBSN — ESTADO DEL CHAT

🟡 Lectura: ACTIVADA.
🎙️ Micrófono: DESCONECTADO.
⚠️ Estado: NO ESTOY LISTO PARA LEER.
Usá "$entrar-sala" para entrar a la llamada.`;
  }

  if (livekitRoom && audioTrack) {
    return `📊 RBSN — ESTADO DEL CHAT

🔇 Lectura: DESACTIVADA.
🎙️ Micrófono: CONECTADO.
⏸️ Estado: CONECTADO, PERO SIN LEER.`;
  }

  return `📊 RBSN — ESTADO DEL CHAT

🔇 Lectura: DESACTIVADA.
🎙️ Micrófono: DESCONECTADO.
⚪ Estado: NO CONECTADO A UNA LLAMADA.`;
}

// PRUEBA DE VOZ POR HTTP
app.get("/prueba-voz", async (req, res) => {
  try {
    const audio = await generarAudio(
      "Hola, soy Rbsn. Estoy listo para leer los mensajes."
    );

    res.set("Content-Type", "audio/wav");

    res.send(audio);

  } catch (error) {
    console.error(error);

    res.status(500).send(error.message);
  }
});

// CONSULTAR SALAS
app.get("/probar-salas", async (req, res) => {
  try {
    const respuesta = await fetch(
      "https://api.anarchyapps.org/bot-api/v1/live-rooms",
      {
        headers: {
          "Authorization":
            "Bot " + process.env.COMMUNITY_BOT_TOKEN
        }
      }
    );

    const datos = await respuesta.json();

    res.status(respuesta.status).json(datos);

  } catch (error) {
    res.status(500).json({
      error: error.message
    });
  }
});

// ENTRAR DESDE RENDER
app.get("/entrar-sala", async (req, res) => {
  try {
    const resultado = await entrarSala();

    res.send(resultado);

  } catch (error) {
    res.status(500).send(
      "Error: " + error.message
    );
  }
});

// ACTIVAR LECTURA DESDE RENDER
app.get("/activar-lectura", (req, res) => {
  if (!livekitRoom || !audioTrack) {
    return res.status(400).send(
      'Primero usá "$entrar-sala" para conectar a Rbsn.'
    );
  }

  leerChat = true;

  res.send(
    "Lectura activada. Rbsn está listo."
  );
});

// DESACTIVAR LECTURA DESDE RENDER
app.get("/desactivar-lectura", (req, res) => {
  leerChat = false;

  if (
    audioSource &&
    typeof audioSource.clearQueue === "function"
  ) {
    audioSource.clearQueue();
  }

  res.send("Lectura desactivada.");
});

// SALIR DESDE RENDER
app.get("/salir-sala", async (req, res) => {
  try {
    const resultado = await salirSala();

    res.send(resultado);

  } catch (error) {
    res.status(500).send(
      "Error: " + error.message
    );
  }
});

// RECIBIR MENSAJES DE ANARCHY
app.post("/webhook", (req, res) => {
  const evento = req.body;

  if (
    evento.type !== "message" ||
    !evento.message
  ) {
    return res.sendStatus(200);
  }

  const mensaje = evento.message;
  const texto = mensaje.content?.trim();

  if (!texto) {
    return res.sendStatus(200);
  }

  const id = mensaje.messageId;

  if (mensajesProcesados.has(id)) {
    return res.sendStatus(200);
  }

  mensajesProcesados.add(id);

  if (mensajesProcesados.size > 1000) {
    mensajesProcesados.clear();
  }

  const comando = texto.toLowerCase();

  const threadId =
    evento.threadId || CHAT_ID;

  // AYUDA
  if (comando === "$ayuda") {
    responder(
      threadId,
      MENSAJE_AYUDA
    );

    return res.sendStatus(200);
  }

  // PRUEBA MUSICAL
  if (comando === "$pruebamusica") {
    probarMusica(threadId).catch(error => {
      console.error(
        "ERROR EN PRUEBA MUSICAL:",
        error
      );

      pruebaMusicaActiva = false;

      responder(
        threadId,
        `🔴 RBSN MUSIC — ERROR

No pude reproducir la prueba musical.
Motivo: ${error.message}`
      );
    });

    return res.sendStatus(200);
  }

  // ACTIVAR REPRODUCTOR
  if (comando === "$musica") {
    activarMusica(threadId);

    return res.sendStatus(200);
  }

  // BUSCAR Y REPRODUCIR
  if (comando.startsWith("$play ")) {
    const termino = texto.slice(6).trim();

    buscarYReproducir(
      termino,
      threadId
    );

    return res.sendStatus(200);
  }

  if (comando === "$play") {
    responder(
      threadId,
      'Usá "$play nombre de canción".'
    );

    return res.sendStatus(200);
  }

  // PAUSAR
  if (comando === "$pausa") {
    pausarMusica(threadId);

    return res.sendStatus(200);
  }

  // REANUDAR
  if (comando === "$reanudar") {
    reanudarMusica(threadId);

    return res.sendStatus(200);
  }

  // SIGUIENTE
  if (comando === "$siguiente") {
    siguienteMusica(threadId);

    return res.sendStatus(200);
  }

  // DETENER
  if (comando === "$pararmusica") {
    detenerReproductor(threadId);

    return res.sendStatus(200);
  }

  // VOLUMEN
  if (comando === "$volumen") {
    controlarVolumen(undefined, threadId);

    return res.sendStatus(200);
  }

  if (comando.startsWith("$volumen ")) {
    const valor = texto.slice(9).trim();

    controlarVolumen(
      valor,
      threadId
    );

    return res.sendStatus(200);
  }

  // ENTRAR A LA LLAMADA
  if (comando === "$entrar-sala") {
    entrarSala()
      .then(resultado => {
        responder(
          threadId,
          `🎙️ RBSN — CONEXIÓN

🟢 ${resultado}
🔇 Lectura: DESACTIVADA.

Para comenzar a leer, escribí "$leerchat".`
        );
      })
      .catch(error => {
        responder(
          threadId,
          `🔴 RBSN — ERROR DE CONEXIÓN

No pude entrar a la llamada.
Motivo: ${error.message}`
        );
      });

    return res.sendStatus(200);
  }

  // ACTIVAR LECTURA
  if (comando === "$leerchat") {
    if (!livekitRoom || !audioTrack) {
      responder(
        threadId,
        `🟡 RBSN — NO ESTOY LISTO

🔇 Lectura: DESACTIVADA.
🎙️ Micrófono: DESCONECTADO.

Primero escribí "$entrar-sala" para que pueda entrar a la llamada.`
      );

      return res.sendStatus(200);
    }

    leerChat = true;

    responder(
      threadId,
      `🟢 RBSN — LECTURA ACTIVADA

🔊 Lectura del chat: ACTIVADA.
🎙️ Micrófono: CONECTADO.
✅ Estado: LISTO Y PREPARADO PARA LEER.`
    );

    console.log("Lectura activada.");

    return res.sendStatus(200);
  }

  // DESACTIVAR LECTURA
  if (comando === "$noleerchat") {
    leerChat = false;

    if (
      audioSource &&
      typeof audioSource.clearQueue === "function"
    ) {
      audioSource.clearQueue();
    }

    responder(
      threadId,
      `🔇 RBSN — LECTURA DESACTIVADA

La lectura de mensajes está desactivada.
🎙️ Conexión a la llamada: ${
        livekitRoom
          ? "CONECTADA."
          : "DESCONECTADA."
      }`
    );

    console.log("Lectura desactivada.");

    return res.sendStatus(200);
  }

  // CONSULTAR ESTADO
  if (comando === "$estadochat") {
    responder(
      threadId,
      obtenerEstado()
    );

    return res.sendStatus(200);
  }

  // PROBAR VOZ
  if (comando === "$voz") {
    if (!livekitRoom || !audioTrack) {
      responder(
        threadId,
        'Primero escribí "$entrar-sala" para conectar a Rbsn.'
      );

      return res.sendStatus(200);
    }

    if (!leerChat) {
      responder(
        threadId,
        'Activá primero la lectura con "$leerchat" para probar la voz.'
      );

      return res.sendStatus(200);
    }

    agregarLectura(
      "Hola, soy Rbsn. Mi voz está funcionando correctamente."
    );

    return res.sendStatus(200);
  }

  // SALIR DE LA LLAMADA
  if (comando === "$salir") {
    salirSala()
      .then(resultado => {
        responder(
          threadId,
          `🚪 RBSN — DESCONEXIÓN

${resultado}`
        );
      })
      .catch(error => {
        responder(
          threadId,
          "No pude salir correctamente: " +
          error.message
        );
      });

    return res.sendStatus(200);
  }

  // IGNORAR OTROS COMANDOS
  if (texto.startsWith("$")) {
    return res.sendStatus(200);
  }

  // LEER MENSAJES
  if (
    leerChat &&
    livekitRoom &&
    audioSource
  ) {
    agregarLectura(texto);
  }

  res.sendStatus(200);
});

// INICIAR SERVIDOR
const PORT = process.env.PORT || 3000;

app.listen(PORT, () => {
  console.log(
    `Rbsn iniciado en el puerto ${PORT}`
  );
});
