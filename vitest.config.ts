import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

/**
 * 顶层 vitest 配置：让 `npm test` 直接发现 renderer 测试，无需 --config。
 * 测试目录：`tests/renderer/*`。
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: [],
    include: ["tests/**/*.test.{ts,tsx}", "frontend/renderer/__tests__/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: {
      "@": resolve(__dirname, "frontend/renderer"),
      "@main": resolve(__dirname, "frontend/main"),
    },
  },
});