import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    watch: {
      // Needed when the source is bind-mounted from Windows into a Linux container.
      usePolling: process.env.VITE_USE_POLLING === "true",
      // Never poll a package-manager store if one ends up in the project folder (thousands of files).
      ignored: ["**/.pnpm-store/**"],
    },
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
});
