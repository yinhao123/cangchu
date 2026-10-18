import { call } from "./client";

/** 账号与设备 / 系统设置 / 备份 —— docs/03 第 9 / 7 章（全部 require_admin） */

export interface AdminUser {
  id: number;
  username: string;
  display_name: string | null;
  role: "admin" | "operator";
  is_active: number;
  must_change_password: number;
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
}

export interface Device {
  id: number;
  user_id: number;
  client_type: string;
  device_name: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
}

export interface SettingsItem {
  key: string;
  /** 已按 value_type 转换过的值（bool 是真布尔、int 是数字） */
  value: string | number | boolean;
  raw_value: string;
  value_type: "string" | "int" | "bool" | "json";
  needs_restart: boolean;
  updated_at: string;
  /** 分组与中文标签由服务端给（`settings_service.SETTING_META`），前端不得另写一套 */
  group: string;
  label: string;
  hint: string;
  /** 仅枚举项存在 */
  options?: string[];
}

export interface SettingsView {
  items: SettingsItem[];
  groups: { id: string; title: string }[];
}

export interface BackupInfo {
  filename: string;
  size_bytes: number;
  created_at: string;
  trigger: string;
}

/** 备份守护线程的进程级状态（`backup_service.status()`） */
export interface BackupStatus {
  last_ok_at?: string | null;
  last_error?: string | null;
  consecutive_failures?: number;
  [key: string]: unknown;
}

export interface SettingsSaveResult {
  /** 真正写入的 key 列表 */
  updated: string[];
  /** 其中需要重启才生效的 key —— 必须显式告知，否则用户遇到「改了没反应」 */
  needs_restart: string[];
  items: SettingsItem[];
}

export interface SystemInfo {
  url: string;
  urls: string[];
  ip: string;
  port: number;
  name: string;
  candidates: { ip: string; label?: string }[];
  firewall_ports: string;
  firewall_commands: string[];
  concurrency_hint: string;
  version: string;
  device_name: string;
  schema_version: number;
  now: string;
  data_dir: string;
  db_path: string;
  backups_dir: string;
  logs_dir: string;
  disk: { total_mb: number; free_mb: number; used_percent: number };
  backup: BackupStatus | null;
  autostart: boolean;
  /** 手机自动发现是否真的在广播（来自广播线程状态，不是配置值） */
  broadcast_active: boolean;
}

export function listUsers(): Promise<{ list: AdminUser[]; roles: { value: string;
  label: string }[]; me: number }> {
  return call("/api/users");
}

export function createUser(fields: { username: string; password: string; password_confirm?: string;
  role: string; display_name?: string; must_change?: boolean }): Promise<{ user: AdminUser }> {
  return call("/api/users", { method: "POST", body: fields, silent: true });
}

export function resetUserPassword(id: number, fields: { password: string; new_password: string;
  new_password_confirm?: string }): Promise<{ user: AdminUser; revoked_tokens: number }> {
  return call(`/api/users/${id}/password`, { method: "POST", body: fields, silent: true });
}

export function setUserActive(id: number, isActive: boolean,
                              password: string): Promise<{ revoked_tokens: number }> {
  // is_active 必须是**显式布尔**：后端拒收字符串与缺省，避免漏传被当成「停用」
  return call(`/api/users/${id}/active`, {
    method: "POST",
    body: { is_active: isActive, password },
    silent: true,
  });
}

export function listDevices(): Promise<{ list: Device[] }> {
  return call("/api/devices");
}

export function revokeDevice(id: number): Promise<{ token: Device }> {
  return call(`/api/devices/${id}/revoke`, { method: "POST", silent: true });
}

export function getSettings(): Promise<SettingsView> {
  return call<SettingsView>("/api/settings");
}

/** 普通设置项（不含网络项）：端口与自动发现必须走 saveNetworkSettings */
export function saveSettings(items: Record<string, string | number | boolean>): Promise<SettingsSaveResult> {
  return call<SettingsSaveResult>("/api/settings", { method: "PUT", body: { items }, silent: true });
}

/** 网络项要再输一次本人口令（PRD 3.5）；后端会拒收非网络项，防止它变成绕过口令的后门 */
export function saveNetworkSettings(items: Record<string, string | number | boolean>,
                                    password: string): Promise<SettingsSaveResult> {
  return call<SettingsSaveResult>("/api/settings/network", {
    method: "POST",
    body: { items, password },
    silent: true,
  });
}

export function listBackups(): Promise<{ list: BackupInfo[]; total: number;
  status: BackupStatus }> {
  return call("/api/backup/list");
}

export function createBackup(): Promise<BackupInfo> {
  return call<BackupInfo>("/api/backup", { method: "POST", silent: true });
}

export function restoreBackup(filename: string, password: string): Promise<Record<string, unknown>> {
  return call("/api/backup/restore", { method: "POST", body: { filename, password }, silent: true });
}

export function recalcPreview(): Promise<{ count: number; list: Record<string, unknown>[];
  truncated: number }> {
  return call("/api/backup/recalc");
}

export function recalcApply(): Promise<{ changed: number; backup: string | null }> {
  return call("/api/backup/recalc", { method: "POST", silent: true });
}

export function systemInfo(): Promise<SystemInfo> {
  return call<SystemInfo>("/api/system/info");
}

export function qrcode(): Promise<{ payload_json: unknown; image_png_base64: string; host: string;
  port: number; expires_at: string; candidates: { ip: string }[]; text: string }> {
  return call("/api/qrcode");
}
