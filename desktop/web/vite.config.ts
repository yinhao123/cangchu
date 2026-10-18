import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

/**
 * ★产物直接写进 Flask 的 static/spa（`emptyOutDir` 会清空旧产物）。
 *   理由：`pytest` 与 PyInstaller 打包都不能依赖 Node —— Win7 服务器上没有 Node（docs/12）。
 * ★build.target 锁 chrome88：antd v5 的样式依赖 `:where()`（Chrome 88+），
 *   而 Win7 上能装的最高版本是 Chrome/Edge 109，不能生成更新的语法。
 */
export default defineConfig({
  plugins: [react()],
  // 旧 Jinja 页面已全部下线，SPA 独占根路径
  base: "/",
  build: {
    outDir: fileURLToPath(new URL("../static/spa", import.meta.url)),
    emptyOutDir: true,
    target: "chrome88",
    assetsDir: "assets",
    sourcemap: false,
    chunkSizeWarningLimit: 1600,
  },
  server: {
    host: "0.0.0.0",
    allowedHosts: true,
    // 开发态把 /api 反代到本机 Flask（默认端口），避免两套源导致的跨域与 Cookie 干扰
    proxy: { "/api": { target: "http://127.0.0.1:5000", changeOrigin: false } },
  },
});
