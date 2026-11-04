/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En développement, Vite sert React sur http://localhost:5173 et redirige /api vers l'orchestrateur
// (le préfixe /api est retiré : /api/tasks devient /tasks), comme le fera Caddy en production.
// L'en-tête Host d'origine est conservé : l'API voit la même origine que le navigateur.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Sous Docker pour Windows, les fichiers modifiés ne sont pas signalés : on les surveille par sondage.
    watch: process.env.VITE_USE_POLLING === "1" ? { usePolling: true } : undefined,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://localhost:5000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
