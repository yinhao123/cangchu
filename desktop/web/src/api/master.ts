import { call } from "./client";
import type { PageQuery, Paged, Partner, Product, Warehouse } from "./types";

/** 基础资料（商品 / 仓库 / 往来单位）—— docs/03 第 4 章 */

export interface ProductQuery extends PageQuery {
  keyword?: string;
  barcode?: string;
  category?: string;
  /** 默认 true；传 false 才带出已停用商品 */
  only_active?: string;
}

export function listProducts(query: ProductQuery): Promise<Paged<Product>> {
  return call<Paged<Product>>("/api/products", { query: { ...query } });
}

export function getProduct(id: number): Promise<Product> {
  return call<Product>(`/api/products/${id}`);
}

export function getProductByBarcode(barcode: string): Promise<Product & { recent_logs: unknown[] }> {
  return call(`/api/products/by-barcode/${encodeURIComponent(barcode)}`, { silent: true });
}

export type ProductFields = Omit<Product, "id" | "created_at" | "updated_at" | "is_active"
  | "deleted_at" | "barcode_text" | "narrow"> & {
  /** 乐观锁版本戳：编辑时回传列表里拿到的 updated_at，不匹配后端给 1011（PRD 5.3） */
  expected_updated_at?: string;
};

export function createProduct(fields: Partial<ProductFields>): Promise<Product> {
  return call<Product>("/api/products", { method: "POST", body: fields, silent: true });
}

export function updateProduct(id: number, fields: Partial<ProductFields>): Promise<Product> {
  return call<Product>(`/api/products/${id}`, { method: "PUT", body: fields, silent: true });
}

export function deleteProduct(id: number, hard = false): Promise<{ deleted: string }> {
  return call(`/api/products/${id}`, { method: "DELETE", query: { hard }, silent: true });
}

export function restoreProduct(id: number): Promise<Product> {
  return call<Product>(`/api/products/${id}/restore`, { method: "POST", silent: true });
}

export function listWarehouses(all = false): Promise<{ list: Warehouse[]; total: number }> {
  return call("/api/warehouses", { query: { all } });
}

export interface WarehouseFields {
  name: string;
  address?: string | null;
  /** 可选编码，唯一 */
  code?: string | null;
  /** 0=顶级仓库；非 0=挂在该仓库下成为库位（两级树，docs/13 4.1） */
  parent_id?: number;
  category?: string | null;
}

export function createWarehouse(fields: WarehouseFields): Promise<Warehouse> {
  return call<Warehouse>("/api/warehouses", { method: "POST", body: fields, silent: true });
}

export function updateWarehouse(id: number,
                                fields: Partial<WarehouseFields & Pick<Warehouse, "sort_order"
                                  | "is_active">>): Promise<Warehouse> {
  return call<Warehouse>(`/api/warehouses/${id}`, { method: "PUT", body: fields, silent: true });
}

export function listPartners(type?: "CUSTOMER" | "SUPPLIER"): Promise<{ list: Partner[]; total: number }> {
  return call("/api/partners", { query: { type } });
}

/**
 * 新增往来单位。
 * ★ `type` 必填且显式挑字段：`type` 决定这条记录能不能在出库/入库里被选中，
 *   漏传会被服务端 422 拦下（`type 不能为空`）。这里用参数类型强制调用方给出 type，
 *   并且逐字段构造请求体 —— 不把整个表单对象原样转发，避免夹带 `undefined`
 *   （JSON.stringify 会直接丢掉它，服务端看起来就是「没传」）。
 */
export function createPartner(
  fields: { type: "CUSTOMER" | "SUPPLIER" } & Partial<Omit<Partner, "type">>,
): Promise<Partner> {
  const { name, type, contact, phone, address, remark } = fields;
  return call<Partner>("/api/partners", {
    method: "POST",
    body: { name, type, contact, phone, address, remark },
    silent: true,
  });
}
