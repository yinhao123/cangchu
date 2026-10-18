---
description: "Day2 上午 —— UDP 广播 + 配对二维码 + 条码打印 + 全量 API 收口"
argument-hint: "[可选：'广播先做，打印明天']"
allowed-tools: Read, Write, Edit, Bash
---

承接 Day1。**本阶段决定 APP 能不能连上来，协议字段一旦发出就不能改，谨慎。**

## 先读
`docs/01-产品需求文档.md` 5.7、5.8、12.2 → `docs/03-API接口规范.md` 第 2、8 章 → `.codebuddy/rules/40-android-contract.md` → 陷阱 T-12/T-13/T-14。

## 任务

1. **`net/udp_broadcaster.py`**：守护线程，每 3s（可配 1000~10000ms）向 UDP:55555 发送
   `INVENTORY_SERVER v1.0 IP:<ip> PORT:<实际端口> NAME:<名称>`
   - UTF-8、≤512 字节、空格分隔 `KEY:VALUE`、**`NAME` 放最后**（可含空格），未知 KEY 忽略。
   - **端口必须是实际绑定成功的端口**（自动换端口后广播要跟着变）；广播线程只在 HTTP 绑定成功后启动。
   - 发送目标：先试子网定向广播（由本机 IP + /24 推算，如 `192.168.1.255`），失败回落 `255.255.255.255`；两者都失败 → 状态栏黄条「自动发现不可用，请扫码配对」，**HTTP 不受影响，进程绝不退出**（T-13）。
   - IP 枚举：排除 `127.*`/`169.254.*`/虚拟网卡特征名，排序「默认路由 > 192.168 > 10 > 172.16-31」；**全部候选**输出到设置页供手选（T-14）。
   - IP 变化时立即补发一次。`BROADCAST_ENABLED=false` 时线程不创建（测试靠这个）。
2. **`/api/ping`（免鉴权，无 DB 查询）** 返回：`name/version/protocol/protocol_version/ip/port/server_time/db_ready/broadcast_active`。`/healthz` 返回纯文本 `ok`（DB 不可连 → `degraded` + 503）。
3. **配对二维码 `GET /api/qrcode`**：内容为紧凑 JSON `{protocol,version,host,port,name}`（**不含任何凭据**），`qrcode` 生成 PNG → base64 data URI；`/connect` 页展示二维码 + 明文 `IP:端口` + 广播状态 + 三级兜底说明（自动发现 → 扫码 → 手输 IP）。
4. **条码生成与打印**（PRD 5.7）：`python-barcode` ClassCode128 → SVG/PNG；`/barcode` 页选商品（可多选）→ 张数 1~50 → 标签网格（40×30mm 默认，2×5）→ `Ctrl+P` 打印，`@media print` 隐藏导航。无条码商品给〔一键生成内码〕。**PyInstaller 需把 barcode 字体与 config 目录加进 `datas`**（否则运行时 `FileNotFoundError`）。
5. **API 收口**：把 `docs/03-API接口规范.md` 列出的 **31 个接口全部实现并逐条比对**：字段名、类型、可空性、错误码、HTTP 状态码。特别检查：响应字段**永不省略**（可空返回 `null`）、路径未匹配返回 JSON 404 而非 HTML、`code!=0` 时 HTTP 必须非 2xx。
6. 补 `tests/api/test_contract_products.py`：对 `GET /api/products/by-barcode/<barcode>` 与 `/api/ping` 做**字段全集断言**（这是安卓端的兼容性护栏）。

## 测试

UT-18（报文构造+解析双向一致，含中文名带空格、未知 KEY 忽略、缺 PORT 整包丢弃）、UT-20 的迁移部分、契约字段断言测试。

## 门禁

`pytest -q` 全绿；**局域网实连验证**：另一台电脑浏览器打开 `http://<本机IP>:5000` 能登录；用一台安卓机或另一进程监听 55555 能收到广播并解析出正确端口；Windows 防火墙首次弹窗授权后广播才通 —— 把「取消授权」的降级表现也验一遍。

## 输出

文件清单、pytest 输出、实连验证结果（含实际 IP:端口）、以及协议字段是否与文档完全一致（有偏差立刻上报，因为这会让安卓端返工）。

补充说明：$ARGUMENTS
