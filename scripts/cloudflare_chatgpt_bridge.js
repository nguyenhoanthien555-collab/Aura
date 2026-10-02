/**
 * Cloudflare Worker Reverse Proxy for ChatGPT Web (Aura 24/7 Cloud Bridge)
 *
 * Mục đích:
 *   Chuyển tiếp các request từ Aura (Render Cloud) sang chatgpt.com thông qua
 *   hạ tầng mạng biên của Cloudflare để không bị Cloudflare WAF chặn 403 Forbidden.
 *   - Vận hành 24/7 hoàn toàn trên Cloud, KHÔNG cần bật máy tính cá nhân.
 *   - Hạn mức gói miễn phí của Cloudflare: 100,000 requests / ngày (hoàn toàn 0đ).
 *
 * Hướng dẫn triển khai 1-Click:
 *   1. Đăng nhập https://dash.cloudflare.com/ -> Vào mục "Workers & Pages".
 *   2. Nhấn "Create Application" -> "Create Worker".
 *   3. Dán toàn bộ nội dung file này vào editor và nhấn "Save and Deploy".
 *   4. Copy URL của Worker vừa tạo (VD: https://aura-bridge.your-subdomain.workers.dev).
 *   5. Cấu hình vào Aura Render:
 *      Biến môi trường: CHATGPT_BASE_URL = https://aura-bridge.your-subdomain.workers.dev
 *      (hoặc trong settings: llm.chatgpt_web_base_url)
 */

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "*",
        },
      });
    }

    const url = new URL(request.url);
    const targetUrl = new URL(url.pathname + url.search, "https://chatgpt.com");

    const modifiedHeaders = new Headers(request.headers);
    modifiedHeaders.set("Host", "chatgpt.com");
    modifiedHeaders.set("Origin", "https://chatgpt.com");
    modifiedHeaders.set("Referer", "https://chatgpt.com/");

    // Đảm bảo User-Agent hợp lệ của trình duyệt
    if (!modifiedHeaders.get("User-Agent") || modifiedHeaders.get("User-Agent").includes("Python")) {
      modifiedHeaders.set(
        "User-Agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
      );
    }

    try {
      const modifiedRequest = new Request(targetUrl.toString(), {
        method: request.method,
        headers: modifiedHeaders,
        body: ["GET", "HEAD"].includes(request.method) ? null : request.body,
        redirect: "follow",
      });

      const response = await fetch(modifiedRequest);

      const responseHeaders = new Headers(response.headers);
      responseHeaders.set("Access-Control-Allow-Origin": "*");
      responseHeaders.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
      responseHeaders.set("Access-Control-Allow-Headers", "*");

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: responseHeaders,
      });
    } catch (err) {
      return new Response(
        JSON.stringify({ error: "Bridge forwarding failed", detail: err.message }),
        {
          status: 502,
          headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
        }
      );
    }
  },
};
