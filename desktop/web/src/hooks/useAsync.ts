import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";

/**
 * 列表页统一的「加载 / 失败可重试 / 覆盖并发」数据获取。
 *
 * ★必须能区分「空」与「失败」：把请求失败渲染成空列表，用户会以为数据丢了 ——
 *   这是列表类页面最常见也最误导人的一种缺陷（PRD 28.2 要求空状态与失败重试分开）。
 * ★`reload` 保持同一引用，可以直接放进依赖数组，不会造成无限请求。
 */
export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  reload: () => void;
}

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [tick, setTick] = useState(0);
  // 只认最后一次请求的结果：快速改筛选条件时，慢的旧响应不能覆盖新数据
  const seq = useRef(0);

  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    const current = ++seq.current;
    setLoading(true);
    loaderRef.current()
      .then((result) => {
        if (current === seq.current) {
          setData(result);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (current === seq.current) {
          setError(err instanceof ApiError ? err : new ApiError("加载失败", 1999, 0, null, "-"));
          setData(null);
        }
      })
      .finally(() => {
        if (current === seq.current) {
          setLoading(false);
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((prev) => prev + 1), []);
  return { data, loading, error, reload };
}
