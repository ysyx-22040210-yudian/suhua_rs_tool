# 有界递归 CRG Source 追踪与 VM GUI 压测验证记录（2026-07-27）

> 复现边界：本文保留所列提交中的历史测试命令。复跑时必须 checkout 该提交并使用其中的旧 fresh driver；当前默认 kdebug 的 driver 需要额外固定 kverif commit 和 ELF SHA-256。

本记录对应 `rtl-rs-check 0.11.0`，最终 VM 签核固定到已推送 GitHub 的完整提交
`b84be55638fd0af9fc9c3874bbc35786fd497a61`。该提交包含有界递归 CRG 来源追踪、inventory v3、report v4、GUI 深度配置、专项 smoke、当前 v3 压测 fixture，以及按完整实例 hierarchy 隔离 trace cache 和补齐 partial KDB 端口集合的修复与回归。

只有最终成功现场 `/root/rscheck_fresh.sUCQbyYo` 是本记录的签核依据。开发过程中发现 smoke/fixture 问题的两个失败 run、一次未进入测试阶段的 GitHub 网络重试，以及提交 `e4a8494` 的成功中间验证，均在第 8 节单独说明，不能代替最终 PASS 证据。

## 1. 签核范围

- 从每个 RS 实例的模块规则 `clk_port` 开始追踪上游驱动；命中上游模块 output 后，继续追踪该模块除精确名称 `clk`、`rst_n` 之外的全部 input 分支。
- `rtl.crg_trace_max_depth` 默认值为 `16`，合法范围为 `1..256`。RS 实例本身是 depth 0，直接上游模块是 depth 1；位于最大深度的模块可以命中，但不会继续展开。
- net、assign、concat、slice 和 primitive 不消耗模块深度；模块环路按最小已访问深度终止；每条 trace 最多检查 100,000 个 netlist object，不存在无上限递归。
- trace cache 键包含 RS 实例完整 hierarchy、clk formal 和连接对象；不同父层次中同名 local net、实例和时钟锥不能复用彼此的 trace。
- 展开上游模块 input 时，collector 合并 Netlist 已解析端口与 Language Model `npiPort` / NPI L1 `npi_mod_inst_get_port` 端口集合。Netlist 已经解析的同名端口不重复追踪，partial KDB 中 Netlist 漏失或未解析的 input 则由 Language/L1 视图补追。
- `CRG_source` 经映射库得到的完整实例 hierarchy 必须精确匹配；多个上游分支中任意一个分支命中即通过。
- 完整追踪未命中、达到深度上限或证据不可用时分别产生 `CRG_SOURCE_NOT_FOUND`、`CRG_TRACE_DEPTH_LIMIT`、`CRG_TRACE_UNAVAILABLE` warning。warning 不使行 FAIL，也不把 CLI 退出码改为非零；GUI/CSV/console 的行状态显示 `WARNING`。
- 模块规则支持逐模块 clk/rst formal port；未注册模块继续采用默认 `clk/rst_n`、`has_rs_cfg_en=true` 和每实例贡献一拍的规则。
- collector 输出 inventory schema v3，检查报告输出 report schema v4；旧 inventory v2 仍可读取，但没有完整递归 trace 能力。

## 2. VM 环境与精确命令

```text
CentOS Linux 7.9.2009
Linux kernel 3.10.0-1160.53.1.el7.x86_64
Python 3.8.13
GCC/G++ 11.2.1
Verdi/NPI O-2018.09-SP2
NPI_PLATFORM=LINUX64
DISPLAY=:0（由实际桌面进程解析，不依赖 GNOME 或 gnome-session-binary）
```

正式运行前先用下列可直接复制的命令核对 GitHub `main`：

```bash
ssh root@<VM_HOST> timeout 30 git -c http.version=HTTP/1.1 ls-remote https://github.com/ysyx-22040210-yudian/suhua_rs_tool.git refs/heads/main
```

核对结果为：

```text
b84be55638fd0af9fc9c3874bbc35786fd497a61	refs/heads/main
```

最终成功签核运行使用的原始命令为：

```bash
ssh root@<VM_HOST> "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=http.version GIT_CONFIG_VALUE_0=HTTP/1.1 bash /root/rscheck_bootstrap.2IAa7Gmi/repo/scripts/test_vm_fresh_checkout.sh --commit b84be55638fd0af9fc9c3874bbc35786fd497a61"
```

