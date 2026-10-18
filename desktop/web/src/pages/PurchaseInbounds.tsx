import { useMemo, useState } from "react";
import {
  App as AntdApp, Button, DatePicker, Drawer, Form, Input, InputNumber, Modal, Popconfirm,
  Select, Space, Table, Tag, Typography,
} from "antd";
import type { FormInstance, TableColumnsType } from "antd";
import { DownloadOutlined, PlusOutlined, PrinterOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import AsyncBoundary from "../components/AsyncBoundary";
import ProductSelect from "../components/ProductSelect";
import { useAsync } from "../hooks/useAsync";
import { usePrint } from "../hooks/usePrint";
import type { PaperKey } from "../api/print";
import { ApiError } from "../api/client";
import { listWarehouses } from "../api/master";
import {
  STATUS_META, cancelPurchaseInbound, createPurchaseInbound, exportBills, getPurchaseInbound,
  getPurchaseOrder, listPurchaseInbounds, listPurchaseOrders, reviewPurchaseInbound,
  submitPurchaseInbound, updatePurchaseInbound,
  type BillStatus, type PurchaseInbound,
} from "../api/wms";
import { isAdmin } from "../store/session";
import { Money, Num } from "../components/Values";

const { Text } = Typography;

interface LineForm {
  product_id: number;
  qty: number;
  price?: number;
  /** 引用采购订单明细时带上，用于回写「已收数量」和超收校验 */
  order_detail_id?: number;
}

interface InboundForm {
  name: string;
  order_id?: number;
  warehouse_id: number;
  in_date: Dayjs;
  remark?: string;
  lines: LineForm[];
}

/**
 * 采购入库单 —— docs/13 第 4.3 / 4.6 节 · docs/03 12'.3。
 *
 * ★审批通过 = **同步过账**：写 stock_logs（ref_type=PURCHASE_IN）+ 加库存 + 回写订单可收量。
 *   所以「审批通过」按钮上必须写清楚会改库存，不能让操作员以为只是盖个章。
 * ★已过账的单据整张只读：要更正只能对流水做红字冲销（账本不可变）。
 */
export default function PurchaseInbounds() {
  const admin = isAdmin();
  const { message } = AntdApp.useApp();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [page, setPage] = useState(1);

  // 打印（P0-3）：勾选批量打印 / 单行打印；纸张默认取设置，可临时切换
  const printer = usePrint();
  const [selected, setSelected] = useState<number[]>([]);
  const [paper, setPaper] = useState<PaperKey | null>(null);
  const paperKey = paper ?? printer.defaultPaper;

  const state = useAsync(() => listPurchaseInbounds({
    page,
    page_size: 20,
    status,
    keyword: keyword || undefined,
    date_from: range?.[0]?.format("YYYY-MM-DD"),
    date_to: range?.[1]?.format("YYYY-MM-DD"),
  }), [page, status, keyword, range]);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<PurchaseInbound | null>(null);
  const [readonly, setReadonly] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<InboundForm>();

  const [reviewing, setReviewing] = useState<{ row: PurchaseInbound; action: "APPROVE" | "REJECT" } | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const warehouses = useAsync(() => listWarehouses(false), []);
  const warehouseOptions = useMemo(
    () => (warehouses.data?.list ?? []).map((w) => ({
      label: w.level === 2 ? `　${w.name}` : w.name, value: w.id,
    })),
    [warehouses.data],
  );

  const approvedOrders = useAsync(() => listPurchaseOrders({ status: "APPROVED", page_size: 100 }), []);
  const orderOptions = useMemo(
    () => (approvedOrders.data?.list ?? []).map((o) => ({ label: `${o.code} ${o.name}`, value: o.id })),
    [approvedOrders.data],
  );

  function openCreate() {
    setEditing(null);
    setReadonly(false);
    form.resetFields();
    form.setFieldsValue({ in_date: dayjs(), lines: [] });
    setDrawerOpen(true);
  }

  async function openDetail(row: PurchaseInbound, asReadonly: boolean) {
    try {
      const full = await getPurchaseInbound(row.id);
      setEditing(full);
      setReadonly(asReadonly || Boolean(full.posted_at));
      form.setFieldsValue({
        name: full.name,
        order_id: full.order_id ?? undefined,
        warehouse_id: full.warehouse_id,
        in_date: dayjs(full.in_date),
        remark: full.remark ?? undefined,
        lines: (full.details ?? []).map((d) => ({
          product_id: d.product_id, qty: d.qty, price: d.price ?? undefined,
          order_detail_id: d.order_detail_id ?? undefined,
        })),
      });
      setDrawerOpen(true);
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  /** 选源订单：把「还剩多少没收」直接带成明细，省掉手工对行（这是现场最容易出错的一步） */
  async function pickOrder(orderId: number | undefined) {
    if (!orderId) {
      return;
    }
    try {
      const order = await getPurchaseOrder(orderId);
      const lines = (order.details ?? [])
        .map((d) => ({
          product_id: d.product_id,
          qty: Math.max(0, d.qty - d.received_qty),
          price: d.price ?? undefined,
          order_detail_id: d.id,
        }))
        .filter((line) => line.qty > 0);
      form.setFieldsValue({
        order_id: orderId,
        name: form.getFieldValue("name") || `${order.name} 入库`,
        lines,
      });
      if (lines.length === 0) {
        message.info("这张订单已经全部收满");
      }
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function onSubmit(values: InboundForm) {
    setSaving(true);
    try {
      const payload = {
        name: values.name,
        order_id: values.order_id ?? null,
        warehouse_id: values.warehouse_id,
        in_date: values.in_date.format("YYYY-MM-DD"),
        remark: values.remark ?? null,
        lines: (values.lines ?? []).map((line) => ({
          product_id: line.product_id,
          qty: line.qty,
          price: line.price ?? 0,
          order_detail_id: line.order_detail_id ?? null,
        })),
      };
      if (editing) {
        await updatePurchaseInbound(editing.id, payload);
        message.success("入库单已保存");
      } else {
        await createPurchaseInbound(payload);
        message.success("入库单已创建");
      }
      setDrawerOpen(false);
      state.reload();
    } catch (err) {
      applyFieldErrors(err, form);
    } finally {
      setSaving(false);
    }
  }

  function applyFieldErrors(err: unknown, target: FormInstance) {
    if (!(err instanceof ApiError)) {
      return;
    }
    const entries = Object.entries(err.fieldErrors);
    if (entries.length === 0) {
      message.error(err.message);
      return;
    }
    target.setFields(entries.map(([name, errors]) => ({
      name: name.replace(/\[(\d+)\]/g, ".$1").split("."),
      errors,
    })));
  }

  async function doSubmit(row: PurchaseInbound) {
    try {
      await submitPurchaseInbound(row.id);
      message.success("已提交，等待审批");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doCancel(row: PurchaseInbound) {
    try {
      await cancelPurchaseInbound(row.id);
      message.success("已作废");
      state.reload();
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
    if (reviewing.action === "REJECT" && !rejectReason.trim()) {
      message.warning("驳回必须填写原因");
      return;
    }
    try {
      const result = await reviewPurchaseInbound(reviewing.row.id, reviewing.action, rejectReason || undefined);
      if (result.posted_at) {
        message.success(`已过账：库存已增加 ${result.total_qty}，可在流水里查到`);
      } else {
        message.success("已驳回");
      }
      setReviewing(null);
      setRejectReason("");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doExport() {
    try {
      await exportBills("purchase-inbounds", {
        status, keyword: keyword || undefined,
        date_from: range?.[0]?.format("YYYY-MM-DD"),
        date_to: range?.[1]?.format("YYYY-MM-DD"),
      });
      message.success("已导出 CSV");
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  const columns: TableColumnsType<PurchaseInbound> = [
    { title: "单号", dataIndex: "code", key: "code", width: 190,
      render: (code: string) => <Text strong>{code}</Text> },
    { title: "名称", dataIndex: "name", key: "name", ellipsis: true },
    { title: "源订单", dataIndex: "order_code_snap", key: "order", width: 160,
      render: (v: string | null) => v || <Text type="secondary">无源单</Text> },
    { title: "入库仓库", dataIndex: "warehouse_name", key: "warehouse", width: 110 },
    { title: "入库日期", dataIndex: "in_date", key: "date", width: 110 },
    { title: "数量", dataIndex: "total_qty", key: "qty", width: 80, align: "right",
      render: (v: number) => <Num value={v} /> },
    { title: "金额", dataIndex: "total_amount", key: "amount", width: 100, align: "right",
      render: (v: number) => <Money value={v} /> },
    { title: "状态", dataIndex: "status", key: "status", width: 100,
      // 过账与否比「已审批」更关键：已过账的单据整张只读，别再让用户点进去才发现
      render: (value: BillStatus, row) => (row.posted_at
        ? <Tag color="green">已过账</Tag>
        : <Tag color={STATUS_META[value].color}>{STATUS_META[value].label}</Tag>) },
    {
      title: "操作", key: "actions", width: 180, fixed: "right",
      render: (_v, row) => (
        <Space size={0} wrap>
          <Button type="link" size="small" onClick={() => void openDetail(row, true)}>查看</Button>
          <Button type="link" size="small" icon={<PrinterOutlined />} loading={printer.busy}
                  onClick={() => void printer.print("purchase-inbounds", row.id, { paper: paperKey })}>
            打印
          </Button>
          {!row.posted_at && (row.status === "DRAFT" || row.status === "REJECTED") ? (
            <>
              <Button type="link" size="small" onClick={() => void openDetail(row, false)}>编辑</Button>
              <Button type="link" size="small" onClick={() => void doSubmit(row)}>提交</Button>
            </>
          ) : null}
          {admin && row.status === "SUBMITTED" ? (
            <Button type="link" size="small"
                    onClick={() => { setReviewing({ row, action: "APPROVE" }); setRejectReason(""); }}>
              审批
            </Button>
          ) : null}
          {admin && !row.posted_at && ["DRAFT", "REJECTED", "SUBMITTED"].includes(row.status) ? (
            <Popconfirm title="作废后不能再提交，确定？" onConfirm={() => void doCancel(row)}>
              <Button type="link" size="small" danger>作废</Button>
            </Popconfirm>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">采购入库</h1>
        <Space wrap>
          <Input.Search allowClear placeholder="单号 / 名称" style={{ width: 200 }}
                        onSearch={(value) => { setPage(1); setKeyword(value.trim()); }} />
          <Select allowClear placeholder="状态" style={{ width: 130 }} value={status}
                  onChange={(value) => { setPage(1); setStatus(value); }}
                  options={["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CANCELED"]
                    .map((key) => ({ label: STATUS_META[key as BillStatus].label, value: key }))} />
          <DatePicker.RangePicker value={range}
                                  onChange={(value) => { setPage(1); setRange(value as [Dayjs, Dayjs] | null); }} />
          <Select style={{ width: 190 }} value={paperKey} onChange={(value) => setPaper(value)}
                  options={printer.papers.map((p) => ({ label: p.label, value: p.key }))} />
          <Button icon={<PrinterOutlined />} disabled={selected.length === 0} loading={printer.busy}
                  onClick={() => void printer.print("purchase-inbounds", selected, { paper: paperKey })}>
            打印所选{selected.length > 0 ? `（${selected.length}）` : ""}
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => void doExport()}>导出</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建入库单</Button>
        </Space>
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                     emptyText="还没有采购入库单">
        {(data) => (
          <Table<PurchaseInbound>
            rowKey="id"
            columns={columns}
            dataSource={data.list}
            rowSelection={{
              selectedRowKeys: selected,
              onChange: (keys) => setSelected(keys as number[]),
            }}
            scroll={{ x: 1140 }}
            pagination={{
              current: data.page, pageSize: data.page_size, total: data.total,
              showSizeChanger: false, onChange: setPage,
              showTotal: (total) => `共 ${total} 张`,
            }}
          />
        )}
      </AsyncBoundary>

      <Drawer
        title={editing ? `${readonly ? "查看" : "编辑"}入库单：${editing.code}` : "新建采购入库单"}
        width={780}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        destroyOnClose
        extra={readonly ? null : (
          <Space>
            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            <Button type="primary" loading={saving} onClick={() => form.submit()}>保存</Button>
          </Space>
        )}
      >
        <Form<InboundForm> form={form} layout="vertical" onFinish={onSubmit} requiredMark={false}
                           disabled={readonly}>
          <Space size={12} style={{ width: "100%" }} align="start" wrap>
            <Form.Item name="name" label="单据名称" style={{ minWidth: 220 }}
                       rules={[{ required: true, message: "请输入单据名称" }]}>
              <Input maxLength={60} placeholder="例如：10 月第一批到货" />
            </Form.Item>
            <Form.Item name="order_id" label="来源采购订单（选填）" style={{ minWidth: 240 }}>
              <Select allowClear showSearch optionFilterProp="label" placeholder="不引用订单也可以入库"
                      options={orderOptions} loading={approvedOrders.loading}
                      onChange={(value) => void pickOrder(value)} />
            </Form.Item>
            <Form.Item name="warehouse_id" label="入库仓库" style={{ minWidth: 180 }}
                       rules={[{ required: true, message: "请选择仓库" }]}>
              <Select placeholder="选择仓库" options={warehouseOptions} loading={warehouses.loading} />
            </Form.Item>
            <Form.Item name="in_date" label="入库日期"
                       rules={[{ required: true, message: "请选择日期" }]}>
              <DatePicker style={{ width: 150 }} />
            </Form.Item>
          </Space>

          <Form.List name="lines">
            {(fields, { add, remove }) => (
              <div>
                <Space style={{ marginBottom: 8 }}>
                  <Text strong>入库明细</Text>
                  {readonly ? null : (
                    <Button size="small" type="dashed" onClick={() => add({ qty: 1, price: 0 })}>
                      添加一行
                    </Button>
                  )}
                </Space>
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" style={{ display: "flex", marginBottom: 8 }}>
                    <Form.Item name={[field.name, "order_detail_id"]} hidden>
                      <InputNumber />
                    </Form.Item>
                    <Form.Item name={[field.name, "product_id"]} style={{ width: 320, marginBottom: 0 }}
                               rules={[{ required: true, message: "请选择商品" }]}>
                      <ProductSelect initial={null} />
                    </Form.Item>
                    <Form.Item name={[field.name, "qty"]} style={{ width: 110, marginBottom: 0 }}
                               rules={[{ required: true, message: "数量" }]}>
                      <InputNumber min={1} max={100000} style={{ width: "100%" }} placeholder="数量" />
                    </Form.Item>
                    <Form.Item name={[field.name, "price"]} style={{ width: 120, marginBottom: 0 }}>
                      <InputNumber min={0} precision={2} style={{ width: "100%" }} placeholder="单价" />
                    </Form.Item>
                    {readonly ? null : (
                      <Button type="link" danger onClick={() => remove(field.name)}>删除</Button>
                    )}
                  </Space>
                ))}
                {fields.length === 0 ? <Text type="secondary">还没有明细行</Text> : null}
              </div>
            )}
          </Form.List>

          <Form.Item name="remark" label="备注" style={{ marginTop: 12 }}>
            <Input.TextArea rows={2} maxLength={200} showCount />
          </Form.Item>
        </Form>
      </Drawer>

      {printer.element}

      <Modal
        title={reviewing ? `审批 ${reviewing.row.code}` : "审批"}
        open={Boolean(reviewing)}
        onCancel={() => setReviewing(null)}
        onOk={() => void doReview()}
        okText="确定"
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text type="warning">「通过」会立即过账：库存增加、流水新增一条 PURCHASE_IN，不可撤销（只能冲销）。</Text>
          <Select
            value={reviewing?.action}
            style={{ width: 160 }}
            onChange={(value) => setReviewing((prev) => (prev ? { ...prev, action: value } : prev))}
            options={[{ label: "通过并过账", value: "APPROVE" }, { label: "驳回", value: "REJECT" }]}
          />
          {reviewing?.action === "REJECT" ? (
            <Input.TextArea rows={3} maxLength={200} showCount placeholder="驳回原因（必填）"
                            value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} />
          ) : null}
        </Space>
      </Modal>
    </div>
  );
}
