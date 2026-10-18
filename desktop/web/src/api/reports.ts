import { call, downloadRaw } from "./client";
import type { CurrentStock, PageQuery, Paged, StockLog, StockType, SummaryResult } from "./types";

/** 查询与报表 —— docs/03 第 6 章 */

export interface LogQuery extends PageQuery {
  date_from?: string;
  date_to?: string;
  type?: string;
  warehouse_id?: number;
  product_id?: number;
  barcode?: string;
  keyword?: string;
  operator_id?: number;
  source?: "WEB" | "APP" | "PDA";
  only_reversed?: boolean;
}

export interface CurrentQuery extends PageQuery {
  warehouse_id?: number;
  keyword?: string;
  low_only?: boolean;
  negative_only?: boolean;
}

export function queryLogs(query: LogQuery): Promise<Paged<StockLog>> {
  return call<Paged<StockLog>>("/api/stock/logs", { query: { ...query } });
}

export function getLog(id: number): Promise<StockLog> {
  return call<StockLog>(`/api/stock/logs/${id}`);
}

export function listOperators(): Promise<{ list: { id: number; username: string;
  display_name: string | null }[] }> {
  return call("/api/stock/operators");
}

export function queryCurrent(query: CurrentQuery): Promise<Paged<CurrentStock>> {
  return call<Paged<CurrentStock>>("/api/stock/current", { query: { ...query } });
}

export function summarize(dimension: string, dateFrom?: string,
                          dateTo?: string): Promise<SummaryResult> {
  return call<SummaryResult>("/api/stock/summary", {
    query: { dimension, date_from: dateFrom, date_to: dateTo },
  });
}

/** CSV 导出：后端 utf-8-soft BOM + \r\n（Excel 双击不乱码，T-10），前端只负责触发下载 */
export function exportLogs(query: LogQuery): Promise<void> {
  return downloadRaw("/api/stock/logs/export", "stock_logs.csv", { ...query });
}

export interface DashboardLowStock {
  id: number;
  name: string;
  barcode: string | null;
  min_stock: number;
  warehouse_name: string;
  qty: number;
}

export interface DashboardRecentLog {
  id: number;
  type: StockType;
  quantity: number;
  product_name_snapshot: string;
  created_at: string;
  source: string;
  username: string | null;
}

export interface DashboardServer {
  ip: string;
  port: number;
  name: string;
  version: string;
  server_time: string;
  broadcast_active: boolean;
}

/**
 * 仪表盘聚合（docs/03 6.4）。★每个聚合项都可能为 null（单查询失败不整页 500），
 * 前端必须按「可能为 null」渲染，不能直接 `.length`。
 */
export interface DashboardData {
  product_count: number | null;
  total_qty: number | null;
  /** 负库存条数：> 0 必须红字告警，禁止用 MAX(0,…) 掩盖 */
  stock_anomaly_count: number | null;
  today: { in_qty: number; out_qty: number; in_count: number; out_count: number } | null;
  low_stock_list: DashboardLowStock[] | null;
  low_stock_total: number | null;
  recent_logs: DashboardRecentLog[] | null;
  last_backup_at: string | null;
  server: DashboardServer;
  generated_at: string;
  errors: string[];
}

export function dashboard(): Promise<DashboardData> {
  return call<DashboardData>("/api/dashboard");
}

export const TYPE_LABEL: Record<StockType, string> = {
  IN: "入库",
  OUT: "出库",
  ADJUST: "盘点调整",
};
