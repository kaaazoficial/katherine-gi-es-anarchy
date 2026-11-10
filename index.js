const express = require('express');
const crypto = require('crypto');

const app = express();
const PORT = process.env.PORT || 3000;
const BOT_TOKEN = process.env.BOT_TOKEN;
const WEBHOOK_SECRET = process.env.WEBHOOK_SECRET;

// Middleware para capturar el body en texto plano y poder verificar la firma HMAC
app.use(express.json({
    verify: (req, res, buf) => {
        req.rawBody = buf;
    }
}));

// 1. Verificación automática del token al arrancar el servidor
async function verificarTokenBot() {
    if (!BOT_TOKEN) {
        console.log("⚠️ Advertencia: La variable de entorno BOT_TOKEN no está configurada.");
        return;
    }
    try {
        console.log("🔍 Verificando token de Katherine con la API de Anarchy...");
        const response = await fetch('https://api.anarchyapps.org/bot-api/v1/me', {
            method: 'GET',
            headers: {
                'Authorization': `Bot ${BOT_TOKEN}`
            }
        });
        const data = await response.json();
        if (response.ok) {
            console.log("✅ ¡Token válido! Conectado como:", data.name || data.botId || "Bot de Katherine");
        } else {
            console.error("❌ Error de autenticación en la API:", data);
        }
    } catch (error) {
        console.error("❌ Error de red al contactar a Anarchy:", error);
    }
}

// 2. Ruta para recibir y verificar los webhooks de Anarchy
app.post('/webhook', async (req, res) => {
    try {
        // Validación opcional de seguridad HMAC si usas WEBHOOK_SECRET
        if (WEBHOOK_SECRET && req.headers['x-signature']) {
            const signature = req.headers['x-signature'];
            const hmac = crypto.createHmac('sha256', WEBHOOK_SECRET);
            const digest = hmac.update(req.rawBody).digest('hex');

            if (signature !== digest) {
                console.warn("⚠️ Advertencia: Firma HMAC inválida en el webhook.");
                return res.status(401).send('Invalid signature');
            }
        }

        const payload = req.body;
        console.log("📥 Webhook recibido:", JSON.stringify(payload, null, 2));

        // Responder inmediatamente a Anarchy con 200 OK para evitar timeouts
        res.status(200).send('OK');

        // Procesar si es un evento de mensaje
        if (payload && payload.type === 'message') {
            const messageText = payload.content || payload.text || '';
            const threadId = payload.threadId;

            // Ejemplo de comando: -ping
            if (messageText.trim() === '-ping') {
                await enviarMensaje(threadId, '¡Pong! 🏓 Hola desde el servidor de Katherine en Render.');
            }
        }
    } catch (error) {
        console.error("❌ Error procesando el webhook:", error);
    }
});

// 3. Función para enviar respuestas de vuelta al chat
async function enviarMensaje(threadId, texto) {
    if (!threadId) return;
    try {
        const url = `https://api.anarchyapps.org/bot-api/v1/threads/${threadId}/messages`;
        console.log(`📤 Enviando respuesta al thread: ${threadId}`);

        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Authorization': `Bot ${BOT_TOKEN}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ content: texto })
        });

        const data = await response.json();
        if (response.ok) {
            console.log("✅ Mensaje enviado con éxito al chat.");
        } else {
            console.error("❌ Error al enviar mensaje (Si sale 403, revisa que Katherine esté agregada a este chat):", data);
        }
    } catch (error) {
        console.error("❌ Error de red al intentar responder:", error);
    }
}

// Ruta base para comprobar que el servicio está online
app.get('/', (req, res) => {
    res.send('🚀 Servidor webhook de Katherine activo y operando.');
});

// Iniciar servidor
app.listen(PORT, () => {
    console.log(`🚀 Servidor corriendo en el puerto ${PORT}`);
    verificarTokenBot();
});
