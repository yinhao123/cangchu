import { useMemo } from "react";
import { Menu } from "antd";
import type { MenuProps } from "antd";
import { useLocation, useNavigate } from "react-router-dom";
import { NAV_GROUP_ORDER, NAV_ITEMS } from "./nav";
import { isAdmin } from "../store/session";

/**
 * 侧边导航：分组 + 图标 + 选中胶囊。
 *
 * ★高亮按「最长匹配」算：`/products/new` 也要让「商品与条码」保持选中，
 *   否则进入子页面后左侧就不亮了，用户会以为走出了模块。
 */
export default function SideNav() {
  const location = useLocation();
  const navigate = useNavigate();
  const admin = isAdmin();

  const items = useMemo<MenuProps["items"]>(() => {
    const visible = NAV_ITEMS.filter((item) => !item.adminOnly || admin);
    return NAV_GROUP_ORDER.map((group) => {
      const children = visible.filter((item) => item.group === group);
      if (children.length === 0) {
        return null;
      }
      return {
        type: "group" as const,
        key: group,
        label: group,
        children: children.map((item) => ({
          key: item.path,
          icon: <item.icon />,
          label: item.label,
        })),
      };
    }).filter((group) => group !== null);
  }, [admin]);

  const selected = useMemo(() => {
    const path = location.pathname;
    const matched = NAV_ITEMS
      .map((item) => item.path)
      .filter((candidate) => candidate === "/" ? path === "/" : path.startsWith(candidate))
      .sort((a, b) => b.length - a.length);
    return matched[0] ?? "/";
  }, [location.pathname]);

  return (
    <Menu
      mode="inline"
      items={items}
      selectedKeys={[selected]}
      onClick={({ key }) => navigate(key)}
      style={{ borderInlineEnd: "none", background: "transparent", paddingBottom: 24 }}
    />
  );
}
