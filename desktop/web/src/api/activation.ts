import { call } from "./client";

export interface ActivationState {
  activated: boolean;
  message: string;
  license_code: number;
  machine_id: string;
  display_machine_id: string;
  expire_text: string;
  expire_ts: number;
  config_error: string;
}

/** 免登录、免门禁（未激活时也要能问「本机机器码是多少」） */
export function getState(): Promise<ActivationState> {
  return call<ActivationState>("/api/activation/state", { silent: true });
}

/**
 * 提交注册码。失败时后端回 `403 {code:1013}`，会在这里被抛成 ApiError ——
 * 页面据此把 `message` 红字显示（**不能**用 200 表示失败，否则弹的是绿条）。
 */
export function activate(code: string): Promise<ActivationState> {
  return call<ActivationState>("/api/activation", {
    method: "POST",
    body: { code },
    silent: true,
  });
}
