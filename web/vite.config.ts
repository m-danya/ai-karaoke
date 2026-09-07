import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  build: { outDir: "out" },
  server: { proxy: { "/api": "http://127.0.0.1:9595" } },
});