`GIT_CONFIG_COUNT/KEY_0/VALUE_0` 只对这次远端进程及其 fresh clone 子进程临时强制 Git HTTP/1.1，没有修改 VM 或仓库的持久 Git 配置。clone 第 1 次成功，随后 detached HEAD 被严格核对为目标 40 位 SHA。

最终现场路径：

```text
RUN_ROOT=/root/rscheck_fresh.sUCQbyYo
FULL_LOG=/root/rscheck_fresh.sUCQbyYo/full_vm_test.log
ARTIFACT_ROOT=/root/rscheck_fresh.sUCQbyYo/artifacts
TEST_ROOT=/root/rscheck_fresh.sUCQbyYo/artifacts/verdi_gui_test.vzYvinRz
REPO_ROOT=/root/rscheck_fresh.sUCQbyYo/repo_attempt1/repository
Verified commit=b84be55638fd0af9fc9c3874bbc35786fd497a61
Fresh-checkout test exit code=0
```

## 3. 完整结果矩阵

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh checkout | clone 第 1 次成功；detached HEAD 等于目标完整 SHA | PASS |
| Linux 全量测试 | `Ran 299 tests in 5.451s`、`OK`；无 skip | PASS |
| collector 编译 | `-Wall -Wextra -pedantic` 编译、L0/L1 链接和 `ldd` 全部完成；C++ 编译输出无 warning/error | PASS |
| partial KDB | 预期 elaboration error 转为 `NPI_LOAD_PARTIAL`；`top`、目标实例、formal ports 和递归 trace 仍可查询；Language/L1 端口集合补追通过 | PASS |
| clean KDB / NPI | 新生成 `kdb.elab++`；collector/NPI 输出 `Total 0 error(s), 0 warning(s)` | PASS |
| trace cache 层次隔离 | partial/clean inventory 都同时采集 `top.u_tile`、`top.u_tile_peer`；两套同名时钟锥只含各自完整 hierarchy | PASS |
| Verdi GUI | 新窗口标题精确显示 elaborated top `top`，窗口 mapped | PASS |
| partial-load Tk GUI | 1 轮；2 行 PASS、0 error、1 个预期 `NPI_LOAD_PARTIAL` warning，窗口 mapped | PASS |
| 普通在线 GUI | 真实 clean KDB/collector 连续 3 轮；2 行 PASS、0 error、0 warning | PASS |
| CRG depth-limit GUI | 最大深度 `2`，连续 20 轮；2 行仍 PASS，固定得到 6 个 `CRG_TRACE_DEPTH_LIMIT` warning | PASS |
| 自定义端口在线 GUI | `clock_i/reset_ni` 规则 1 轮；错误来源得到 1 个 `CRG_SOURCE_NOT_FOUND` warning，行保持通过 | PASS |
| clk 存在/rst 缺失 | 连续 20 轮均为预期 FAIL；只含 `RST_PORT_MISSING`，无错误的 `CLK_PORT_MISSING` | PASS |
| 在线反例 | 1 轮得到预期 FAIL；1 行、2 errors、0 warning | PASS |
| 未注册模块默认规则 | 1 轮；`clk/rst_n`、`has_rs_cfg_en=true`、physical/effective=`2/2` | PASS |
| `has_rs_cfg_en=false` | 连续 20 轮；Excel 任意文本保留，RTL 无 `RS_CRG_EN`，findings 为空 | PASS |
| `RS_CFG_EN=NA` | 连续 20 轮；RTL `RS_CRG_EN=1` 被逐行跳过，findings 为空 | PASS |
| CRG Source 映射 | 连续 20 轮；alias/full、GUI/JSON/CSV 和 depth 3 精确追踪均通过 | PASS |
| Tk 稳定性 | 同一 mapped 窗口连续 100 轮；2 行全 PASS | PASS |
| GUI 大表负载 | 单轮加载并显示 10,000 行；10,000 行全 PASS | PASS |
| 十二日志门禁 | 十二份 GUI 日志逐一通过 mapped、配置往返、数据库、端口和 schema marker 断言 | PASS |

partial KDB 的 elaboration error 是测试 fixture 故意制造的兼容场景。collector 返回 `NPI_LOAD_PARTIAL` 后继续 fail-closed 取证，是预期行为，不是本轮编译或签核错误。

## 4. 递归 CRG 追踪证据

clean 和 partial KDB 都实际包含下列多级、分支结构：

```text
RS clk -> u_occ output
u_occ non-clk/non-rst input -> u_clk_mux output
u_clk_mux non-clk/non-rst inputs -> {u_crg output, u_aux_crg output}
```

