# RTL RS_CRG_EN 匹配与 VM GUI 压测验证记录（2026-07-26）

> 复现边界：本文保留所列提交中的历史测试命令。复跑时必须 checkout 该提交并使用其中的旧 fresh driver；当前默认 kdebug 的 driver 需要额外固定 kverif commit 和 ELF SHA-256。

本记录对应 `rtl-rs-check 0.8.1`，完整被测功能提交为
`a9a26869b99d69d3826ffb0071e967cfedbf5c92`。该提交先推送到 GitHub，再由 VM
fresh-checkout 驱动重新 clone 并核对 40 位 SHA；没有复用 Windows 开发目录中的 Python
文件、collector 或 KDB。

## 1. 命名与兼容合同

- 工具逐实例匹配的 RTL effective parameter 已从 `RS_CFG_EN` 改为 `RS_CRG_EN`。
- Excel/internal、JSON report 和 CSV 的标签字段仍为 `RS_CFG_EN`，现有九列映射和表格无需迁移。
- 配置数据库键仍为 `has_rs_cfg_en`，现有完整配置导入/导出格式无需迁移。
- finding code 仍使用 `RS_CFG_EN_*` 前缀，避免破坏既有报告消费者；finding message 明确显示
  实际缺失、非零或无法解析的是 RTL `RS_CRG_EN`。
- 工具不会把只有 RTL `RS_CFG_EN` 的实例当成具有 `RS_CRG_EN`；该情况 fail-closed，并产生
  `RS_CFG_EN_PARAMETER_MISSING`。
- `RS_CRG_EN` 由门控专用逻辑处理，不能加入新规则的 `step_parameters`。若 RTL 另有真正名为
  `RS_CFG_EN` 的 parameter，它可以作为普通动态拍参数，不与 Excel 标签字段混淆。
- inventory 保持 schema v2，report 保持 schema v3。GUI report viewer 同时接受旧 report v3 中
  作为普通动态拍参数的 `RS_CRG_EN` 和新报告中的 `RS_CFG_EN`，保证历史报告可读。

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
  --visible-tab config `
  --visible-seconds 3
```

结果：`Ran 223 tests`，`OK (skipped=44)`；179 项执行通过，44 项仅因 Windows 不适用
Linux Bash/X11/POSIX 合同而按预期跳过。`compileall`、wheel/sdist 构建和补丁检查通过。
可见 Tk 窗口成功映射，离线 smoke 为 2 行 PASS、0 error、0 warning，并包含完整配置往返、
任意表头列号映射、Position 映射和 report-v3/inventory-v2 marker。

最终功能提交构建包的 SHA-256：

```text
82827ee0cffd66eacc0e6b4f8f968c47bc55c70656e44c9c60e0f5873e5b2e03  rtl_rs_check-0.8.1-py3-none-any.whl
36cf9fa967909965ef6c498d8687644ff318c5614a5a56fa1f4438d2d4a7b968  rtl_rs_check-0.8.1.tar.gz
```

## 3. CentOS/Verdi VM fresh-checkout

环境：CentOS 7.9、Python 3.8.13、G++ 11.2.1、Verdi/NPI O-2018.09-SP2。GUI resolver
从实际桌面进程选择 `DISPLAY=:0`，不依赖 GNOME 或 `gnome-session-binary`。license 值没有进入
命令行、仓库或本记录。

在可信 bootstrap checkout 中执行的固定提交命令为：

```bash
bash scripts/test_vm_fresh_checkout.sh \
  --commit a9a26869b99d69d3826ffb0071e967cfedbf5c92
```

fresh clone 第 1 次成功，detached HEAD 精确等于请求 SHA。本轮保留现场：

