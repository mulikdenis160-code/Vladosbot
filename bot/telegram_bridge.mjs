import http from "node:http";
import { ReplitConnectors } from "@replit/connectors-sdk";

const port = Number(process.env.TELEGRAM_BRIDGE_PORT || 8010);
const connectors = new ReplitConnectors();

async function telegram(path, options = {}) {
  const response = await connectors.proxy("telegram", path, options);
  const body = await response.json();
  if (!response.ok || !body.ok) {
    throw new Error(body.description || `Telegram request failed (${response.status})`);
  }
  return body.result;
}

async function bodyOf(request) {
  let raw = "";
  for await (const chunk of request) raw += chunk;
  return raw ? JSON.parse(raw) : {};
}

const server = http.createServer(async (request, response) => {
  try {
    const body = await bodyOf(request);
    if (request.method === "GET" && request.url === "/health") {
      const bot = await telegram("/getMe");
      return send(response, 200, { ok: true, bot: bot.username });
    }
    if (request.method === "GET" && request.url === "/check-chat") {
      const chat = await telegram(`/getChat?chat_id=${encodeURIComponent(process.env.TELEGRAM_CHAT_ID || "")}`);
      return send(response, 200, {
        ok: true,
        chat: { id: chat.id, type: chat.type, title: chat.title, username: chat.username },
      });
    }
    if (request.method === "POST" && request.url === "/send-message") {
      const result = await telegram("/sendMessage", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          chat_id: process.env.TELEGRAM_CHAT_ID,
          text: body.text,
          disable_notification: Boolean(body.disable_notification),
        }),
      });
      return send(response, 200, { ok: true, message_id: result.message_id });
    }
    if (request.method === "POST" && request.url === "/send-photo") {
      if (body.image_url) {
        const result = await telegram("/sendPhoto", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            chat_id: process.env.TELEGRAM_CHAT_ID,
            caption: body.caption || "",
            photo: body.image_url,
          }),
        });
        return send(response, 200, { ok: true, message_id: result.message_id });
      }
      const bytes = Buffer.from(body.image_base64 || "", "base64");
      if (!bytes.length) throw new Error("image_base64 is required");
      const form = new FormData();
      form.append("chat_id", process.env.TELEGRAM_CHAT_ID);
      form.append("caption", body.caption || "");
      form.append("photo", new Blob([bytes], { type: "image/png" }), body.filename || "signal.png");
      const result = await telegram("/sendPhoto", { method: "POST", body: form });
      return send(response, 200, { ok: true, message_id: result.message_id });
    }
    return send(response, 404, { ok: false, error: "Not found" });
  } catch (error) {
    return send(response, 502, { ok: false, error: error instanceof Error ? error.message : String(error) });
  }
});

function send(response, status, payload) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(payload));
}

server.listen(port, "127.0.0.1", () => {
  console.log(`Telegram bridge listening on ${port}`);
});