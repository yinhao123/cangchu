/**
 * 销售订单 / 发货 / 销售退货 / 客户对账 —— docs/14 第 6 章 · docs/03 第 12''''' 章
 *
 * ★字段名与后端逐字一致（`customer_id` / `warehouse_id` / `shipped_qty` / `remaining_qty`）。
 * ★发货必须带 `shipment_id`（UUID v4）：它把「同一批发货」绑成幂等单元，网络抖动后重试不会扣两次库存。
 * ★对账单金额一律用服务端 `totals`，前端不做加减（免得页面和账本对不上）。
 */
import { call, downloadRaw, type QueryValue } from "./client";
import type { BillStatus } from "./wms";
import type { Paged, PageQuery } from "./types";

export interface SalesLine {
  product_id: number;
  product_name_snap?: string;
  barcode_snap?: string | null;
  unit_snap?: string;
  qty: number;
  price?: number | null;
  amount?: number | null;
  remark?: string | null;
}

export interface SalesOrderDetail extends SalesLine {
  id: number;
  order_id: number;
  shipped_qty: number;
  remaining_qty: number;
  sort_order: number;
}

export interface SalesOrder {
  id: number;
  code: string;
  name: string;
  applicant_user_id: number;
  customer_id: number | null;
  customer_name_snap: string | null;
  warehouse_id: number;
  warehouse_name?: string | null;
  order_date: string;
  total_qty: number;
  total_amount: number;
  status: BillStatus;
  submit_at: string | null;
  approve_at: string | null;
  approver_id: number | null;
  reject_reason: string | null;
  remark: string | null;
  is_active: number;
  created_at: string;
  updated_at: string;
  applicant_name?: string | null;
  details?: SalesOrderDetail[];
  shipped_qty?: number;
  remaining_qty?: number;
  shipment?: { shipment_id: string; duplicated: boolean; lines: { detail_id: number; qty: number }[] };
}

export interface SalesReturnDetail extends SalesLine {
  id: number;
  return_id: number;
  order_detail_id: number | null;
  sort_order: number;
}

export interface SalesReturn {
  id: number;
  code: string;
  name: string;
  source_order_id: number | null;
  order_code_snap: string | null;
  customer_id: number;
  customer_name_snap: string | null;
  warehouse_id: number;
  warehouse_name?: string | null;
  return_date: string;
  total_qty: number;
  total_amount: number;
  status: BillStatus;
  posted_at: string | null;
  posted_by: number | null;
  submit_at: string | null;
  approve_at: string | null;
  approver_id: number | null;
  reject_reason: string | null;
  return_reason: string;
  remark: string | null;
  is_active: number;
  created_at: string;
  updated_at: string;
  details?: SalesReturnDetail[];
}

export interface StatementLine {
  date: string;
  code: string;
  biz: string;
  direction: "IN" | "OUT";
  product_name: string;
  qty: number;
  price: number;
  amount: number;
  log_id: number;
}

export interface CustomerStatement {
  customer: {
    id: number;
    name: string;
    contact: string | null;
    phone: string | null;
    address: string | null;
  };
  from: string;
  to: string;
  lines: StatementLine[];
  totals: {
    out_qty: number;
    out_amount: number;
    return_qty: number;
    return_amount: number;
    net_qty: number;
    net_amount: number;
    line_count: number;
  };
}

export interface SalesQuery extends PageQuery {
  status?: string;
  keyword?: string;
  customer_id?: number;
  warehouse_id?: number;
  date_from?: string;
  date_to?: string;
  source_order_id?: number;
}

export type ReviewAction = "APPROVE" | "REJECT";

// ---------------- 销售订单 ----------------

export function listSalesOrders(query: SalesQuery = {}): Promise<Paged<SalesOrder>> {
  return call<Paged<SalesOrder>>("/api/wms/sales-orders", { query: query as Record<string, QueryValue> });
}

export function getSalesOrder(id: number): Promise<SalesOrder> {
  return call<SalesOrder>(`/api/wms/sales-orders/${id}`);
}

export function createSalesOrder(body: Record<string, unknown>): Promise<SalesOrder> {
  return call<SalesOrder>("/api/wms/sales-orders", { method: "POST", body });
}

export function updateSalesOrder(id: number, body: Record<string, unknown>): Promise<SalesOrder> {
  return call<SalesOrder>(`/api/wms/sales-orders/${id}`, { method: "PUT", body });
}

