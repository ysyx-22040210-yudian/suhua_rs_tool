# 逐模块 clk/rst 端口与 CRG 暂停判定验证记录（2026-07-26）

本记录对应 `rtl-rs-check 0.9.0`，完整被测功能提交为
`2e90d6636accee3d5450a1feac64dc2f36edc608`。该提交先推送到 GitHub，再由 VM
fresh-checkout 驱动重新 clone 并核对 40 位 SHA；没有复用 Windows 开发目录中的 Python
文件、collector 或 KDB。

## 1. 功能合同

- 每个 `module_rules.<RS_module>` 支持独立的 `clk_port`、`rst_port` formal port 名。
- GUI 新建或修改规则时，两个输入留空分别规范化为 `clk`、`rst_n`。
- 未登记模块的隐式默认规则固定使用 `clk`、`rst_n`。
- 为兼容旧配置，旧显式规则缺少这两个键时继承历史全局
  `rtl.clk_port/rst_port`；下一次保存规则库或导出完整配置时显式写回。
- checker 只使用最终模块规则指定的端口，不再用全局端口覆盖逐模块规则。实例的实际
  `module` 与 Excel `RS_module` 不匹配时不附加误导性的 missing-port finding。
- collector 枚举每个实例的全部 formal ports，并以 NPI L1
  `npi_mod_inst_get_port` 补采 partial KDB 中 Language Model 关系缺失的端口。
- `CRG_source` 仍为必填映射属性并原样进入报告，但当前完全不参与 PASS/FAIL。新采
  `clk_sources` 固定为 `[]`；旧 inventory 的三类 clock-source traversal warning 也不再
  转换为 `NPI_UNRESOLVED`，其余 collector warning 仍 fail-closed。
- 当前仍要求每条模块规则提供一个 rst formal port；没有 rst 端口的模块会产生
  `RST_PORT_MISSING`，本版本没有“跳过 rst”开关。

## 2. Windows 发布门禁

环境：Windows，Python 3.11，工作区 `D:\suhua_rs_tool`。

```powershell
Set-Location D:\suhua_rs_tool
python -B -m unittest discover -s tests -v
python -m compileall -q rscheck scripts tests
python -m build
git diff --check
python -B scripts/test_rscheck_gui_smoke.py `
  --project-root D:\suhua_rs_tool `
  --iterations 1 `
  --visible-tab rules `
  --visible-seconds 1
```

结果：`Ran 233 tests`，`OK (skipped=44)`；189 项执行通过，44 项仅因 Windows 不适用
Linux Bash/X11/POSIX 合同而按预期跳过。`compileall`、wheel/sdist 构建和补丁检查通过。
可见 Tk 窗口成功映射，规则页在 `980x680` 下无溢出；离线 smoke 为 2 行 PASS、0 error、
0 warning，并命中逐模块端口保存、完整配置往返和 report-v3/inventory-v2 marker。

最终功能提交构建包的 SHA-256：

```text
13cde5c5b13c30e3046d08f637ea40b2dcd012e3145812ad4c216048219bbc62  rtl_rs_check-0.9.0-py3-none-any.whl
bc27f745f7b196f91c770282dd629e09ae34324ebf432d5a4eca80bdf5c8fcfd  rtl_rs_check-0.9.0.tar.gz
```

## 3. CentOS/Verdi VM fresh-checkout

环境：CentOS 7.9、Python 3.8.13、G++ 11.2.1、Verdi/NPI O-2018.09-SP2。GUI resolver
从实际桌面进程选择 `DISPLAY=:0`，不依赖 GNOME 或 `gnome-session-binary`。license 值没有进入
命令行、仓库或本记录。

在可信 bootstrap checkout 中执行的固定提交命令为：

```bash
bash scripts/test_vm_fresh_checkout.sh \
  --commit 2e90d6636accee3d5450a1feac64dc2f36edc608
```

fresh clone 第 1 次成功，detached HEAD 精确等于请求 SHA。本轮保留现场：

