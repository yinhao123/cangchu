import { useCallback, useEffect, useRef, useState } from "react";
import { App as AntdApp } from "antd";
import PrintDocument from "../components/PrintDocument";
import {
  getPrintMeta, getPrintPayload,
  type PaperKey, type PrintBiz, type PrintMeta, type PrintPayload, type StocktakePrintMode,
} from "../api/print";
import { ApiError } from "../api/client";

interface PrintOptions {
  mode?: StocktakePrintMode;
  paper?: PaperKey;
}

/**
 * 单据打印的统一入口（P0-3）—— 页面只调 `printer.print(biz, ids)`，
 * 版式、纸张、`@page` 注入、打印后清理都在 PrintDocument 里。
 *
 * ★返回的 `element` 必须放在页面根节点 `.sd-page` 的**直接子级**：
 *   打印时 CSS 用 `body.sd-printing .sd-page > *:not(.sd-print-docs){display:none}`
 *   把页面本身整体隐藏，只留打印文档。
 * ★纸张默认取服务端设置（`print_paper`），页面可临时覆盖（打印前现场切换）。
 */
export function usePrint() {
  const { message } = AntdApp.useApp();
  const [meta, setMeta] = useState<PrintMeta | null>(null);
  const [job, setJob] = useState<{ payload: PrintPayload; paper: PaperKey; key: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    let alive = true;
    getPrintMeta()
      .then((data) => { if (alive) { setMeta(data); } })
      .catch(() => undefined);   // 元信息失败不影响打字，打印时会再报错
    return () => { alive = false; };
  }, []);

  const print = useCallback(async (biz: PrintBiz, ids: number[] | number, options: PrintOptions = {}) => {
    setBusy(true);
    try {
      const payload = await getPrintPayload(biz, ids, options);
      seq.current += 1;
      setJob({
        payload,
        paper: options.paper ?? meta?.paper ?? "a4",
        key: seq.current,
      });
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    } finally {
      setBusy(false);
    }
  }, [meta, message]);

  const element = job ? (
    <PrintDocument
      payload={job.payload}
      paper={job.paper}
      printKey={job.key}
      onDone={() => setJob(null)}
    />
  ) : null;

  return {
    print,
    busy,
    meta,
    element,
    papers: meta?.papers ?? [],
    defaultPaper: (meta?.paper ?? "a4") as PaperKey,
  };
}
