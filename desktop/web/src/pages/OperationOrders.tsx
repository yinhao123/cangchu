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
  OP_TYPE_META, STATUS_META, cancelOperationOrder, createOperationOrder, exportBills,
  getOperationOrder, listOperationOrders, reverseOperationOrder, reviewOperationOrder,
  submitOperationOrder, updateOperationOrder,
  type BillStatus, type OpType, type OperationOrder,
} from "../api/wms";
import { isAdmin } from "../store/session";
import { Money, Num } from "../components/Values";

const { Text } = Typography;

interface LineForm {
  product_id: number;
  qty: number;
  price?: number;
  serial_number?: string;
}

interface OrderForm {
  name: string;
  op_type: OpType;
  warehouse_id: number;
  to_warehouse_id?: number;
  op_date: Dayjs;
  expect_return_date?: Dayjs;
  remark?: string;
  lines: LineForm[];
}

/**
 * 领用 / 退库 / 归还 / 借用 / 调拨 —— docs/13 第 4.4 / 4.6 节 · docs/03 12'.4。
 *
 * ★五种单据一套界面，区别只在「过账方向」：领用/借用扣库存，退库/归还加库存，调拨一扣一加。
 *   方向写死在服务端映射表里，前端不参与判断（否则会出现「界面说加、后端在减」）。
 * ★冲销不是删单：对已过账流水逐条红字反向，单据留在列表里标「已冲销」。
 */
