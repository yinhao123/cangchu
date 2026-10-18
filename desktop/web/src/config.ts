/**
 * SPA 挂载前缀，单一来源就是 Vite 的 `base`（vite.config.ts）。
 *
 * ★双轨过渡期是 `/app` —— 旧 Jinja 页面此时还占着 `/login`、`/activate`，SPA 不能抢。
 *   旧页面全部下线后，把 vite.config.ts 的 base 与 views/spa.py 的 SPA_PREFIX 一起改回 `/`，
 *   这里的值会自动跟着变，**不要**在两处硬编码前缀。
 */
export const SPA_BASE = import.meta.env.BASE_URL.replace(/\/+$/, "");

/** 把应用内路径拼上挂载前缀（如 `/login` → `/app/login`） */
export function appPath(path: string): string {
  return `${SPA_BASE}${path}`;
}
