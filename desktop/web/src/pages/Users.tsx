import { useState } from "react";
import { App as AntdApp, Button, Card, Checkbox, Drawer, Form, Input, Modal, Select, Space, Table,
  Tag, Tooltip, Typography } from "antd";
import type { TableColumnsType } from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import AsyncBoundary from "../components/AsyncBoundary";
import { useAsync } from "../hooks/useAsync";
import { ApiError } from "../api/client";
import {
  createUser, listDevices, listUsers, resetUserPassword, revokeDevice, setUserActive,
} from "../api/admin";
import type { AdminUser, Device } from "../api/admin";
import { getUser } from "../store/session";

const { Text } = Typography;

interface UserForm {
  username: string;
  display_name?: string;
  role: "admin" | "operator";
  password: string;
  password_confirm: string;
  must_change: boolean;
}

/**
 * 账号与设备 —— PRD 5.10。
 *
 * ★停用账号 / 重置他人密码都要再输**自己**的口令（PRD 3.5）：
 *   防「趁管理员离开电脑把他锁在门外」这类操作。
 * ★停用会同时吊销该账号的全部设备令牌（服务端做），所以界面上要说清「手机也要重新配对」。
 * ★`is_active` 传显式布尔：后端拒收字符串与缺省字段，避免漏传被静默当成「停用」。
 */