```text
RUN_ROOT=/root/rscheck_fresh.HchhVeCy
FULL_LOG=/root/rscheck_fresh.HchhVeCy/full_vm_test.log
ARTIFACT_ROOT=/root/rscheck_fresh.HchhVeCy/artifacts
TEST_ROOT=/root/rscheck_fresh.HchhVeCy/artifacts/verdi_gui_test.8hOTCxyW
```

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh clone | 第 1 次成功；HEAD 精确等于被测 SHA | PASS |
| Linux 全量测试 | `Ran 233 tests in 4.473s`，`OK`，无 skip | PASS |
| collector 构建 | `-Wall -Wextra -pedantic` 通过；同时链接 `libnpiL1.so`、`libNPI.so` | PASS |
| partial KDB | 1 个预期 elaboration error；top 仍可查询，L0/L1 合并后全部 formal ports 完整 | PASS |
| partial CLI/GUI | 2 行 PASS、0 error、1 个 `NPI_LOAD_PARTIAL` warning | PASS |
| clean KDB | `vericom`、`elabcom` 均 0 error、0 warning | PASS |
| Verdi GUI | 4 秒内出现严格匹配 elaborated top `top` 的 mapped 窗口 | PASS |
| 全端口 inventory | `rs_pipe={clk,rst,d,q}`；`rs_custom={clock_i,reset_ni,d,q}`；新采 `clk_sources=[]` | PASS |
| 动态拍数 | 首组 physical=6、effective/expected=`5/5`、贡献 `[1,1,0,1,1,1]` | PASS |
| clean 在线 GUI 正例 | 真实 collector/KDB 连续 3 轮；2 行 PASS、0 error、0 warning | PASS |
| 自定义端口 CLI | `CUSTOM_RS` 使用 `clock_i/reset_ni`；1 行 PASS | PASS |
| 自定义端口在线 GUI | `case=custom-port`；1 行 PASS、0 error、0 warning | PASS |
| CRG 暂停判定 | 专用行填写 `intentionally_wrong_source`，仍 PASS 且报告保留原值 | PASS |
| clean 在线 GUI 反例 | 1 行预期 FAIL、2 errors、0 warning | PASS |
| 默认模块规则 | 未登记模块使用 `clk/rst_n`，physical/effective=`2/2`，贡献 `[1,1]` | PASS |
| 离线 GUI 稳定性 | 同一 mapped Tk 窗口连续 100 轮；2 行 PASS | PASS |
| GUI 大表负载 | 同一 mapped Tk 窗口渲染 10,000 行；全部 PASS | PASS |
| 配置往返门禁 | 七份 GUI 日志逐一包含五根完整配置和 `module-rule-ports=preserved` marker | PASS |
| 清理 | fresh 驱动退出码 `0`，本轮 Verdi 按默认策略关闭 | PASS |

七份 GUI 日志的关键结果：

```text
partial_load_gui.log: case=partial-load iterations=1 rows=2 errors=0 warnings=1 window=mapped
online_gui_positive.log: case=positive iterations=3 rows=2 errors=0 warnings=0 window=mapped
online_gui_custom_port.log: case=custom-port iterations=1 rows=1 errors=0 warnings=0 rule-ports=clock_i/reset_ni window=mapped
online_gui_negative.log: case=negative iterations=1 rows=1 errors=2 warnings=0 window=mapped
offline_gui_default_rule.log: case=default-rule iterations=1 rows=1 errors=0 warnings=0 rule-ports=clk/rst_n window=mapped
offline_gui_100_rounds.log: case=positive iterations=100 rows=2 errors=0 warnings=0 window=mapped
offline_gui_10000_rows.log: case=positive iterations=1 rows=10000 errors=0 warnings=0 window=mapped
```

每份日志还包含
`config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules`、
`module-rule-ports=preserved` 和 `schemas=report-v3/inventory-v2`。在线普通正例、在线自定义端口、
partial-load 和离线稳定性日志同时验证 `tile_core -> top.u_tile` 以及只把 RTL 全路径交给 NPI。

## 4. VM 审计文件

```text
aa8a48084ee0ad7262f820e00382e91986e57fe22a1c10855dc2d01537940519  full_vm_test.log
82aa3985472d7e5e0b9872f2ed08c31a7f16024273611d5dfa7b0c2bd9779274  python_tests.log
80f087f79f593f5bb55f8ec2b13766bafd17b8727b7e7e94b13b347b3cf94e19  partial_load_gui.log
cc7b37a04255126ebedfe5cd3735d1c7fd0303c1754bb8a8aa4e0b1f0b81322d  online_gui_positive.log
4dfad5d6a52061b6699e4e876e4aeeeb226da9cc5d5369865b207ec352255f55  online_gui_custom_port.log
0b58c0d3131110e834c80095145d144780c2ed2dc254b7c7ec646a30716fe8c4  online_gui_negative.log
8447e264d4ff40a97b07135cdecdc422054d2db803062088d4ac4098b23e546a  offline_gui_default_rule.log
1fd443a7a1fda76404163331d3cffb07d2d0cd78c6a21a6b1888c868215bb05c  offline_gui_100_rounds.log
91dc89a157f3f22f5dcb8836226297de433cfb9a4424089ce91085f88bd13668  offline_gui_10000_rows.log
2fdd19de60f6abcea653ab07ad907bb64c7e88650db499e0576e98b25614e294  positive_report.json
60cc2d5f03fd84b061b448ece710da7744be13d581f421b41ab8211dc6d1f6fc  custom_port_report.json
43bf05148ca572c2a1529b1e2ea9960678280ecc7b73e333d554faec631ae83d  partial_load_report.json
87fc95e5319721d869b0c4c3bbac8e5793c6d2b69d162823c1790d5c596ad755  positive_inventory.json
a7d872d5236486eb2cefbd52a0f75ddfe5b340d204738591650dd4b324d5753d  rs_npi_collector
```

仓库只提交生成、执行和断言这些结果的源码、脚本、测试与本记录，不提交 KDB、collector
二进制、完整运行日志、license 或 X11 认证数据。

## 5. 结论

`rtl-rs-check 0.9.0` 已在 Windows 可见 Tk 和 CentOS/Verdi 真实桌面完成回归。GUI 可为每种
`RS_module` 维护并导入/导出独立的 clk/rst formal port 名；真实 NPI collector 能在 clean 和
partial elaborated KDB 中提供这些端口，checker 使用逐模块规则完成连线判断。`CRG_source`
当前只保留为规格与报告证据，不影响 PASS/FAIL。修改没有放宽 module、parameter、step、
position、端口存在性或端口连线的其他 fail-closed 检查。
