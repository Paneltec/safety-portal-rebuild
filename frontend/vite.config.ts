import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs on port 8000; in production nginx proxies /api.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  preview: { proxy: { "/api": "http://localhost:8000" } },
});