总日志中的 clean-load 固定证据：

```text
clean-load NPI clock trace evidence OK: RS->u_occ->u_clk_mux->{u_crg,u_aux_crg}, custom clock_i, witness-depth=3
CRG recursion evidence OK: depth=3, branched path accepts exact full-path match
```

在线正例的两行分别精确命中：

```text
crg_core -> top.u_tile.u_crg
crg_aux  -> top.u_tile.u_aux_crg
```

深度限制专项的固定 marker 为：

```text
mode=online case=crg-depth-limit iterations=20 window=mapped
crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6
schemas=report-v4/inventory-v3
```

这证明用户配置的模块深度确实阻止了 depth 3 目标匹配，并以 warning 而不是 error 结束。CRG Source 映射专项使用已经包含 depth 3 trace 的当前 v3 inventory，固定 marker 为：

```text
crg-source-map=core_clock_source->top.u_soc.u_crg_core
gui-json-csv=alias+full crg-source-check=pass trace-depth=3 findings=none
```

自定义端口专项还证明 trace 起点取自模块规则的 `clock_i`，不是硬编码 `clk`；故意填写不存在的完整来源路径后，结果为 `CRG_SOURCE_NOT_FOUND` warning，0 error。

同一个 `top` 下还例化了两份结构完全相同的 `tile`，其 local net、OCC、mux、CRG 和 RS 实例名均相同，只有父层次分别为 `top.u_tile` 与 `top.u_tile_peer`。partial 和 clean collector 都同时请求这两个 position，并逐实例断言前者的 trace 只包含 `top.u_tile.*`，后者只包含 `top.u_tile_peer.*`。总日志中的两个固定 marker 为：

```text
partial-load trace cache scope isolation OK: top.u_tile and top.u_tile_peer contain only their own same-named clock cones
clean-load trace cache scope isolation OK: top.u_tile and top.u_tile_peer contain only their own same-named clock cones
```

对 `partial_load_inventory.json` 和 `clean_load_inventory.json` 的独立审计结果一致：每个文件包含 2 个 scope；每个 scope 精确为 13 个实例、33 个 trace node、4 个 unique module，所有 trace node 的 foreign-prefix 计数均为 0。这直接验证 cache key 以完整实例 hierarchy 隔离，而不是仅以相同的 clk formal 或 local connection 名复用结果。partial KDB 还输出：

```text
partial NPI formal-port L0/L1 inventory evidence OK
```

追踪上游模块 input 时，Netlist 视图先记录已分类、已解析和未知端口集合；随后无条件查询 Language Model `npiPort` 并结合 NPI L1 `npi_mod_inst_get_port`。已由 Netlist 解析的同名端口跳过，Netlist 未提供或未解析的 input 由 Language/L1 补追。对于方向未知端口，只有两侧都不能分类时才因方向证据不足标为 unresolved；无法解析连接、对象预算耗尽等其他 NPI 证据问题仍会独立标为 unresolved。这样既不会重复展开同一 input，也不会因 partial KDB 返回“非空但不完整”的 Netlist 端口集合而漏掉 CRG 分支。

## 5. KDB、schema 与可见窗口证据

```text
Clean KDB=/root/rscheck_fresh.sUCQbyYo/artifacts/verdi_gui_test.vzYvinRz/example_elab/kdb.elab++
Partial KDB=/root/rscheck_fresh.sUCQbyYo/artifacts/verdi_gui_test.vzYvinRz/partial_load_elab/partial.elab++
Clean inventory=/root/rscheck_fresh.sUCQbyYo/artifacts/verdi_gui_test.vzYvinRz/clean_load_inventory.json
Online inventory=/root/rscheck_fresh.sUCQbyYo/artifacts/verdi_gui_test.vzYvinRz/positive_inventory.json
Online report=/root/rscheck_fresh.sUCQbyYo/artifacts/verdi_gui_test.vzYvinRz/positive_report.json
Partial report=/root/rscheck_fresh.sUCQbyYo/artifacts/verdi_gui_test.vzYvinRz/partial_load_report.json
Collector=/root/rscheck_fresh.sUCQbyYo/artifacts/verdi_gui_test.vzYvinRz/npi_build/rs_npi_collector
```

三个 inventory 文件的 `schema_version` 均为 `3`；其中 partial/clean inventory 的 position 集合精确为 `{top.u_tile, top.u_tile_peer}`，online positive inventory 只含业务检查所需的 `top.u_tile`。positive、partial、custom-port 和 clk-present/rst-missing 四个报告的 `schema_version` 均为 `4`。GUI smoke 进一步把它们统一断言为 `schemas=report-v4/inventory-v3`。

