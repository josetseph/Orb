import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev only: same-origin API like the packaged app, where FastAPI serves dist/.
const api = process.env.API_PROXY_TARGET ?? "http://127.0.0.1:17401";

export default defineConfig({
  plugins: [react({ compiler: true })],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "src") } },
  server: {
    proxy: Object.fromEntries(
      ["/api/v1", "/vault-files", "/health"].map((p) => [p, { target: api, changeOrigin: true }]),
    ),
  },
});
