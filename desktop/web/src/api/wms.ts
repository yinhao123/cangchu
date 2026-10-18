/**
 * WMS 单据 API —— docs/03 第 12' 章 · docs/13 第 6 章
 *
 * ★字段名与后端逐字一致（`*_snap` 是快照、`*_name` 是 JOIN 出来的展示名），前端不做二次命名。
 * ★所有写操作走 `call`（失败必须抛，不允许「部分成功」）；导出走 `downloadRaw`（带 BOM 的 CSV）。
 */
import { call, downloadRaw, type QueryValue } from "./client";
import type { Paged, PageQuery } from "./types";

export type BillStatus =
  | "DRAFT" | "SUBMITTED" | "APPROVED" | "REJECTED"
  | "CLOSED" | "CANCELED" | "DONE" | "REVERSED";

export type OpType = "RECEIVE" | "RETURN_STOCK" | "RETURN_BORROW" | "BORROW" | "TRANSFER";

/** 状态 → 展示文案与颜色（唯一来源；页面不再各写一套 switch） */
export const STATUS_META: Record<BillStatus, { label: string; color: string }> = {
  DRAFT: { label: "草稿", color: "default" },
  SUBMITTED: { label: "待审批", color: "processing" },
  APPROVED: { label: "已审批", color: "blue" },
  REJECTED: { label: "已驳回", color: "error" },
  CLOSED: { label: "已关闭", color: "success" },
  CANCELED: { label: "已作废", color: "default" },
  DONE: { label: "已完成", color: "success" },
  REVERSED: { label: "已冲销", color: "warning" },
};

export const OP_TYPE_META: Record<OpType, { label: string; direction: "OUT" | "IN" | "BOTH" }> = {
  RECEIVE: { label: "领用", direction: "OUT" },
  RETURN_STOCK: { label: "退库", direction: "IN" },
  RETURN_BORROW: { label: "归还", direction: "IN" },
  BORROW: { label: "借用", direction: "OUT" },
  TRANSFER: { label: "调拨", direction: "BOTH" },
};

export interface BillLine {
  /** 新建时不需要；详情里由服务端给 */
  id?: number;
  product_id: number;
  product_name_snap?: string;
  unit_snap?: string;
  qty: number;
  price?: number | null;
  amount?: number | null;
  serial_number?: string | null;
  is_join_asset?: number;
  remark?: string | null;
}

export interface PurchaseOrderDetail extends BillLine {
  id: number;
  order_id: number;
  barcode_snap: string | null;
  received_qty: number;
  sort_order: number;
}

export interface PurchaseOrder {
  id: number;
  code: string;
  name: string;
  applicant_user_id: number;
  partner_id: number | null;
  partner_name_snap: string | null;
  applicant_date: string;
  total_qty: number;
  total_amount: number;
  status: BillStatus;
  submit_at: string | null;
  approve_at: string | null;
  approve_id?: number | null;
  reject_reason: string | null;
  remark: string | null;
  is_active: number;
  created_at: string;
  updated_at: string;
  applicant_name?: string | null;
  details?: PurchaseOrderDetail[];
}

export interface InboundDetail extends BillLine {
  id: number;
  inbound_id: number;
  order_detail_id: number | null;
  sort_order: number;
}

export interface PurchaseInbound {
  id: number;
  code: string;
  name: string;
  order_id: number | null;
  order_code_snap: string | null;
  warehouse_id: number;
  partner_id: number | null;
  in_date: string;
  total_qty: number;
  total_amount: number;
  status: BillStatus;
  posted_at: string | null;
  posted_by: number | null;
  submit_at: string | null;
  approve_at: string | null;
  reject_reason: string | null;
  remark: string | null;
  is_active: number;
  created_at: string;
  updated_at: string;
  warehouse_name?: string | null;
  partner_name?: string | null;
  details?: InboundDetail[];
}

