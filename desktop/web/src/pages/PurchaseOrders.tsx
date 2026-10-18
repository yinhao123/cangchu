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
import { listPartners } from "../api/master";
import {
  STATUS_META, cancelPurchaseOrder, createPurchaseOrder, exportBills, getPurchaseOrder,
  listPurchaseOrders, reviewPurchaseOrder, submitPurchaseOrder, updatePurchaseOrder,
  type BillStatus, type PurchaseOrder,
} from "../api/wms";
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
  partner_id?: number;
  applicant_date: Dayjs;
  remark?: string;
  lines: LineForm[];
}

/**
 * 采购订单 —— docs/13 第 4.2 / 5 章 · docs/03 12'.2。
 *
 * ★订单只是「打算买什么」，不碰库存；真正加库存的是采购入库单审批通过那一步。
 * ★已审批的订单不能改（要改先作废）：否则审批意见对应的内容会被悄悄换掉。
 */
export default function PurchaseOrders() {
  const admin = isAdmin();
  const { message } = AntdApp.useApp();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [page, setPage] = useState(1);

  // 打印（P0-3）：勾选批量打印，或单行「打印」；纸张默认取服务端设置，可临时切换
  const printer = usePrint();
  const [selected, setSelected] = useState<number[]>([]);
  const [paper, setPaper] = useState<PaperKey | null>(null);
  const paperKey = paper ?? printer.defaultPaper;

  const state = useAsync(() => listPurchaseOrders({
    page,
    page_size: 20,
    status,
    keyword: keyword || undefined,
    date_from: range?.[0]?.format("YYYY-MM-DD"),
    date_to: range?.[1]?.format("YYYY-MM-DD"),
  }), [page, status, keyword, range]);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<PurchaseOrder | null>(null);
  const [readonly, setReadonly] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<OrderForm>();

  const [reviewing, setReviewing] = useState<{ order: PurchaseOrder; action: "APPROVE" | "REJECT" } | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const partners = useAsync(() => listPartners("SUPPLIER"), []);
  const partnerOptions = useMemo(
    () => (partners.data?.list ?? []).map((p) => ({ label: p.name, value: p.id })),
    [partners.data],
  );

  function resetForm() {
    form.resetFields();
    form.setFieldsValue({ applicant_date: dayjs(), lines: [] });
  }

  function openCreate() {
    setEditing(null);
    setReadonly(false);
    resetForm();
    setDrawerOpen(true);
  }

  async function openEdit(row: PurchaseOrder, asReadonly: boolean) {
    try {
      const full = await getPurchaseOrder(row.id);
      setEditing(full);
      setReadonly(asReadonly);
      form.setFieldsValue({
        name: full.name,
        partner_id: full.partner_id ?? undefined,
        applicant_date: dayjs(full.applicant_date),
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

  async function onSubmit(values: OrderForm) {
    setSaving(true);
    try {
      const payload = {
        name: values.name,
        partner_id: values.partner_id ?? null,
        remark: values.remark ?? null,
        applicant_date: values.applicant_date.format("YYYY-MM-DD"),
        lines: (values.lines ?? []).map((line) => ({
          product_id: line.product_id,
          qty: line.qty,
          price: line.price ?? 0,
        })),
      };
      if (editing) {
        await updatePurchaseOrder(editing.id, payload);
        message.success("采购订单已保存");
      } else {
        await createPurchaseOrder(payload);
        message.success("采购订单已创建");
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
    // 明细行的错误键形如 lines[2].qty，antd 只认 lines.2.qty
    target.setFields(entries.map(([name, errors]) => ({
      name: name.replace(/\[(\d+)\]/g, ".$1").split("."),
      errors,
    })));
  }

  async function doSubmit(row: PurchaseOrder) {
    try {
      await submitPurchaseOrder(row.id);
      message.success("已提交，等待审批");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doCancel(row: PurchaseOrder) {
    try {
      await cancelPurchaseOrder(row.id);
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
      const result = await reviewPurchaseOrder(reviewing.order.id, reviewing.action, rejectReason || undefined);
      message.success(result.status === "APPROVED" ? "已审批通过" : "已驳回");
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
      await exportBills("purchase-orders", {
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

  const columns: TableColumnsType<PurchaseOrder> = [
    { title: "单号", dataIndex: "code", key: "code", width: 190,
      render: (code: string) => <Text strong>{code}</Text> },
    { title: "名称", dataIndex: "name", key: "name", ellipsis: true },
    { title: "供应商", dataIndex: "partner_name_snap", key: "partner",
      render: (v: string | null) => v || <Text type="secondary">—</Text> },
    { title: "申请日期", dataIndex: "applicant_date", key: "date", width: 120 },
    { title: "数量", dataIndex: "total_qty", key: "qty", width: 90, align: "right",
      render: (v: number) => <Num value={v} /> },
    { title: "金额", dataIndex: "total_amount", key: "amount", width: 110, align: "right",
      render: (v: number) => <Money value={v} /> },
    { title: "状态", dataIndex: "status", key: "status", width: 100,
      render: (value: BillStatus) => <Tag color={STATUS_META[value].color}>{STATUS_META[value].label}</Tag> },
    {
      title: "操作", key: "actions", width: 210, fixed: "right",
      render: (_v, row) => (
        <Space size={0} wrap>
          <Button type="link" size="small" onClick={() => void openEdit(row, true)}>查看</Button>
          <Button type="link" size="small" icon={<PrinterOutlined />} loading={printer.busy}
                  onClick={() => void printer.print("purchase-orders", row.id, { paper: paperKey })}>
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
          {admin && ["DRAFT", "REJECTED", "SUBMITTED", "APPROVED"].includes(row.status) ? (
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
        <h1 className="sd-page-title">采购订单</h1>
        <Space wrap>
          <Input.Search allowClear placeholder="单号 / 名称" style={{ width: 200 }}
                        onSearch={(value) => { setPage(1); setKeyword(value.trim()); }} />
          <Select allowClear placeholder="状态" style={{ width: 130 }} value={status}
                  onChange={(value) => { setPage(1); setStatus(value); }}
                  options={Object.entries(STATUS_META)
                    .filter(([key]) => ["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CLOSED", "CANCELED"].includes(key))
                    .map(([key, meta]) => ({ label: meta.label, value: key }))} />
          <DatePicker.RangePicker value={range}
                                  onChange={(value) => { setPage(1); setRange(value as [Dayjs, Dayjs] | null); }} />
          <Select style={{ width: 190 }} value={paperKey} onChange={(value) => setPaper(value)}
                  options={printer.papers.map((p) => ({ label: p.label, value: p.key }))} />
          <Button icon={<PrinterOutlined />} disabled={selected.length === 0} loading={printer.busy}
                  onClick={() => void printer.print("purchase-orders", selected, { paper: paperKey })}>
            打印所选{selected.length > 0 ? `（${selected.length}）` : ""}
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => void doExport()}>导出</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建订单</Button>
        </Space>
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                     emptyText="还没有采购订单">
        {(data) => (
          <Table<PurchaseOrder>
            rowKey="id"
            columns={columns}
            dataSource={data.list}
            scroll={{ x: 1200 }}
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
        title={editing ? `${readonly ? "查看" : "编辑"}采购订单：${editing.code}` : "新建采购订单"}
        width={760}
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
          <Space size={12} style={{ width: "100%" }} align="start">
            <Form.Item name="name" label="订单名称" style={{ minWidth: 240 }}
                       rules={[{ required: true, message: "请输入订单名称" }]}>
              <Input maxLength={60} placeholder="例如：10 月第一批" />
            </Form.Item>
            <Form.Item name="partner_id" label="供应商" style={{ minWidth: 200 }}>
              <Select allowClear placeholder="选择供应商" options={partnerOptions}
                      loading={partners.loading} />
            </Form.Item>
            <Form.Item name="applicant_date" label="申请日期"
                       rules={[{ required: true, message: "请选择日期" }]}>
              <DatePicker style={{ width: 150 }} />
            </Form.Item>
          </Space>

          <Form.List name="lines">
            {(fields, { add, remove }) => (
              <div>
                <Space style={{ marginBottom: 8 }}>
                  <Text strong>采购明细</Text>
                  {readonly ? null : (
                    <Button size="small" type="dashed" onClick={() => add({ product_id: 0, qty: 1, price: 0 })}>
                      添加一行
                    </Button>
                  )}
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
        </Form>
      </Drawer>

      {printer.element}

      <Modal
        title={reviewing ? `审批 ${reviewing.order.code}` : "审批"}
        open={Boolean(reviewing)}
        onCancel={() => setReviewing(null)}
        onOk={() => void doReview()}
        okText="确定"
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text>通过后订单可被采购入库单引用；驳回需要写明原因。</Text>
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
