import { useState } from "react";
import { App as AntdApp, Button, Card, Form, Input, Typography } from "antd";
import { LockOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { changePassword } from "../api/auth";
import { getUser, mustChangePassword, setUser } from "../store/session";

const { Title, Text } = Typography;

interface FormValues {
  old_password: string;
  new_password: string;
  confirm_password: string;
}

/**
 * 修改密码。首登强制改密时也走这里。
 *
 * ★`must_change_password=1` 时后端除改密/退出/我是谁外全部 403（docs/03 3.3），
 *   所以这一页必须是**独立布局**：挂在控制台外壳里，左侧导航点哪儿都是 403，很吓人。
 * ★改密成功后要用返回的用户覆盖本地状态，否则守卫会一直把人拽回这一页。
 */
export default function ChangePassword() {
  const [form] = Form.useForm<FormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState("");
  const navigate = useNavigate();
  const { message } = AntdApp.useApp();
  const forced = mustChangePassword();
  const user = getUser();

  async function onSubmit(values: FormValues) {
    setSubmitting(true);
    setErrorText("");
    try {
      const updated = await changePassword(values.old_password, values.new_password);
      setUser(updated);
      message.success(forced ? "初始密码已修改，可以开始使用了" : "密码已修改");
      navigate("/", { replace: true });
    } catch (err) {
      setErrorText(err instanceof ApiError ? err.message : "修改失败，请重试");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="sd-center-wrap">
      <Card className="sd-center-card" bordered={false}>
        <Title level={4} style={{ marginBottom: 4 }}>
          {forced ? "请修改初始密码" : "修改密码"}
        </Title>
        <Text type="secondary">
          {forced
            ? "初始密码仅用于首次登录，为了账本安全请立即修改。"
            : `当前账号：${user?.username ?? "—"}`}
        </Text>

        {forced ? (
          <div className="sd-callout" role="note">
            未修改前，除本页与退出登录外的所有功能都会被拦截。
          </div>
        ) : null}

        <Form<FormValues> form={form} layout="vertical" size="large" requiredMark={false}
                          onFinish={onSubmit} style={{ marginTop: 20 }}>
          <Form.Item name="old_password" label="原密码" rules={[{ required: true, message: "请输入原密码" }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" autoFocus />
          </Form.Item>
          <Form.Item name="new_password" label="新密码"
                     rules={[{ required: true, message: "请输入新密码" },
                             { min: 8, message: "至少 8 位" }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="confirm_password" label="确认新密码" dependencies={["new_password"]}
                     rules={[
                       { required: true, message: "请再输入一次新密码" },
                       ({ getFieldValue }) => ({
                         validator: (_rule, value) => (
                           !value || value === getFieldValue("new_password")
                             ? Promise.resolve()
                             : Promise.reject(new Error("两次输入的新密码不一致"))
                         ),
                       }),
                     ]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="new-password" />
          </Form.Item>

          {errorText ? <div className="sd-login-error" role="alert">{errorText}</div> : null}

          <Button type="primary" htmlType="submit" block size="large" loading={submitting}>
            确认修改
          </Button>
          {!forced ? (
            <Button type="text" block style={{ marginTop: 8 }} onClick={() => navigate(-1)}>
              取消
            </Button>
          ) : null}
        </Form>
      </Card>
    </div>
  );
}
