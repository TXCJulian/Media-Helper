import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [],
  },
  server: {
    proxy: {
      '/directories': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/rename': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/transcribe': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/config': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/cutter': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