export interface OperationOrderDetail extends BillLine {
  id: number;
  order_id: number;
  barcode_snap: string | null;
  sort_order: number;
}

export interface OperationOrder {
  id: number;
  code: string;
  name: string;
  op_type: OpType;
  order_id: number | null;
  in_warehousing_id: number | null;
  warehouse_id: number;
  to_warehouse_id: number | null;
  operator_user_id: number;
  partner_id: number | null;
  op_date: string;
  expect_return_date: string | null;
  total_qty: number;
  total_amount: number;
  status: BillStatus;
  posted_at: string | null;
  submit_at: string | null;
  approve_at: string | null;
  reject_reason: string | null;
  reversed_at: string | null;
  remark: string | null;
  is_active: number;
  created_at: string;
  updated_at: string;
  warehouse_name?: string | null;
  to_warehouse_name?: string | null;
  operator_name?: string | null;
  details?: OperationOrderDetail[];
}

export interface PendingApproval {
  biz:
    | "PURCHASE_ORDER"
    | "PURCHASE_INBOUND"
    | "OPERATION_ORDER"
    | "STOCKTAKE"
    | "SALES_ORDER"    // 销货单（P1-4）：审批只改状态，发货才出库
    | "SALES_RETURN";  // 销售退货单（P1-4）：审批即过账 IN
  biz_label: string;
  id: number;
  code: string;
  name: string;
  total_qty: number;
  total_amount: number;
  submit_at: string | null;
  op_type: OpType | null;
  op_label: string | null;
  submitter?: string | null;
  counterparty?: string | null;
  warehouse_name?: string | null;
  to_warehouse_name?: string | null;
  operator_name?: string | null;
}

export interface BillQuery extends PageQuery {
  status?: string;
  keyword?: string;
  date_from?: string;
  date_to?: string;
  partner_id?: number;
  warehouse_id?: number;
  order_id?: number;
  op_type?: string;
}

export type ReviewAction = "APPROVE" | "REJECT";

// ---------------- 采购订单 ----------------

export function listPurchaseOrders(query: BillQuery = {}): Promise<Paged<PurchaseOrder>> {
  return call<Paged<PurchaseOrder>>("/api/wms/purchase-orders", { query: query as Record<string, QueryValue> });
}

export function getPurchaseOrder(id: number): Promise<PurchaseOrder> {
  return call<PurchaseOrder>(`/api/wms/purchase-orders/${id}`);
}

export function createPurchaseOrder(body: Record<string, unknown>): Promise<PurchaseOrder> {
  return call<PurchaseOrder>("/api/wms/purchase-orders", { method: "POST", body });
}

export function updatePurchaseOrder(id: number, body: Record<string, unknown>): Promise<PurchaseOrder> {
  return call<PurchaseOrder>(`/api/wms/purchase-orders/${id}`, { method: "PUT", body });
}

export function submitPurchaseOrder(id: number): Promise<PurchaseOrder> {
  return call<PurchaseOrder>(`/api/wms/purchase-orders/${id}/submit`, { method: "POST", body: {} });
}

export function reviewPurchaseOrder(id: number, action: ReviewAction, rejectReason?: string) {
  return call<PurchaseOrder>(`/api/wms/purchase-orders/${id}/approve`, {
    method: "POST",
    body: { action, reject_reason: rejectReason },
  });
}

export function cancelPurchaseOrder(id: number): Promise<PurchaseOrder> {
  return call<PurchaseOrder>(`/api/wms/purchase-orders/${id}/cancel`, { method: "POST", body: {} });
}

// ---------------- 采购入库单 ----------------

export function listPurchaseInbounds(query: BillQuery = {}): Promise<Paged<PurchaseInbound>> {
  return call<Paged<PurchaseInbound>>("/api/wms/purchase-inbounds", { query: query as Record<string, QueryValue> });
}

