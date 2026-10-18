import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  App as AntdApp, Button, Drawer, Input, Modal, Select, Space, Table, Tag, Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import {
  getOperationOrder, getPurchaseInbound, getPurchaseOrder, listApprovals,
  reviewOperationOrder, reviewPurchaseInbound, reviewPurchaseOrder,
  type BillLine, type PendingApproval, type ReviewAction,
} from "../api/wms";
import { postStocktake, rejectStocktake } from "../api/stocktake";
import {
  getSalesOrder, getSalesReturn, reviewSalesOrder, reviewSalesReturn,
} from "../api/sales";
import { Money, Num } from "../components/Values";

const { Text } = Typography;

/**
 * 审批待办 —— docs/03 12'.5。
 *
 * ★一屏看完三类单据：审批人不需要在「采购订单 / 入库单 / 操作单」之间来回切页。
 * ★审批前必须能看明细：盲批是这类系统最典型的错误，所以「查看」里带完整明细行。
 * ★通过采购入库/操作单会**立即过账改库存**，弹窗里必须明写（不能让人以为只是盖章）。
 */
const BIZ_TAG: Record<PendingApproval["biz"], { color: string }> = {
  PURCHASE_ORDER: { color: "blue" },
  PURCHASE_INBOUND: { color: "geekblue" },
  OPERATION_ORDER: { color: "purple" },
  STOCKTAKE: { color: "gold" },
  SALES_ORDER: { color: "cyan" },
  SALES_RETURN: { color: "volcano" },
};

