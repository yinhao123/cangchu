import { useMemo, useState } from "react";
import {
  App as AntdApp, Alert, Button, DatePicker, Drawer, Form, Input, InputNumber, Modal, Popconfirm,
  Select, Space, Table, Tag, Typography,
} from "antd";
import type { FormInstance, TableColumnsType } from "antd";
import { DownloadOutlined, PlusOutlined, PrinterOutlined, SendOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import AsyncBoundary from "../components/AsyncBoundary";
import ProductSelect from "../components/ProductSelect";
import { useAsync } from "../hooks/useAsync";
import { usePrint } from "../hooks/usePrint";
import type { PaperKey } from "../api/print";
import { ApiError } from "../api/client";
import { listPartners, listWarehouses } from "../api/master";
import { STATUS_META } from "../api/wms";
import {
  cancelSalesOrder, createSalesOrder, exportSalesBills, getSalesOrder, listSalesOrders,
  newShipmentId, reviewSalesOrder, shipSalesOrder, submitSalesOrder, updateSalesOrder,
  type SalesOrder,
} from "../api/sales";
import { isAdmin } from "../store/session";
import { Money, Num } from "../components/Values";

const { Text } = Typography;

interface LineForm {
  product_id: number;
  qty: number;
  price?: number;
}

interface OrderForm {
  name: string;
  customer_id: number;
  warehouse_id: number;
  order_date: Dayjs;
  remark?: string;
  lines: LineForm[];
}

/**
 * 销货单 —— docs/14 第 6 章 · docs/03 12'''''。
 *
 * ★审批通过 ≠ 已出库：审批只改状态，必须点「发货」才扣库存（可多次，部分发货）。
 * ★发货的幂等键在「点开发货框」那一刻生成，重试沿用同一个值 —— 网络抖动不会扣两次库存。
 */
export default function SalesOrders() {
  const admin = isAdmin();
  const { message } = AntdApp.useApp();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [customerId, setCustomerId] = useState<number | undefined>();
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [page, setPage] = useState(1);

  const printer = usePrint();
  const [selected, setSelected] = useState<number[]>([]);
  const [paper, setPaper] = useState<PaperKey | null>(null);
  const paperKey = paper ?? printer.defaultPaper;

  const state = useAsync(() => listSalesOrders({
    page,
    page_size: 20,
    status,
    keyword: keyword || undefined,
    customer_id: customerId,
    date_from: range?.[0]?.format("YYYY-MM-DD"),
    date_to: range?.[1]?.format("YYYY-MM-DD"),
  }), [page, status, keyword, customerId, range]);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<SalesOrder | null>(null);
  const [readonly, setReadonly] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<OrderForm>();

  const [reviewing, setReviewing] = useState<{ order: SalesOrder; action: "APPROVE" | "REJECT" } | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  // 发货：可部分发；shipmentId 在打开弹窗时生成，重试沿用
  const [shipping, setShipping] = useState<SalesOrder | null>(null);
  const [shipmentId, setShipmentId] = useState("");
  const [shipQtys, setShipQtys] = useState<Record<number, number>>({});
  const [shipBusy, setShipBusy] = useState(false);

  const customers = useAsync(() => listPartners("CUSTOMER"), []);
  const customerOptions = useMemo(
    () => (customers.data?.list ?? []).map((p) => ({ label: p.name, value: p.id })),
    [customers.data],
  );
  const warehouses = useAsync(() => listWarehouses(), []);
  const warehouseOptions = useMemo(
    () => (warehouses.data?.list ?? []).map((w) => ({ label: w.name, value: w.id })),
    [warehouses.data],
  );
  const defaultWarehouseId = useMemo(
    () => (warehouses.data?.list ?? [])[0]?.id,
    [warehouses.data],
  );

  function openCreate() {
    setEditing(null);
    setReadonly(false);
    form.resetFields();
    form.setFieldsValue({
      order_date: dayjs(),
      lines: [],
      warehouse_id: defaultWarehouseId,
    });
    setDrawerOpen(true);
  }

  async function openEdit(row: SalesOrder, asReadonly: boolean) {
    try {
      const full = await getSalesOrder(row.id);
      setEditing(full);
      setReadonly(asReadonly);
      form.setFieldsValue({
        name: full.name,
        customer_id: full.customer_id ?? undefined,
        warehouse_id: full.warehouse_id,
        order_date: dayjs(full.order_date),
        remark: full.remark ?? undefined,
        lines: (full.details ?? []).map((d) => ({
          product_id: d.product_id, qty: d.qty, price: d.price ?? undefined,
        })),
      });
      setDrawerOpen(true);
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
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

  async function onSubmit(values: OrderForm) {
    setSaving(true);
    try {
      const payload = {
        name: values.name,
        customer_id: values.customer_id,
        warehouse_id: values.warehouse_id,
        order_date: values.order_date.format("YYYY-MM-DD"),
        remark: values.remark ?? null,
        lines: (values.lines ?? []).map((line) => ({
          product_id: line.product_id,
          qty: line.qty,
          price: line.price ?? null,
        })),
      };
      if (editing) {
        await updateSalesOrder(editing.id, payload);
        message.success("销货单已保存");
      } else {
        await createSalesOrder(payload);
        message.success("销货单已创建");
      }
      setDrawerOpen(false);
      state.reload();
    } catch (err) {
      applyFieldErrors(err, form);
    } finally {
      setSaving(false);
    }
  }

  async function doSubmit(row: SalesOrder) {
    try {
      await submitSalesOrder(row.id);
      message.success("已提交，等待审批");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doCancel(row: SalesOrder) {
    try {
      await cancelSalesOrder(row.id);
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
      const result = await reviewSalesOrder(reviewing.order.id, reviewing.action, rejectReason || undefined);
      message.success(result.status === "APPROVED" ? "已审批通过，可以发货了" : "已驳回");
      setReviewing(null);
      setRejectReason("");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function openShip(row: SalesOrder) {
    try {
      const full = await getSalesOrder(row.id);
      const todo = (full.details ?? []).filter((d) => d.remaining_qty > 0);
      if (todo.length === 0) {
        message.info("这张单已经发满了");
        return;
      }
      setShipping(full);
      setShipmentId(newShipmentId());
      setShipQtys(Object.fromEntries(todo.map((d) => [d.id, d.remaining_qty])));
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doShip() {
    if (!shipping) {
      return;
    }
    const lines = Object.entries(shipQtys)
      .map(([detailId, qty]) => ({ detail_id: Number(detailId), qty: Number(qty) || 0 }))
      .filter((line) => line.qty > 0);
    if (lines.length === 0) {
      message.warning("请填写本次发货数量");
      return;
    }
    setShipBusy(true);
    try {
      const result = await shipSalesOrder(shipping.id, shipmentId, lines);
      const duplicated = result.shipment?.duplicated;
      message.success(duplicated ? "这一批已经发过，未重复扣减库存" : "已出库");
      setShipping(null);
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        // 超发会带 receivable：直接提示真实可发量，避免用户反复试
        const receivable = err.payload.receivable;
        if (typeof receivable === "number") {
          message.error(`最多还能发 ${receivable}`);
        } else {
          message.error(err.message);
        }
      }
    } finally {
      setShipBusy(false);
    }
  }

  async function doExport() {
    try {
      await exportSalesBills("sales-orders", {
        status, keyword: keyword || undefined, customer_id: customerId,
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

  const columns: TableColumnsType<SalesOrder> = [
    { title: "单号", dataIndex: "code", key: "code", width: 190,
      render: (code: string) => <Text strong>{code}</Text> },
    { title: "名称", dataIndex: "name", key: "name", ellipsis: true },
    { title: "客户", dataIndex: "customer_name_snap", key: "customer",
      render: (v: string | null) => v || <Text type="secondary">—</Text> },
    { title: "出库仓库", dataIndex: "warehouse_name", key: "warehouse", width: 120,
      render: (v: string | null) => v || <Text type="secondary">—</Text> },
    { title: "销货日期", dataIndex: "order_date", key: "date", width: 110,
      render: (v: string) => String(v).slice(0, 10) },
    { title: "数量", dataIndex: "total_qty", key: "qty", width: 80, align: "right",
      render: (v: number) => <Num value={v} /> },
    { title: "金额", dataIndex: "total_amount", key: "amount", width: 100, align: "right",
      render: (v: number) => <Money value={v} /> },
    { title: "状态", dataIndex: "status", key: "status", width: 100,
      render: (value: SalesOrder["status"]) => (
        <Tag color={STATUS_META[value].color}>{STATUS_META[value].label}</Tag>
      ) },
    {
      title: "操作", key: "actions", width: 250, fixed: "right",
      render: (_v, row) => (
        <Space size={0} wrap>
          <Button type="link" size="small" onClick={() => void openEdit(row, true)}>查看</Button>
          <Button type="link" size="small" icon={<PrinterOutlined />} loading={printer.busy}
                  onClick={() => void printer.print("sales-orders", row.id, { paper: paperKey })}>
            打印
          </Button>
          {row.status === "DRAFT" || row.status === "REJECTED" ? (
            <>
              <Button type="link" size="small" onClick={() => void openEdit(row, false)}>编辑</Button>
              <Button type="link" size="small" onClick={() => void doSubmit(row)}>提交</Button>
            </>
          ) : null}
          {admin && row.status === "SUBMITTED" ? (
            <Button type="link" size="small"
                    onClick={() => { setReviewing({ order: row, action: "APPROVE" }); setRejectReason(""); }}>
              审批
            </Button>
          ) : null}
          {row.status === "APPROVED" ? (
            <Button type="link" size="small" icon={<SendOutlined />} onClick={() => void openShip(row)}>
              发货
            </Button>
          ) : null}
          {admin && ["DRAFT", "REJECTED", "SUBMITTED"].includes(row.status) ? (
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
        <h1 className="sd-page-title">销货单</h1>
        <Space wrap>
          <Input.Search allowClear placeholder="单号 / 名称" style={{ width: 190 }}
                        onSearch={(value) => { setPage(1); setKeyword(value.trim()); }} />
          <Select allowClear showSearch optionFilterProp="label" placeholder="客户"
                  style={{ width: 170 }} value={customerId}
                  onChange={(value) => { setPage(1); setCustomerId(value); }}
                  options={customerOptions} loading={customers.loading} />
          <Select allowClear placeholder="状态" style={{ width: 120 }} value={status}
                  onChange={(value) => { setPage(1); setStatus(value); }}
                  options={Object.entries(STATUS_META)
                    .filter(([key]) => ["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CLOSED", "CANCELED"].includes(key))
                    .map(([key, meta]) => ({ label: meta.label, value: key }))} />
          <DatePicker.RangePicker value={range}
                                  onChange={(value) => { setPage(1); setRange(value as [Dayjs, Dayjs] | null); }} />
          <Select style={{ width: 150 }} value={paperKey} onChange={(value) => setPaper(value)}
                  options={printer.papers.map((p) => ({ label: p.label, value: p.key }))} />
          <Button icon={<PrinterOutlined />} disabled={selected.length === 0} loading={printer.busy}
                  onClick={() => void printer.print("sales-orders", selected, { paper: paperKey })}>
            打印所选{selected.length > 0 ? `（${selected.length}）` : ""}
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => void doExport()}>导出</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建销货单</Button>
        </Space>
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                     emptyText="还没有销货单">
        {(data) => (
          <Table<SalesOrder>
            rowKey="id"
            columns={columns}
            dataSource={data.list}
            scroll={{ x: 1280 }}
            rowSelection={{
              selectedRowKeys: selected,
              onChange: (keys) => setSelected(keys as number[]),
            }}
            pagination={{
              current: data.page, pageSize: data.page_size, total: data.total,
              showSizeChanger: false, onChange: setPage,
              showTotal: (total) => `共 ${total} 张`,
            }}
          />
        )}
      </AsyncBoundary>

      <Drawer
        title={editing ? `${readonly ? "查看" : "编辑"}销货单：${editing.code}` : "新建销货单"}
        width={800}
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
        <Form<OrderForm> form={form} layout="vertical" onFinish={onSubmit} requiredMark={false}
                         disabled={readonly}>
          <Space size={12} style={{ width: "100%" }} align="start" wrap>
            <Form.Item name="name" label="单据名称" style={{ minWidth: 220 }}
                       rules={[{ required: true, message: "请输入单据名称" }]}>
              <Input maxLength={60} placeholder="例如：张三 10 月订单" />
            </Form.Item>
            <Form.Item name="customer_id" label="客户" style={{ minWidth: 190 }}
                       rules={[{ required: true, message: "请选择客户" }]}>
              <Select showSearch optionFilterProp="label" placeholder="选择客户"
                      options={customerOptions} loading={customers.loading} />
            </Form.Item>
            <Form.Item name="warehouse_id" label="出库仓库" style={{ minWidth: 160 }}
                       rules={[{ required: true, message: "请选择出库仓库" }]}>
              <Select placeholder="发货从哪个仓扣" options={warehouseOptions}
                      loading={warehouses.loading} />
            </Form.Item>
            <Form.Item name="order_date" label="销货日期"
                       rules={[{ required: true, message: "请选择日期" }]}>
              <DatePicker style={{ width: 150 }} />
            </Form.Item>
          </Space>

          <Form.List name="lines">
            {(fields, { add, remove }) => (
              <div>
                <Space style={{ marginBottom: 8 }}>
                  <Text strong>销售明细</Text>
                  {readonly ? null : (
                    <Button size="small" type="dashed" onClick={() => add({ product_id: 0, qty: 1 })}>
                      添加一行
                    </Button>
                  )}
                  <Text type="secondary">单价留空 = 取商品售价</Text>
                </Space>
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" style={{ display: "flex", marginBottom: 8 }}>
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

          {readonly && editing?.reject_reason ? (
            <Text type="danger">驳回原因：{editing.reject_reason}</Text>
          ) : null}
          {readonly && editing ? (
            <div style={{ marginTop: 8 }}>
              <Text type="secondary">
                已发 {editing.shipped_qty ?? 0} / 合计 {editing.total_qty}
                {editing.remaining_qty ? `，未发 ${editing.remaining_qty}` : "（已发满）"}
              </Text>
            </div>
          ) : null}
        </Form>
      </Drawer>

      {printer.element}

      <Modal
        title={shipping ? `发货：${shipping.code}` : "发货"}
        open={Boolean(shipping)}
        onCancel={() => setShipping(null)}
        onOk={() => void doShip()}
        confirmLoading={shipBusy}
        okText="确认出库"
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Alert type="info" showIcon
                 message={`从「${shipping?.warehouse_name ?? "仓库"}」出库，出库后库存立即减少`} />
          {(shipping?.details ?? []).filter((d) => d.remaining_qty > 0).map((d) => (
            <Space key={d.id} style={{ display: "flex", justifyContent: "space-between" }}>
              <Text ellipsis style={{ maxWidth: 320 }}>{d.product_name_snap}</Text>
              <Space>
                <Text type="secondary">未发 {d.remaining_qty}</Text>
                <InputNumber min={0} max={d.remaining_qty} value={shipQtys[d.id]}
                             onChange={(value) => setShipQtys((prev) => ({ ...prev, [d.id]: Number(value) || 0 }))}
                             style={{ width: 100 }} />
              </Space>
            </Space>
          ))}
          <Text type="secondary">允许分批发货；全部发满后单据自动关闭。</Text>
        </Space>
      </Modal>

      <Modal
        title={reviewing ? `审批 ${reviewing.order.code}` : "审批"}
        open={Boolean(reviewing)}
        onCancel={() => setReviewing(null)}
        onOk={() => void doReview()}
        okText="确定"
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text>通过后还需点「发货」才扣库存；驳回需要写明原因。</Text>
          <Select
            value={reviewing?.action}
            style={{ width: 160 }}
            onChange={(value) => setReviewing((prev) => (prev ? { ...prev, action: value } : prev))}
            options={[{ label: "通过", value: "APPROVE" }, { label: "驳回", value: "REJECT" }]}
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
