/**
 * 单据打印 API —— docs/03 第 12'''' 章 · docs/14 第 5.3 节（P0-3）
 *
 * ★打印数据包是「只读、稳定、含中文枚举与合计」的形状；合计一律用服务端字段，前端不重算。
 * ★一次可打印多张（`ids` 逗号分隔，最多 20 张），版式前端渲染，服务端不产出 HTML。
 */
import { call, type QueryValue } from "./client";

export type PrintBiz =
  | "purchase-orders"
  | "purchase-inbounds"
  | "operation-orders"
  | "stocktakes"
  | "sales-orders";   // 销货单（P1-4）：客户签收栏在这套签字里
export type PaperKey = "a4" | "dot";
export type StocktakePrintMode = "blank" | "diff";

export interface PaperPreset {
  key: PaperKey;
  label: string;
  /** CSS `@page size` 值，如 `A4 portrait` / `241mm 140mm` */
  page: string;
  margin: string;
  /** 0 = 不限；针式为 6 行/页 */
  rows_per_page: number;
}

export interface SignOffBiz {
  key: PrintBiz;
  label: string;
  sign_off: string[];
  operation_sign_off: Record<string, string[]> | null;
}

export interface PrintMeta {
  company: { name: string; phone: string; address: string; title_from_setting: boolean };
  paper: PaperKey;
  papers: PaperPreset[];
  biz: SignOffBiz[];
  printed_by: string;
  server_time: string;
}

export interface PrintLine {
  no: number;
  product_name: string;
  barcode: string;
  unit: string;
  qty: number;
  price: number | null;
  amount: number | null;
  remark: string;
  real_qty?: number | null;
  diff_qty?: number | null;
}

export interface PrintDoc {
  biz: PrintBiz;
  biz_label: string;
  kind: "bill" | "stocktake";
  id: number;
  code: string;
  name: string;
  status: string;
  status_label: string;
  op_type?: string | null;
  op_label?: string;
  date: string;
  date_label: string;
  warehouse?: string;
  to_warehouse?: string;
  counterparty_label: string;
  counterparty: string;
  person_label: string;
  person: string;
  remark: string;
  mode?: StocktakePrintMode;
  lines: PrintLine[];
  summary: { label: string; value: number }[];
  totals: Record<string, number>;
  sign_off: string[];
}

export interface PrintPayload {
  biz: PrintBiz;
  biz_label: string;
  mode: string | null;
  docs: PrintDoc[];
  total: number;
  company: PrintMeta["company"];
  printed_by: string;
  server_time: string;
}

export function getPrintMeta(): Promise<PrintMeta> {
  return call<PrintMeta>("/api/print/meta", { silent: true });
}

/** `ids` 支持 `12` 或 `1,2,3`（批量）；`mode` 仅盘点单用 */
export function getPrintPayload(
  biz: PrintBiz,
  ids: number[] | number,
  options: { mode?: StocktakePrintMode; paper?: PaperKey } = {},
): Promise<PrintPayload> {
  const idText = Array.isArray(ids) ? ids.join(",") : String(ids);
  const query: Record<string, QueryValue> = {};
  if (options.mode) {
    query.mode = options.mode;
  }
  return call<PrintPayload>(`/api/print/${biz}/${idText}`, { query, silent: true });
}
