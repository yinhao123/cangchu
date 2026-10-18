/**
 * 批量导入 / 导出 —— docs/14 第 3 章（P0-1）
 *
 * ★ 上传走 `upload`（FormData），不要手写 Content-Type（浏览器要自己带 boundary）。
 * ★ 预检与提交**发同一个文件**：服务端共用同一套解析校验，前端不做二次猜测。
 */
import { downloadRaw, upload } from "./client";

export type ImportKind = "products" | "partners" | "opening-stock";
export type ExportKind = "products" | "partners" | "stock-current";

export interface ImportRowError {
  row: number;
  field: string;
  value: string;
  msg: string;
}

export interface PreviewResult {
  kind: string;
  total: number;
  valid: number;
  invalid: number;
  errors: ImportRowError[];
  errors_truncated: boolean;
  preview: Record<string, unknown>[];
  unknown_columns: string[];
}

export interface CommitResult {
  kind: string;
  total: number;
  ok_count: number;
  failed_count: number;
  duplicated_count: number;
  duplicated?: boolean;
  invalid: number;
  errors: ImportRowError[];
  failed_rows: { row: number; msg: string }[];
  unknown_columns: string[];
}

function form(kind: ImportKind, file: File, skipInvalid?: boolean): FormData {
  const data = new FormData();
  data.append("kind", kind);
  data.append("file", file);
  if (skipInvalid !== undefined) {
    data.append("skip_invalid", skipInvalid ? "true" : "false");
  }
  return data;
}

/** 预检（dry_run）：不落库，只回逐行错误 */
export function previewImport(kind: ImportKind, file: File): Promise<PreviewResult> {
  return upload<PreviewResult>("/api/imports/preview", form(kind, file), { silent: true });
}

/** 正式导入：skipInvalid=false 时整批回滚（服务端 1019） */
export function commitImport(kind: ImportKind, file: File,
                             skipInvalid: boolean): Promise<CommitResult> {
  return upload<CommitResult>("/api/imports/commit", form(kind, file, skipInvalid), { silent: true });
}

/** 下载模板 / 导出数据（CSV，utf-8-sig + CRLF） */
export function downloadTemplate(kind: ImportKind): Promise<void> {
  return downloadRaw(`/api/imports/templates/${kind}`, `${kind}_template.csv`);
}

export function exportData(kind: ExportKind): Promise<void> {
  return downloadRaw(`/api/imports/export/${kind}`, `${kind}.csv`);
}