export function submitSalesOrder(id: number): Promise<SalesOrder> {
  return call<SalesOrder>(`/api/wms/sales-orders/${id}/submit`, { method: "POST", body: {} });
}

/** 审批只改状态，**不出库**；出库必须再调 `shipSalesOrder` */
export function reviewSalesOrder(id: number, action: ReviewAction, rejectReason?: string) {
  return call<SalesOrder>(`/api/wms/sales-orders/${id}/approve`, {
    method: "POST",
    body: { action, reject_reason: rejectReason },
  });
}

/**
 * 发货出库（可多次，部分发货）。
 * ★`shipmentId` 由调用方在「用户点发货」这一刻生成一次；重试必须复用同一个值。
 */
export function shipSalesOrder(
  id: number,
  shipmentId: string,
  lines: { detail_id: number; qty: number }[],
): Promise<SalesOrder> {
  return call<SalesOrder>(`/api/wms/sales-orders/${id}/ship`, {
    method: "POST",
    body: { shipment_id: shipmentId, lines },
  });
}

export function cancelSalesOrder(id: number): Promise<SalesOrder> {
  return call<SalesOrder>(`/api/wms/sales-orders/${id}/cancel`, { method: "POST", body: {} });
}

// ---------------- 销售退货 ----------------

export function listSalesReturns(query: SalesQuery = {}): Promise<Paged<SalesReturn>> {
  return call<Paged<SalesReturn>>("/api/wms/sales-returns", { query: query as Record<string, QueryValue> });
}

export function getSalesReturn(id: number): Promise<SalesReturn> {
  return call<SalesReturn>(`/api/wms/sales-returns/${id}`);
}

export function createSalesReturn(body: Record<string, unknown>): Promise<SalesReturn> {
  return call<SalesReturn>("/api/wms/sales-returns", { method: "POST", body });
}

export function updateSalesReturn(id: number, body: Record<string, unknown>): Promise<SalesReturn> {
  return call<SalesReturn>(`/api/wms/sales-returns/${id}`, { method: "PUT", body });
}

export function submitSalesReturn(id: number): Promise<SalesReturn> {
  return call<SalesReturn>(`/api/wms/sales-returns/${id}/submit`, { method: "POST", body: {} });
}

/** ★审批通过 = 同步过账（写 IN 流水 + 加库存），前端只需提示结果 */
export function reviewSalesReturn(id: number, action: ReviewAction, rejectReason?: string) {
  return call<SalesReturn>(`/api/wms/sales-returns/${id}/approve`, {
    method: "POST",
    body: { action, reject_reason: rejectReason },
  });
}

export function cancelSalesReturn(id: number): Promise<SalesReturn> {
  return call<SalesReturn>(`/api/wms/sales-returns/${id}/cancel`, { method: "POST", body: {} });
}

// ---------------- 客户对账 ----------------

export function getCustomerStatement(partnerId: number, from: string, to: string): Promise<CustomerStatement> {
  return call<CustomerStatement>("/api/wms/customer-statement", {
    query: { partner_id: partnerId, from, to },
  });
}

export function exportCustomerStatement(partnerId: number, from: string, to: string): Promise<void> {
  return downloadRaw("/api/wms/customer-statement", `客户对账单-${partnerId}-${from}_${to}.csv`, {
    partner_id: partnerId, from, to, export: "csv",
  });
}

/** 销售单据 CSV 导出（列表页「导出」按钮） */
export type SalesExportBiz = "sales-orders" | "sales-returns";

export function exportSalesBills(biz: SalesExportBiz, query: SalesQuery = {}): Promise<void> {
  const names: Record<SalesExportBiz, string> = {
    "sales-orders": "销货单.csv",
    "sales-returns": "销售退货单.csv",
  };
  return downloadRaw(`/api/wms/export/${biz}`, names[biz], query as Record<string, QueryValue>);
}

/** 前端生成幂等键：crypto.randomUUID 在 Win7 老浏览器上可能没有，兜底按 v4 格式拼一个 */
export function newShipmentId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) {
    return uuid;
  }
  const hex = "0123456789abcdef";
  let out = "";
  for (let i = 0; i < 36; i += 1) {
    if (i === 8 || i === 13 || i === 18 || i === 23) {
      out += "-";
    } else if (i === 14) {
      out += "4";
    } else if (i === 19) {
      out += hex[(Math.random() * 4 | 0) + 8];
    } else {
      out += hex[Math.random() * 16 | 0];
    }
  }
  return out;
}
