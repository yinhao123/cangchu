import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Button, Result } from "antd";
import type { ReactNode } from "react";
import PageLoading from "../components/PageLoading";
import { me } from "../api/auth";
import { appPath } from "../config";
import { clearSession, getToken, getUser, isAdmin, mustChangePassword, setUser } from "../store/session";

/**
 * 登录与角色守卫。
 *
 * ★Token 只在内存 + sessionStorage：刷新页面后内存是空的，必须先用 `/api/me` 恢复身份，
 *   否则「刷新一下就被踢回登录页」——这是 Token 方案最容易被做坏的地方。
 * ★强制改密必须由前端也拦一道：后端虽然会 403，但让用户看到一屏 403 提示不如直接跳到改密页。
 */
export default function RequireAuth({ children, adminOnly = false }: {
  children: ReactNode;
  adminOnly?: boolean;
}) {
  const location = useLocation();
  const [state, setState] = useState<"checking" | "ready" | "anonymous">(
    getToken() ? "checking" : "anonymous",
  );

  useEffect(() => {
    if (!getToken()) {
      setState("anonymous");
      return;
    }
    if (getUser()) {
      setState("ready");
      return;
    }
    me()
      .then((user) => {
        setUser(user);
        setState("ready");
      })
      .catch(() => {
        clearSession();
        setState("anonymous");
      });
  }, []);

  if (state === "checking") {
    return <PageLoading />;
  }
  if (state === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (mustChangePassword() && location.pathname !== "/change-password") {
    return <Navigate to="/change-password" replace />;
  }
  if (adminOnly && !isAdmin()) {
    return <Forbidden />;
  }
  return <>{children}</>;
}

/** 角色不足：明确说清「需要什么角色」，不静默跳首页（PRD 3.4） */
export function Forbidden() {
  return (
    <Result
      status="403"
      title="没有权限"
      subTitle="该功能仅管理员可用。如需操作，请让管理员账号登录后处理。"
      extra={<Button type="primary" href={appPath("/")}>返回首页</Button>}
    />
  );
}
