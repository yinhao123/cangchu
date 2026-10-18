/**
 * 追踪码（trace_id）—— 状态栏展示，供运维按日志回溯（docs/07 / docs/12 §5）。
 *
 * ★只保留「最近一次」：用户报问题时看到的那个码就是他要念给运维的码，
 *   保存整个历史反而会让「该念哪一个」变模糊。
 */
let traceId = "-";
const listeners = new Set<() => void>();

export function getTrace(): string {
  return traceId;
}

export function setTrace(next: string): void {
  if (!next || next === traceId) {
    return;
  }
  traceId = next;
  listeners.forEach((fn) => fn());
}

export function subscribeTrace(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
