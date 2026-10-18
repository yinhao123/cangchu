/**
 * 盘点单 API —— docs/03 第 12''' 章 · docs/14 第 4 章（P0-2）
 *
 * ★字段名与后端逐字一致；统计口径一律用服务端返回值（`diff_count` / `diff_abs_qty` / `period_changes`），
 *   前端不自己重算，避免与「过账按实时当前值」的口径打架。
 * ★写操作走 `call`（失败必抛），导出走 `downloadRaw`（BOM + CRLF）。
 */
import { call, downloadRaw, type QueryValue } from "./client";
import type { Paged, PageQuery } from "./types";

export type StocktakeStatus = "DRAFT" | "SUBMITTED" | "REJECTED" | "POSTED" | "CANCELED";

export const STOCKTAKE_STATUS_META: Record<StocktakeStatus, { label: string; color: string }> = {
  DRAFT: { label: "草稿", color: "default" },
  SUBMITTED: { label: "待审批", color: "processing" },
  REJECTED: { label: "已驳回", color: "error" },
  POSTED: { label: "已过账", color: "success" },
  CANCELED: { label: "已作废", color: "default" },
};

export interface StocktakeLine {
  id: number;
  stocktake_id: number;
  product_id: number;
  product_name_snapshot: string;
  barcode: string | null;
  unit: string | null;
  category: string | null;
  /** 建单快照的账面数（只用于差异报表） */
  book_qty: number;
  /** 实盘数；null = 未盘，不参与过账 */
  real_qty: number | null;
  /** real_qty - book_qty（快照口径） */
  diff_qty: number;
  /** 当前实时库存（过账按它算数量，可能因期间出入库与 book_qty 不同） */
  current_qty: number;
  remark: string | null;
}

export interface Stocktake {
  id: number;
  code: string;
  name: string;
  warehouse_id: number;
  warehouse_name: string | null;
  scope_category: string | null;
  status: StocktakeStatus;
  book_locked_at: string;
  posted_at: string | null;
  operator_id: number;
  operator_name: string | null;
  submitter_id: number | null;
  submitter_name: string | null;
  approver_id: number | null;
  approver_name: string | null;
  remark: string | null;
  created_at: string;
  updated_at: string;
  line_count: number;
  counted_count: number;
  diff_count: number;
  diff_abs_qty: number;
  /** 建单快照之后该仓库的流水笔数：>0 说明快照已过时（过账按实盘数，不阻断） */
  period_changes?: number;
  has_diff?: boolean;
  details?: StocktakeLine[];
}

export interface StocktakeQuery extends PageQuery {
  status?: string;
  keyword?: string;
  warehouse_id?: number;
  date_from?: string;
  date_to?: string;
}

export interface StocktakePrintLine {
  line_no: number;
  barcode: string | null;
  product_name: string;
  unit: string;
  book_qty: number;
  real_qty: number | null;
  diff_qty: number | null;
  remark: string | null;
}

export interface StocktakePrint {
  mode: "blank" | "diff";
  stocktake: Stocktake;
  lines: StocktakePrintLine[];
  total: number;
}

export function listStocktakes(query: StocktakeQuery = {}): Promise<Paged<Stocktake>> {
  return call<Paged<Stocktake>>("/api/wms/stocktakes", { query: query as Record<string, QueryValue> });
}

export function getStocktake(id: number): Promise<Stocktake> {
  return call<Stocktake>(`/api/wms/stocktakes/${id}`);
}

export function createStocktake(body: Record<string, unknown>): Promise<Stocktake> {
  return call<Stocktake>("/api/wms/stocktakes", { method: "POST", body });
}

/** 批量录实盘数 / 追加行（一次 HTTP 改多行，避免一百次请求） */
export function updateStocktake(
  id: number,
  body: {
    name?: string;
    remark?: string | null;
    lines?: { detail_id: number; real_qty: number | null }[];
    append?: { barcode: string; real_qty: number | null; remark?: string | null }[];
  },
): Promise<Stocktake> {
  return call<Stocktake>(`/api/wms/stocktakes/${id}`, { method: "PUT", body });
}

/** 作废（不是删除）：只置 CANCELED，明细保留 */
export function cancelStocktake(id: number): Promise<Stocktake> {
  return call<Stocktake>(`/api/wms/stocktakes/${id}`, { method: "DELETE" });
}

export function submitStocktake(id: number): Promise<Stocktake> {
  return call<Stocktake>(`/api/wms/stocktakes/${id}/submit`, { method: "POST", body: {} });
}

export function rejectStocktake(id: number, reason: string): Promise<Stocktake> {
  return call<Stocktake>(`/api/wms/stocktakes/${id}/reject`, { method: "POST", body: { reason } });
}

/** ★过账 = 按「实盘 − 实时当前」写 ADJUST 流水，整单原子；无差异行不落账 */
export function postStocktake(id: number): Promise<Stocktake> {
  return call<Stocktake>(`/api/wms/stocktakes/${id}/post`, { method: "POST", body: {} });
}

export function printStocktake(id: number, mode: "blank" | "diff"): Promise<StocktakePrint> {
  return call<StocktakePrint>(`/api/wms/stocktakes/${id}/print`, { query: { mode } });
}

export function exportStocktakes(query: StocktakeQuery = {}): Promise<void> {
  return downloadRaw("/api/wms/export/stocktakes", "盘点单.csv", query as Record<string, QueryValue>);
}
