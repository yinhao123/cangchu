/** 跨模块共用的响应形状。字段名与后端逐字一致，前端不做二次命名。 */
export interface Paged<T> {
  list: T[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
}

/** 分页与排序的公共入参（列表页统一 20/50/100，PRD 28.2） */
export interface PageQuery {
  page?: number;
  page_size?: number;
  sort?: string;
  order?: "asc" | "desc";
}

export interface Product {
  id: number;
  name: string;
  barcode: string | null;
  category: string;
  unit: string;
  cost_price: number;
  sale_price: number;
  min_stock: number;
  remark: string | null;
  is_active: number;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
  /** 仅 /api/labels 会补这两个字段 */
  barcode_text?: string;
  narrow?: boolean;
}

export interface Warehouse {
  id: number;
  /** 仓库编码（0002 起可选；唯一索引允许多个 NULL） */
  code: string | null;
  name: string;
  /** 0=顶级仓库；非 0=该仓库下的库位（最多两级） */
  parent_id: number;
  /** 1=仓库 2=库位 */
  level: number;
  category: string | null;
  address: string | null;
  is_active: number;
  sort_order: number;
  created_at: string;
  updated_at: string;
  /** 列表接口附带：有库存的商品数 / 库存总量 / 子库位数 */
  product_count?: number;
  total_qty?: number;
  child_count?: number;
}

export interface Partner {
  id: number;
  name: string;
  type: "CUSTOMER" | "SUPPLIER";
  contact: string | null;
  phone: string | null;
  address: string | null;
  remark: string | null;
  is_active: number;
  created_at: string;
  updated_at: string;
}

export type StockType = "IN" | "OUT" | "ADJUST";

export interface StockLog {
  id: number;
  client_msg_id: string;
  product_id: number;
  warehouse_id: number;
  operator_id: number;
  partner_id: number | null;
  type: StockType;
  quantity: number;
  before_qty: number;
  after_qty: number;
  product_name: string;
  unit_price: number | null;
  amount: number | null;
  source: "WEB" | "APP" | "PDA";
  device_name: string | null;
  remark: string | null;
  reversed_by: number | null;
  trace_id: string | null;
  created_at: string;
  barcode: string | null;
  unit: string | null;
  warehouse_name: string | null;
  operator_name: string | null;
  partner_name: string | null;
}

export interface CurrentStock {
  product_id: number;
  warehouse_id: number;
  qty: number;
  updated_at: string;
  product_name: string;
  barcode: string | null;
  unit: string;
  min_stock: number;
  category: string;
  warehouse_name: string;
  last_in_at: string | null;
  last_out_at: string | null;
  /** 服务端算好的口径，前端不得重算（docs/02 铁律：同一口径只定义一次） */
  is_low_stock: boolean;
}

export interface SummaryRow {
  key: string;
  label: string;
  in_qty: number;
  out_qty: number;
  in_amount: number;
  out_amount: number;
  bill_count: number;
}

export interface SummaryResult {
  dimension: string;
  date_from: string;
  date_to: string;
  rows: SummaryRow[];
  total: {
    in_qty: number;
    out_qty: number;
    in_amount: number;
    out_amount: number;
    bill_count: number;
  };
}

/** 写库存接口的统一返回（入库/出库/盘点） */
export interface StockWriteResult {
  log_id: number;
  type: StockType;
  product_id: number;
  product_name: string;
  warehouse_id: number;
  quantity: number;
  before_qty: number;
  after_qty: number;
  created_at: string;
  /** true = 幂等命中，本次没有产生新流水（T-09） */
  duplicated: boolean;
  diff?: number;
}