export default function Approvals() {
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();
  const state = useAsync(() => listApprovals(), []);
  const [reviewing, setReviewing] = useState<{ row: PendingApproval; action: ReviewAction } | null>(null);
  const [reason, setReason] = useState("");
  const [detail, setDetail] = useState<{ row: PendingApproval; lines: BillLine[] } | null>(null);

  async function openDetail(row: PendingApproval) {
    if (row.biz === "STOCKTAKE") {
      // 盘点单的明细要配合「录实盘数 / 打印差异表」，抽屉里放不下 → 直接进详情页
      navigate(`/wms/stocktakes/${row.id}`);
      return;
    }
    try {
      let lines: BillLine[] = [];
      if (row.biz === "PURCHASE_ORDER") {
        lines = (await getPurchaseOrder(row.id)).details ?? [];
      } else if (row.biz === "PURCHASE_INBOUND") {
        lines = (await getPurchaseInbound(row.id)).details ?? [];
      } else if (row.biz === "SALES_ORDER") {
        lines = ((await getSalesOrder(row.id)).details ?? []).map((d) => ({
          id: d.id, product_id: d.product_id, product_name_snap: d.product_name_snap ?? "",
          barcode_snap: d.barcode_snap, unit_snap: d.unit_snap, qty: d.qty,
          price: d.price, amount: d.amount, serial_number: null,
        }));
      } else if (row.biz === "SALES_RETURN") {
        lines = ((await getSalesReturn(row.id)).details ?? []).map((d) => ({
          id: d.id, product_id: d.product_id, product_name_snap: d.product_name_snap ?? "",
          barcode_snap: d.barcode_snap, unit_snap: d.unit_snap, qty: d.qty,
          price: d.price, amount: d.amount, serial_number: null,
        }));
      } else {
        lines = (await getOperationOrder(row.id)).details ?? [];
      }
      setDetail({ row, lines });
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doReview() {
    if (!reviewing) {
      return;
    }
    if (reviewing.action === "REJECT" && !reason.trim()) {
      message.warning("驳回必须填写原因");
      return;
    }
    const { row, action } = reviewing;
    try {
      if (row.biz === "PURCHASE_ORDER") {
        await reviewPurchaseOrder(row.id, action, reason || undefined);
      } else if (row.biz === "PURCHASE_INBOUND") {
        await reviewPurchaseInbound(row.id, action, reason || undefined);
      } else if (row.biz === "STOCKTAKE") {
        // 盘点单没有 approve 接口：通过就是过账（ADJUST），驳回走 /reject
        if (action === "APPROVE") {
          await postStocktake(row.id);
        } else {
          await rejectStocktake(row.id, reason.trim());
        }
      } else if (row.biz === "SALES_ORDER") {
        // 销货单审批只改状态，真正出库要在销货单页点「发货」
        await reviewSalesOrder(row.id, action, reason || undefined);
      } else if (row.biz === "SALES_RETURN") {
        // 退货单审批通过 = 同步过账 IN
        await reviewSalesReturn(row.id, action, reason || undefined);
      } else {
        await reviewOperationOrder(row.id, action, reason || undefined);
      }
      message.success(
        action === "APPROVE"
          ? row.biz === "SALES_ORDER"
            ? "已通过，请到「销货单」页点发货"
            : "已通过（涉及库存的已过账）"
          : "已驳回",
      );
      setReviewing(null);
      setReason("");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  const columns: TableColumnsType<PendingApproval> = [
    { title: "类型", dataIndex: "biz", key: "biz", width: 90,
      render: (value: PendingApproval["biz"], row) => (
        <Tag color={BIZ_TAG[value].color}>{row.op_label ?? row.biz_label}</Tag>
      ) },
    { title: "单号", dataIndex: "code", key: "code", width: 190,
      render: (code: string) => <Text strong>{code}</Text> },
    { title: "名称", dataIndex: "name", key: "name", width: 180, ellipsis: true },
    { title: "仓库 / 往来", key: "where", width: 160,
      render: (_v, row) => (
        <Text type="secondary">
          {row.warehouse_name
            ? `${row.warehouse_name}${row.to_warehouse_name ? ` → ${row.to_warehouse_name}` : ""}`
            : row.counterparty ?? "—"}
        </Text>
      ) },
    { title: "数量", dataIndex: "total_qty", key: "qty", width: 70, align: "right",
      render: (v: number) => <Num value={v} /> },
    { title: "金额", dataIndex: "total_amount", key: "amount", width: 90, align: "right",
      render: (v: number) => <Money value={v} /> },
    { title: "提交人", dataIndex: "submitter", key: "submitter", width: 110,
      render: (v: string | null) => v || "—" },
    { title: "提交时间", dataIndex: "submit_at", key: "submit_at", width: 170,
      render: (v: string | null) => (v ? v.replace("T", " ").slice(0, 19) : "—") },
    {
      title: "操作", key: "actions", width: 150, fixed: "right",
      render: (_v, row) => (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => void openDetail(row)}>查看</Button>
          <Button type="link" size="small"
                  onClick={() => { setReviewing({ row, action: "APPROVE" }); setReason(""); }}>
            审批
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">审批待办</h1>
        <Space>
          <Button onClick={state.reload}>刷新</Button>
        </Space>
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                     emptyText="没有待审批的单据">
        {(data) => (
          <Table<PendingApproval>
            rowKey={(row) => `${row.biz}-${row.id}`}
            columns={columns}
            dataSource={data.list}
            pagination={false}
            scroll={{ x: 1180 }}
          />
        )}
      </AsyncBoundary>

      <Drawer
        title={detail ? `${detail.row.biz_label}：${detail.row.code}` : "明细"}
        width={640}
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
      >
        <Table<BillLine>
          rowKey={(row, index) => String(row.id ?? index)}
          size="small"
          pagination={false}
          dataSource={detail?.lines ?? []}
          columns={[
            { title: "商品", dataIndex: "product_name_snap", key: "product" },
            { title: "数量", dataIndex: "qty", key: "qty", width: 90, align: "right" },
            { title: "单价", dataIndex: "price", key: "price", width: 100, align: "right",
              render: (v: number | null) => (v === null || v === undefined ? "—" : v.toFixed(2)) },
            { title: "序列号", dataIndex: "serial_number", key: "sn", width: 140,
              render: (v: string | null) => v || "—" },
          ]}
        />
      </Drawer>

      <Modal
        title={reviewing ? `审批 ${reviewing.row.code}` : "审批"}
        open={Boolean(reviewing)}
        onCancel={() => setReviewing(null)}
        onOk={() => void doReview()}
        okText="确定"
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          {reviewing?.row.biz === "PURCHASE_ORDER" ? (
            <Text>通过后订单可被入库单引用，暂不改动库存。</Text>
          ) : reviewing?.row.biz === "SALES_ORDER" ? (
            <Text>通过后订单进入可发货状态；<b>真正出库还要在「销货单」页点「发货」</b>，通过本身不动库存。</Text>
          ) : reviewing?.row.biz === "STOCKTAKE" ? (
            <Text type="warning">
              「通过」= 按实盘数过账：库存调整为正负 ADJUST 流水，整单原子，之后不能修改也不能作废。
            </Text>
          ) : (
            <Text type="warning">「通过」会立即过账并改动库存，且不可撤销（只能对流水做红字冲销）。</Text>
          )}
          <Select
            value={reviewing?.action}
            style={{ width: 180 }}
            onChange={(value) => setReviewing((prev) => (prev ? { ...prev, action: value } : prev))}
            options={[{ label: "通过", value: "APPROVE" }, { label: "驳回", value: "REJECT" }]}
          />
          {reviewing?.action === "REJECT" ? (
            <Input.TextArea rows={3} maxLength={200} showCount placeholder="驳回原因（必填）"
                            value={reason} onChange={(e) => setReason(e.target.value)} />
          ) : null}
        </Space>
      </Modal>
    </div>
  );
}
