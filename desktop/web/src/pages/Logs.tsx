import { useState } from "react";
import {
  App as AntdApp, Button, Card, DatePicker, Form, Input, Modal, Select, Space, Switch, Table, Tag,
  Tooltip, Typography,
} from "antd";
import type { TableColumnsType } from "antd";
import { ExportOutlined, SearchOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import AsyncBoundary from "../components/AsyncBoundary";
import { Money, Num } from "../components/Values";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import { exportLogs, listOperators, queryLogs, TYPE_LABEL } from "../api/reports";
import type { LogQuery } from "../api/reports";
import { listWarehouses } from "../api/master";
import { reverseLog } from "../api/stock";
import type { Paged, StockLog, StockType } from "../api/types";
import { isAdmin } from "../store/session";

const { Text } = Typography;
const { RangePicker } = DatePicker;

const TYPE_COLOR: Record<StockType, string> = { IN: "green", OUT: "orange", ADJUST: "blue" };

/**
 * 流水查询 —— PRD 5.6。
 *
 * ★账本不可变：这里**只有**「红字冲销」，没有删除/编辑入口。
 *   已冲销的单据要显式标记，否则同一笔业务看两遍会以为重复记账。
 * ★CSV 导出交给后端（utf-8-sig + \r\n），前端只负责触发下载（T-10）。
 */
export default function Logs() {
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(6, "day"), dayjs()]);
  const [type, setType] = useState<StockType[]>([]);
  const [warehouseId, setWarehouseId] = useState<number | undefined>();
  const [operatorId, setOperatorId] = useState<number | undefined>();
  const [keyword, setKeyword] = useState("");
  const [onlyReversed, setOnlyReversed] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [reversing, setReversing] = useState<StockLog | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const { message } = AntdApp.useApp();
  const admin = isAdmin();

  const warehouses = useAsync(() => listWarehouses(), []);
  const operators = useAsync(() => listOperators(), []);

  const params: LogQuery = {
    date_from: range[0]?.format("YYYY-MM-DD"),
    date_to: range[1]?.format("YYYY-MM-DD"),
    type: type.length > 0 ? type.join(",") : undefined,
    warehouse_id: warehouseId,
    operator_id: operatorId,
    keyword: keyword.trim() || undefined,
    only_reversed: onlyReversed || undefined,
    page,
    page_size: pageSize,
    sort: "created_at",
    order: "desc",
  };

  const state = useAsync<Paged<StockLog>>(
    () => queryLogs(params),
    [range, type, warehouseId, operatorId, keyword, onlyReversed, page, pageSize],
  );

  async function doExport() {
    try {
      await exportLogs({ ...params, page: undefined, page_size: undefined });
      message.success("已开始下载，可直接用 Excel 打开");
    } catch (err) {
      // 超过 5 万行的提示在服务端，这里原样透出（含「请缩小日期范围」的建议）
      message.error(err instanceof ApiError ? err.message : "导出失败");
    }
  }

  async function doReverse() {
    if (!reversing) {
      return;
    }
    if (!reason.trim()) {
      message.warning("请填写冲销原因");
      return;
    }
    setBusy(true);
    try {
      const data = await reverseLog(reversing.id, reason.trim());
      message.success(data.duplicated ? "该冲销已提交过，未重复生成" : "已生成反向流水，原单标记为已冲销");
      setReversing(null);
      setReason("");
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        const { current, requested, lack } = err.payload as
          { current?: number; requested?: number; lack?: number };
        message.error(err.code === 1009
          ? `库存不足，无法冲销该入库单（现有 ${current}，需要 ${requested}，缺 ${lack}）；请改用盘点调整`
          : err.message);
      }
    } finally {
      setBusy(false);
    }
  }

  const columns: TableColumnsType<StockLog> = [
    { title: "时间", dataIndex: "created_at", key: "created_at", width: 160,
      render: (value: string) => value.replace("T", " ").slice(0, 19) },
    { title: "类型", dataIndex: "type", key: "type", width: 92,
      render: (value: StockType, row) => (
        <Space direction="vertical" size={0}>
          <Tag color={TYPE_COLOR[value]}>{TYPE_LABEL[value]}</Tag>
          {row.reversed_by ? <Tag color="red">已冲销</Tag> : null}
        </Space>
      ) },
    {
      title: "商品", dataIndex: "product_name", key: "product_name", ellipsis: true,
      render: (value: string, row) => (
        <Space direction="vertical" size={0}>
          <Text>{value}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{row.barcode || "无条码"}</Text>
        </Space>
      ),
    },
    { title: "仓库", dataIndex: "warehouse_name", key: "warehouse_name", width: 110, ellipsis: true },
    { title: "数量", dataIndex: "quantity", key: "quantity", width: 90, align: "right",
      render: (value: number) => <Num value={value} danger={value < 0} /> },
    { title: "结存", dataIndex: "after_qty", key: "after_qty", width: 90, align: "right",
      render: (value: number) => <Num value={value} danger={value < 0} /> },
    { title: "金额", dataIndex: "amount", key: "amount", width: 100, align: "right",
      render: (value: number | null) => <Money value={value} /> },
    { title: "往来单位", dataIndex: "partner_name", key: "partner_name", width: 130, ellipsis: true,
      render: (value: string | null) => value || <Text type="secondary">—</Text> },
    { title: "操作员", dataIndex: "operator_name", key: "operator_name", width: 100 },
    { title: "来源", dataIndex: "source", key: "source", width: 76,
      render: (value: string) => <Tag>{value}</Tag> },
    { title: "备注", dataIndex: "remark", key: "remark", width: 160, ellipsis: true,
      render: (value: string | null) => value ? <Tooltip title={value}>{value}</Tooltip>
        : <Text type="secondary">—</Text> },
    {
      title: "操作", key: "actions", width: 88, fixed: "right",
      render: (_v, row) => (
        row.type === "ADJUST" ? <Text type="secondary" style={{ fontSize: 12 }}>不可冲销</Text>
          : row.reversed_by ? <Text type="secondary" style={{ fontSize: 12 }}>已冲销</Text>
            : admin ? (
              <Button type="link" size="small" danger onClick={() => {
                setReversing(row);
                setReason("");
              }}>冲销</Button>
            ) : <Text type="secondary" style={{ fontSize: 12 }}>仅管理员</Text>
      ),
    },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">流水查询</h1>
        <Button icon={<ExportOutlined />} onClick={doExport}>导出 CSV</Button>
      </div>

      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Space size={12} wrap>
          <RangePicker
            value={range}
            allowClear={false}
            onChange={(values) => {
              if (values?.[0] && values[1]) {
                setRange([values[0], values[1]]);
                setPage(1);
              }
            }}
          />
          <Select
            mode="multiple"
            allowClear
            placeholder="类型"
            style={{ minWidth: 190 }}
            value={type}
            onChange={(values) => {
              setType(values as StockType[]);
              setPage(1);
            }}
            options={[
              { value: "IN", label: "入库" },
              { value: "OUT", label: "出库" },
              { value: "ADJUST", label: "盘点调整" },
            ]}
          />
          <Select
            allowClear
            placeholder="仓库"
            style={{ width: 160 }}
            value={warehouseId}
            onChange={(value) => {
              setWarehouseId(value);
              setPage(1);
            }}
            options={(warehouses.data?.list ?? []).map((w) => ({ value: w.id, label: w.name }))}
          />
          <Select
            allowClear
            placeholder="操作员"
            style={{ width: 150 }}
            value={operatorId}
            onChange={(value) => {
              setOperatorId(value);
              setPage(1);
            }}
            options={(operators.data?.list ?? []).map((o) => ({
              value: o.id,
              label: o.display_name || o.username,
            }))}
          />
          <Input.Search
            allowClear
            style={{ width: 220 }}
            placeholder="商品名 / 条码"
            enterButton={<SearchOutlined />}
            onSearch={(value) => {
              setKeyword(value);
              setPage(1);
            }}
          />
          <Space>
            <Switch checked={onlyReversed} onChange={(checked) => {
              setOnlyReversed(checked);
              setPage(1);
            }} />
            <Text>只看已冲销</Text>
          </Space>
        </Space>
      </Card>

      <AsyncBoundary state={state} emptyWhen={(data) => data.total === 0}
                     emptyText="该条件下没有流水记录">
        {(data) => (
          <Table<StockLog>
            rowKey="id"
            size="middle"
            columns={columns}
            dataSource={data.list}
            scroll={{ x: 1280 }}
            pagination={{
              current: data.page,
              pageSize: data.page_size,
              total: data.total,
              showSizeChanger: true,
              pageSizeOptions: [20, 50, 100],
              showTotal: (total) => `共 ${total} 条`,
              onChange: (nextPage, nextSize) => {
                setPage(nextSize === pageSize ? nextPage : 1);
                setPageSize(nextSize);
              },
            }}
          />
        )}
      </AsyncBoundary>

      <Modal
        open={reversing !== null}
        title="红字冲销"
        okText="确认冲销"
        cancelText="取消"
        okButtonProps={{ danger: true, loading: busy, disabled: !reason.trim() }}
        onOk={doReverse}
        onCancel={() => setReversing(null)}
        destroyOnClose
      >
        {reversing ? (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Form layout="vertical">
              <Form.Item label="单据信息" style={{ marginBottom: 12 }}>
                <Space direction="vertical" size={0}>
                  <Text>
                    #{reversing.id} {TYPE_LABEL[reversing.type]} · {reversing.product_name} ×{" "}
                    {reversing.quantity}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {reversing.created_at.replace("T", " ").slice(0, 19)} ·{" "}
                    {reversing.warehouse_name} · 操作员 {reversing.operator_name}
                  </Text>
                </Space>
              </Form.Item>
              <Form.Item label="冲销原因" required>
                <Input.TextArea
                  rows={3}
                  maxLength={160}
                  showCount
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="例如：录错数量，按实际重新录入"
                />
              </Form.Item>
            </Form>
            <Text type="secondary" style={{ fontSize: 12 }}>
              冲销会生成一条等额反向流水，原单与冲销单都永久保留（账本不允许删除或修改）。
            </Text>
          </Space>
        ) : null}
      </Modal>
    </div>
  );
}
