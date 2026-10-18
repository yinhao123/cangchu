/**
 * 后端包络 `{code,msg,data,trace_id}` 的**唯一**出口（docs/03 1.3）。
 *
 * ★不用 axios：原生 fetch + 这一个封装就够，少一个依赖（PRD 2.2 的精神）。
 * ★认证走 `X-Auth-Token`，不依赖 Cookie 自动携带 → CSRF 天然规避，因此全站没有 form_token。
 * ★`code !== 0` 一律抛 ApiError：绝不返回「部分成功」让调用方去猜，也绝不把失败当成功弹绿条。
 * ★1003（登录失效）与 1013（未激活）在这里集中跳转，页面不需要各自处理。
 */
import { appPath } from "../config";
import { clearSession, getToken } from "../store/session";
import { setTrace } from "../store/trace";

export interface Envelope<T> {
  code: number;
  msg: string;
  data: T;
  trace_id: string;
}

export class ApiError extends Error {
  readonly code: number;
  readonly status: number;
  readonly data: unknown;
  readonly traceId: string;

  constructor(message: string, code: number, status: number, data: unknown, traceId: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.data = data;
    this.traceId = traceId;
  }

  /** 字段级错误（后端 `data.errors`）：表单用来就地标红，而不是弹一个大红条 */
  get fieldErrors(): Record<string, string[]> {
    const data = this.data;
    if (data && typeof data === "object" && "errors" in data) {
      const errors = (data as { errors?: Record<string, string[]> }).errors;
      if (errors && typeof errors === "object") {
        return errors;
      }
    }
    return {};
  }

  /** 业务数据（如库存不足时的 current/requested/lack），供页面做更具体的提示 */
  get payload(): Record<string, unknown> {
    return this.data && typeof this.data === "object" ? (this.data as Record<string, unknown>) : {};
  }
}

type ErrorNotifier = (error: ApiError) => void;

/** 由 main.tsx 注入 antd 的 message/notification —— client 本身不依赖 UI 库 */
let notifyError: ErrorNotifier = () => undefined;

export function setErrorNotifier(fn: ErrorNotifier): void {
  notifyError = fn;
}

export type QueryValue = string | number | boolean | null | undefined;

export interface CallOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  query?: Record<string, QueryValue>;
  body?: unknown;
  /** 表单自带就地错误展示时置 true：不弹全局提示，但异常照抛 */
  silent?: boolean;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  if (!query) {
    return path;
  }
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }
    params.set(key, String(value));
  });
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) {
    headers["X-Auth-Token"] = token;
  }
  return headers;
}

function redirectTo(path: string, reason: string): void {
  clearSession();
  const target = appPath(path);
  if (!window.location.pathname.startsWith(target)) {
    window.location.assign(`${target}?reason=${encodeURIComponent(reason)}`);
  }
}

function handleFatal(error: ApiError, silent: boolean): void {
  if (error.code === 1003) {
    // Token 失效：清会话回登录页。页面切换本身就是反馈，不再叠一个弹窗
    redirectTo("/login", "登录已失效，请重新登录");
    return;
  }
  if (error.code === 1013) {
    redirectTo("/activate", "软件尚未激活");
    return;
  }
  if (!silent) {
    notifyError(error);
  }
}

function envelopeError(envelope: Envelope<unknown>, status: number): ApiError {
  if (envelope.trace_id) {
    setTrace(envelope.trace_id);
  }
  return new ApiError(envelope.msg || "请求失败", envelope.code, status, envelope.data,
    envelope.trace_id || "-");
}

function networkError(): ApiError {
  return new ApiError("无法连接服务端，请确认服务端程序正在运行", -1, 0, null, "-");
}

/** JSON 接口：成功返回 `data`，失败抛 ApiError */
export async function call<T>(path: string, options: CallOptions = {}): Promise<T> {
  const { method = "GET", query, body, silent = false } = options;
  const headers = authHeaders();
  headers.Accept = "application/json";
  let payload: BodyInit | undefined;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), { method, headers, body: payload });
  } catch {
    const error = networkError();
    if (!silent) {
      notifyError(error);
    }
    throw error;
  }

  const contentType = response.headers.get("Content-Type") || "";
  if (contentType.includes("application/json")) {
    const envelope = (await response.json()) as Envelope<T>;
    if (envelope.code === 0 && response.ok) {
      if (envelope.trace_id) {
        setTrace(envelope.trace_id);
      }
      return envelope.data;
    }
    const error = envelopeError(envelope, response.status);
    handleFatal(error, silent);
    throw error;
  }

  const error = new ApiError(`响应格式异常（HTTP ${response.status}）`, 1999, response.status,
    null, "-");
  if (!silent) {
    notifyError(error);
  }
  throw error;
}

