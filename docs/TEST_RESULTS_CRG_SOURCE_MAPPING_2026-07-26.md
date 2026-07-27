# CRG_source 映射库与 VM GUI 压测验证记录（2026-07-26）

> 复现边界：本文保留所列提交中的历史测试命令。复跑时必须 checkout 该提交并使用其中的旧 fresh driver；当前默认 kdebug 的 driver 需要额外固定 kverif commit 和 ELF SHA-256。

本记录对应 `rtl-rs-check 0.10.0`，最终 VM 签核固定到 GitHub 提交
`366c54114bc23f2878e0715357f7ab40f2ef7ea5`。该提交同时包含 CRG Source 映射功能、测试、文档和
CentOS/Python 3.8 下避免 Tk 工作线程 fork 死锁的 GUI 后台进程启动修复。

本记录已脱敏，不包含 VM 地址、密码、license 值、Xauthority 路径、KDB 内容、collector 二进制或完整原始日志。
下文保留固定 SHA、复现命令、现场路径、关键 marker 和文件哈希。

## 1. 被测行为

- 完整配置新增 `crg_source_mappings` 根对象，与 `excel`、`columns`、`rtl`、`position_mappings` 和
  `module_rules` 一起参与 GUI 导入、导出和六根完整校验。
- GUI 的“CRG Source映射库”页支持搜索、新建、修改、删除和原子保存；未保存修改会阻止运行，外部修改冲突不会被静默覆盖。
- Excel 中的 `CRG_source` 是用户简称。命中数据库时精确解析为 RTL 层次全路径；未命中时按用户已经填写完整路径处理。
- 解析后同时保留 `crg_source_alias` 和完整 `CRG_source`。GUI 结果、JSON report 和 CSV report 均显示或写出两者。
- CRG Source 当前只作为报告证据，不参与 PASS/FAIL，不产生 `CRG_SOURCE_*` finding，也不加入 collector 的 NPI positions。
- 专项固定样例为 `core_clock_source -> top.u_soc.u_crg_core`。

## 2. VM 环境与固定 SHA 命令

```text
CentOS 7.9
Python 3.8.13
GCC/G++ 11.2.1
Verdi/NPI O-2018.09-SP2
NPI_PLATFORM=LINUX64
DISPLAY=:0（由实际桌面进程解析，不依赖 GNOME）
```

在 VM 图形会话环境中执行：

```bash
bash /root/rscheck_bootstrap.2IAa7Gmi/repo/scripts/test_vm_fresh_checkout.sh \
  --commit 366c54114bc23f2878e0715357f7ab40f2ef7ea5
```

GitHub clone 第 1 次成功，detached HEAD 精确等于目标 SHA，最终退出码为 0。保留现场为：

```text
RUN_ROOT=/root/rscheck_fresh.1uVTEc0H
FULL_LOG=/root/rscheck_fresh.1uVTEc0H/full_vm_test.log
ARTIFACT_ROOT=/root/rscheck_fresh.1uVTEc0H/artifacts
TEST_ROOT=/root/rscheck_fresh.1uVTEc0H/artifacts/verdi_gui_test.x5JSn5yD
Verified commit=366c54114bc23f2878e0715357f7ab40f2ef7ea5
Fresh-checkout test exit code=0
```

## 3. 完整结果

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh checkout | clone 第 1 次成功；verified SHA 为目标 40 位提交 | PASS |
| Linux 全量测试 | `Ran 272 tests in 5.391s`、`OK`，无 skip | PASS |
| Excel 模板 | 两张工作表结构、公式错误扫描和 2 倍视觉渲染均通过；仓库模板与独立交付副本哈希一致 | PASS |
| collector 构建 | NPI Language Model/L0 与 L1 头文件、库均完成编译、链接和动态库检查 | PASS |
| partial KDB | 预期 elaboration error 转为 `NPI_LOAD_PARTIAL`；top、所需实例和 L0/L1 formal ports 仍可查询 | PASS |
| clean KDB / Verdi | 新生成 `kdb.elab++`；可见 Verdi 窗口严格匹配 elaborated top `top` | PASS |
| 普通在线 GUI | 真实 collector/KDB 连续 3 轮；2 行 PASS、0 error、0 warning | PASS |
| 自定义端口在线 GUI | `clock_i/reset_ni` 规则 1 轮；1 行 PASS | PASS |
| clk 存在/rst 缺失 | 连续 20 轮均为预期 FAIL；finding 精确且仅为 `RST_PORT_MISSING` | PASS |
| 在线反例 | 1 轮得到预期 FAIL；1 行、2 errors、0 warning | PASS |
| 未登记模块默认规则 | 1 轮 PASS；`has_rs_cfg_en=true`、`clk/rst_n`、physical/effective=`2/2` | PASS |
| `has_rs_cfg_en=false` | don't-care 专项连续 20 轮；任意 Excel 文本保留且 findings 为空 | PASS |
| `RS_CFG_EN=NA` | 专项连续 20 轮；RTL `RS_CRG_EN=1` 被逐行豁免，findings 为空 | PASS |
| CRG Source 映射 | 专项连续 20 轮；简称解析、GUI/JSON/CSV alias+full 和不参与判断均通过 | PASS |
| Tk 稳定性 | 同一可见 mapped Tk 窗口连续 100 轮；2 行全 PASS，无启动死锁 | PASS |
| GUI 大表负载 | 单轮渲染 10,000 行；10,000 行全 PASS | PASS |
| 十一日志门禁 | 十一份 GUI 日志逐一通过六根配置、三个数据库、端口和 schema 固定 marker 断言 | PASS |

