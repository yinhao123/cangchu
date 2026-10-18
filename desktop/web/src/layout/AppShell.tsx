import { useEffect, useState } from "react";
import { Drawer, Grid, Layout } from "antd";
import { Outlet, useLocation } from "react-router-dom";
import SideNav from "./SideNav";
import StatusBar from "./StatusBar";
import TopBar from "./TopBar";

const { Sider, Header, Content, Footer } = Layout;
const { useBreakpoint } = Grid;

/**
 * 控制台外壳：左侧导航（208px）+ 顶栏（56px）+ 内容区 + 底部状态栏（32px）。
 *
 * ★<992px 导航收成图标条，<768px 变抽屉：仓库现场有 1366×768 的老机器，
 *   内容区绝不因为导航挤掉宽度 —— 表格宁可可横向滚动（PRD 28.3）。
 */
export default function AppShell() {
  const screens = useBreakpoint();
  const location = useLocation();
  const compact = !screens.lg;
  const [collapsed, setCollapsed] = useState(compact);
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    setCollapsed(compact);
  }, [compact]);

  useEffect(() => {
    setDrawerOpen(false);
  }, [location.pathname]);

  const useDrawer = !screens.md;

  return (
    <Layout style={{ minHeight: "100vh" }}>
      {useDrawer ? (
        <Drawer placement="left" open={drawerOpen} onClose={() => setDrawerOpen(false)}
                width={248} styles={{ body: { padding: 0 }, header: { display: "none" } }}>
          <div style={{ paddingTop: 12 }}>
            <SideNav />
          </div>
        </Drawer>
      ) : (
        <Sider
          theme="light"
          collapsedWidth={64}
          collapsed={collapsed}
          width={208}
          style={{ borderInlineEnd: "1px solid var(--sd-border)", position: "sticky",
            top: 0, height: "100vh", overflow: "auto" }}
        >
          <div className="sd-sider-brand">
            <span className="sd-sider-logo">扫</span>
            {!collapsed ? <span className="sd-sider-title">扫码出入库</span> : null}
          </div>
          <SideNav />
        </Sider>
      )}

      <Layout>
        <Header style={{ background: "var(--sd-surface)", borderBottom: "1px solid var(--sd-border)",
          position: "sticky", top: 0, zIndex: 10, padding: 0 }}>
          <TopBar
            collapsed={useDrawer ? true : collapsed}
            onToggleCollapsed={() => {
              if (useDrawer) {
                setDrawerOpen(true);
              } else {
                setCollapsed((prev) => !prev);
              }
            }}
          />
        </Header>

        <Content style={{ padding: 16 }}>
          <Outlet />
        </Content>

        <Footer style={{ padding: 0, height: 32, lineHeight: "32px" }}>
          <StatusBar />
        </Footer>
      </Layout>
    </Layout>
  );
}