/**
 * multipart 上传（批量导入的 CSV）—— 与 `call` 共用认证、包络解析与 1003/1013 跳转。
 * ★不要改 `Content-Type`：浏览器要自己带 `boundary`，手写会直接 400。
 */
export async function upload<T>(path: string, form: FormData,
                               options: { silent?: boolean } = {}): Promise<T> {
  const silent = options.silent === true;
  const headers = authHeaders();
  headers.Accept = "application/json";

  let response: Response;
  try {
    response = await fetch(buildUrl(path), { method: "POST", headers, body: form });
  } catch {
    const error = networkError();
    if (!silent) {
      notifyError(error);
    }
    throw error;
  }

  const contentType = response.headers.get("Content-Type") || "";
  if (contentType.includes("application/json")) {
    const envelope = (await response.json()) as Envelope<T>;
    if (envelope.code === 0 && response.ok) {
      if (envelope.trace_id) {
        setTrace(envelope.trace_id);
      }
      return envelope.data;
    }
    const error = envelopeError(envelope, response.status);
    handleFatal(error, silent);
    throw error;
  }

  const error = new ApiError(`响应格式异常（HTTP ${response.status}）`, 1999, response.status,
    null, "-");
  if (!silent) {
    notifyError(error);
  }
  throw error;
}

/**
 * 原始响应（条码图片、CSV 导出）—— 这些接口不套 JSON 包络，是文档里的**显式豁免**。
 * ★失败时后端仍回 JSON 包络（1002/1003/1013…），必须按包络解析，
 *   否则会把一条错误提示当成图片渲染成「碎图」。
 */
export async function callRaw(path: string, options: CallOptions = {}): Promise<Response> {
  const { method = "GET", query, body } = options;
  const headers = authHeaders();
  let payload: BodyInit | undefined;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), { method, headers, body: payload });
  } catch {
    const error = networkError();
    notifyError(error);
    throw error;
  }

  if (!response.ok) {
    const contentType = response.headers.get("Content-Type") || "";
    const error = contentType.includes("application/json")
      ? envelopeError((await response.json()) as Envelope<unknown>, response.status)
      : new ApiError(`请求失败（HTTP ${response.status}）`, 1999, response.status, null, "-");
    handleFatal(error, false);
    throw error;
  }
  return response;
}

/** 取图片并转成 object URL。调用方负责在卸载时 `URL.revokeObjectURL`。 */
export async function callBlobUrl(path: string, query?: Record<string, QueryValue>): Promise<string> {
  const response = await callRaw(path, { query });
  return URL.createObjectURL(await response.blob());
}

/** 下载原始文件（CSV 导出）。文件名优先取后端 Content-Disposition。 */
export async function downloadRaw(path: string, fallbackName: string,
                                  query?: Record<string, QueryValue>): Promise<void> {
  const response = await callRaw(path, { query });
  const disposition = response.headers.get("Content-Disposition") || "";
  const matched = /filename="?([^";]+)"?/.exec(disposition);
  const filename = matched ? matched[1] : fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

/**
 * `client_msg_id`（UUID v4，≤36 字符）：库存写接口**必填**，重试同一笔必须复用同一个 id（T-09）。
 *
 * ★局域网走 http://192.168.x.x，属于**非安全上下文**，`crypto.randomUUID` 在 Chrome 里是 undefined，
 *   所以必须有降级实现。这里只需要「不重复」，不需要密码学强度。
 */
export function newClientMsgId(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return cryptoObj.randomUUID();
  }
  let seed = "";
  if (cryptoObj && typeof cryptoObj.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    cryptoObj.getRandomValues(bytes);
    seed = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  } else {
    seed = `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 18)}`.padEnd(32, "0");
  }
  const hex = seed.slice(0, 32);
  return [hex.slice(0, 8), hex.slice(8, 12), `4${hex.slice(13, 16)}`, `a${hex.slice(17, 20)}`,
    hex.slice(20, 32)].join("-");
}
