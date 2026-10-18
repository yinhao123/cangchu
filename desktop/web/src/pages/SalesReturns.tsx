import { useMemo, useState } from "react";
import {
  App as AntdApp, Button, DatePicker, Drawer, Form, Input, InputNumber, Modal, Popconfirm,
  Select, Space, Table, Tag, Typography,
} from "antd";
import type { FormInstance, TableColumnsType } from "antd";
import { DownloadOutlined, PlusOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import AsyncBoundary from "../components/AsyncBoundary";
import ProductSelect from "../components/ProductSelect";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import { listPartners, listWarehouses } from "../api/master";
import { STATUS_META } from "../api/wms";
import {
  cancelSalesReturn, createSalesReturn, exportSalesBills, getSalesReturn, listSalesOrders,
  listSalesReturns, reviewSalesReturn, submitSalesReturn, updateSalesReturn,
  type SalesReturn,
} from "../api/sales";
import { isAdmin } from "../store/session";
import { Money, Num } from "../components/Values";

const { Text } = Typography;

interface LineForm {
  product_id: number;
  qty: number;
  price?: number;
}

interface ReturnForm {
  name: string;
  customer_id: number;
  warehouse_id: number;
  return_date: Dayjs;
  source_order_id?: number;
  return_reason: string;
  remark?: string;
  lines: LineForm[];
}

/**
 * 销售退货单 —— docs/14 第 6 章。
 *
 * ★审批通过 = 同步过账（写 IN 流水、加库存），与采购入库同构；所以「已过账」的单不许再改。
 * ★退货原因必填：没有原因的退货单在对账时无法解释，等于给自己埋雷。
 */
export default function SalesReturns() {
  const admin = isAdmin();
  const { message } = AntdApp.useApp();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [customerId, setCustomerId] = useState<number | undefined>();
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [page, setPage] = useState(1);

  const state = useAsync(() => listSalesReturns({
    page,
    page_size: 20,
    status,
    keyword: keyword || undefined,
    customer_id: customerId,
    date_from: range?.[0]?.format("YYYY-MM-DD"),
    date_to: range?.[1]?.format("YYYY-MM-DD"),
  }), [page, status, keyword, customerId, range]);

  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<SalesReturn | null>(null);
  const [readonly, setReadonly] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<ReturnForm>();

  const [reviewing, setReviewing] = useState<{ doc: SalesReturn; action: "APPROVE" | "REJECT" } | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [orderOptions, setOrderOptions] = useState<{ label: string; value: number }[]>([]);

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

  async function loadOrders(cid?: number) {
    if (!cid) {
      setOrderOptions([]);
      return;
    }
    try {
      const data = await listSalesOrders({ customer_id: cid, page: 1, page_size: 50 });
      setOrderOptions(data.list.map((o) => ({
        label: `${o.code} ${String(o.order_date).slice(0, 10)}`, value: o.id,
      })));
    } catch {
      setOrderOptions([]);
    }
  }

  function openCreate() {
    setEditing(null);
    setReadonly(false);
    setOrderOptions([]);
    form.resetFields();
    form.setFieldsValue({ return_date: dayjs(), lines: [] });
    setDrawerOpen(true);
  }

  async function openEdit(row: SalesReturn, asReadonly: boolean) {
    try {
      const full = await getSalesReturn(row.id);
      setEditing(full);
      setReadonly(asReadonly);
      await loadOrders(full.customer_id);
      form.setFieldsValue({
        name: full.name,
        customer_id: full.customer_id,
        warehouse_id: full.warehouse_id,
        return_date: dayjs(full.return_date),
        source_order_id: full.source_order_id ?? undefined,
        return_reason: full.return_reason,
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

  async function onSubmit(values: ReturnForm) {
    setSaving(true);
    try {
      const payload = {
        name: values.name,
        customer_id: values.customer_id,
        warehouse_id: values.warehouse_id,
        return_date: values.return_date.format("YYYY-MM-DD"),
        source_order_id: values.source_order_id ?? null,
        return_reason: values.return_reason,
        remark: values.remark ?? null,
        lines: (values.lines ?? []).map((line) => ({
          product_id: line.product_id,
          qty: line.qty,
          price: line.price ?? null,
        })),
      };
      if (editing) {
        await updateSalesReturn(editing.id, payload);
        message.success("退货单已保存");
      } else {
        await createSalesReturn(payload);
        message.success("退货单已创建");
      }
      setDrawerOpen(false);
      state.reload();
    } catch (err) {
      applyFieldErrors(err, form);
    } finally {
      setSaving(false);
    }
  }

  async function doSubmit(row: SalesReturn) {
    try {
      await submitSalesReturn(row.id);
      message.success("已提交，等待审批");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doCancel(row: SalesReturn) {
    try {
      await cancelSalesReturn(row.id);
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
      const result = await reviewSalesReturn(reviewing.doc.id, reviewing.action, rejectReason || undefined);
      message.success(result.posted_at ? "已通过并入库" : "已驳回");
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
      await exportSalesBills("sales-returns", {
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

  const columns: TableColumnsType<SalesReturn> = [
    { title: "单号", dataIndex: "code", key: "code", width: 190,
      render: (code: string) => <Text strong>{code}</Text> },
    { title: "名称", dataIndex: "name", key: "name", ellipsis: true },
    { title: "客户", dataIndex: "customer_name_snap", key: "customer" },
    { title: "退回仓库", dataIndex: "warehouse_name", key: "warehouse", width: 110 },
    { title: "源销货单", dataIndex: "order_code_snap", key: "source", width: 180,
      render: (v: string | null) => v || <Text type="secondary">无源单</Text> },
    { title: "退货日期", dataIndex: "return_date", key: "date", width: 110,
      render: (v: string) => String(v).slice(0, 10) },
    { title: "数量", dataIndex: "total_qty", key: "qty", width: 80, align: "right",
      render: (v: number) => <Num value={v} /> },
    { title: "金额", dataIndex: "total_amount", key: "amount", width: 100, align: "right",
      render: (v: number) => <Money value={v} /> },
    { title: "状态", dataIndex: "status", key: "status", width: 100,
      render: (value: SalesReturn["status"]) => (
        <Tag color={STATUS_META[value].color}>{STATUS_META[value].label}</Tag>
      ) },
    {
      title: "操作", key: "actions", width: 190, fixed: "right",
      render: (_v, row) => (
        <Space size={0} wrap>
          <Button type="link" size="small" onClick={() => void openEdit(row, true)}>查看</Button>
          {!row.posted_at && (row.status === "DRAFT" || row.status === "REJECTED") ? (
            <>
              <Button type="link" size="small" onClick={() => void openEdit(row, false)}>编辑</Button>
              <Button type="link" size="small" onClick={() => void doSubmit(row)}>提交</Button>
            </>
          ) : null}
          {admin && row.status === "SUBMITTED" ? (
            <Button type="link" size="small"
                    onClick={() => { setReviewing({ doc: row, action: "APPROVE" }); setRejectReason(""); }}>
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
        <h1 className="sd-page-title">销售退货</h1>
        <Space wrap>
          <Input.Search allowClear placeholder="单号 / 名称" style={{ width: 190 }}
                        onSearch={(value) => { setPage(1); setKeyword(value.trim()); }} />
          <Select allowClear showSearch optionFilterProp="label" placeholder="客户"
                  style={{ width: 170 }} value={customerId}
                  onChange={(value) => { setPage(1); setCustomerId(value); }}
                  options={customerOptions} loading={customers.loading} />
          <Select allowClear placeholder="状态" style={{ width: 120 }} value={status}
                  onChange={(value) => { setPage(1); setStatus(value); }}
                  options={["DRAFT", "SUBMITTED", "APPROVED", "REJECTED", "CANCELED"].map((key) => ({
                    label: STATUS_META[key as SalesReturn["status"]].label, value: key,
                  }))} />
          <DatePicker.RangePicker value={range}
                                  onChange={(value) => { setPage(1); setRange(value as [Dayjs, Dayjs] | null); }} />
          <Button icon={<DownloadOutlined />} onClick={() => void doExport()}>导出</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建退货单</Button>
        </Space>
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                     emptyText="还没有销售退货单">
        {(data) => (
          <Table<SalesReturn>
            rowKey="id"
            columns={columns}
            dataSource={data.list}
            scroll={{ x: 1300 }}
            pagination={{
              current: data.page, pageSize: data.page_size, total: data.total,
              showSizeChanger: false, onChange: setPage,
              showTotal: (total) => `共 ${total} 张`,
            }}
          />
        )}
      </AsyncBoundary>

      <Drawer
        title={editing ? `${readonly ? "查看" : "编辑"}退货单：${editing.code}` : "新建退货单"}
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
        <Form<ReturnForm> form={form} layout="vertical" onFinish={onSubmit} requiredMark={false}
                          disabled={readonly}>
          <Space size={12} style={{ width: "100%" }} align="start" wrap>
            <Form.Item name="name" label="单据名称" style={{ minWidth: 210 }}
                       rules={[{ required: true, message: "请输入单据名称" }]}>
              <Input maxLength={60} placeholder="例如：张三退货" />
            </Form.Item>
            <Form.Item name="customer_id" label="客户" style={{ minWidth: 180 }}
                       rules={[{ required: true, message: "请选择客户" }]}>
              <Select showSearch optionFilterProp="label" placeholder="选择客户"
                      options={customerOptions} loading={customers.loading}
                      onChange={(value) => { void loadOrders(value); form.setFieldsValue({ source_order_id: undefined }); }} />
            </Form.Item>
            <Form.Item name="warehouse_id" label="退回仓库" style={{ minWidth: 160 }}
                       rules={[{ required: true, message: "请选择仓库" }]}>
              <Select placeholder="退回到哪个仓" options={warehouseOptions}
                      loading={warehouses.loading} />
            </Form.Item>
            <Form.Item name="return_date" label="退货日期"
                       rules={[{ required: true, message: "请选择日期" }]}>
              <DatePicker style={{ width: 150 }} />
            </Form.Item>
          </Space>

          <Space size={12} style={{ width: "100%" }} align="start" wrap>
            <Form.Item name="source_order_id" label="源销货单（可选）" style={{ minWidth: 260 }}>
              <Select allowClear showSearch optionFilterProp="label" placeholder="无单退货可留空"
                      options={orderOptions} notFoundContent="该客户暂无销货单" />
            </Form.Item>
            <Form.Item name="return_reason" label="退货原因" style={{ minWidth: 320, flex: 1 }}
                       rules={[{ required: true, message: "请填写退货原因" }]}>
              <Input maxLength={200} placeholder="例如：包装破损 / 发错货" />
            </Form.Item>
          </Space>

          <Form.List name="lines">
            {(fields, { add, remove }) => (
              <div>
                <Space style={{ marginBottom: 8 }}>
                  <Text strong>退货明细</Text>
                  {readonly ? null : (
                    <Button size="small" type="dashed" onClick={() => add({ product_id: 0, qty: 1 })}>
                      添加一行
                    </Button>
                  )}
                  <Text type="secondary">审批通过后按数量入库</Text>
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

          {readonly && editing?.posted_at ? (
            <Text type="success">已过账（{String(editing.posted_at).slice(0, 19).replace("T", " ")}），不可再改</Text>
          ) : null}
          {readonly && editing?.reject_reason ? (
            <div><Text type="danger">驳回原因：{editing.reject_reason}</Text></div>
          ) : null}
        </Form>
      </Drawer>

      <Modal
        title={reviewing ? `审批 ${reviewing.doc.code}` : "审批"}
        open={Boolean(reviewing)}
        onCancel={() => setReviewing(null)}
        onOk={() => void doReview()}
        okText="确定"
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text>通过后立即入库（写 IN 流水，不可撤销）；驳回需要写明原因。</Text>
          <Select
            value={reviewing?.action}
            style={{ width: 160 }}
            onChange={(value) => setReviewing((prev) => (prev ? { ...prev, action: value } : prev))}
            options={[{ label: "通过并入库", value: "APPROVE" }, { label: "驳回", value: "REJECT" }]}
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
