import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Card, Input, InputNumber, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { PrinterOutlined } from "@ant-design/icons";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { callBlobUrl } from "../api/client";
import { barcodeSvgPath, listLabels } from "../api/labels";
import type { LabelPage } from "../api/labels";
import type { Product } from "../api/types";

const { Text } = Typography;

/**
 * 标签打印 —— PRD 5.7。
 *
 * ★条码图是**独立图片资源**（`/api/products/<id>/barcode.svg`）：SPA 里不做任何 HTML 注入，
 *   白名单校验仍在服务端 `net/barcode_gen.py`；前端只拿到 blob URL 交给 `<img>`。
 * ★用 fetch 取图再转 blob URL，是因为 `<img src>` 带不了 `X-Auth-Token`；
 *   把 Token 挂到 URL 上会被访问日志与浏览器历史留痕，属于禁止做法。
 * ★换页清空勾选：打印网格只渲染当前页的选中项，否则会出现「明明勾了 8 款，只打出来 3 款」。
 */
export default function Labels() {
  const [keyword, setKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [selected, setSelected] = useState<number[]>([]);
  const [copies, setCopies] = useState(1);
  const [images, setImages] = useState<Record<number, string>>({});
  const urlsRef = useRef<Record<number, string>>({});

  const state = useAsync<LabelPage>(
    () => listLabels({ keyword: keyword || undefined, page, page_size: pageSize }),
    [keyword, page, pageSize],
  );

  // 只取「选中商品」的条码图：整页 20 条全取一遍纯属浪费
  useEffect(() => {
    let alive = true;
    const missing = selected.filter((id) => !urlsRef.current[id]);
    if (missing.length === 0) {
      setImages({ ...urlsRef.current });
      return () => {
        alive = false;
      };
    }
    Promise.all(missing.map(async (id) => [id, await callBlobUrl(barcodeSvgPath(id))] as const))
      .then((pairs) => {
        if (!alive) {
          pairs.forEach(([, url]) => URL.revokeObjectURL(url));
          return;
        }
        pairs.forEach(([id, url]) => {
          urlsRef.current[id] = url;
        });
        setImages({ ...urlsRef.current });
      })
      .catch(() => {
        /* 单张条码生成失败不影响其它标签：位置留空，原因在服务端日志（带 trace_id） */
      });
    return () => {
      alive = false;
    };
  }, [selected]);

  useEffect(() => {
    const urls = urlsRef.current;
    return () => {
      Object.values(urls).forEach((url) => URL.revokeObjectURL(url));
    };
  }, []);

  const selectedProducts = useMemo(() => {
    const items = state.data?.list ?? [];
    return items.filter((item) => selected.includes(item.id));
  }, [state.data, selected]);

  const narrow = selectedProducts.filter((item) => item.narrow);
  const labelW = state.data?.label_w ?? 40;
  const labelH = state.data?.label_h ?? 30;

  const columns: TableColumnsType<Product> = [
    {
      title: "商品", dataIndex: "name", key: "name", ellipsis: true,
      render: (value: string, row) => (
        <Space direction="vertical" size={0}>
          <Text strong>{value}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{row.category} · {row.unit}</Text>
        </Space>
      ),
    },
    {
      title: "条码内容", dataIndex: "barcode_text", key: "barcode_text", width: 220,
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: "提示", key: "narrow", width: 160,
      render: (_v, row) => (row.narrow ? <Tag color="orange">标签过窄，建议换大纸</Tag> : null),
    },
  ];

  return (
    <div className="sd-page">
      <div className="sd-screen-only">
        <div className="sd-page-head">
          <h1 className="sd-page-title">标签打印</h1>
          <Space>
            <Text type="secondary">每款张数</Text>
            <InputNumber min={1} max={state.data?.max_copies ?? 50} value={copies}
                         onChange={(value) => setCopies(value ?? 1)} />
            <Button type="primary" icon={<PrinterOutlined />} disabled={selectedProducts.length === 0}
                    onClick={() => window.print()}>
              打印（{selectedProducts.length * copies} 张）
            </Button>
          </Space>
        </div>

        <Card bordered={false} style={{ marginBottom: 16 }}>
          <Text type="secondary">
            当前标签纸：{state.data ? `${state.data.label_size} mm` : "读取中…"}
            （在「系统设置 → 业务与提醒」中修改）
          </Text>
        </Card>

        {narrow.length > 0 ? (
          <Alert style={{ marginBottom: 16 }} type="warning" showIcon
                 message={`有 ${narrow.length} 款商品的条码在当前标签纸上过窄，扫码枪可能扫不出`}
                 description="建议换更大的标签纸，或给商品改用更短的条码内容。" />
        ) : null}

        <div className="sd-toolbar">
          <Input.Search allowClear style={{ width: 280 }} placeholder="搜索商品名 / 条码"
                        onSearch={(value) => {
                          setKeyword(value.trim());
                          setPage(1);
                          setSelected([]);
                        }} />
          <Text type="secondary">勾选要打印的商品（换页会清空勾选，避免打到看不见的商品）</Text>
        </div>

        <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                       emptyText={keyword ? "没有匹配的商品" : "还没有可打印的商品"}>
          {(data) => (
            <Table<Product>
              rowKey="id"
              size="middle"
              rowSelection={{
                selectedRowKeys: selected,
                onChange: (keys) => setSelected(keys as number[]),
              }}
              columns={columns}
              dataSource={data.list}
              scroll={{ x: 720 }}
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
                  setSelected([]);
                },
              }}
            />
          )}
        </AsyncBoundary>
      </div>

      {/* 打印区：屏幕上隐藏；@media print 时只留它（PRD 5.7） */}
      <div className="sd-print-only">
        <div className="sd-label-grid">
          {selectedProducts.flatMap((item) =>
            Array.from({ length: copies }, (_unused, index) => (
              <div className="sd-label" key={`${item.id}-${index}`}
                   style={{ width: `${labelW}mm`, height: `${labelH}mm` }}>
                <div className="sd-label-name">{item.name}</div>
                {images[item.id] ? (
                  <img src={images[item.id]} alt={`条码 ${item.barcode_text}`}
                       style={{ width: `${Math.max(labelW - 6, 12)}mm`, maxHeight: "9mm" }} />
                ) : null}
                <div className="sd-label-code">{item.barcode_text}</div>
              </div>
            )),
          )}
        </div>
      </div>
    </div>
  );
}
