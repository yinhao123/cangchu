import { useState } from "react";
import { Card, Descriptions, Segmented, Space, Table, Typography } from "antd";
import type { TableColumnsType } from "antd";
import dayjs from "dayjs";
import AsyncBoundary from "../components/AsyncBoundary";
import { Money, Num } from "../components/Values";
import { useAsync } from "../hooks/useAsync";
import { summarize } from "../api/reports";
import type { SummaryRow } from "../api/types";

const { Text } = Typography;

const DIMENSIONS = [
  { value: "DAY", label: "按日期" },
  { value: "PRODUCT", label: "按商品" },
  { value: "CATEGORY", label: "按分类" },
  { value: "WAREHOUSE", label: "按仓库" },
  { value: "OPERATOR", label: "按操作员" },
];

const DIMENSION_TITLE: Record<string, string> = {
  DAY: "日期", PRODUCT: "商品", CATEGORY: "分类", WAREHOUSE: "仓库", OPERATOR: "操作员",
};

/**
 * 汇总报表 —— PRD 5.6。
 *
 * ★汇总口径只在服务端算一次（`report_service.summarize`）：合计行直接取服务端给的 total，
 *   前端不要再把列表加一遍 —— 两份加法迟早不一致（而且分页之后前端根本加不全）。
 */
export default function Summary() {
  const [dimension, setDimension] = useState("DAY");
  const dateFrom = dayjs().subtract(6, "day").format("YYYY-MM-DD");
  const dateTo = dayjs().format("YYYY-MM-DD");
  const state = useAsync(() => summarize(dimension, dateFrom, dateTo), [dimension]);

  const columns: TableColumnsType<SummaryRow> = [
    { title: DIMENSION_TITLE[dimension] ?? "维度", dataIndex: "label", key: "label", ellipsis: true },
    { title: "入库数量", dataIndex: "in_qty", key: "in_qty", width: 120, align: "right",
      render: (value: number) => <Num value={value} /> },
    { title: "出库数量", dataIndex: "out_qty", key: "out_qty", width: 120, align: "right",
      render: (value: number) => <Num value={value} /> },
    { title: "入库金额", dataIndex: "in_amount", key: "in_amount", width: 130, align: "right",
      render: (value: number) => <Money value={value} /> },
    { title: "出库金额", dataIndex: "out_amount", key: "out_amount", width: 130, align: "right",
      render: (value: number) => <Money value={value} /> },
    { title: "单据数", dataIndex: "bill_count", key: "bill_count", width: 100, align: "right",
      render: (value: number) => <Num value={value} /> },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">汇总报表</h1>
        <Segmented value={dimension} onChange={(value) => setDimension(value as string)}
                   options={DIMENSIONS} />
      </div>

      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Text type="secondary">
          统计区间：{dateFrom} ~ {dateTo}（最近 7 天。需要更长区间请到「流水查询」按日期筛选后导出 CSV）
        </Text>
      </Card>

      <AsyncBoundary state={state} emptyWhen={(data) => data.rows.length === 0}
                     emptyText="该区间内没有出入库记录">
        {(data) => (
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Descriptions bordered size="small" column={{ xs: 1, md: 3, xl: 5 }}
                          items={[
                            { key: "in_qty", label: "入库合计",
                              children: <Num value={data.total.in_qty} /> },
                            { key: "out_qty", label: "出库合计",
                              children: <Num value={data.total.out_qty} /> },
                            { key: "in_amount", label: "入库金额",
                              children: <Money value={data.total.in_amount} /> },
                            { key: "out_amount", label: "出库金额",
                              children: <Money value={data.total.out_amount} /> },
                            { key: "bills", label: "单据总数",
                              children: <Num value={data.total.bill_count} /> },
                          ]}
            />
            <Table<SummaryRow>
              rowKey="key"
              columns={columns}
              dataSource={data.rows}
              pagination={false}
              scroll={{ x: 760 }}
            />
          </Space>
        )}
      </AsyncBoundary>
    </div>
  );
}
