import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Le front appelle /api/* ; en dev, Vite proxifie vers l'API FastAPI (port 8000).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
