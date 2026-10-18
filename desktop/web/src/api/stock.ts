import { call, newClientMsgId } from "./client";
import type { StockWriteResult } from "./types";

/**
 * 库存写接口 —— docs/03 第 5 章。
 *
 * ★`client_msg_id` 必填且**重试必须复用同一个 id**：丢了它 = 重复提交会变成两笔真实流水（T-09）。
 *   所以每个提交动作在「用户按下确认」时生成一次，重试时沿用（见 each page 的 draft 对象）。
 * ★方向由接口路径表达（in/out），前端**不能**用负数表达出库；盘点传目标值而不是差值。
 */

export interface MovePayload {
  client_msg_id: string;
  product_id?: number;
  barcode?: string;
  warehouse_id: number;
  quantity: number;
  partner_id?: number | null;
  remark?: string;
}

export interface AdjustPayload {
  client_msg_id: string;
  product_id?: number;
  barcode?: string;
  warehouse_id: number;
  /** 盘点目标值（服务端自己算 diff），不是差值 */
  target_qty: number;
  remark: string;
}

export function stockIn(payload: MovePayload): Promise<StockWriteResult> {
  return call<StockWriteResult>("/api/stock/in", { method: "POST", body: payload, silent: true });
}

export function stockOut(payload: MovePayload): Promise<StockWriteResult> {
  return call<StockWriteResult>("/api/stock/out", { method: "POST", body: payload, silent: true });
}

export function stockAdjust(payload: AdjustPayload): Promise<StockWriteResult> {
  return call<StockWriteResult>("/api/stock/adjust", { method: "POST", body: payload, silent: true });
}

export function reverseLog(logId: number,
                           reason: string,
                           clientMsgId?: string): Promise<{ reversal_log_id: number; type?: string;
  after_qty?: number; duplicated: boolean }> {
  return call(`/api/stock/reverse/${logId}`, {
    method: "POST",
    body: { client_msg_id: clientMsgId ?? newClientMsgId(), reason },
    silent: true,
  });
}
