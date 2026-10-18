/**
 * `/api/ping` 的封装（免鉴权、零 DB 查询，docs/03 2.1）。
 *
 * ★登录页与状态栏共用：它既是最轻的存活探针，也是版本号 / 服务地址的唯一来源 ——
 *   界面上任何「版本」字样都必须从这里取，**不要**在前端硬编码。
 */
import { call } from "./client";

export interface PingData {
  name: string;
  version: string;
  protocol: string;
  protocol_version: string;
  ip: string;
  port: number;
  server_time: string;
  db_ready: boolean;
  broadcast_active: boolean;
}

export function ping(): Promise<PingData> {
  return call<PingData>("/api/ping", { silent: true });
}
