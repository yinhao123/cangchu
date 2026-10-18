import { useState } from "react";
import { App as AntdApp, Button, Drawer, Form, Input, InputNumber, Select, Space, Switch, Table,
  Tag, Tooltip, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import AsyncBoundary from "../components/AsyncBoundary";
import { Money, Num } from "../components/Values";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import {
  createProduct, deleteProduct, listProducts, restoreProduct, updateProduct,
} from "../api/master";
import type { Paged, Product } from "../api/types";
import { isAdmin } from "../store/session";

const { Text } = Typography;

interface ProductForm {
  name: string;
  barcode?: string;
  category?: string;
  unit?: string;
  cost_price?: number;
  sale_price?: number;
  min_stock?: number;
  remark?: string;
}

/**
 * 商品与条码 —— PRD 5.3。
 *
 * ★停用而不是删除：软删后条码**永久占用**（防一码两品），需要再用就走「恢复」。
 *   界面必须把这条规则说清楚，否则用户会反复试图「删掉重建」。
 * ★编辑要回传 updated_at 做乐观锁：两个人同时改同一商品，后提交的会被 1011 挡下并提示刷新（PRD 5.3）。
 */
export default function Products() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [keyword, setKeyword] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<ProductForm>();
  const { message, modal } = AntdApp.useApp();
  const admin = isAdmin();

  const state = useAsync<Paged<Product>>(
    () => listProducts({
      page,
      page_size: pageSize,
      keyword: keyword || undefined,
      only_active: includeInactive ? "false" : "true",
    }),
    [page, pageSize, keyword, includeInactive],
  );

  function openCreate() {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({ unit: "件", category: "未分类", cost_price: 0, sale_price: 0, min_stock: 0 });
    setDrawerOpen(true);
  }

  function openEdit(product: Product) {
    setEditing(product);
    form.setFieldsValue({
      name: product.name,
      barcode: product.barcode ?? undefined,
      category: product.category,
      unit: product.unit,
      cost_price: product.cost_price,
      sale_price: product.sale_price,
      min_stock: product.min_stock,
      remark: product.remark ?? undefined,
    });
    setDrawerOpen(true);
  }

  async function onSubmit(values: ProductForm) {
    setSaving(true);
    try {
      if (editing) {
        await updateProduct(editing.id, {
          ...values,
          barcode: values.barcode ?? null,
          // 乐观锁：不回传版本戳就等于放弃了并发保护
          expected_updated_at: editing.updated_at,
        });
        message.success("商品已保存");
      } else {
        await createProduct(values);
        message.success("商品已新增");
      }
      setDrawerOpen(false);
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = err.fieldErrors;
        const entries = Object.entries(fields);
        if (entries.length > 0) {
          form.setFields(entries.map(([name, errors]) => ({ name: name as keyof ProductForm,
            errors })));
        }
        message.error(err.message);
      }
    } finally {
      setSaving(false);
    }
  }

  function confirmDisable(product: Product) {
    modal.confirm({
      title: `停用「${product.name}」？`,
      content: "停用后商品不再出现在出入库选择里，历史流水与条码都会保留。",
      okText: "停用",
      okButtonProps: { danger: true },
      onOk: async () => {
        const result = await deleteProduct(product.id);
        message.success(result.deleted === "hard" ? "商品已彻底删除" : "商品已停用，可在「含已停用」里恢复");
        state.reload();
      },
    });
  }

  const columns: TableColumnsType<Product> = [
    {
      title: "商品", dataIndex: "name", key: "name", ellipsis: true,
      render: (name: string, row) => (
        <Space direction="vertical" size={0}>
          <Space size={6}>
            <Text strong>{name}</Text>
            {row.is_active ? null : <Tag color="default">已停用</Tag>}
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>{row.category} · {row.unit}</Text>
        </Space>
      ),
    },
    {
      title: "条码", dataIndex: "barcode", key: "barcode", width: 160,
      render: (barcode: string | null) => (
        barcode ? <Text code>{barcode}</Text> : <Text type="secondary">无条码</Text>
      ),
    },
    { title: "成本价", dataIndex: "cost_price", key: "cost_price", width: 100, align: "right",
      render: (value: number) => <Money value={value} /> },
    { title: "售价", dataIndex: "sale_price", key: "sale_price", width: 100, align: "right",
      render: (value: number) => <Money value={value} /> },
    { title: "预警下限", dataIndex: "min_stock", key: "min_stock", width: 100, align: "right",
      render: (value: number) => <Num value={value} /> },
    {
      title: "操作", key: "actions", width: 140, fixed: "right",
      render: (_v, row) => admin ? (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => openEdit(row)}>编辑</Button>
          {row.is_active ? (
            <Button type="link" size="small" danger onClick={() => confirmDisable(row)}>停用</Button>
          ) : (
            <Button type="link" size="small" onClick={async () => {
              await restoreProduct(row.id);
              message.success("商品已恢复");
              state.reload();
            }}>恢复</Button>
          )}
        </Space>
      ) : <Text type="secondary">仅管理员可改</Text>,
    },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">商品与条码</h1>
        <Space>
          <Tooltip title="刷新">
            <Button icon={<ReloadOutlined />} onClick={state.reload} aria-label="刷新" />
          </Tooltip>
          {admin ? (
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增商品</Button>
          ) : null}
        </Space>
      </div>

      <div className="sd-toolbar">
        <Input.Search
          allowClear
          style={{ width: 280 }}
          placeholder="搜索名称、条码或内码"
          onSearch={(value) => {
            setKeyword(value.trim());
            setPage(1);
          }}
        />
        <Space>
          <Switch checked={includeInactive} onChange={(checked) => {
            setIncludeInactive(checked);
            setPage(1);
          }} />
          <Text>含已停用商品</Text>
        </Space>
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.total === 0}
                     emptyText={keyword ? "没有匹配的商品" : "还没有商品，先新增一个"}>
        {(data) => (
          <Table<Product>
            rowKey="id"
            size="middle"
            columns={columns}
            dataSource={data.list}
            scroll={{ x: 900 }}
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

      <Drawer
        title={editing ? `编辑商品：${editing.name}` : "新增商品"}
        width={480}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        destroyOnClose
        extra={
          <Space>
            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            <Button type="primary" loading={saving} onClick={() => form.submit()}>保存</Button>
          </Space>
        }
      >
        <Form<ProductForm> form={form} layout="vertical" onFinish={onSubmit} requiredMark={false}>
          <Form.Item name="name" label="商品名称" rules={[{ required: true, message: "请输入商品名称" }]}>
            <Input placeholder="例如：可口可乐 500ml" maxLength={100} autoFocus />
          </Form.Item>
          <Form.Item name="barcode" label="条码"
                     extra="留空表示无条码商品（可用内码打印标签）；填错会占用别人的条码">
            <Input placeholder="扫码枪扫一下就自动填入" maxLength={48} />
          </Form.Item>
          <Space size={12} style={{ width: "100%" }}>
            <Form.Item name="category" label="分类" style={{ width: 210 }}>
              <Input placeholder="未分类" maxLength={30} />
            </Form.Item>
            <Form.Item name="unit" label="单位" style={{ width: 150 }}>
              <Select options={["件", "箱", "包", "瓶", "千克", "米"].map((v) => ({ value: v, label: v }))} />
            </Form.Item>
          </Space>
          <Space size={12} style={{ width: "100%" }}>
            <Form.Item name="cost_price" label="成本价" style={{ width: 170 }}>
              <InputNumber min={0} precision={2} style={{ width: "100%" }} addonBefore="¥" />
            </Form.Item>
            <Form.Item name="sale_price" label="售价" style={{ width: 170 }}>
              <InputNumber min={0} precision={2} style={{ width: "100%" }} addonBefore="¥" />
            </Form.Item>
            <Form.Item name="min_stock" label="预警下限" style={{ width: 170 }}>
              <InputNumber min={0} precision={0} style={{ width: "100%" }} addonAfter="件" />
            </Form.Item>
          </Space>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={3} maxLength={200} showCount />
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}
