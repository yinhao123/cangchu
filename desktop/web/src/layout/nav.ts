import {
  AccountBookOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BarChartOutlined,
  BarcodeOutlined,
  CloudUploadOutlined,
  ContainerOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  ImportOutlined,
  InboxOutlined,
  MobileOutlined,
  ProfileOutlined,
  DiffOutlined,
  RollbackOutlined,
  TagOutlined,
  SettingOutlined,
  ShopOutlined,
  ShoppingCartOutlined,
  SwapOutlined,
  UserSwitchOutlined,
  WalletOutlined,
} from "@ant-design/icons";
import type { ComponentType } from "react";

/**
 * 侧边导航的唯一配置表。
 *
 * ★分组与顺序对齐 PRD 7.1 / 28.2；`adminOnly` 只控制**入口显隐**，
 *   真正的拦截在服务端（`require_admin`）与 `RequireAuth` 上 —— 藏起来不等于挡住。
 */
export interface NavItem {
  key: string;
  label: string;
  path: string;
  group: string;
  icon: ComponentType;
  adminOnly?: boolean;
}

export const NAV_GROUP_ORDER: string[] = ["工作台", "出入库", "单据", "库存报表", "基础资料", "系统管理"];

export const NAV_ITEMS: NavItem[] = [
  { key: "dashboard", label: "仪表盘", path: "/", group: "工作台", icon: AppstoreOutlined },
  { key: "io", label: "出入库", path: "/stock/io", group: "出入库", icon: ImportOutlined },
  // 单据：采购订单 → 采购入库（审批即过账）→ 领用/退库/借用/调拨（docs/13 第 3 章）
  { key: "purchase-orders", label: "采购订单", path: "/wms/purchase-orders", group: "单据", icon: ShoppingCartOutlined },
  { key: "purchase-inbounds", label: "采购入库", path: "/wms/purchase-inbounds", group: "单据", icon: InboxOutlined },
  { key: "operations", label: "领用 / 借还 / 调拨", path: "/wms/operations", group: "单据", icon: SwapOutlined },
  // 盘点单（P0-2）：任务 → 录入 → 差异 → 过账；过账写 ADJUST 流水
  { key: "stocktakes", label: "盘点单", path: "/wms/stocktakes", group: "单据", icon: DiffOutlined },
  // 销货（P1-4）：审批只改状态，发货才出库；退货审批即入库
  { key: "sales-orders", label: "销货单", path: "/wms/sales-orders", group: "单据", icon: TagOutlined },
  { key: "sales-returns", label: "销售退货", path: "/wms/sales-returns", group: "单据", icon: RollbackOutlined },
  { key: "customer-statement", label: "客户对账", path: "/wms/customer-statement", group: "库存报表", icon: AccountBookOutlined },
  { key: "approvals", label: "审批待办", path: "/wms/approvals", group: "单据", icon: AuditOutlined, adminOnly: true },
  { key: "current", label: "库存现状", path: "/stock/current", group: "库存报表", icon: ContainerOutlined },
  { key: "logs", label: "流水查询", path: "/stock/logs", group: "库存报表", icon: FileSearchOutlined },
  { key: "summary", label: "汇总报表", path: "/stock/summary", group: "库存报表", icon: BarChartOutlined },
  { key: "products", label: "商品与条码", path: "/products", group: "基础资料", icon: BarcodeOutlined },
  { key: "partners", label: "客户 / 供应商", path: "/partners", group: "基础资料", icon: ShopOutlined },
  { key: "warehouses", label: "仓库", path: "/warehouses", group: "基础资料", icon: DatabaseOutlined, adminOnly: true },
  { key: "imports", label: "批量导入", path: "/imports", group: "基础资料", icon: CloudUploadOutlined },
  { key: "labels", label: "标签打印", path: "/labels", group: "系统管理", icon: ProfileOutlined },
  { key: "connect", label: "手机配对", path: "/connect", group: "系统管理", icon: MobileOutlined },
  { key: "settings", label: "系统设置", path: "/settings", group: "系统管理", icon: SettingOutlined, adminOnly: true },
  { key: "users", label: "账号与设备", path: "/users", group: "系统管理", icon: UserSwitchOutlined, adminOnly: true },
  { key: "backup", label: "备份与恢复", path: "/backup", group: "系统管理", icon: WalletOutlined, adminOnly: true },
];
