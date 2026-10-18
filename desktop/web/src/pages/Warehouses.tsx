import { useMemo, useState } from "react";
import { App as AntdApp, Button, Drawer, Form, Input, InputNumber, Select, Space, Switch, Table,
  Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import { createWarehouse, listWarehouses, updateWarehouse } from "../api/master";
import type { Warehouse } from "../api/types";
import { Num } from "../components/Values";

const { Text } = Typography;

interface WarehouseForm {
  name: string;
  code?: string;
  category?: string;
  address?: string;
  parent_id?: number;
  sort_order?: number;
  is_active?: boolean;
}

/**
 * 仓库与库位（两级树）—— PRD 5.3（4.6 / 4.7）· docs/13 4.1。
 *
 * ★有库存余额的仓库不能停用：否则那些库存「无处可去」，账面立刻变成悬空数据。
 *   服务端会返回 1010 并带上按商品拆分的明细，这里必须把明细显示出来，
 *   只显示「操作失败」等于让管理员自己一个个去猜哪个仓库还有货。
 * ★两级封顶（仓库 / 库位）：有子库位的仓库不能停用（1017），已建仓库不能改挂上级（1017）。
 *   树只用于归类与选择，库存**不做父子汇总** —— 余额永远挂在具体那个仓库/库位上。
 */
export default function Warehouses() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [editing, setEditing] = useState<Warehouse | null>(null);
  /** 新建时挂在哪一级下（仅用于抽屉标题显示「新增仓库 / 新增库位」） */
  const [creatingUnder, setCreatingUnder] = useState<number | undefined>();
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<WarehouseForm>();
  const { message } = AntdApp.useApp();

  // 一次性带出停用仓库：管理页就是要看到全部，否则「停用了但不知道有哪几个」
  const state = useAsync(() => listWarehouses(true), []);

  const parents = useMemo(
    () => (state.data?.list ?? []).filter((w) => w.level === 1),
    [state.data],
  );
  const nameById = useMemo(() => {
    const map = new Map<number, string>();
    (state.data?.list ?? []).forEach((w) => map.set(w.id, w.name));
    return map;
  }, [state.data]);

  function openCreate(parentId?: number) {
    setEditing(null);
    setCreatingUnder(parentId);
    form.resetFields();
    form.setFieldsValue({ sort_order: 0, is_active: true, parent_id: parentId });
    setDrawerOpen(true);
  }

  function openEdit(row: Warehouse) {
    setEditing(row);
    setCreatingUnder(undefined);
    form.setFieldsValue({
      name: row.name,
      code: row.code ?? undefined,
      category: row.category ?? undefined,
      address: row.address ?? undefined,
      parent_id: row.parent_id || undefined,
      sort_order: row.sort_order,
      is_active: row.is_active === 1,
    });
    setDrawerOpen(true);
  }

  async function onSubmit(values: WarehouseForm) {
    setSaving(true);
    try {
      // 布尔在库与 JSON 里都是 0/1（两端一致），表单里的 true/false 在这里转一次
      const payload = {
        name: values.name,
        code: values.code?.trim() ? values.code.trim() : null,
        category: values.category?.trim() ? values.category.trim() : null,
        address: values.address ?? null,
        parent_id: values.parent_id ?? 0,
        sort_order: values.sort_order ?? 0,
        is_active: values.is_active === false ? 0 : 1,
      };
      if (editing) {
        await updateWarehouse(editing.id, {
          name: payload.name, code: payload.code, category: payload.category,
          address: payload.address, sort_order: payload.sort_order, is_active: payload.is_active,
        });
        message.success("已保存");
      } else {
        await createWarehouse({
          name: payload.name, code: payload.code, category: payload.category,
          address: payload.address ?? undefined, parent_id: payload.parent_id,
        });
        message.success(payload.parent_id ? "库位已新增" : "仓库已新增");
      }
      setDrawerOpen(false);
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        const breakdown = (err.payload.breakdown as { name?: string; qty?: number }[]) || [];
        if (breakdown.length > 0) {
          message.error({
            content: `该仓库仍有库存，不能停用：${breakdown
              .map((item) => `${item.name} × ${item.qty}`)
              .join("、")}`,
            duration: 0,
          });
        } else {
          message.error(err.message);
        }
      }
    } finally {
      setSaving(false);
    }
  }

  const columns: TableColumnsType<Warehouse> = [
    {
      title: "仓库 / 库位", dataIndex: "name", key: "name",
      render: (name: string, row) => (
        <Space size={6}>
          {row.level === 2 ? <Text type="secondary">└</Text> : null}
          <Text strong={row.level === 1}>{name}</Text>
          {row.level === 2 ? <Tag color="default">库位</Tag> : null}
          {row.is_active ? null : <Tag color="error">已停用</Tag>}
        </Space>
      ),
    },
    { title: "编码", dataIndex: "code", key: "code", width: 120,
      render: (value: string | null) => value || <Text type="secondary">—</Text> },
    { title: "上级", key: "parent", width: 140,
      render: (_v, row) => (row.parent_id ? nameById.get(row.parent_id) ?? `#${row.parent_id}` : "—") },
    { title: "地址", dataIndex: "address", key: "address", ellipsis: true,
      render: (value: string | null) => value || <Text type="secondary">—</Text> },
    { title: "库位数", dataIndex: "child_count", key: "child_count", width: 90, align: "right",
      render: (value: number | undefined) => <Num value={value ?? 0} /> },
    { title: "库存量", dataIndex: "total_qty", key: "total_qty", width: 100, align: "right",
      render: (value: number | undefined) => <Num value={value ?? 0} /> },
    { title: "排序", dataIndex: "sort_order", key: "sort_order", width: 80, align: "right" },
    {
      title: "操作", key: "actions", width: 170, fixed: "right",
      render: (_v, row) => (
        <Space size={0}>
          <Button type="link" size="small" onClick={() => openEdit(row)}>编辑</Button>
          {row.level === 1 ? (
            <Button type="link" size="small" onClick={() => openCreate(row.id)}>加库位</Button>
          ) : null}
        </Space>
      ),
    },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">仓库与库位</h1>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => openCreate()}>新增仓库</Button>
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                     emptyText="还没有仓库，先新增一个">
        {(data) => (
          <Table<Warehouse>
            rowKey="id"
            columns={columns}
            dataSource={data.list}
            pagination={false}
            scroll={{ x: 900 }}
          />
        )}
      </AsyncBoundary>

      <Drawer
        title={editing
          ? `编辑${editing.level === 2 ? "库位" : "仓库"}：${editing.name}`
          : (creatingUnder ? "新增库位" : "新增仓库")}
        width={440}
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
        <Form<WarehouseForm> form={form} layout="vertical" onFinish={onSubmit} requiredMark={false}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input maxLength={30} autoFocus placeholder="例如：主仓库 / A区-01" />
          </Form.Item>
          <Form.Item name="parent_id" label="上级仓库"
                     extra={editing ? "已建仓库不能改挂上级（要调整请新建）" : "不选=顶级仓库；选了就建成库位（最多两级）"}>
            <Select allowClear disabled={Boolean(editing)} placeholder="不选=顶级仓库"
                    options={parents.filter((p) => p.id !== editing?.id)
                      .map((p) => ({ label: p.name, value: p.id }))} />
          </Form.Item>
          <Form.Item name="code" label="编码" extra="选填，用于对账/条码；全库唯一">
            <Input maxLength={32} placeholder="例如：WH-A" />
          </Form.Item>
          <Form.Item name="category" label="类别">
            <Input maxLength={32} placeholder="选填，例如：原料 / 成品" />
          </Form.Item>
          <Form.Item name="address" label="地址">
            <Input maxLength={100} placeholder="选填" />
          </Form.Item>
          <Form.Item name="sort_order" label="排序（越小越靠前）">
            <InputNumber min={0} max={9999} style={{ width: "100%" }} />
          </Form.Item>
          {editing ? (
            <Form.Item name="is_active" label="启用" valuePropName="checked"
                       extra="仍有库存或有库位的仓库不允许停用">
              <Switch />
            </Form.Item>
          ) : null}
        </Form>
      </Drawer>
    </div>
  );
}
