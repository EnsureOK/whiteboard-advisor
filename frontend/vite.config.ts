import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  base: "./",  // 相对路径:支持挂在 /app/ 下(桌面版)与根路径
  server: {
    // PORT 由预览器注入(autoPort);手动 npm run dev 时保持 5173
    port: Number(process.env.PORT) || 5173,
    proxy: {
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/api": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
