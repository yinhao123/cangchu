/**
 * 会话状态（PRD 3.3）—— Token 与当前用户。
 *
 * ★Token 只放内存 + sessionStorage：不放 localStorage（关掉浏览器即失效，公用电脑更安全），
 *   也不用 Cookie（不依赖浏览器自动携带 → CSRF 天然规避，所以全站没有 form_token）。
 * ★刷新页面后内存丢了，靠 `/api/me` 恢复（见 router 的 RequireAuth）。
 */
export interface CurrentUser {
  id: number;
  username: string;
  display_name: string | null;
  role: "admin" | "operator";
  /** 1 = 必须先改初始密码，除改密/退出/我是谁外全被后端 403（docs/03 3.3） */
  must_change_password: number;
  last_login_at?: string | null;
}

const TOKEN_KEY = "scanable.token";

/** sessionStorage 在极端隐私设置下会抛异常，不能让它炸掉整个应用 */
function readStorage(): string | null {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeStorage(value: string | null): void {
  try {
    if (value === null) {
      window.sessionStorage.removeItem(TOKEN_KEY);
    } else {
      window.sessionStorage.setItem(TOKEN_KEY, value);
    }
  } catch {
    /* 存不进去就只在内存里活着，不阻断使用 */
  }
}

let token: string | null = readStorage();
let user: CurrentUser | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((fn) => fn());
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getToken(): string | null {
  return token;
}

export function getUser(): CurrentUser | null {
  return user;
}

export function isAdmin(): boolean {
  return user?.role === "admin";
}

export function mustChangePassword(): boolean {
  return (user?.must_change_password ?? 0) === 1;
}

export function setToken(next: string | null): void {
  token = next;
  writeStorage(next);
  emit();
}

export function setUser(next: CurrentUser | null): void {
  user = next;
  emit();
}

/** 登录成功：Token 与用户信息一起落位，只通知一次 */
export function startSession(nextToken: string, nextUser: CurrentUser): void {
  token = nextToken;
  user = nextUser;
  writeStorage(nextToken);
  emit();
}

/** 退出/失效：清干净（内存 + sessionStorage） */
export function clearSession(): void {
  token = null;
  user = null;
  writeStorage(null);
  emit();
}
