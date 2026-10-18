import { useEffect, useRef } from "react";
import type { PaperKey, PrintDoc, PrintPayload } from "../api/print";

interface Props {
  payload: PrintPayload;
  paper: PaperKey;
  /** 每次点打印 +1：同一个组件重渲染也要能再次唤出打印对话框 */
  printKey: number;
  onDone?: () => void;
}

const PAGE_PRESETS: Record<PaperKey, { page: string; margin: string }> = {
  a4: { page: "A4 portrait", margin: "12mm" },
  dot: { page: "241mm 140mm", margin: "4mm" },
};

function money(value: number | null | undefined): string {
  if (value === null || value === undefined) {
    return "";
  }
  return value.toFixed(2);
}

/**
 * 单据打印版式（P0-3）—— docs/14 第 5.2 节。
 *
 * ★**不用任何 antd 组件**：原生 table/div。antd 5 的 CSS-in-JS 在打印时可能因注入顺序导致表格塌陷，
 *   而且 Table 的分页器会被印出来。
 * ★`@page` 规则按纸张动态注入 `<style>`：CSS 无法用 body class 选 `@page`，
 *   写死两套又只能生效最后一条（Chrome 109 仍如此）。
 * ★打印完成后移除 body class、删掉注入的样式 —— 否则下次屏幕渲染会带着打印样式。
 */
export default function PrintDocument({ payload, paper, printKey, onDone }: Props) {
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;

  useEffect(() => {
    const preset = PAGE_PRESETS[paper] ?? PAGE_PRESETS.a4;
    const style = document.createElement("style");
    style.id = "sd-print-page";
    style.textContent = `@page { size: ${preset.page}; margin: ${preset.margin}; }`;
    document.head.appendChild(style);

    const classes = ["sd-printing", `sd-print-${paper}`];
    classes.forEach((name) => document.body.classList.add(name));

    const finish = () => {
      classes.forEach((name) => document.body.classList.remove(name));
      style.remove();
      window.removeEventListener("afterprint", finish);
      onDoneRef.current?.();
    };
    window.addEventListener("afterprint", finish);
    // 等一帧，确保表格已经排版完，否则会打出空白页
    const timer = window.setTimeout(() => window.print(), 150);

    return () => {
      window.clearTimeout(timer);
      classes.forEach((name) => document.body.classList.remove(name));
      style.remove();
      window.removeEventListener("afterprint", finish);
    };
    // 只在「点了一次打印」时重建；payload 变化必然伴随 printKey 变化
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [printKey, paper]);

  const company = payload.company;

  return (
    <div className="sd-print-docs">
      {payload.docs.map((doc) => (
        <Document key={`${doc.biz}-${doc.id}`} doc={doc} payload={payload} company={company} />
      ))}
    </div>
  );
}

function Document({
  doc,
  payload,
  company,
}: {
  doc: PrintDoc;
  payload: PrintPayload;
  company: PrintPayload["company"];
}) {
  const isStocktake = doc.kind === "stocktake";
  const headParts: string[] = [`${doc.date_label}：${doc.date || "—"}`];
  if (doc.warehouse) {
    headParts.push(`仓库：${doc.warehouse}${doc.to_warehouse ? ` → ${doc.to_warehouse}` : ""}`);
  }
  if (doc.counterparty) {
    headParts.push(`${doc.counterparty_label}：${doc.counterparty}`);
  }
  headParts.push(`${doc.person_label}：${doc.person || "—"}`);
  if (doc.op_label) {
    headParts.push(`类型：${doc.op_label}`);
  }

  return (
    <div className="sd-doc">
      <div className="sd-doc-head" style={{ marginBottom: "3mm" }}>
        <div className="sd-doc-title" style={{ textAlign: "center", fontWeight: 700 }}>
          {company.name}
        </div>
        <div style={{ textAlign: "center", fontSize: "1.5em", fontWeight: 600, margin: "1mm 0" }}>
          {doc.biz_label}
          {doc.mode === "diff" ? "（差异表）" : doc.mode === "blank" ? "（空白表）" : ""}
        </div>
        <div style={{ textAlign: "center" }}>
          单号：{doc.code}　状态：{doc.status_label}
          {doc.name ? `　名称：${doc.name}` : ""}
        </div>
        <div style={{ marginTop: "1mm" }}>{headParts.join("　")}</div>
        {company.phone || company.address ? (
          <div style={{ marginTop: "0.5mm" }}>
            {[company.address, company.phone].filter(Boolean).join("　")}
          </div>
        ) : null}
      </div>

      <table>
        <thead>
          <tr>
            <th className="sd-center" style={{ width: "8%" }}>序号</th>
            <th className="sd-col-barcode" style={{ width: "16%" }}>条码</th>
            <th>商品名称</th>
            <th className="sd-center" style={{ width: "8%" }}>单位</th>
            {isStocktake ? (
              <>
                <th className="sd-num" style={{ width: "10%" }}>账面数</th>
                <th className="sd-num" style={{ width: "10%" }}>实盘数</th>
                <th className="sd-num" style={{ width: "10%" }}>差异</th>
              </>
            ) : (
              <>
                <th className="sd-num" style={{ width: "10%" }}>数量</th>
                <th className="sd-num" style={{ width: "12%" }}>单价</th>
                <th className="sd-num" style={{ width: "14%" }}>金额</th>
              </>
            )}
            <th style={{ width: "16%" }}>备注</th>
          </tr>
        </thead>
        <tbody>
          {doc.lines.length === 0 ? (
            <tr>
              <td colSpan={8} className="sd-center">（无明细）</td>
            </tr>
          ) : (
            doc.lines.map((line) => (
              <tr key={line.no}>
                <td className="sd-center">{line.no}</td>
                <td className="sd-col-barcode">{line.barcode}</td>
                <td>{line.product_name}</td>
                <td className="sd-center">{line.unit}</td>
                {isStocktake ? (
                  <>
                    <td className="sd-num">{line.qty}</td>
                    <td className="sd-num">{line.real_qty === null || line.real_qty === undefined ? "" : line.real_qty}</td>
                    <td className="sd-num">
                      {line.diff_qty === null || line.diff_qty === undefined || line.diff_qty === 0
                        ? ""
                        : (line.diff_qty > 0 ? `+${line.diff_qty}` : line.diff_qty)}
                    </td>
                  </>
                ) : (
                  <>
                    <td className="sd-num">{line.qty}</td>
                    <td className="sd-num">{money(line.price)}</td>
                    <td className="sd-num">{money(line.amount)}</td>
                  </>
                )}
                <td>{line.remark}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      <div style={{ marginTop: "2mm" }}>
        {doc.summary.map((item) => (
          <span key={item.label} style={{ marginRight: "6mm" }}>
            {item.label}：<strong>{item.label.includes("金额") ? money(item.value) : item.value}</strong>
          </span>
        ))}
        {doc.remark ? <div style={{ marginTop: "1mm" }}>备注：{doc.remark}</div> : null}
      </div>

      <div style={{ marginTop: "6mm" }}>
        {doc.sign_off.map((label) => (
          <span key={label} style={{ display: "inline-block", marginRight: "12mm" }}>
            {label}：________________
          </span>
        ))}
      </div>

      <div style={{ marginTop: "2mm", fontSize: "0.85em" }}>
        打印人：{payload.printed_by || "—"}　打印时间：{payload.server_time.slice(0, 19).replace("T", " ")}
        　共 {payload.total} 张
      </div>
    </div>
  );
}
