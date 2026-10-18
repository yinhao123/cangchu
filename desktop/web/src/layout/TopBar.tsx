import { App as AntdApp, Avatar, Button, Dropdown, Space, Typography } from "antd";
import {
  BulbOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  MoonOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { logout } from "../api/auth";
import { clearSession, getUser, mustChangePassword } from "../store/session";
import { useUi } from "../store/ui";

const { Text } = Typography;

interface Props {
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

/** 顶栏：系统名 + 当前用户 + 主题切换。折叠按钮在窄屏是打开抽屉。 */
export default function TopBar({ collapsed, onToggleCollapsed }: Props) {
  const user = getUser();
  const { dark, toggleDark } = useUi();
  const navigate = useNavigate();
  const { modal, message } = AntdApp.useApp();

  function confirmLogout() {
    modal.confirm({
      title: "退出登录",
      content: "退出后需要重新输入用户名与密码。",
      okText: "退出",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await logout();
        } catch {
          // 服务端已失效也算退出成功：本地照样清干净，避免卡在登录态里出不去
        }
        clearSession();
        message.success("已退出登录");
        // BrowserRouter 带 basename，这里用应用内相对路径（不要再拼 appPath）
        navigate("/login", { replace: true });
      },
    });
  }

  return (
    <div className="sd-topbar">
      <Space size={12}>
        <Button type="text" aria-label={collapsed ? "展开导航" : "收起导航"} onClick={onToggleCollapsed}
                icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />} />
        <Text strong style={{ fontSize: 16 }}>扫码出入库系统</Text>
        {mustChangePassword() ? (
          <Text type="warning" style={{ fontSize: 12 }}>待修改初始密码</Text>
        ) : null}
      </Space>

      <Space size={8}>
        <Button type="text" aria-label={dark ? "切换到浅色主题" : "切换到深色主题"} onClick={toggleDark}
                icon={dark ? <BulbOutlined /> : <MoonOutlined />} />
        <Dropdown
          menu={{
            items: [
              { key: "password", icon: <UserOutlined />, label: "修改密码" },
              { type: "divider" },
              { key: "logout", icon: <LogoutOutlined />, label: "退出登录", danger: true },
            ],
            onClick: ({ key }) => {
              if (key === "password") {
                navigate("/change-password");
              } else if (key === "logout") {
                confirmLogout();
              }
            },
          }}
        >
          <Space style={{ cursor: "pointer", padding: "4px 8px" }} size={8}>
            <Avatar size={28} icon={<UserOutlined />} style={{ background: "#1677FF" }} />
            <span>
              {user?.display_name || user?.username || "未登录"}
              <Text type="secondary" style={{ marginLeft: 6, fontSize: 12 }}>
                {user?.role === "admin" ? "管理员" : "操作员"}
              </Text>
            </span>
          </Space>
        </Dropdown>
      </Space>
    </div>
  );
}
