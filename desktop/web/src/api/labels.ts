import { call } from "./client";
import type { PageQuery, Paged, Product } from "./types";

/** 标签打印 —— docs/03 第 10 章 */

export interface LabelQuery extends PageQuery {
  keyword?: string;
  barcode?: string;
  category?: string;
  only_active?: string;
}

export interface LabelPage extends Paged<Product> {
  label_size: string;
  label_w: number;
  label_h: number;
  max_copies: number;
}

export function listLabels(query: LabelQuery): Promise<LabelPage> {
  return call<LabelPage>("/api/labels", { query: { ...query } });
}

/**
 * 条码图是**独立图片资源**（不走 JSON 包络，是文档里的显式豁免）：
 * 用 fetch + X-Auth-Token 取回 blob，再交给 `<img src>` —— 不在 JS 里注入 SVG（无需 innerHTML）。
 */
export function barcodeSvgPath(productId: number): string {
  return `/api/products/${productId}/barcode.svg`;
}

export function barcodePngPath(productId: number): string {
  return `/api/products/${productId}/barcode.png`;
}