Verdi 实际映射窗口证据：

```text
window_id=0x2a00310
title=<Verdi:nTraceMain:1> top top (.../examples/rtl/rs_example.sv)
size=1143x745
```

partial-load Tk 窗口为 `0x2a00004`，clean KDB 主测试使用的 Tk 窗口为 `0x3400004`；全部十二份 GUI 日志均含 `window=mapped`。GUI resolver 的总日志 marker 为 `GUI access OK: source=process 3057 user=host DISPLAY=:0`，没有检查或依赖 `gnome-session-binary`。

## 6. 十二份 GUI 日志门禁

```text
partial_load_gui.log
online_gui_positive.log
online_gui_crg_trace_depth_limit.log
online_gui_custom_port.log
online_gui_clk_present_rst_missing.log
online_gui_negative.log
offline_gui_default_rule.log
offline_gui_rs_cfg_dontcare.log
offline_gui_rs_cfg_na.log
offline_gui_crg_source_mapping.log
offline_gui_100_rounds.log
offline_gui_10000_rows.log
```

每份日志都包含 `GUI_SMOKE_PASS`、`window=mapped`、六根配置往返、模块端口保留、CRG Source 数据库 CRUD、dirty export/import 和 `schemas=report-v4/inventory-v3` marker。negative 与 clk-present/rst-missing 日志中的 `state=FAIL` 是被 smoke 精确断言的预期业务反例；日志本身仍以 `GUI_SMOKE_PASS` 表示测试通过，不能把预期 FAIL 改写成产品回归。

六根配置 marker 为：

```text
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules
module-rule-ports=preserved crg-source-db=crud-complete
dirty-copy=export-preserved/import-cleared
```

## 7. 签核产物 SHA-256

以下哈希均从最终成功现场直接计算：

```text
b0b8f0d1216d4138e45f40f41af97550c180a443964d0c1217f5c46b4f36f01d  full_vm_test.log
eb674aa125dd7ddecb998eea5d9a604ef18d4bdfc9ce88859346ceca138ba430  python_tests.log
98091c9dc73cde49cdecae859bfc128b84cd912a32a6acfa9a7bef29ac128636  partial_load_gui.log
e670dcee0f41b0ae4e011b65063267893c69a2102d8aa89ea8931122956fdb5d  online_gui_positive.log
75b6b4b7bb1aaed3e8a12574bc77270f97123e03c5291ff09d0bdc7df51adf3b  online_gui_crg_trace_depth_limit.log
ebcbebba070871d532018c85fff6b2eacfecb349db7e7ecc3f5bf10f105f5481  online_gui_custom_port.log
b62e44d1096de34d76346f2d6a0f0eaf4a37d8f96ed3469d2404100bab1faaf4  online_gui_clk_present_rst_missing.log
a6669bcc4e019e1ec3e6429416311872acafe4ffa3901c476ce7c369f36091cc  online_gui_negative.log
32ebbfb15c6d3fd18363a8a97eed84110f1fe32bbfed12f3c791b72e28b8ec17  offline_gui_default_rule.log
36e48aa4e50ba924e4acb8d103844b8f7b28efa301e0f4f4495f917356c2e3df  offline_gui_rs_cfg_dontcare.log
8822e64967adf13dfc64d87d71662248157517023d84f97a635288089213d7a7  offline_gui_rs_cfg_na.log
5039579ee17a0328ecedc2280f603c4ac6b62fc6bf0800b0b991f55940693f23  offline_gui_crg_source_mapping.log
6f5e8a56f6e58ff9f61e03343a19368ef85d1fa7b287ca7e7f5066e49fec9333  offline_gui_100_rounds.log
812631c2b71915059d646947c8ef0bae18adb3545f34ca8c6a12bdf3356597cf  offline_gui_10000_rows.log
e4ecdb9539a720036a752717955502d72cc46eb6440a38dab8f8407c69f4aec6  positive_report.json
98e6693e80c2397fa8891dd219b36b435d8d693f21d1ffd2461667518eda50ef  positive_report.csv
859f25cbc7b1bb4f209c5eff430c24f49f5c06e020e055ab04d5a91a8d88f5f5  positive_inventory.json
c858de605c3e104207ef72b5c4f0e6005328fd0d4f81b82eabc7587a930b4e93  partial_load_report.json
4a1fcd11b3a061b1d47d0e590a42670dd33eb8fc8d2754fed58a0a4f27b181eb  partial_load_inventory.json
86ca70e8df76e6f6b7c3e949992b60077f37cf8747cc05b5075dd01156ad5b4c  clean_load_inventory.json
3475259a3f057c583c6e374f2fa31a7bc990e515102c9a2b007a48aa3dfb9cfa  custom_port_report.json
3184224eb0b28b90ff4b84733dd175faa1e309f945fd14e120a86fe1ec267a04  clk_present_rst_missing_report.json
11b3465c903cd46c07e563bcea539d3e71a4ca4baba643d19f009c5d276de0bd  npi_build/rs_npi_collector
492bc626627fd5ef63e9a56c8b499ee62c2839f01ff4875d459f0fea97fd1a7f  clean_load_collector.log
ae8890f54e04322efd78357c73f08b29ff79340702591626049eb0954e5d283c  partial_load.stderr
97128c8f4932bbdddc26a5e34899a660c92a0980c769740eb4b3c148e772cc34  partial_load.stdout
969ee5bf834ad2594d97171014406cf1df1fd5b677ec3d7438ce06d97c7141e3  collector_ldd.txt
559e18f1cfd6633744a75536e344485d8862dba1b4e57deaa196600444c583bf  verdi_gui.log
ea0c12a90d253c5343c4c8bf99bb11539336172c402606c94f869297d18d32cf  verdi_windows.new.txt
9419f2ae4fa074c633ad081d960fd3f5091a48517288545fb18acda9a2a1f6a7  examples/RS_Check_Excel_Template.xlsx
```

