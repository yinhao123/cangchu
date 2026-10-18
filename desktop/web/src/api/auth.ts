import { call } from "./client";
import type { CurrentUser } from "../store/session";

export interface LoginResult {
  token: string;
  expires_at: string | null;
  user: CurrentUser;
}

export function login(username: string, password: string, deviceName: string): Promise<LoginResult> {
  // client_type=web：与手机端同一套令牌体系，仅设备类型不同（docs/03 第 3 章）
  return call<LoginResult>("/api/login", {
    method: "POST",
    body: { username, password, device_name: deviceName, client_type: "web" },
    silent: true,
  });
}

export function logout(): Promise<{ revoked: boolean }> {
  return call<{ revoked: boolean }>("/api/logout", { method: "POST", silent: true });
}

export function me(): Promise<CurrentUser & { token_expires_at: string | null }> {
  return call("/api/me", { silent: true });
}

export function changePassword(oldPassword: string, newPassword: string): Promise<CurrentUser> {
  return call<CurrentUser>("/api/me/password", {
    method: "POST",
    body: { old_password: oldPassword, new_password: newPassword },
    silent: true,
  });
}
