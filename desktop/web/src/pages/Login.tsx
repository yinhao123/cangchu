import { useEffect, useState } from "react";
import { Alert, App as AntdApp, Button, Form, Input, Typography } from "antd";
import { SafetyCertificateOutlined } from "@ant-design/icons";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { login } from "../api/auth";
import { getState } from "../api/activation";
import type { ActivationState } from "../api/activation";
import { ping } from "../api/system";
import type { PingData } from "../api/system";
import { startSession } from "../store/session";

const { Text } = Typography;

interface LoginForm {
  username: string;
  password: string;
}

/** 设备名会写进 api_tokens.device_name，方便管理员在「设备」里认出是哪台机器 */
function deviceName(): string {
  const platform = window.navigator.platform || "未知系统";
  return `浏览器 · ${platform}`.slice(0, 40);
}

/* ------------------------------------------------------------------
 * 线性图标：设计稿是 1.8px 线宽的统一风格，@ant-design/icons 是面性图标，
 * 混在里面会「一重一轻」。这里手写 SVG，颜色一律走 currentColor 由 CSS 控制。
 * ------------------------------------------------------------------ */

function IconScan() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4 9V6.5A2.5 2.5 0 0 1 6.5 4H9M20 9V6.5A2.5 2.5 0 0 0 17.5 4H15M4 15v2.5A2.5 2.5 0 0 0 6.5 20H9M20 15v2.5A2.5 2.5 0 0 1 17.5 20H15"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      <path d="M3.2 12h17.6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

