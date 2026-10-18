import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

/**
 * 界面偏好（深浅色）。用 Context 而不是一层层传 props：
 * 顶栏要切换、打印页要强制浅色、状态栏要读，谁都能直接取，不必让中间层转发。
 */
interface UiState {
  dark: boolean;
  toggleDark: () => void;
  setDark: (value: boolean) => void;
}

const THEME_KEY = "scanable.theme";

const UiContext = createContext<UiState>({
  dark: false,
  toggleDark: () => undefined,
  setDark: () => undefined,
});

export function useUi(): UiState {
  return useContext(UiContext);
}

function initialDark(): boolean {
  try {
    const saved = window.localStorage.getItem(THEME_KEY);
    if (saved === "dark") {
      return true;
    }
    if (saved === "light") {
      return false;
    }
  } catch {
    /* 隐私模式下取不到，跟随系统即可 */
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function UiProvider({ children }: { children: ReactNode }) {
  const [dark, setDark] = useState<boolean>(initialDark);

  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    try {
      window.localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
    } catch {
      /* 存不下来不影响使用 */
    }
  }, [dark]);

  const toggleDark = useCallback(() => setDark((prev) => !prev), []);
  const value = useMemo<UiState>(() => ({ dark, toggleDark, setDark }), [dark, toggleDark]);

  return <UiContext.Provider value={value}>{children}</UiContext.Provider>;
}