export default function Users() {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState<AdminUser | null>(null);
  const [toggling, setToggling] = useState<AdminUser | null>(null);
  const [selfPassword, setSelfPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [form] = Form.useForm<UserForm>();
  const { message } = AntdApp.useApp();
  const current = getUser();

  const users = useAsync(() => listUsers(), []);
  const devices = useAsync(() => listDevices(), []);

  async function onCreate(values: UserForm) {
    setSaving(true);
    try {
      await createUser(values);
      message.success("账号已创建");
      setDrawerOpen(false);
      users.reload();
    } catch (err) {
      if (err instanceof ApiError) {
        const entries = Object.entries(err.fieldErrors);
        if (entries.length > 0) {
          form.setFields(entries.map(([name, errors]) => ({ name: name as keyof UserForm, errors })));
        }
        message.error(err.message);
      }
    } finally {
      setSaving(false);
    }
  }

  async function doReset() {
    if (!resetting) {
      return;
    }
    setSaving(true);
    try {
      const result = await resetUserPassword(resetting.id, {
        password: selfPassword,
        new_password: newPassword,
        new_password_confirm: newPassword,
      });
      message.success(`已重置 ${resetting.username} 的密码，并吊销 ${result.revoked_tokens} 个设备令牌`);
      setResetting(null);
      setSelfPassword("");
      setNewPassword("");
      users.reload();
      devices.reload();
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : "重置失败");
    } finally {
      setSaving(false);
    }
  }

  async function doToggle() {
    if (!toggling) {
      return;
    }
    setSaving(true);
    try {
      await setUserActive(toggling.id, toggling.is_active === 0, selfPassword);
      message.success(toggling.is_active === 0
        ? "账号已启用"
        : "账号已停用，该账号的手机会被要求重新配对");
      setToggling(null);
      setSelfPassword("");
      users.reload();
      devices.reload();
    } catch (err) {
      message.error(err instanceof ApiError ? err.message : "操作失败");
    } finally {
      setSaving(false);
    }
  }

  const userColumns: TableColumnsType<AdminUser> = [
    {
      title: "账号", dataIndex: "username", key: "username",
      render: (value: string, row) => (
        <Space size={6}>
          <Text strong>{value}</Text>
          {row.id === current?.id ? <Tag color="blue">当前登录</Tag> : null}
          {row.is_active ? null : <Tag>已停用</Tag>}
          {row.must_change_password ? <Tag color="orange">待改密</Tag> : null}
        </Space>
      ),
    },
    { title: "显示名", dataIndex: "display_name", key: "display_name",
      render: (value: string | null) => value || <Text type="secondary">—</Text> },
    { title: "角色", dataIndex: "role", key: "role", width: 100,
      render: (value: string) => (value === "admin" ? "管理员" : "操作员") },
    { title: "上次登录", dataIndex: "last_login_at", key: "last_login_at", width: 175,
      render: (value: string | null) => (value
        ? value.replace("T", " ").slice(0, 19)
        : <Text type="secondary">从未登录</Text>) },
    {
      title: "操作", key: "actions", width: 170, fixed: "right",
      render: (_v, row) => (
        <Space size={4}>
          <Button type="link" size="small" onClick={() => {
            setResetting(row);
            setSelfPassword("");
            setNewPassword("");
          }}>重置密码</Button>
          <Button type="link" size="small" danger={row.is_active === 1}
                  disabled={row.id === current?.id}
                  onClick={() => {
                    setToggling(row);
                    setSelfPassword("");
                  }}>
            {row.is_active === 1 ? "停用" : "启用"}
          </Button>
        </Space>
      ),
    },
  ];

  const deviceColumns: TableColumnsType<Device> = [
    { title: "设备", dataIndex: "device_name", key: "device_name",
      render: (value: string | null) => value || <Text type="secondary">未命名</Text> },
    { title: "类型", dataIndex: "client_type", key: "client_type", width: 90,
      render: (value: string) => <Tag>{value}</Tag> },
    { title: "账号", dataIndex: "user_id", key: "user_id", width: 140,
      render: (value: number) => users.data?.list.find((user) => user.id === value)?.username
        ?? `#${value}` },
    { title: "最近使用", dataIndex: "last_used_at", key: "last_used_at", width: 175,
      render: (value: string | null) => (value
        ? value.replace("T", " ").slice(0, 19)
        : <Text type="secondary">从未使用</Text>) },
    { title: "有效期至", dataIndex: "expires_at", key: "expires_at", width: 175,
      render: (value: string | null) => (value
        ? value.replace("T", " ").slice(0, 19)
        : <Text type="secondary">长期</Text>) },
    {
      title: "操作", key: "actions", width: 90, fixed: "right",
      render: (_v, row) => (
        <Button type="link" size="small" danger onClick={async () => {
          await revokeDevice(row.id);
          message.success("该设备令牌已吊销，手机需重新配对");
          devices.reload();
        }}>吊销</Button>
      ),
    },
  ];

  return (
    <div className="sd-page">
      <div className="sd-page-head">
        <h1 className="sd-page-title">账号与设备</h1>
        <Space>
          <Tooltip title="刷新">
            <Button icon={<ReloadOutlined />} aria-label="刷新"
                    onClick={() => {
                      users.reload();
                      devices.reload();
                    }} />
          </Tooltip>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => {
            form.resetFields();
            form.setFieldsValue({ role: "operator", must_change: true });
            setDrawerOpen(true);
          }}>新增账号</Button>
        </Space>
      </div>

      <AsyncBoundary state={users} emptyWhen={(data) => data.list.length === 0} skeletonRows={4}>
        {(data) => (
          <Card bordered={false} title="账号">
            <Table<AdminUser>
              rowKey="id"
              size="middle"
              columns={userColumns}
              dataSource={data.list}
              pagination={false}
              scroll={{ x: 780 }}
            />
          </Card>
        )}
      </AsyncBoundary>

      <div style={{ height: 16 }} />

      <AsyncBoundary state={devices} emptyWhen={(data) => data.list.length === 0}
                     emptyText="还没有已配对的设备" skeletonRows={4}>
        {(data) => (
          <Card bordered={false} title="已配对设备（手机 / 其它客户端）"
                extra={<Text type="secondary" style={{ fontSize: 12 }}>
                  吊销后该设备需重新扫码配对
                </Text>}>
            <Table<Device>
              rowKey="id"
              size="middle"
              columns={deviceColumns}
              dataSource={data.list}
              pagination={false}
              scroll={{ x: 780 }}
            />
          </Card>
        )}
      </AsyncBoundary>

      <Drawer title="新增账号" width={440} open={drawerOpen} onClose={() => setDrawerOpen(false)}
              destroyOnClose
              extra={
                <Space>
                  <Button onClick={() => setDrawerOpen(false)}>取消</Button>
                  <Button type="primary" loading={saving} onClick={() => form.submit()}>保存</Button>
                </Space>
              }>
        <Form<UserForm> form={form} layout="vertical" onFinish={onCreate} requiredMark={false}>
          <Form.Item name="username" label="用户名"
                     rules={[{ required: true, message: "请输入用户名" },
                             { pattern: /^[0-9A-Za-z_.]{2,32}$/,
                               message: "2~32 位，只能用字母、数字、下划线、点" }]}>
            <Input autoFocus maxLength={32} />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input maxLength={30} placeholder="例如：仓库小王" />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true }]}>
            <Select options={[
              { value: "operator", label: "操作员（出入库、查询）" },
              { value: "admin", label: "管理员（含盘点、设置、账号与备份）" },
            ]} />
          </Form.Item>
          <Form.Item name="password" label="初始密码"
                     rules={[{ required: true, message: "请输入初始密码" },
                             { min: 8, message: "至少 8 位" }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="password_confirm" label="确认初始密码" dependencies={["password"]}
                     rules={[{ required: true, message: "请再输一次" },
                             ({ getFieldValue }) => ({
                               validator: (_rule, value) => (
                                 !value || value === getFieldValue("password")
                                   ? Promise.resolve()
                                   : Promise.reject(new Error("两次输入的密码不一致"))
                               ),
                             })]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="must_change" valuePropName="checked"
                     extra="勾选后，该账号首次登录必须改密（推荐保持勾选）">
            <Checkbox>要求首次登录修改密码</Checkbox>
          </Form.Item>
        </Form>
      </Drawer>

      <Modal open={resetting !== null} title={`重置密码：${resetting?.username ?? ""}`}
             okText="确认重置" cancelText="取消" destroyOnClose
             okButtonProps={{ danger: true, loading: saving,
               disabled: newPassword.length < 8 || selfPassword.length === 0 }}
             onOk={doReset} onCancel={() => setResetting(null)}>
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <div>
            <Text strong>新密码（至少 8 位）</Text>
            <Input.Password style={{ marginTop: 6 }} value={newPassword}
                            onChange={(event) => setNewPassword(event.target.value)} />
          </div>
          <div>
            <Text strong>你自己的密码</Text>
            <Input.Password style={{ marginTop: 6 }} value={selfPassword}
                            onChange={(event) => setSelfPassword(event.target.value)} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              重置他人密码属于敏感操作，需要再次确认是你本人。
            </Text>
          </div>
        </Space>
      </Modal>

      <Modal open={toggling !== null}
             title={toggling?.is_active === 1
               ? `停用账号：${toggling?.username ?? ""}`
               : `启用账号：${toggling?.username ?? ""}`}
             okText="确认" cancelText="取消" destroyOnClose
             okButtonProps={{ danger: toggling?.is_active === 1, loading: saving,
               disabled: selfPassword.length === 0 }}
             onOk={doToggle} onCancel={() => setToggling(null)}>
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Text>
            {toggling?.is_active === 1
              ? "停用后该账号无法登录，其已配对的手机设备令牌会一并失效。"
              : "启用后该账号可以正常登录，原有设备需要重新配对。"}
          </Text>
          <div>
            <Text strong>你自己的密码</Text>
            <Input.Password style={{ marginTop: 6 }} value={selfPassword}
                            onChange={(event) => setSelfPassword(event.target.value)} />
          </div>
        </Space>
      </Modal>
    </div>
  );
}
