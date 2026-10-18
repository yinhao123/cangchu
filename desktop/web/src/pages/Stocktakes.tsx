import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  App as AntdApp, Alert, Button, DatePicker, Form, Input, Modal,
  Select, Space, Table, Tag, Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { DownloadOutlined, PlusOutlined } from "@ant-design/icons";
import type { Dayjs } from "dayjs";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import { listWarehouses } from "../api/master";
import {
  STOCKTAKE_STATUS_META, createStocktake, exportStocktakes, listStocktakes, postStocktake,
  rejectStocktake, submitStocktake, type Stocktake, type StocktakeStatus,
} from "../api/stocktake";
import { isAdmin } from "../store/session";
import { Num } from "../components/Values";

const { Text } = Typography;

interface CreateForm {
  name: string;
  warehouse_id: number;
  category?: string;
  remark?: string;
}

/**
 * 盘点单列表 —— docs/14 第 4 章（P0-2）。
 *
 * ★列表只做「建单 / 提交 / 审批过账 / 作废」四个动作，录实盘数在详情页（整表录入更快）。
 * ★「差异」两列用服务端算好的 `diff_count` / `diff_abs_qty`（快照口径），前端不重算。
 * ★无库存的仓库建单会被后端拒绝（409/1014），前端只把提示原样抛出来。
 */
