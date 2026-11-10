const express = require('express');
const crypto = require('crypto');
const { spawn } = require('child_process');
const {
  Room, RoomEvent, AudioSource, AudioFrame,
  LocalAudioTrack, TrackPublishOptions, TrackSource
} = require('@livekit/rtc-node');

// ───────────── Configuración ─────────────
const BOT_TOKEN = (process.env.BOT_TOKEN || '').trim();        // token del Bot de la comunidad (ACM)
const SECRET = (process.env.WEBHOOK_SECRET || '').trim();      // secreto del webhook
const PORT = process.env.PORT || 3000;
const PREFIX = process.env.PREFIX || '!';           // 1 a 3 signos de: ! $ % & * + - = ~ > ;


if (!BOT_TOKEN) console.warn('[AVISO] Falta BOT_TOKEN.');
if (!SECRET) console.warn('[AVISO] Falta WEBHOOK_SECRET: se rechazarán todos los eventos.');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log(new Date().toISOString(), ...a);

// ───────────── Cliente de la API ─────────────
async function api(path, method = 'GET', body = null, reintento = true) {
  try {
    const res = await fetch(`${API}${path}`, {
      method,
      headers: {
        Authorization: `Bot ${BOT_TOKEN}`,
        ...(body ? { 'Content-Type': 'application/json' } : {})
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(10000)
    });
    if (res.status === 429 && reintento) {
      const espera = Math.min(parseInt(res.headers.get('retry-after') || '2', 10), 30);
      await sleep(espera * 1000);
      return api(path, method, body, false);
    }
    const text = await res.text();
    let data;
    try { data = JSON.parse(text); } catch { data = { raw: text }; }
    return { ok: res.ok, status: res.status, data };
  } catch (err) {
    log(`[API ERROR] ${method} ${path}:`, err.message);
    return { ok: false, status: 0, data: null };
  }
}

// Mensajes: cola por chat (límite: 1 cada 2 s por chat, máx. 2000 caracteres)
const cadenas = new Map();
function say(threadId, content, buttons = null, replyMessageId = null) {
  const previa = cadenas.get(threadId) || Promise.resolve();
  const siguiente = previa.then(async () => {
    const body = { content: String(content).slice(0, 2000) };
    if (buttons) body.buttons = buttons;
    if (replyMessageId) body.replyMessageId = String(replyMessageId);
    const r = await api(`/threads/${threadId}/messages`, 'POST', body);
    if (!r.ok) log(`[MSG ERROR] ${r.status}`, JSON.stringify(r.data));
    await sleep(2100);
  }).catch(() => {});
  cadenas.set(threadId, siguiente);
  return siguiente;
}

function setStatus(texto) {
  api('/me', 'PATCH', { status: texto ? String(texto).slice(0, 40) : null });
}

// ───────────── Búsqueda de pistas (yt-dlp) ─────────────
function resolverPista(consulta) {
  return new Promise((resolve, reject) => {
    const objetivo = /^https?:\/\//i.test(consulta) ? consulta : `ytsearch1:${consulta}`;
    const p = spawn('yt-dlp', [
      '--no-playlist', '--playlist-items', '1', '-q', '--no-warnings',
      '--print', '%(title)s', '--print', '%(webpage_url)s', '--print', '%(duration_string)s',
      objetivo
    ], { stdio: ['ignore', 'pipe', 'ignore'] });
    let out = '';
    const timer = setTimeout(() => { p.kill('SIGKILL'); reject(new Error('Tiempo agotado buscando la canción.')); }, 25000);
    p.stdout.on('data', (d) => (out += d));
    p.on('error', () => { clearTimeout(timer); reject(new Error('yt-dlp no está instalado.')); });
    p.on('close', () => {
      clearTimeout(timer);
      const [titulo, url, duracion] = out.split('\n').map((l) => l.trim());
      if (!titulo || !url) return reject(new Error('No encontré esa canción.'));
      resolve({ titulo, url, duracion: duracion && duracion !== 'NA' ? duracion : '?' });
    });
  });
}

// ───────────── Sesiones de voz ─────────────
const sesiones = new Map(); // threadId -> sesión

async function entrarSala(threadId) {
  if (sesiones.has(threadId)) return sesiones.get(threadId);
  if (sesiones.size >= MAX_SALAS) throw new Error(`Ya estoy en ${MAX_SALAS} salas (máximo).`);

  const r = await api(`/live-rooms/${threadId}/join`, 'POST');
  if (!r.ok || !r.data?.url || !r.data?.token) {
    const motivo = r.status === 404 || r.status === 403
      ? 'esta sala no está en vivo o no pertenece a mi comunidad'
      : `error ${r.status}`;
    throw new Error(`No pude entrar: ${motivo}.`);
  }

  const room = new Room();
  await room.connect(r.data.url, r.data.token, { autoSubscribe: false, dynacast: false });

  const source = new AudioSource(SAMPLE_RATE, 1);
  const track = LocalAudioTrack.createAudioTrack('musica', source);
  const opts = new TrackPublishOptions();
  opts.source = TrackSource.SOURCE_MICROPHONE;

  // Anarchy habilita el micrófono unos segundos después de entrar: reintentar
  let publicado = false;
  for (let i = 0; i < 20 && !publicado; i++) {
    try {
      await room.localParticipant.publishTrack(track, opts);
      publicado = true;
    } catch {
      await sleep(1500);
    }
  }
  if (!publicado) {
    try { await room.disconnect(); } catch {}
    await api(`/live-rooms/${threadId}/leave`, 'POST');
    throw new Error('Entré, pero no me dieron permiso para hablar en la sala.');
  }

  const s = {
    threadId, room, source, track,
    cola: [], actual: null, pausado: false, abortar: false,
    volumen: 80, reproduciendo: false, procs: []
  };
  room.on(RoomEvent.Disconnected, () => limpiar(threadId));
  sesiones.set(threadId, s);
  setStatus('En pausa');
  log(`[VOZ] Entré a la sala ${threadId}`);
  return s;
}

function matarProcesos(s) {
  for (const p of s.procs) { try { p.kill('SIGKILL'); } catch {} }
  s.procs = [];
}

function limpiar(threadId) {
  const s = sesiones.get(threadId);
  if (!s) return;
  s.cola = [];
  s.abortar = true;
  s.cerrada = true;
  matarProcesos(s);
  sesiones.delete(threadId);
  if (sesiones.size === 0) setStatus(null);
}

async function salirSala(threadId) {
  const s = sesiones.get(threadId);
  if (!s) return false;
  limpiar(threadId);
  try { await s.room.disconnect(); } catch {}
  await api(`/live-rooms/${threadId}/leave`, 'POST');
  log(`[VOZ] Salí de la sala ${threadId}`);
  return true;
}

// Decodifica con yt-dlp + ffmpeg y envía el PCM a LiveKit
async function transmitir(s, pista) {
  const yt = spawn('yt-dlp', ['-f', 'bestaudio/best', '--no-playlist', '-q', '-o', '-', pista.url],
    { stdio: ['ignore', 'pipe', 'ignore'] });
  const ff = spawn('ffmpeg', ['-loglevel', 'quiet', '-i', 'pipe:0', '-f', 's16le', '-ar', String(SAMPLE_RATE), '-ac', '1', 'pipe:1'],
    { stdio: ['pipe', 'pipe', 'ignore'] });
  s.procs = [yt, ff];
  yt.on('error', (e) => log('[yt-dlp]', e.message));
  ff.on('error', (e) => log('[ffmpeg]', e.message));
  yt.stdout.on('error', () => {});
  ff.stdin.on('error', () => {});
  yt.stdout.pipe(ff.stdin);

  let buf = Buffer.alloc(0);
  let t0 = Date.now();
  let n = 0;
  try {
    for await (const chunk of ff.stdout) {
      if (s.abortar) break;
      buf = Buffer.concat([buf, chunk]);
      while (buf.length >= FRAME_BYTES && !s.abortar) {
        while (s.pausado && !s.abortar) { await sleep(100); t0 = Date.now(); n = 0; }
        if (s.abortar) break;

        const samples = new Int16Array(FRAME_SAMPLES);
        const ganancia = s.volumen / 100;
        for (let i = 0; i < FRAME_SAMPLES; i++) {
          const v = Math.round(buf.readInt16LE(i * 2) * ganancia);
          samples[i] = v > 32767 ? 32767 : v < -32768 ? -32768 : v;
        }
        buf = buf.subarray(FRAME_BYTES);
        await s.source.captureFrame(new AudioFrame(samples, SAMPLE_RATE, 1, FRAME_SAMPLES));

        // No adelantarse más de ~400 ms al tiempo real
        n++;
        const adelanto = n * 10 - (Date.now() - t0);
        if (adelanto > 400) await sleep(adelanto - 300);
      }
    }
    if (!s.abortar) { try { await s.source.waitForPlayout(); } catch {} }
  } finally {
    matarProcesos(s);
  }
}

async function bucleReproduccion(s) {
  if (s.reproduciendo) return;
  s.reproduciendo = true;
  try {
    while (s.cola.length && !s.cerrada) {
      s.actual = s.cola.shift();
      s.abortar = false;
      s.pausado = false;
      setStatus(`Sonando: ${s.actual.titulo}`);
      say(s.threadId, `🎵 **Reproduciendo:** ${s.actual.titulo} (${s.actual.duracion})`, [
        { id: 'pause', label: '⏸ Pausa' },
        { id: 'resume', label: '▶ Reanudar' },
        { id: 'next', label: '⏭ Siguiente' },
        { id: 'stop', label: '⏹ Parar' }
      ]);
      try {
        await transmitir(s, s.actual);
      } catch (err) {
        log('[REPRODUCCIÓN ERROR]', err.message);
        say(s.threadId, `⚠️ Error reproduciendo **${s.actual.titulo}**.`);
      }
    }
  } finally {
    s.reproduciendo = false;
    s.actual = null;
    if (!s.cerrada) setStatus('En pausa');
  }
}

// ───────────── Comandos ─────────────
function ayuda() {
  const p = PREFIX;
  return '🤖 **COMANDOS**\n' +
    `\`${p}entrar\` — Entrar a la sala de este chat\n` +
    `\`${p}salir\` — Salir de la sala\n` +
    `\`${p}play <canción o link>\` — Reproducir / añadir a la cola\n` +
    `\`${p}pausa\` · \`${p}reanudar\` · \`${p}siguiente\` · \`${p}parar\`\n` +
    `\`${p}cola\` — Ver la cola\n` +
    `\`${p}ahora\` — Canción actual\n` +
    `\`${p}volumen <0-100>\` — Ajustar volumen\n` +
    `\`${p}ping\` · \`${p}info\``;
}

function accion(threadId, nombre) {
  const s = sesiones.get(threadId);
  if (!s) return '⚠️ No estoy en la sala. Usa `' + PREFIX + 'entrar`.';
  switch (nombre) {
    case 'pausa':
      if (!s.actual) return '⚠️ No hay nada reproduciéndose.';
      s.pausado = true; setStatus('En pausa');
      return '⏸️ Música pausada.';
    case 'reanudar':
      if (!s.actual) return '⚠️ No hay ninguna pista para reanudar.';
      s.pausado = false; setStatus(`Sonando: ${s.actual.titulo}`);
      return `▶️ Reanudando **${s.actual.titulo}**.`;
    case 'siguiente':
      if (!s.actual) return '⚠️ No hay nada reproduciéndose.';
      s.abortar = true; matarProcesos(s);
      return '⏭️ Saltando a la siguiente...';
    case 'parar':
      s.cola = []; s.abortar = true; matarProcesos(s);
      return '⏹️ Reproductor detenido y cola borrada.';
  }
  return null;
}

async function manejarComando(evento) {
  const { threadId } = evento;
  const msg = evento.message || {};
  const contenido = (msg.content || '').trim();
  if (!threadId || !contenido.startsWith(PREFIX)) return;
  const resto = contenido.slice(PREFIX.length);
  if (!/^[a-zA-ZáéíóúñÁÉÍÓÚÑ]/.test(resto)) return;

  const args = resto.split(/\s+/);
  const cmd = args[0].toLowerCase();
  const rid = msg.messageId;
  const responder = (t, b) => say(threadId, t, b, rid);
  log(`[CMD] ${cmd} | ${msg.author?.nickname || '?'} | ${threadId}`);

  switch (cmd) {
    case 'ayuda': case 'help':
      return responder(ayuda());

    case 'ping': {
      const seg = Math.floor((Date.now() - startTime) / 1000);
      return responder(`🏓 **Pong!** Activo hace ${seg} s. Salas: ${sesiones.size}/${MAX_SALAS}.`);
    }

    case 'info':
      return responder('🤖 **Bot de música** · Node.js + LiveKit · Webhook con HMAC-SHA256.');

    case 'entrar': case 'join':
      try {
        await entrarSala(threadId);
        return responder('🎙️ Estoy en la sala. Usa `' + PREFIX + 'play <canción>`.');
      } catch (e) { return responder(`⚠️ ${e.message}`); }

    case 'salir': case 'leave':
      return responder((await salirSala(threadId)) ? '🚪 Salí de la sala.' : '⚠️ No estoy en ninguna sala de este chat.');

    case 'play': case 'p': {
      const consulta = args.slice(1).join(' ');
      if (!consulta) return responder(`⚠️ Ejemplo: \`${PREFIX}play Follow Me\``);
      try {
        const s = await entrarSala(threadId);
        const pista = await resolverPista(consulta);
        s.cola.push(pista);
        if (s.reproduciendo) responder(`📜 Añadida a la cola (#${s.cola.length}): **${pista.titulo}**`);
        bucleReproduccion(s);
      } catch (e) { responder(`⚠️ ${e.message}`); }
      return;
    }

    case 'pausa': case 'reanudar': case 'siguiente': case 'skip': case 'parar': case 'stop': {
      const mapa = { skip: 'siguiente', stop: 'parar' };
      return responder(accion(threadId, mapa[cmd] || cmd));
    }

    case 'cola': {
      const s = sesiones.get(threadId);
      if (!s || (!s.actual && !s.cola.length)) return responder('📜 La cola está vacía.');
      const lineas = s.cola.slice(0, 10).map((x, i) => `${i + 1}. ${x.titulo}`);
      return responder(`📜 **Cola**\n${s.actual ? `▶️ ${s.actual.titulo}\n` : ''}${lineas.join('\n')}`);
    }

    case 'ahora': {
      const s = sesiones.get(threadId);
      return responder(s?.actual ? `🎵 **${s.actual.titulo}** (${s.actual.duracion})` : '⏸️ No suena nada.');
    }

    case 'volumen': case 'vol': {
      const s = sesiones.get(threadId);
      if (!s) return responder('⚠️ No estoy en la sala.');
      if (args[1] === undefined) return responder(`🔊 Volumen: **${s.volumen}/100**`);
      const v = Number(args[1]);
      if (!Number.isInteger(v) || v < 0 || v > 100) return responder('⚠️ Usa un número entero entre 0 y 100.');
      s.volumen = v;
      return responder(`🎚️ Volumen fijado en **${v}/100**.`);
    }
  }
}

function manejarInteraccion(evento) {
  const mapa = { pause: 'pausa', resume: 'reanudar', next: 'siguiente', stop: 'parar' };
  const nombre = mapa[evento.buttonId];
  if (!nombre || !evento.threadId) return;
  const texto = accion(evento.threadId, nombre);
  if (texto) say(evento.threadId, texto);
}

// ───────────── Webhook ─────────────
const app = express();
app.use(express.json({ verify: (req, res, buf) => { req.rawBody = buf.toString('utf8'); } }));

function firmaValida(req) {
  const ts = req.headers['x-anarchy-bot-timestamp'];
  const firma = req.headers['x-anarchy-bot-signature'];
  if (!SECRET || !ts || !firma || !String(firma).startsWith('v1=')) return false;
  const tsNum = parseInt(ts, 10);
  if (isNaN(tsNum) || Math.abs(Math.floor(Date.now() / 1000) - tsNum) > 300) return false;
  const esperado = crypto.createHmac('sha256', SECRET).update(`${ts}.${req.rawBody || ''}`).digest('hex');
  const recibido = String(firma).slice(3);
  const a = Buffer.from(esperado);
  const b = Buffer.from(recibido);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

app.get('/', (req, res) => res.status(200).send('Bot online'));

app.post('/', (req, res) => {
  if (!firmaValida(req)) return res.status(401).send('Firma inválida.');
  res.status(200).send('OK'); // responder en < 5 s

  const ev = req.body || {};
  (async () => {
    try {
      switch (ev.type) {
        case 'message': return await manejarComando(ev);
        case 'interaction': return manejarInteraccion(ev);
        case 'room.end': return limpiar(ev.threadId);
        default: return;
      }
    } catch (err) {
      log('[ERROR EVENTO]', err);
    }
  })();
});

app.use((err, req, res, next) => {
  if (!res.headersSent) res.status(400).send('Petición inválida.');
});

app.listen(PORT, async () => {
  log(`[SERVER] Puerto ${PORT}`);
   const me = await api('/me');
  if (me.ok) log('[BOT]', JSON.stringify(me.data));
  else log(`[BOT] /me falló: HTTP ${me.status} | ${JSON.stringify(me.data)} | token cargado: ${BOT_TOKEN ? BOT_TOKEN.length + ' caracteres' : 'NO'}`);
  if (/^[!$%&*+\-=~>;]{1,3}$/.test(PREFIX)) api('/me', 'PATCH', { commandPrefix: PREFIX });
});

async function apagar() {
  await Promise.all([...sesiones.keys()].map(salirSala));
  process.exit(0);
}
process.on('SIGTERM', apagar);
process.on('SIGINT', apagar);
