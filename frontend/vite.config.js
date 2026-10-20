import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api is proxied to the Python server, so the browser only ever talks to
// one origin and there is no CORS to think about in development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
