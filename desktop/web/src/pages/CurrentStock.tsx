import { useState } from "react";
import { Card, Col, Empty, Input, Row, Segmented, Select, Space, Statistic, Table, Tag,
  Typography } from "antd";
import type { TableColumnsType } from "antd";
import AsyncBoundary from "../components/AsyncBoundary";
import { Num } from "../components/Values";
import { useAsync } from "../hooks/useAsync";
import { dashboard, queryCurrent } from "../api/reports";
import type { CurrentQuery } from "../api/reports";
import { listWarehouses } from "../api/master";
import type { CurrentStock } from "../api/types";

const { Text } = Typography;

type Filter = "all" | "low" | "negative";

/**
 * 库存现状 —— PRD 5.6。
 *
 * ★负库存**原样显示并标红**，禁止用 MAX(0,…) 掩盖（这是「账实不符」的唯一可见信号）。
 * ★`is_low_stock` 由服务端算好，前端不得另算一套：同一口径出现两份实现，迟早对不上。
 */
export default function CurrentStock() {
  const [warehouseId, setWarehouseId] = useState<number | undefined>();
  const [keyword, setKeyword] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const warehouses = useAsync(() => listWarehouses(), []);
  const overview = useAsync(() => dashboard(), []);
  const negativeFilter = filter === "negative";

  const params: CurrentQuery = {
    warehouse_id: warehouseId,
    keyword: keyword.trim() || undefined,
    low_only: filter === "low" || undefined,
    negative_only: negativeFilter || undefined,
    page,
    page_size: pageSize,
  };

  const state = useAsync(() => queryCurrent(params), [warehouseId, keyword, filter, page, pageSize]);

  const columns: TableColumnsType<CurrentStock> = [
    {
      title: "商品", dataIndex: "product_name", key: "product_name", ellipsis: true,
      render: (value: string, row) => (
        <Space direction="vertical" size={0}>
          <Text strong>{value}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {row.barcode || "无条码"} · {row.category}
          </Text>
        </Space>
      ),
    },
    { title: "仓库", dataIndex: "warehouse_name", key: "warehouse_name", width: 130, ellipsis: true },
    {
      title: "当前库存", dataIndex: "qty", key: "qty", width: 130, align: "right", sorter: false,
      render: (value: number, row) => (
        <Num value={value} danger={value < 0 || row.is_low_stock} suffix={` ${row.unit}`} />
      ),
    },
    { title: "预警下限", dataIndex: "min_stock", key: "min_stock", width: 100, align: "right",
      render: (value: number) => <Num value={value} /> },
    {
      title: "状态", key: "status", width: 110,
      render: (_v, row) => (
        row.qty < 0 ? <Tag color="red">负数异常</Tag>
          : row.is_low_stock ? <Tag color="orange">低于下限</Tag>
            : <Tag color="green">正常</Tag>
      ),
    },
    { title: "最近入库", dataIndex: "last_in_at", key: "last_in_at", width: 160,
      render: (value: string | null) => value ? value.replace("T", " ").slice(0, 16)
        : <Text type="secondary">—</Text> },
    { title: "最近出库", dataIndex: "last_out_at", key: "last_out_at", width: 160,
      render: (value: string | null) => value ? value.replace("T", " ").slice(0, 16)
        : <Text type="secondary">—</Text> },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">库存现状</h1>
        <Segmented
          value={filter}
          onChange={(value) => {
            setFilter(value as Filter);
            setPage(1);
          }}
          options={[
            { value: "all", label: "全部" },
            { value: "low", label: "仅看预警" },
            { value: "negative", label: "仅看异常" },
          ]}
        />
      </div>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} md={6}>
          <Card bordered={false}>
            <Statistic title="库存总量（件）" value={overview.data?.total_qty ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card bordered={false}>
            <Statistic title="低于预警线" value={overview.data?.low_stock_total ?? 0}
                       valueStyle={{ color: "#faad14" }} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card bordered={false}>
            <Statistic title="负数异常" value={overview.data?.stock_anomaly_count ?? 0}
                       valueStyle={{ color: (overview.data?.stock_anomaly_count ?? 0) > 0
                         ? "#ff4d4f" : undefined }} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card bordered={false}>
            <Statistic title="当前筛选结果" value={state.data?.total ?? 0} suffix="条" />
          </Card>
        </Col>
      </Row>

      <div className="sd-toolbar">
        <Select
          allowClear
          placeholder="全部仓库"
          style={{ width: 180 }}
          value={warehouseId}
          onChange={(value) => {
            setWarehouseId(value);
            setPage(1);
          }}
          options={(warehouses.data?.list ?? []).map((w) => ({ value: w.id, label: w.name }))}
        />
        <Input.Search
          allowClear
          style={{ width: 260 }}
          placeholder="商品名 / 条码"
          onSearch={(value) => {
            setKeyword(value);
            setPage(1);
          }}
        />
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.total === 0}
                     emptyText={filter === "all" ? "还没有库存记录" : "没有符合条件的库存"}>
        {(data) => (
          <Table<CurrentStock>
            rowKey={(row) => `${row.product_id}-${row.warehouse_id}`}
            size="middle"
            columns={columns}
            dataSource={data.list}
            scroll={{ x: 1020 }}
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
            locale={{ emptyText: <Empty description="没有数据" /> }}
          />
        )}
      </AsyncBoundary>
    </div>
  );
}