export default function OperationOrders() {
  const admin = isAdmin();
  const { message } = AntdApp.useApp();
  const [opType, setOpType] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);

  // 打印（P0-3）：签字栏按 op_type 由服务端下发（领用与调拨责任人不同）
  const printer = usePrint();
  const [selected, setSelected] = useState<number[]>([]);
  const [paper, setPaper] = useState<PaperKey | null>(null);
  const paperKey = paper ?? printer.defaultPaper;

  const state = useAsync(() => listOperationOrders({
    page, page_size: 20, op_type: opType, status, keyword: keyword || undefined,
  }), [page, opType, status, keyword]);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<OperationOrder | null>(null);
  const [readonly, setReadonly] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<OrderForm>();
  const currentType = Form.useWatch("op_type", form) as OpType | undefined;

  const [reviewing, setReviewing] = useState<{ row: OperationOrder; action: "APPROVE" | "REJECT" } | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [reversing, setReversing] = useState<OperationOrder | null>(null);
  const [reverseReason, setReverseReason] = useState("");

  const warehouses = useAsync(() => listWarehouses(false), []);
  const warehouseOptions = useMemo(
    () => (warehouses.data?.list ?? []).map((w) => ({
      label: w.level === 2 ? `　${w.name}` : w.name, value: w.id,
    })),
    [warehouses.data],
  );

  function openCreate() {
    setEditing(null);
    setReadonly(false);
    form.resetFields();
    form.setFieldsValue({ op_date: dayjs(), op_type: "RECEIVE", lines: [] });
    setDrawerOpen(true);
  }

  async function openDetail(row: OperationOrder, asReadonly: boolean) {
    try {
      const full = await getOperationOrder(row.id);
      setEditing(full);
      setReadonly(asReadonly || Boolean(full.posted_at));
      form.setFieldsValue({
        name: full.name,
        op_type: full.op_type,
        warehouse_id: full.warehouse_id,
        to_warehouse_id: full.to_warehouse_id ?? undefined,
        op_date: dayjs(full.op_date),
        expect_return_date: full.expect_return_date ? dayjs(full.expect_return_date) : undefined,
        remark: full.remark ?? undefined,
        lines: (full.details ?? []).map((d) => ({
          product_id: d.product_id, qty: d.qty, price: d.price ?? undefined,
          serial_number: d.serial_number ?? undefined,
        })),
      });
      setDrawerOpen(true);
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function onSubmit(values: OrderForm) {
    setSaving(true);
    try {
      const payload = {
        name: values.name,
        op_type: values.op_type,
        warehouse_id: values.warehouse_id,
        to_warehouse_id: values.op_type === "TRANSFER" ? values.to_warehouse_id ?? null : null,
        op_date: values.op_date.format("YYYY-MM-DD"),
        expect_return_date: values.op_type === "BORROW" && values.expect_return_date
          ? values.expect_return_date.format("YYYY-MM-DD")
          : null,
        remark: values.remark ?? null,
        lines: (values.lines ?? []).map((line) => ({
          product_id: line.product_id,
          qty: line.qty,
          price: line.price ?? null,
          serial_number: line.serial_number ?? null,
        })),
      };
      if (editing) {
        await updateOperationOrder(editing.id, payload);
        message.success("单据已保存");
      } else {
        await createOperationOrder(payload);
        message.success("单据已创建");
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

  async function doSubmit(row: OperationOrder) {
    try {
      const result = await submitOperationOrder(row.id);
      // 免审模式（wms_operation_approve_required=false）下提交即过账
      message.success(result.posted_at ? "已提交并过账" : "已提交，等待审批");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doCancel(row: OperationOrder) {
    try {
      await cancelOperationOrder(row.id);
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
      await reviewOperationOrder(reviewing.row.id, reviewing.action, rejectReason || undefined);
      message.success(reviewing.action === "APPROVE" ? "已通过并过账" : "已驳回");
      setReviewing(null);
      setRejectReason("");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doReverse() {
    if (!reversing) {
      return;
    }
    if (!reverseReason.trim()) {
      message.warning("请填写冲销原因");
      return;
    }
    try {
      await reverseOperationOrder(reversing.id, reverseReason.trim());
      message.success("已冲销：库存已还原，原流水保留并标记");
      setReversing(null);
      setReverseReason("");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doExport() {
    try {
      await exportBills("operation-orders", { op_type: opType, status, keyword: keyword || undefined });
      message.success("已导出 CSV");
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  const columns: TableColumnsType<OperationOrder> = [
    { title: "单号", dataIndex: "code", key: "code", width: 190,
      render: (code: string) => <Text strong>{code}</Text> },
    { title: "类型", dataIndex: "op_type", key: "op_type", width: 90,
      render: (value: OpType) => <Tag>{OP_TYPE_META[value].label}</Tag> },
    { title: "名称", dataIndex: "name", key: "name", ellipsis: true },
    { title: "仓库", key: "warehouse", width: 180,
      render: (_v, row) => (
        <Text>
          {row.warehouse_name}
          {row.to_warehouse_id ? ` → ${row.to_warehouse_name ?? ""}` : ""}
        </Text>
      ) },
    { title: "日期", dataIndex: "op_date", key: "op_date", width: 110 },
    { title: "数量", dataIndex: "total_qty", key: "qty", width: 80, align: "right",
      render: (v: number) => <Num value={v} /> },
    { title: "金额", dataIndex: "total_amount", key: "amount", width: 100, align: "right",
      render: (v: number) => <Money value={v} /> },
    { title: "状态", dataIndex: "status", key: "status", width: 90,
      render: (value: BillStatus) => <Tag color={STATUS_META[value].color}>{STATUS_META[value].label}</Tag> },
    {
      title: "操作", key: "actions", width: 180, fixed: "right",
      render: (_v, row) => (
        <Space size={0} wrap>
          <Button type="link" size="small" onClick={() => void openDetail(row, true)}>查看</Button>
          <Button type="link" size="small" icon={<PrinterOutlined />} loading={printer.busy}
                  onClick={() => void printer.print("operation-orders", row.id, { paper: paperKey })}>
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
          {admin && row.status === "DONE" && !row.reversed_at ? (
            <Button type="link" size="small" danger
                    onClick={() => { setReversing(row); setReverseReason(""); }}>
              冲销
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
        <h1 className="sd-page-title">领用 / 退库 / 借用 / 调拨</h1>
        <Space wrap>
          <Select allowClear placeholder="类型" style={{ width: 120 }} value={opType}
                  onChange={(value) => { setPage(1); setOpType(value); }}
                  options={Object.entries(OP_TYPE_META).map(([key, meta]) => ({ label: meta.label, value: key }))} />
          <Select allowClear placeholder="状态" style={{ width: 130 }} value={status}
                  onChange={(value) => { setPage(1); setStatus(value); }}
                  options={["DRAFT", "SUBMITTED", "REJECTED", "DONE", "REVERSED", "CANCELED"]
                    .map((key) => ({ label: STATUS_META[key as BillStatus].label, value: key }))} />
          <Input.Search allowClear placeholder="单号 / 名称" style={{ width: 200 }}
                        onSearch={(value) => { setPage(1); setKeyword(value.trim()); }} />
          <Select style={{ width: 190 }} value={paperKey} onChange={(value) => setPaper(value)}
                  options={printer.papers.map((p) => ({ label: p.label, value: p.key }))} />
          <Button icon={<PrinterOutlined />} disabled={selected.length === 0} loading={printer.busy}
                  onClick={() => void printer.print("operation-orders", selected, { paper: paperKey })}>
            打印所选{selected.length > 0 ? `（${selected.length}）` : ""}
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => void doExport()}>导出</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建单据</Button>
        </Space>
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                     emptyText="还没有领用/退库等单据">
        {(data) => (
          <Table<OperationOrder>
            rowKey="id"
            columns={columns}
            dataSource={data.list}
            rowSelection={{
              selectedRowKeys: selected,
              onChange: (keys) => setSelected(keys as number[]),
            }}
            scroll={{ x: 1120 }}
            pagination={{
              current: data.page, pageSize: data.page_size, total: data.total,
              showSizeChanger: false, onChange: setPage,
              showTotal: (total) => `共 ${total} 张`,
            }}
          />
        )}
      </AsyncBoundary>

      <Drawer
        title={editing ? `${readonly ? "查看" : "编辑"}单据：${editing.code}` : "新建单据"}
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
        <Form<OrderForm> form={form} layout="vertical" onFinish={onSubmit} requiredMark={false}
                         disabled={readonly}>
          <Space size={12} style={{ width: "100%" }} align="start" wrap>
            <Form.Item name="op_type" label="单据类型" style={{ minWidth: 140 }}
                       rules={[{ required: true, message: "请选择类型" }]}>
              <Select disabled={Boolean(editing)} options={Object.entries(OP_TYPE_META)
                .map(([key, meta]) => ({ label: meta.label, value: key }))} />
            </Form.Item>
            <Form.Item name="name" label="单据名称" style={{ minWidth: 200 }}
                       rules={[{ required: true, message: "请输入名称" }]}>
              <Input maxLength={60} placeholder="例如：生产部领用" />
            </Form.Item>
            <Form.Item name="warehouse_id" label={currentType === "TRANSFER" ? "调出仓库" : "仓库"}
                       style={{ minWidth: 170 }}
                       rules={[{ required: true, message: "请选择仓库" }]}>
              <Select placeholder="选择仓库" options={warehouseOptions} loading={warehouses.loading} />
            </Form.Item>
            {currentType === "TRANSFER" ? (
              <Form.Item name="to_warehouse_id" label="调入仓库" style={{ minWidth: 170 }}
                         rules={[{ required: true, message: "调拨必须选调入仓库" }]}>
                <Select placeholder="选择仓库" options={warehouseOptions} loading={warehouses.loading} />
              </Form.Item>
            ) : null}
            <Form.Item name="op_date" label="日期" rules={[{ required: true, message: "请选择日期" }]}>
              <DatePicker style={{ width: 150 }} />
            </Form.Item>
            {currentType === "BORROW" ? (
              <Form.Item name="expect_return_date" label="预计归还日期"
                         rules={[{ required: true, message: "借用必须填归还日期" }]}>
                <DatePicker style={{ width: 150 }} />
              </Form.Item>
            ) : null}
          </Space>

          <Form.List name="lines">
            {(fields, { add, remove }) => (
              <div>
                <Space style={{ marginBottom: 8 }}>
                  <Text strong>明细</Text>
                  {readonly ? null : (
                    <Button size="small" type="dashed" onClick={() => add({ qty: 1 })}>添加一行</Button>
                  )}
                </Space>
                {fields.map((field) => (
                  <Space key={field.key} align="baseline" style={{ display: "flex", marginBottom: 8 }}>
                    <Form.Item name={[field.name, "product_id"]} style={{ width: 300, marginBottom: 0 }}
                               rules={[{ required: true, message: "请选择商品" }]}>
                      <ProductSelect initial={null} />
                    </Form.Item>
                    <Form.Item name={[field.name, "qty"]} style={{ width: 100, marginBottom: 0 }}
                               rules={[{ required: true, message: "数量" }]}>
                      <InputNumber min={1} max={100000} style={{ width: "100%" }} placeholder="数量" />
                    </Form.Item>
                    <Form.Item name={[field.name, "price"]} style={{ width: 110, marginBottom: 0 }}>
                      <InputNumber min={0} precision={2} style={{ width: "100%" }} placeholder="单价(选填)" />
                    </Form.Item>
                    <Form.Item name={[field.name, "serial_number"]} style={{ width: 160, marginBottom: 0 }}>
                      <Input maxLength={64} placeholder="序列号(选填)" />
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

      <Modal title={reviewing ? `审批 ${reviewing.row.code}` : "审批"} open={Boolean(reviewing)}
             onCancel={() => setReviewing(null)} onOk={() => void doReview()} okText="确定">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text type="warning">「通过」会立即过账并改动库存（调拨会同时扣一个仓、加另一个仓）。</Text>
          <Select value={reviewing?.action} style={{ width: 180 }}
                  onChange={(value) => setReviewing((prev) => (prev ? { ...prev, action: value } : prev))}
                  options={[{ label: "通过并过账", value: "APPROVE" }, { label: "驳回", value: "REJECT" }]} />
          {reviewing?.action === "REJECT" ? (
            <Input.TextArea rows={3} maxLength={200} showCount placeholder="驳回原因（必填）"
                            value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} />
          ) : null}
        </Space>
      </Modal>

      <Modal title={reversing ? `冲销 ${reversing.code}` : "冲销"} open={Boolean(reversing)}
             onCancel={() => setReversing(null)} onOk={() => void doReverse()} okText="确定冲销"
             okButtonProps={{ danger: true }}>
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text type="warning">
            冲销不会删除单据：系统会对该单据产生的流水逐条生成反向流水，把库存还原，原记录保留并标记「已冲销」。
          </Text>
          <Input.TextArea rows={3} maxLength={200} showCount placeholder="冲销原因（必填）"
                          value={reverseReason} onChange={(e) => setReverseReason(e.target.value)} />
        </Space>
      </Modal>
    </div>
  );
}