## 8. 非签核开发运行

下列现场只记录问题发现与修复链，均不能代替第 2 至 7 节的最终 PASS：

- `/root/rscheck_fresh.0hXoBbz2`，提交 `bcec5ce6774b3b4f3a3255d1be4636e8f07760c0`：CRG mapping smoke 的 CSV 局部变量在首次访问后才赋值，触发 Tk callback `UnboundLocalError` 并保持 mainloop；诊断后人工终止，退出码 143。提交 `9ae5ba14deb4eaf1f4bccf9104d8668a487ebbff` 修复变量位置并增加 fail-fast。
- `/root/rscheck_fresh.yxz0qfzJ`，提交 `9ae5ba14deb4eaf1f4bccf9104d8668a487ebbff`：CRG 专项已经通过，但 offline 100-round 首轮发现通用 smoke 仍使用 legacy inventory v2 fixture，v3 schema 门禁按预期拒绝并退出 1。提交 `743a5146331329e0b482aa8f52b6fe8ff09bbf25` 增加当前 inventory v3 example fixture。
- `/root/rscheck_fresh.3a1mH57s` 固定到当时的候选提交 `743a5146331329e0b482aa8f52b6fe8ff09bbf25`，但三次 clone 都因 GitHub EOF/443 timeout 以 128 退出，尚未 checkout 或开始任何测试。这是网络传输重试，不是代码测试失败；随后用一次性 HTTP/1.1 覆盖完成了该阶段的 fresh clone。
- `/root/rscheck_fresh.MLCYBm7Y`，提交 `e4a8494aab1ee167b409499b64367f20cf87609f`：fresh checkout、299 项 Linux 测试和全套 VM GUI 压测均成功，验证了端口集合补追实现和 hierarchy cache key 的代码合同。但该 fixture 只有一个 `top.u_tile`，没有在同一 KDB 中同时构造第二个同名 local clock cone，因而属于成功的中间开发验证，不作为最终 cache 隔离签核。

## 9. 结论

固定 SHA `b84be55638fd0af9fc9c3874bbc35786fd497a61` 已在真实 Verdi O-2018.09-SP2/NPI 环境完成 GitHub fresh checkout、299 项 Linux 测试且无 skip、collector 无 warning 构建、partial/clean elaborated KDB、可见 mapped Verdi/Tk GUI、depth 3 分支递归命中、depth 2 有界截断、逐模块 clk formal、Language/L1 input 端口集合补追、双层次同名时钟锥 cache 隔离、全部既有专项、100 轮稳定性、10,000 行负载和十二日志门禁。

签核结果确认：工具能够从 RS 实例 clk 端口递归跨越 OCC、mux、门控等中间模块的非 clk/非 rst_n 输入分支，在用户配置的有限模块深度内精确查找 CRG Source 完整层次。命中为 PASS；完整追踪未命中、深度不足或证据不可用均为可见 warning，不会被误判为无限递归、静默通过或业务 error。