export function getPurchaseInbound(id: number): Promise<PurchaseInbound> {
  return call<PurchaseInbound>(`/api/wms/purchase-inbounds/${id}`);
}

export function createPurchaseInbound(body: Record<string, unknown>): Promise<PurchaseInbound> {
  return call<PurchaseInbound>("/api/wms/purchase-inbounds", { method: "POST", body });
}

export function updatePurchaseInbound(id: number, body: Record<string, unknown>): Promise<PurchaseInbound> {
  return call<PurchaseInbound>(`/api/wms/purchase-inbounds/${id}`, { method: "PUT", body });
}

export function submitPurchaseInbound(id: number): Promise<PurchaseInbound> {
  return call<PurchaseInbound>(`/api/wms/purchase-inbounds/${id}/submit`, { method: "POST", body: {} });
}

/** ★审批通过 = 同步过账（写流水 + 加库存 + 回写订单可收量），前端只需提示结果 */
export function reviewPurchaseInbound(id: number, action: ReviewAction, rejectReason?: string) {
  return call<PurchaseInbound>(`/api/wms/purchase-inbounds/${id}/approve`, {
    method: "POST",
    body: { action, reject_reason: rejectReason },
  });
}

export function cancelPurchaseInbound(id: number): Promise<PurchaseInbound> {
  return call<PurchaseInbound>(`/api/wms/purchase-inbounds/${id}/cancel`, { method: "POST", body: {} });
}

// ---------------- 通用操作单 ----------------

export function listOperationOrders(query: BillQuery = {}): Promise<Paged<OperationOrder>> {
  return call<Paged<OperationOrder>>("/api/wms/operation-orders", { query: query as Record<string, QueryValue> });
}

export function getOperationOrder(id: number): Promise<OperationOrder> {
  return call<OperationOrder>(`/api/wms/operation-orders/${id}`);
}

export function createOperationOrder(body: Record<string, unknown>): Promise<OperationOrder> {
  return call<OperationOrder>("/api/wms/operation-orders", { method: "POST", body });
}

export function updateOperationOrder(id: number, body: Record<string, unknown>): Promise<OperationOrder> {
  return call<OperationOrder>(`/api/wms/operation-orders/${id}`, { method: "PUT", body });
}

export function submitOperationOrder(id: number): Promise<OperationOrder> {
  return call<OperationOrder>(`/api/wms/operation-orders/${id}/submit`, { method: "POST", body: {} });
}

export function reviewOperationOrder(id: number, action: ReviewAction, rejectReason?: string) {
  return call<OperationOrder>(`/api/wms/operation-orders/${id}/approve`, {
    method: "POST",
    body: { action, reject_reason: rejectReason },
  });
}

/** 冲销：对已过账流水逐条红字反向，单据置 REVERSED（不是删单） */
export function reverseOperationOrder(id: number, reason: string): Promise<OperationOrder> {
  return call<OperationOrder>(`/api/wms/operation-orders/${id}/reverse`, {
    method: "POST",
    body: { reason },
  });
}

export function cancelOperationOrder(id: number): Promise<OperationOrder> {
  return call<OperationOrder>(`/api/wms/operation-orders/${id}/cancel`, { method: "POST", body: {} });
}

// ---------------- 待办与导出 ----------------

export function listApprovals(type?: string): Promise<{ list: PendingApproval[]; total: number }> {
  return call<{ list: PendingApproval[]; total: number }>("/api/wms/approvals", { query: { type } });
}

export type ExportBiz = "purchase-orders" | "purchase-inbounds" | "operation-orders";

export function exportBills(biz: ExportBiz, query: BillQuery = {}): Promise<void> {
  const names: Record<ExportBiz, string> = {
    "purchase-orders": "采购订单.csv",
    "purchase-inbounds": "采购入库单.csv",
    "operation-orders": "操作单.csv",
  };
  return downloadRaw(`/api/wms/export/${biz}`, names[biz], query as Record<string, QueryValue>);
}