partial KDB 中的 elaboration error 是测试夹具故意制造的兼容场景。collector 正确输出
`NPI_LOAD_PARTIAL` 并继续 fail-closed 取证，不是本轮失败。

## 4. CRG Source 专项硬证据

20 轮可见 GUI 专项的固定成功行包含：

```text
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0
mode=offline case=crg-source-mapping iterations=20 window=mapped
crg-source-map=core_clock_source->top.u_soc.u_crg_core
gui-json-csv=alias+full crg-source-check=not-judged findings=none
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules
module-rule-ports=preserved crg-source-db=crud-complete
schemas=report-v3/inventory-v2
```

smoke 在输出该 marker 前已经逐项断言：

- GUI 结果表同时显示 `core_clock_source` 和 `top.u_soc.u_crg_core`；
- report v3 的 `crg_source_alias` 保留 Excel 简称，`CRG_source` 保存解析后的完整路径；
- CSV 同时保留 alias/full；
- finding 列表为空，不存在任何 `CRG_SOURCE_*`；
- NPI inventory positions 不含 CRG Source 简称或完整路径。

普通在线正例还验证了 `crg_core -> top.u_tile.u_crg` 和
`crg_aux -> top.u_tile.u_aux_crg`，并保持 2 行 PASS、0 error、0 warning。

## 5. 十一份 GUI 日志门禁

本轮逐一验收以下日志，不能用一份成功日志代替其他入口：

```text
partial_load_gui.log
online_gui_positive.log
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

十一份日志均包含六根配置 round-trip、`module-rule-ports=preserved`、
`crg-source-db=crud-complete`、dirty export/import 和 `report-v3/inventory-v2` marker；各专项日志还通过其独有的 finding、轮次和证据断言。

## 6. Excel 模板签核

仓库模板 `examples/RS_Check_Excel_Template.xlsx` 已独立导入并检查：

- `RS_Check!A1:I3` 包含九个可映射字段的示例数据；
- `字段说明!A1:G21` 明确说明 position/CRG Source 简称映射、完整路径直通和 CRG 不参与 PASS/FAIL；
- 全工作簿公式错误扫描匹配 0 项；
- 两张工作表均完成 2 倍全区域渲染，未发现文字截断、重叠或空白内容区；
- 仓库模板与本次独立交付副本的 SHA-256 均为
  `218af080102924985d10ff2c4112cfb0e71a3d8442eae3fee62acd448ec93a3b`。

## 7. 主签核文件哈希

```text
f7e1a3576decb77d689c1eaee1a100de0d246c271a6a2ea663863e904d520a3a  full_vm_test.log
84cdabb5187376d24f572a5048cd4a49aa9848f48a18d14e80cc4211fb593074  python_tests.log
a4cb8f1fc2a54604a45da726ffb99c15aa3cb29320fa4b31c2b11116bcc54a3e  partial_load_gui.log
6538a970faa0ef5b58a0db2769be9aacfa365452efe1c17ee805ece31ba76ff8  online_gui_positive.log
0cbdd3de61ba68f48319c68840eab7fa77283d7829adbb0d9a862257051c1746  online_gui_custom_port.log
33695bf0d4f91b1c13853a4ab43549cf687ecfbccdbc75261af27ac358794933  online_gui_clk_present_rst_missing.log
68d5ca470ee59104ee5fdda0337923db1ab31bf6a94c786b5ffad80065e9bf2b  online_gui_negative.log
c483da6fc12df98bb62b27524d5f6cee518b7296a1d730d39173767e0cb7b62a  offline_gui_default_rule.log
20fdcfd2d67ea4aba3776554c5457b4378e64d327fbf5930ef3b0df27c377184  offline_gui_rs_cfg_dontcare.log
d1d961169f9d8846152490f6a2246730ba90c7d51b9f525d345293e0df3a27e0  offline_gui_rs_cfg_na.log
8551644c0297b32aa249ad2f637b929d354c2cbe77f47ef0a34c207070af66ec  offline_gui_crg_source_mapping.log
b2e8a39e3e3a3e58158a72ef9f6a7df52b8611b3ab520a81f578524177eef5f8  offline_gui_100_rounds.log
bd1735f10982e6c7eef5facddd955c9b7267f0f42945de5762f065e542d2ac17  offline_gui_10000_rows.log
5af6e7fc8d1e476bd89723694a3251cf34cad3f304035ebd4028445566e3afda  positive_report.json
8478fd8133bf1d7deb699b4ad13471177cded0ddba89e3faec9016cae7fba397  positive_report.csv
337ba98c1a2fa0d6162d79e0098656e0465cc985d76f27c5cc4f093d91799a30  positive_inventory.json
ed20c21764801a10ed689e827c811cd0943613076b83f2d34c13715bcabe4f9a  partial_load_report.json
4087ab03ef80544389715cc453731c19c03dbbd47b2cd8b596167967e08d5fb1  partial_load_inventory.json
9cdc4613b5da58432f80e4d30b369bfa9c4f3a61b2a2e738e2775c0edcda67f8  rs_npi_collector
```

## 8. 结论

`CRG_source` 已具备与 `position` 独立的简称到 RTL 全路径数据库，并完成 GUI CRUD、六根配置导入/导出、Excel 解析以及 GUI/JSON/CSV alias+full 报告闭环。未命中值继续支持完整路径直通；映射后的 CRG 路径不会进入 NPI positions，也不会改变现阶段 PASS/FAIL。

该行为已通过 GitHub fresh checkout、Linux 272 项无 skip 回归、NPI L0/L1、partial/clean elaborated KDB、可见 Verdi/Tk GUI、CRG 专项 20 轮、全部既有专项、Tk 100 轮、10,000 行负载和十一份日志门禁。
