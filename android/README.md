# android/ —— 安卓端（本期**不实现**）

本期只交付电脑端。本目录用于固化**双端契约**，让 V2 接入时不回头改电脑端。

## 目标形态（V2）
Kotlin 2.0 · Jetpack Compose + Material 3 · Room 2.6.1（离线队列）· Retrofit 2.11 + OkHttp 4.12 ·
ZXing `zxing-android-embedded:4.3.0` · Min SDK 24 / Target SDK 34 · 底部导航：首页 / 扫码 / 库存 / 我的

## 接入前必须逐条核对（违反任一条都会导致数据问题）

1. 认证：所有 `/api/*` 请求必须带请求头 `X-Auth-Token`，值取自本地存储的登录令牌；收到 `401 {code:1003}` → 清本地 Token 回配对页，**不清空本地队列**。
   登录成功后**必须用响应里的新令牌覆盖本地存储**：同一设备重复登录时服务端会作废旧令牌（库里只存哈希，无法回发旧明文）。
2. 端口：以 `/api/ping` 返回的 `port` 为准（电脑可能自动换到 5001~5010），**禁止写死 5000**。
3. UDP 报文：`INVENTORY_SERVER v1.0 IP:.. PORT:.. NAME:..`，空格分隔 `KEY:VALUE`；
   `NAME` 取「`NAME:` 后到行尾」（值可含空格）；未知 KEY 忽略；缺 `IP`/`PORT` 整包丢弃。
4. 收到广播后**必须**再调 `/api/ping` 确认 HTTP 可达，才显示「可连接」（避免 UDP 通 HTTP 挂的假阳性）。
5. 扫码瞬间生成 `client_msg_id`（UUID v4）并**永久不变**存 Room；网络超时/失败后**用同一个 UUID 重发**。
   ⚠️ **超时 ≠ 失败**：请求超时绝不能标记失败或换新 UUID，否则重复入账。
6. 同步结果处理：`ok:true`（含 `duplicated:true`）→ 置 `SYNCED`；
   `code` ∈ {1007,1008,1002,1009} → 置 `FAILED` 并停止自动重试（业务错重试无用）；
   `503 {code:1012}` → 按 `Retry-After` 退避（连续 3 次暂停自动同步 60s）；`401` → 整批中止、队列保留。
7. 批量同步单次 ≤200 条，超出 APP 自行分页。
8. 响应字段可空：所有可空字段返回 `null`（键恒存在）→ data class 全部给默认值。
9. 派生值用服务端返回（`is_low_stock`、`total_qty`、`server_time`），**不要本地重算**；「今日」以服务器时钟为准。
10. 明文 HTTP：Android 9+ 用 `networkSecurityConfig` **按局域网网段**允许 cleartext，
    禁止全局 `usesCleartextTraffic=true`。
11. UDP 监听需 `ACCESS_WIFI_STATE`；`DatagramSocket(55555)` 端口占用异常必须捕获并回落「扫码配对」。
12. 后台被厂商 ROM 杀进程会延迟同步 → 需要前台服务 + 电池优化白名单引导。

## 届时使用的提示词
见 `docs/05-CodeBuddy提示词手册.md` 第 6 章（`/day2-android-pair`、`/day2-android-scan`、`/day3-android-release` 三条指令要点）。
接入前先跑一次 `/security-review` 与 `/contract-check` 确认电脑端契约无漂移。