export default function Stocktakes() {
  const admin = isAdmin();
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const [status, setStatus] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [page, setPage] = useState(1);

  const state = useAsync(() => listStocktakes({
    page,
    page_size: 20,
    status,
    keyword: keyword || undefined,
    date_from: range?.[0]?.format("YYYY-MM-DD"),
    date_to: range?.[1]?.format("YYYY-MM-DD"),
  }), [page, status, keyword, range]);

  const warehouses = useAsync(() => listWarehouses(false), []);
  const warehouseOptions = useMemo(
    () => (warehouses.data?.list ?? []).map((w) => ({
      label: w.level === 2 ? `　${w.name}` : w.name, value: w.id,
    })),
    [warehouses.data],
  );

  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<CreateForm>();

  const [reviewing, setReviewing] = useState<{ row: Stocktake; action: "APPROVE" | "REJECT" } | null>(null);
  const [reason, setReason] = useState("");

  function openCreate() {
    form.resetFields();
    setCreating(true);
  }

  async function doCreate(values: CreateForm) {
    setSaving(true);
    try {
      const doc = await createStocktake({
        name: values.name,
        warehouse_id: values.warehouse_id,
        category: values.category || null,
        remark: values.remark ?? null,
      });
      message.success(`已建单 ${doc.code}，共 ${doc.line_count} 行快照，去录入实盘数`);
      setCreating(false);
      navigate(`/wms/stocktakes/${doc.id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        const entries = Object.entries(err.fieldErrors);
        if (entries.length > 0) {
          form.setFields(entries.map(([name, errors]) => ({
            name: name as keyof CreateForm, errors,
          })));
        } else {
          message.error(err.message);
        }
      }
    } finally {
      setSaving(false);
    }
  }

  async function doSubmit(row: Stocktake) {
    try {
      await submitStocktake(row.id);
      message.success("已提交，等待审批");
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
    try {
      if (reviewing.action === "REJECT") {
        if (!reason.trim()) {
          message.warning("驳回必须填写原因");
          return;
        }
        await rejectStocktake(reviewing.row.id, reason.trim());
        message.success("已驳回，操作员可修改后重新提交");
      } else {
        const doc = await postStocktake(reviewing.row.id);
        message.success(`已过账：${doc.diff_count} 行差异已写入流水，库存已按实盘数对齐`);
      }
      setReviewing(null);
      setReason("");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    }
  }

  async function doExport() {
    try {
      await exportStocktakes({
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

  const columns: TableColumnsType<Stocktake> = [
    { title: "单号", dataIndex: "code", key: "code", width: 180,
      render: (code: string, row) => (
        <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/wms/stocktakes/${row.id}`)}>
          <Text strong>{code}</Text>
        </Button>
      ) },
    { title: "名称", dataIndex: "name", key: "name", ellipsis: true },
    { title: "仓库", dataIndex: "warehouse_name", key: "warehouse", width: 110 },
    { title: "快照时刻", dataIndex: "book_locked_at", key: "locked", width: 160,
      render: (value: string) => value.slice(0, 19).replace("T", " ") },
    { title: "已盘 / 明细", key: "counted", width: 110, align: "right",
      render: (_v, row) => `${row.counted_count} / ${row.line_count}` },
    { title: "差异行", dataIndex: "diff_count", key: "diff", width: 90, align: "right",
      render: (value: number) => (value > 0 ? <Text type="danger">{value}</Text> : <Num value={value} />) },
    { title: "差异绝对值", dataIndex: "diff_abs_qty", key: "abs", width: 110, align: "right",
      render: (value: number) => <Num value={value} /> },
    { title: "状态", dataIndex: "status", key: "status", width: 100,
      render: (value: StocktakeStatus) => (
        <Tag color={STOCKTAKE_STATUS_META[value].color}>{STOCKTAKE_STATUS_META[value].label}</Tag>
      ) },
    {
      title: "操作", key: "actions", width: 200, fixed: "right",
      render: (_v, row) => (
        <Space size={0} wrap>
          <Button type="link" size="small" onClick={() => navigate(`/wms/stocktakes/${row.id}`)}>
            {["DRAFT", "REJECTED"].includes(row.status) ? "录入" : "查看"}
          </Button>
          {["DRAFT", "REJECTED"].includes(row.status) ? (
            <Button type="link" size="small" onClick={() => void doSubmit(row)}>提交</Button>
          ) : null}
          {admin && row.status === "SUBMITTED" ? (
            <Button type="link" size="small"
                    onClick={() => { setReviewing({ row, action: "APPROVE" }); setReason(""); }}>
              审批
            </Button>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">盘点单</h1>
        <Space wrap>
          <Input.Search allowClear placeholder="单号 / 名称" style={{ width: 200 }}
                        onSearch={(value) => { setPage(1); setKeyword(value.trim()); }} />
          <Select allowClear placeholder="状态" style={{ width: 130 }} value={status}
                  onChange={(value) => { setPage(1); setStatus(value); }}
                  options={(Object.keys(STOCKTAKE_STATUS_META) as StocktakeStatus[])
                    .map((key) => ({ label: STOCKTAKE_STATUS_META[key].label, value: key }))} />
          <DatePicker.RangePicker value={range}
                                  onChange={(value) => { setPage(1); setRange(value as [Dayjs, Dayjs] | null); }} />
          <Button icon={<DownloadOutlined />} onClick={() => void doExport()}>导出</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建盘点单</Button>
        </Space>
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                     emptyText="还没有盘点单：先建一张，系统会按当前库存生成盘点清单">
        {(data) => (
          <Table<Stocktake>
            rowKey="id"
            columns={columns}
            dataSource={data.list}
            scroll={{ x: 1180 }}
            pagination={{
              current: data.page, pageSize: data.page_size, total: data.total,
              showSizeChanger: false, onChange: setPage,
              showTotal: (total) => `共 ${total} 张`,
            }}
          />
        )}
      </AsyncBoundary>

      <Modal
        title="新建盘点单"
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => form.submit()}
        okText="建单并生成快照"
        confirmLoading={saving}
        destroyOnClose
      >
        <Alert type="info" showIcon style={{ marginBottom: 12 }}
               message="建单时会锁定当前账面数（book_qty）作为盘点底稿"
               description="零库存商品与已停用商品不参与盘点；盘点期间若有出入库，过账仍按实盘数对齐库存。" />
        <Form<CreateForm> form={form} layout="vertical" onFinish={doCreate} requiredMark={false}>
          <Form.Item name="name" label="单据名称" rules={[{ required: true, message: "请输入单据名称" }]}>
            <Input maxLength={50} placeholder="例如：10 月月末全盘" />
          </Form.Item>
          <Space size={12} style={{ width: "100%" }} align="start" wrap>
            <Form.Item name="warehouse_id" label="盘点仓库" style={{ minWidth: 200 }}
                       rules={[{ required: true, message: "请选择仓库" }]}>
              <Select placeholder="选择仓库" options={warehouseOptions} loading={warehouses.loading} />
            </Form.Item>
            <Form.Item name="category" label="只盘某个分类（选填）" style={{ minWidth: 200 }}>
              <Input maxLength={50} placeholder="留空=整仓全盘" />
            </Form.Item>
          </Space>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={2} maxLength={200} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={reviewing ? `审批 ${reviewing.row.code}` : "审批"}
        open={Boolean(reviewing)}
        onCancel={() => setReviewing(null)}
        onOk={() => void doReview()}
        okText="确定"
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text type="warning">
            「通过并过账」会立即按实盘数调整库存并写入 ADJUST 流水，之后不能修改也不能作废；
            要更正只能再盘一次。
          </Text>
          <Select
            value={reviewing?.action}
            style={{ width: 180 }}
            onChange={(value) => setReviewing((prev) => (prev ? { ...prev, action: value } : prev))}
            options={[{ label: "通过并过账", value: "APPROVE" }, { label: "驳回", value: "REJECT" }]}
          />
          {reviewing?.action === "REJECT" ? (
            <Input.TextArea rows={3} maxLength={200} showCount placeholder="驳回原因（必填）"
                            value={reason} onChange={(e) => setReason(e.target.value)} />
          ) : null}
          {reviewing?.action === "APPROVE" && (reviewing?.row.period_changes ?? 0) > 0 ? (
            <Alert type="warning" showIcon
                   message={`盘点期间该仓库有 ${reviewing?.row.period_changes} 笔出入库`}
                   description="过账按「实盘 − 实时当前」计算，最终库存等于实盘数；期间出入库不会被加回来。" />
          ) : null}
        </Space>
      </Modal>
    </div>
  );
}
