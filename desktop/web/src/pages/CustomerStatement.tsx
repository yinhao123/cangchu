import { useMemo, useState } from "react";
import { App as AntdApp, Button, DatePicker, Descriptions, Empty, Select, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { DownloadOutlined, SearchOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import type { Dayjs } from "dayjs";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import { listPartners } from "../api/master";
import {
  exportCustomerStatement, getCustomerStatement,
  type CustomerStatement as StatementData, type StatementLine,
} from "../api/sales";
import { Money, Num } from "../components/Values";

const { Text } = Typography;

const EMPTY_STATEMENT: StatementData = {
  customer: { id: 0, name: "", contact: null, phone: null, address: null },
  from: "",
  to: "",
  lines: [],
  totals: {
    out_qty: 0, out_amount: 0, return_qty: 0, return_amount: 0,
    net_qty: 0, net_amount: 0, line_count: 0,
  },
};

/**
 * 客户对账单 —— docs/14 第 6 章。
 *
 * ★明细与合计都由服务端算：金额取自 `stock_logs` 账本，不取单据合计，
 *   所以部分发货、退货、红字冲销都能对上；前端只负责展示与导出。
 * ★范围边界：这是「往来对账」，不是财务应收 —— 不做凭证、不做账龄、不连开票。
 */
export default function CustomerStatement() {
  const { message } = AntdApp.useApp();
  const [customerId, setCustomerId] = useState<number | undefined>();
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().startOf("month"), dayjs()]);
  const [query, setQuery] = useState<{ partner_id: number; from: string; to: string } | null>(null);
  const [exporting, setExporting] = useState(false);

  const customers = useAsync(() => listPartners("CUSTOMER"), []);
  const customerOptions = useMemo(
    () => (customers.data?.list ?? []).map((p) => ({ label: p.name, value: p.id })),
    [customers.data],
  );

  // 未查询时给一个空壳：`AsyncBoundary` 的泛型不接受 null，页面用 `!query` 单独挡住这一态
  const state = useAsync<StatementData>(async () => {
    if (!query) {
      return EMPTY_STATEMENT;
    }
    return getCustomerStatement(query.partner_id, query.from, query.to);
  }, [query]);

  function doSearch() {
    if (!customerId) {
      message.warning("请先选择客户");
      return;
    }
    setQuery({
      partner_id: customerId,
      from: range[0].format("YYYY-MM-DD"),
      to: range[1].format("YYYY-MM-DD"),
    });
  }

  async function doExport() {
    if (!query) {
      message.warning("请先查询");
      return;
    }
    setExporting(true);
    try {
      await exportCustomerStatement(query.partner_id, query.from, query.to);
      message.success("已导出 CSV");
    } catch (err) {
      if (err instanceof ApiError) {
        message.error(err.message);
      }
    } finally {
      setExporting(false);
    }
  }

  const columns: TableColumnsType<StatementLine> = [
    { title: "日期", dataIndex: "date", key: "date", width: 110 },
    { title: "单据号", dataIndex: "code", key: "code", width: 200,
      render: (v: string) => <Text strong>{v || "—"}</Text> },
    { title: "业务", dataIndex: "biz", key: "biz", width: 110,
      render: (v: string, row) => (
        <Tag color={row.direction === "OUT" ? "blue" : "orange"}>{v}</Tag>
      ) },
    { title: "商品", dataIndex: "product_name", key: "product", ellipsis: true },
    { title: "数量", dataIndex: "qty", key: "qty", width: 90, align: "right",
      render: (v: number, row) => (
        <span style={{ color: row.direction === "OUT" ? undefined : "#fa8c16" }}>
          {row.direction === "OUT" ? "" : "-"}<Num value={v} />
        </span>
      ) },
    { title: "单价", dataIndex: "price", key: "price", width: 100, align: "right",
      render: (v: number) => <Money value={v} /> },
    { title: "金额", dataIndex: "amount", key: "amount", width: 110, align: "right",
      render: (v: number, row) => (
        <span style={{ color: row.direction === "OUT" ? undefined : "#fa8c16" }}>
          {row.direction === "OUT" ? "" : "-"}<Money value={v} />
        </span>
      ) },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">客户对账单</h1>
        <Space wrap>
          <Select allowClear showSearch optionFilterProp="label" placeholder="选择客户"
                  style={{ width: 200 }} value={customerId} onChange={setCustomerId}
                  options={customerOptions} loading={customers.loading} />
          <DatePicker.RangePicker value={range}
                                  onChange={(value) => setRange((value as [Dayjs, Dayjs] | null) ?? [dayjs().startOf("month"), dayjs()])} />
          <Button type="primary" icon={<SearchOutlined />} onClick={doSearch}>查询</Button>
          <Button icon={<DownloadOutlined />} loading={exporting} disabled={!query}
                  onClick={() => void doExport()}>导出 CSV</Button>
        </Space>
      </div>

      {!query ? (
        <Empty description="选择客户与期间后点「查询」" />
      ) : (
        <AsyncBoundary state={state} emptyWhen={() => false}>
          {(data) => (
            <>
              <Descriptions size="small" bordered column={4} style={{ marginBottom: 12 }}>
                <Descriptions.Item label="客户">{data.customer.name}</Descriptions.Item>
                <Descriptions.Item label="联系人">
                  {data.customer.contact || "—"}{data.customer.phone ? ` / ${data.customer.phone}` : ""}
                </Descriptions.Item>
                <Descriptions.Item label="期间" span={2}>{data.from} ~ {data.to}</Descriptions.Item>
                <Descriptions.Item label="出库">
                  <Num value={data.totals.out_qty} /> 件 / <Money value={data.totals.out_amount} />
                </Descriptions.Item>
                <Descriptions.Item label="退货">
                  <Num value={data.totals.return_qty} /> 件 / <Money value={data.totals.return_amount} />
                </Descriptions.Item>
                <Descriptions.Item label="净数量">
                  <Text strong><Num value={data.totals.net_qty} /> 件</Text>
                </Descriptions.Item>
                <Descriptions.Item label="净金额">
                  <Text strong type="danger"><Money value={data.totals.net_amount} /></Text>
                </Descriptions.Item>
              </Descriptions>

              <Table<StatementLine>
                rowKey="log_id"
                columns={columns}
                dataSource={data.lines}
                size="small"
                pagination={false}
                scroll={{ x: 900 }}
                summary={() => (
                  <Table.Summary.Row>
                    <Table.Summary.Cell index={0} colSpan={4}>
                      <Text strong>合计（{data.totals.line_count} 行）</Text>
                    </Table.Summary.Cell>
                    <Table.Summary.Cell index={1} align="right">
                      <Text strong><Num value={data.totals.net_qty} /></Text>
                    </Table.Summary.Cell>
                    <Table.Summary.Cell index={2} />
                    <Table.Summary.Cell index={3} align="right">
                      <Text strong type="danger"><Money value={data.totals.net_amount} /></Text>
                    </Table.Summary.Cell>
                  </Table.Summary.Row>
                )}
              />
              {data.lines.length === 0 ? (
                <Empty description="该期间没有出库或退货记录" style={{ marginTop: 24 }} />
              ) : null}
            </>
          )}
        </AsyncBoundary>
      )}
    </div>
  );
}
