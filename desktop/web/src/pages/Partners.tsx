import { useState } from "react";
import { App as AntdApp, Button, Drawer, Form, Input, Segmented, Space, Table, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import { createPartner, listPartners } from "../api/master";
import type { Partner } from "../api/types";

const { Text } = Typography;

type PartnerType = "CUSTOMER" | "SUPPLIER";

interface PartnerForm {
  name: string;
  contact?: string;
  phone?: string;
  address?: string;
  remark?: string;
}

const TYPE_TEXT: Record<PartnerType, string> = { CUSTOMER: "客户", SUPPLIER: "供应商" };

/**
 * 客户 / 供应商 —— PRD 5.3。
 *
 * ★类型（客户/供应商）决定它在出库还是入库里可选：服务端会拒收类型不匹配的往来单位（422）。
 *   所以这里用分段控件强制先选类型，不让用户在建单时才发现选错。
 * ★同名同类型不能重复登记（库级 UNIQUE），冲突时后端返回 1010 并给出已存在的那条。
 */
export default function Partners() {
  const [type, setType] = useState<PartnerType>("CUSTOMER");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<PartnerForm>();
  const { message } = AntdApp.useApp();

  const state = useAsync(() => listPartners(type), [type]);

  function openCreate() {
    form.resetFields();
    setDrawerOpen(true);
  }

  async function onSubmit(values: PartnerForm) {
    setSaving(true);
    try {
      await createPartner({ ...values, type });
      message.success(`${TYPE_TEXT[type]}已新增`);
      setDrawerOpen(false);
      state.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = err.fieldErrors;
        const entries = Object.entries(fields);
        if (entries.length > 0) {
          form.setFields(entries.map(([name, errors]) => ({ name: name as keyof PartnerForm, errors })));
        }
        const conflict = err.payload.conflict as { name?: string } | null | undefined;
        message.error(conflict ? `「${conflict.name}」已登记过，请直接使用` : err.message);
      }
    } finally {
      setSaving(false);
    }
  }

  const columns: TableColumnsType<Partner> = [
    {
      title: "名称", dataIndex: "name", key: "name", ellipsis: true,
      render: (name: string) => <Text strong>{name}</Text>,
    },
    { title: "联系人", dataIndex: "contact", key: "contact", width: 120,
      render: (value: string | null) => value || <Text type="secondary">—</Text> },
    { title: "电话", dataIndex: "phone", key: "phone", width: 150,
      render: (value: string | null) => value || <Text type="secondary">—</Text> },
    { title: "地址", dataIndex: "address", key: "address", ellipsis: true,
      render: (value: string | null) => value || <Text type="secondary">—</Text> },
    { title: "状态", dataIndex: "is_active", key: "is_active", width: 90,
      render: (value: number) => (value ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>) },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">客户 / 供应商</h1>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增{TYPE_TEXT[type]}
        </Button>
      </div>

      <div className="sd-toolbar">
        <Segmented
          value={type}
          onChange={(value) => setType(value as PartnerType)}
          options={[
            { value: "CUSTOMER", label: "客户（出库对象）" },
            { value: "SUPPLIER", label: "供应商（入库来源）" },
          ]}
        />
      </div>

      <AsyncBoundary state={state} emptyWhen={(data) => data.list.length === 0}
                     emptyText={`还没有${TYPE_TEXT[type]}，点右上角新增`}>
        {(data) => (
          <Table<Partner>
            rowKey="id"
            columns={columns}
            dataSource={data.list}
            pagination={false}
            scroll={{ x: 760 }}
          />
        )}
      </AsyncBoundary>

      <Drawer
        title={`新增${TYPE_TEXT[type]}`}
        width={420}
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
        <Form<PartnerForm> form={form} layout="vertical" onFinish={onSubmit} requiredMark={false}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input maxLength={50} autoFocus placeholder={type === "CUSTOMER" ? "例如：某某超市" : "例如：某某批发商"} />
          </Form.Item>
          <Form.Item name="contact" label="联系人">
            <Input maxLength={30} placeholder="选填" />
          </Form.Item>
          <Form.Item name="phone" label="电话" extra="仅用于业务联系，不要录入无关他人信息">
            <Input maxLength={20} placeholder="选填" />
          </Form.Item>
          <Form.Item name="address" label="地址">
            <Input maxLength={100} placeholder="选填" />
          </Form.Item>
          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={3} maxLength={200} showCount />
          </Form.Item>
        </Form>
      </Drawer>
    </div>
  );
}
