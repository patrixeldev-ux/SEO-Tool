import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    cloudflareTest({
      main: "./src/index.ts",
      miniflare: {
        compatibilityDate: "2026-01-01",
        kvNamespaces: ["RULES"],
        bindings: { SIGNING_SECRET: "test-secret", SITE_TOKEN: "tok_test_1234567890abcdef", API_BASE: "https://api.test" },
      },
    }),
  ],
});
