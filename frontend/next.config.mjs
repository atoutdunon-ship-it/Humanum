/** Proxy /api vers l'API FastAPI : le jeton ne quitte jamais l'origine de l'application. */
const api = process.env.HC_API_URL ?? "http://127.0.0.1:8000";
export default {
  async rewrites() { return [{ source: "/api/:path*", destination: `${api}/api/:path*` }]; },
  async headers() {
    return [{ source: "/(.*)", headers: [
      { key: "X-Frame-Options", value: "DENY" },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    ] }];
  },
};
