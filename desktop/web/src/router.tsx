import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import PageLoading from "./components/PageLoading";
import AppShell from "./layout/AppShell";
import RequireAuth from "./layout/RequireAuth";
import NotFound from "./pages/NotFound";

/**
 * 路由表（PRD 7.1）。
 *
 * ★除登录/激活/改密外，所有页面都套「RequireAuth + AppShell」：
 *   守卫负责恢复身份与角色，外壳负责导航与状态栏，页面本身只管内容。
 * ★每页 lazy：首屏只加载登录/外壳那几个 chunk，内网千兆下目标 <2s。
 * ★`adminOnly` 的页面**再套一层** RequireAuth —— 服务端仍然会独立校验（require_admin），
 *   前端这层只是不让操作员撞上一屏 403。
 */
const Login = lazy(() => import("./pages/Login"));
const Activate = lazy(() => import("./pages/Activate"));
const ChangePassword = lazy(() => import("./pages/ChangePassword"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const StockIO = lazy(() => import("./pages/StockIO"));
const Products = lazy(() => import("./pages/Products"));
const Partners = lazy(() => import("./pages/Partners"));
const Warehouses = lazy(() => import("./pages/Warehouses"));
const Logs = lazy(() => import("./pages/Logs"));
const CurrentStock = lazy(() => import("./pages/CurrentStock"));
const Summary = lazy(() => import("./pages/Summary"));
const Labels = lazy(() => import("./pages/Labels"));
const Settings = lazy(() => import("./pages/Settings"));
const Users = lazy(() => import("./pages/Users"));
const Backup = lazy(() => import("./pages/Backup"));
const Connect = lazy(() => import("./pages/Connect"));
const PurchaseOrders = lazy(() => import("./pages/PurchaseOrders"));
const PurchaseInbounds = lazy(() => import("./pages/PurchaseInbounds"));
const OperationOrders = lazy(() => import("./pages/OperationOrders"));
const Stocktakes = lazy(() => import("./pages/Stocktakes"));
const StocktakeDetail = lazy(() => import("./pages/StocktakeDetail"));
const SalesOrders = lazy(() => import("./pages/SalesOrders"));
const SalesReturns = lazy(() => import("./pages/SalesReturns"));
const CustomerStatement = lazy(() => import("./pages/CustomerStatement"));
const Approvals = lazy(() => import("./pages/Approvals"));
const ImportExport = lazy(() => import("./pages/ImportExport"));

export default function AppRoutes() {
  return (
    <Suspense fallback={<PageLoading />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/activate" element={<Activate />} />
        <Route path="/change-password" element={<ChangePassword />} />

        <Route element={<RequireAuth><AppShell /></RequireAuth>}>
          <Route index element={<Dashboard />} />

          {/* 出入库：一个页面用 Segmented 切换 入库/出库/盘点，扫码台只做一份 */}
          <Route path="stock/io" element={<StockIO />} />

          {/* 单据（docs/13）：采购订单与入库单不碰库存，审批通过才过账；操作单建议走审批 */}
          <Route path="wms/purchase-orders" element={<PurchaseOrders />} />
          <Route path="wms/purchase-inbounds" element={<PurchaseInbounds />} />
          <Route path="wms/operations" element={<OperationOrders />} />
          {/* 盘点单：列表建单 → 详情录实盘数 → 提交 → admin 过账（写 ADJUST 流水） */}
          <Route path="wms/stocktakes" element={<Stocktakes />} />
          <Route path="wms/stocktakes/:id" element={<StocktakeDetail />} />
          {/* 销货（docs/14 第 6 章）：审批 ≠ 出库，发货才扣库存；退货审批即入库 */}
          <Route path="wms/sales-orders" element={<SalesOrders />} />
          <Route path="wms/sales-returns" element={<SalesReturns />} />
          <Route path="wms/customer-statement" element={<CustomerStatement />} />
          <Route path="wms/approvals"
                 element={<RequireAuth adminOnly><Approvals /></RequireAuth>} />

          {/* 库存报表：流水查询里的「冲销」动作只对管理员开放（后端 require_admin 兜底） */}
          <Route path="stock/logs" element={<Logs />} />
          <Route path="stock/current" element={<CurrentStock />} />
          <Route path="stock/summary" element={<Summary />} />

          {/* 基础资料 */}
          <Route path="products" element={<Products />} />
          <Route path="partners" element={<Partners />} />
          <Route path="warehouses"
                 element={<RequireAuth adminOnly><Warehouses /></RequireAuth>} />
          {/* 批量导入：operators 也能导商品/往来单位；期初库存由服务端再拦一道（仅 admin） */}
          <Route path="imports" element={<ImportExport />} />

          {/* 标签打印：任何已登录用户都能打印 */}
          <Route path="labels" element={<Labels />} />

          {/* 手机配对：任何已登录用户都能看（现场排障要用） */}
          <Route path="connect" element={<Connect />} />

          {/* 系统管理：仅管理员（服务端 require_admin 是最终裁判） */}
          <Route path="settings"
                 element={<RequireAuth adminOnly><Settings /></RequireAuth>} />
          <Route path="users" element={<RequireAuth adminOnly><Users /></RequireAuth>} />
          <Route path="backup" element={<RequireAuth adminOnly><Backup /></RequireAuth>} />

          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