function IconBarcode() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4 5v14M8 5v14M12 5v10M16 5v14M20 5v14"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function IconReceipt() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path d="M9 8h6M9 12h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function IconShield() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 3l7 3v6c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6l7-3z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
      />
      <path
        d="M9 12l2 2 4-4"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconUser() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M4.5 20c1.3-3.3 4-5 7.5-5s6.2 1.7 7.5 5"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function IconLock() {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="4" y="10.5" width="16" height="9.5" rx="2" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M8 10.5V7.5a4 4 0 0 1 8 0v3"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function Login() {
  const [form] = Form.useForm<LoginForm>();
  const [submitting, setSubmitting] = useState(false);
  const [errorText, setErrorText] = useState("");
  const [license, setLicense] = useState<ActivationState | null>(null);
  const [server, setServer] = useState<PingData | null>(null);
  const [serverDown, setServerDown] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { message } = AntdApp.useApp();

  const reason = new URLSearchParams(location.search).get("reason");

  useEffect(() => {
    // 未激活时给出明确入口：否则用户只会看到「登录失败」，不知道该去激活
    getState().then(setLicense).catch(() => setLicense(null));
  }, []);

  useEffect(() => {
    // 页面能打开说明 HTTP 是通的，但数据库可能没就绪 —— 不显示这一条的话，
    // 用户会一直以为是自己密码打错了。版本号也只认服务端返回值。
    let alive = true;
    ping()
      .then((data) => {
        if (alive) {
          setServer(data);
        }
      })
      .catch(() => {
        if (alive) {
          setServerDown(true);
        }
      });
    return () => {
      alive = false;
    };
  }, []);

  async function onSubmit(values: LoginForm) {
    setSubmitting(true);
    setErrorText("");
    try {
      const result = await login(values.username.trim(), values.password, deviceName());
      startSession(result.token, result.user);
      if (result.user.must_change_password === 1) {
        message.warning("首次登录，请先修改初始密码");
        navigate("/change-password", { replace: true });
        return;
      }
      navigate("/", { replace: true });
    } catch (err) {
      // 登录失败就地红字提示（不弹全局框）：用户名/口令错是最常见的操作，弹窗更打断人
      setErrorText(err instanceof ApiError ? err.message : "登录失败，请重试");
      form.setFieldsValue({ password: "" });
    } finally {
      setSubmitting(false);
    }
  }

  const notActivated = license !== null && !license.activated;

  const status = serverDown
    ? { tone: "err", text: "服务未响应" }
    : server
      ? server.db_ready
        ? { tone: "ok", text: "服务运行中" }
        : { tone: "warn", text: "数据库未就绪" }
      : { tone: "idle", text: "正在连接" };

  return (
    <div className="sd-login">
      <aside className="sd-login-hero">
        <div className="sd-login-hero-inner">
          <div className="sd-login-brand">
            <span className="sd-login-logo" aria-hidden="true">
              <IconScan />
            </span>
            <span className="sd-login-brandtext">
              <strong>扫码出入库系统</strong>
              <span>SCANABLESELF</span>
            </span>
          </div>

          <div className="sd-login-statement">
            <span className="sd-login-eyebrow">
              <i aria-hidden="true" />
              局域网自用 · 不联网也能用
            </span>
            <h1>
              让每一件货
              <br />
              都账实相符
            </h1>
            <p>电脑当账本，手机当扫码枪。库存在这台机器上算清楚，每一笔出入都有据可查。</p>
          </div>

          <ul className="sd-login-points">
            <li>
              <span className="sd-login-point-icon">
                <IconBarcode />
              </span>
              扫码即录，入库出库不用手打
            </li>
            <li>
              <span className="sd-login-point-icon">
                <IconReceipt />
              </span>
              每笔流水留痕，红冲可回溯
            </li>
            <li>
              <span className="sd-login-point-icon">
                <IconShield />
              </span>
              库存实时校验，不足当场拦截
            </li>
          </ul>

          <div className="sd-login-hero-foot">
            <i aria-hidden="true" />
            数据存于本机 · 无需联网 · Win7 可运行
          </div>
        </div>
      </aside>

      <main className="sd-login-main">
        <div className="sd-login-statusbar">
          <span className={`sd-login-status sd-login-status--${status.tone}`}>
            <i aria-hidden="true" />
            {status.text}
            {server ? (
              <>
                <span className="sd-login-status-sep" aria-hidden="true" />
                <span className="sd-login-status-ver">v{server.version}</span>
              </>
            ) : null}
          </span>
        </div>

        <div className="sd-login-center">
          <div className="sd-login-card">
            <header className="sd-login-head">
              <h2>登录</h2>
              <p>使用管理账号登录本机账本</p>
            </header>

            {reason ? (
              <Alert className="sd-login-alert" type="warning" showIcon message={reason} />
            ) : null}

            {notActivated ? (
              <Alert
                className="sd-login-alert"
                type="error"
                showIcon
                icon={<SafetyCertificateOutlined />}
                message="软件尚未激活"
                description={
                  <span>
                    {license?.message || "需要先输入注册码"}，本机机器码
                    <Text code copyable={{ text: license?.machine_id }}>
                      {license?.display_machine_id}
                    </Text>
                    <Link to="/activate">前往激活</Link>
                  </span>
                }
              />
            ) : null}

            <Form<LoginForm>
              className="sd-login-form"
              form={form}
              layout="vertical"
              requiredMark={false}
              onFinish={onSubmit}
              initialValues={{ username: "" }}
            >
              <Form.Item
                name="username"
                label="用户名"
                rules={[{ required: true, message: "请输入用户名" }]}
              >
                <Input
                  prefix={<IconUser />}
                  placeholder="请输入用户名"
                  autoComplete="username"
                  autoFocus
                />
              </Form.Item>
              <Form.Item
                name="password"
                label="密码"
                rules={[{ required: true, message: "请输入密码" }]}
              >
                <Input.Password
                  prefix={<IconLock />}
                  placeholder="请输入密码"
                  autoComplete="current-password"
                  onPressEnter={() => form.submit()}
                />
              </Form.Item>

              {errorText ? (
                <div className="sd-login-error" role="alert">
                  {errorText}
                </div>
              ) : null}

              <Button
                className="sd-login-submit"
                type="primary"
                htmlType="submit"
                block
                loading={submitting}
              >
                登 录
              </Button>
            </Form>

            <div className="sd-login-foot">忘记密码请联系管理员重置</div>
          </div>
        </div>

        <div className="sd-login-copy">2026 ScanableSelf　本机部署 · 数据仅保存在这台电脑</div>
      </main>
    </div>
  );
}