```text
RUN_ROOT=/root/rscheck_fresh.8Bb5wGHM
FULL_LOG=/root/rscheck_fresh.8Bb5wGHM/full_vm_test.log
ARTIFACT_ROOT=/root/rscheck_fresh.8Bb5wGHM/artifacts
TEST_ROOT=/root/rscheck_fresh.8Bb5wGHM/artifacts/verdi_gui_test.mBvZRTiD
```

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh clone | 第 1 次成功；HEAD 精确等于被测 SHA | PASS |
| Linux 全量测试 | `Ran 223 tests in 4.439s`，`OK`，无 skip | PASS |
| collector 构建 | `-Wall -Wextra -pedantic` 通过；链接当前 Verdi `libNPI.so` | PASS |
| partial KDB | 1 个预期 elaboration error；`npi_load_design` 返回失败但 `top` 和目标证据可查询 | PASS |
| partial CLI/GUI | 2 行 PASS、0 error、1 个 `NPI_LOAD_PARTIAL` warning | PASS |
| clean KDB | `vericom`、`elabcom` 均 0 error/0 warning | PASS |
| Verdi GUI | 4 秒内出现严格匹配 elaborated top `top` 的 mapped 窗口 | PASS |
| RTL parameter 证据 | 7 个 RS 实例的 inventory/report 均包含 `RS_CRG_EN="0"` | PASS |
| 动态拍数 | 首组 physical=6、effective/expected=`5/5`、贡献 `[1,1,0,1,1,1]` | PASS |
| clean 在线 GUI 正例 | 真实 collector/KDB 连续 3 轮；2 行 PASS、0 error/0 warning | PASS |
| clean 在线 GUI 反例 | 1 行预期 FAIL、2 errors、0 warning | PASS |
| 默认模块规则 | 未登记模块 physical/effective=`2/2`，贡献 `[1,1]` | PASS |
| 离线 GUI 稳定性 | 同一 mapped Tk 窗口连续 100 轮；2 行 PASS | PASS |
| GUI 大表负载 | 同一 mapped Tk 窗口渲染 10,000 行；全部 PASS | PASS |
| 配置往返门禁 | 六份 GUI 日志逐一包含五根完整配置 marker | PASS |
| 清理 | fresh 驱动退出码 `0`，本轮 Verdi 按默认策略关闭 | PASS |

六份 GUI 日志的关键结果：

```text
partial_load_gui.log: case=partial-load iterations=1 rows=2 errors=0 warnings=1 window=mapped
online_gui_positive.log: case=positive iterations=3 rows=2 errors=0 warnings=0 window=mapped
online_gui_negative.log: case=negative iterations=1 rows=1 errors=2 warnings=0 window=mapped
offline_gui_default_rule.log: case=default-rule iterations=1 rows=1 errors=0 warnings=0 window=mapped
offline_gui_100_rounds.log: case=positive iterations=100 rows=2 errors=0 warnings=0 window=mapped
offline_gui_10000_rows.log: case=positive iterations=1 rows=10000 errors=0 warnings=0 window=mapped
```

每份日志还包含
`config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules` 和
`schemas=report-v3/inventory-v2`。在线正例、partial-load 和离线稳定性日志同时验证
`tile_core -> top.u_tile` 以及只把 RTL 全路径交给 NPI。

## 4. VM 审计文件

```text
76cb9142c8b6acf0fb793677601604dc624b70ef32ab0f33a4616f921a2613a1  full_vm_test.log
6c3f195ac4d29f6819969276703e91f9ee8ffe24967272da1845be41fc7c4205  python_tests.log
384718f2cabd07d61c339b4f3f25aa88e0aa268c92a6197faf72f1efe141c345  partial_load_gui.log
d7006200f9ed5010775d13561e5cc5eb506482d7c13fe228e68a32918f9bf742  online_gui_positive.log
48a79f296d311628d92d28256f11a49300207aae34260128021ee70598a5182c  online_gui_negative.log
8f4f94acad2472d197b2f3d67cd275a7412c0f64601a0f8c8ae5c892c48613b6  offline_gui_default_rule.log
4fbb9f6b1c72543bbb59b64025937d9f3a255e61245d4014f5568a9a5796920e  offline_gui_100_rounds.log
36f50c9e98e5f65c3945910828dfbfe62fa64deb621df1b7998499ce7e7ed603  offline_gui_10000_rows.log
ce59d9fb395a2dfa978e6891ead0582a93e7a7f3c25afced08aa9590bcfd9a6d  positive_report.json
0f3c78c2d1994be4a02413b171f597365c4a11c9b0ca19b5d8d894e508edda07  partial_load_report.json
```

仓库只提交生成、执行和断言这些结果的源码、脚本、测试与本记录，不提交 KDB、collector
二进制、完整运行日志、license 或 X11 认证数据。

## 5. 结论

`rtl-rs-check 0.8.1` 已在 Windows 可见 Tk 和 CentOS/Verdi 真实桌面完成回归。实际 RTL
门控 parameter 只匹配 `RS_CRG_EN`，同时保留现有 Excel、配置和报告的 `RS_CFG_EN`
兼容接口。修改没有改变 elaborated KDB-only 输入边界、partial-load fail-closed 策略、动态
step、任意 Excel 表头列位置、Position 映射、完整实例名或完整配置导入/导出合同。
