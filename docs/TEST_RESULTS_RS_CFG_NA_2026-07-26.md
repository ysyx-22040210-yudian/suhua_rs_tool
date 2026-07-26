# RS_CFG_EN=NA 逐行跳过与 VM GUI 压测验证记录（2026-07-26）

本记录对应 `rtl-rs-check 0.9.2`。`NA` 功能、测试、Excel 模板、GUI 压测入口和十日志门禁在提交
`ddf2ade03f8530d8166e92434023afa0c05454cc` 引入；运行时版本号和包元数据一致性测试在提交
`7570ed09879abc85c7b5e40de57fde735898e47f` 完成。两个提交均已推送至 GitHub `main`，最终 VM 签核固定到后一个完整 SHA。

本记录已脱敏，不包含 VM 地址、密码、license 值、Xauthority 路径、KDB 内容、collector 二进制或完整原始日志。下文保留了固定 SHA、复现命令、现场路径、关键 marker 和文件哈希。

## 1. 被测行为

Excel/internal `RS_CFG_EN` 先按通用规则去除首尾空白，再按下表判定：

| 解析后单元格 | `has_rs_cfg_en` | RTL `RS_CRG_EN` | 门控检查 |
|---|---|---|---|
| 精确大写 `NA` | `true` | 缺失、`0`、非零、`null` 或非法值 | 全部跳过，不产生任何 `RS_CFG_EN_*` finding |
| 精确大写 `NA` | `false` | 不存在或意外存在 | 全部跳过，不报 `PARAMETER_UNEXPECTED` |
| `假门控` | `true` | 存在且为数值 `0` | 通过严格门控检查 |
| 非 `NA` 其他文本 | `true` | 任意 | 沿用标签、参数存在性和值检查 |
| 非 `NA` 任意文本 | `false` | 不存在 | 标签 don't-care，但文本仍进入报告 |
| 非 `NA` 任意文本 | `false` | 意外存在 | `RS_CFG_EN_PARAMETER_UNEXPECTED` |

`na`、`N/A`、`Na` 均不等价于 `NA`。`NA` 只跳过该行 `RS_CFG_EN` 标签和 RTL `RS_CRG_EN`
参数检查；`position`、实例组、`RS_module`、动态 `step`、clk 和 rst 仍正常执行。`NA` 保留在 GUI、JSON 和 CSV 报告中。映射单元格中的公式和 Excel error 值仍按通用合同拒绝。

## 2. Windows 发布门禁

环境为 Windows、Python 3.11，从本机 Git checkout 执行：

```powershell
python -B -m unittest discover -s tests -v
python -B scripts\test_rscheck_gui_smoke.py `
  --project-root . `
  --rs-cfg-na `
  --iterations 20 `
  --visible-tab results `
  --visible-seconds 1
python -m compileall -q rscheck scripts tests
python -m build
D:\Git\bin\bash.exe -n scripts/test_vm_verdi_gui.sh
D:\Git\bin\bash.exe -n scripts/test_vm_fresh_checkout.sh
git diff --check
```

结果为 `Ran 248 tests in 2.745s`、`OK (skipped=45)`：203 项执行通过，45 项仅因 Windows 不适用
Linux Bash/X11/POSIX 合同而按预期跳过。本机真实 Tk GUI 连续 20 轮窗口 mapped，1 行 PASS、0 error、0 warning。`compileall`、wheel/sdist 构建、Bash 语法和补丁检查全部通过。

本轮本地成品 SHA-256：

```text
d2a1ce85f0fd074ff0eff4bd6369c165dce5620132bfb7ac4796a607e232a623  rtl_rs_check-0.9.2-py3-none-any.whl
3dfb124b270bcf49ea85db0ce4bfe6cbe4afa949f1237f8a2ab498d169c51ce2  rtl_rs_check-0.9.2.tar.gz
3e101b851c49233f5c8e45c938ecf4d1b0b15ec1f6cff1e4e865d1357ca6c3d4  RS_Check_Excel_Template.xlsx
```

## 3. Excel 模板验收

`examples/RS_Check_Excel_Template.xlsx` 通过 `@oai/artifact-tool` 导入、关键范围值/公式检查、两张工作表渲染和二次导出验收：

- `字段说明!A14:G14` 明确精确大写 `NA`、大小写边界和非门控检查继续执行；
- `RS_Check!E2:E3` 保留 `0..2147483647` 非负整数数据验证；
- `RS_Check!I2:I3` 无列表验证，允许自由填写 `NA` 或其他文本；
- 模板无公式，公式错误扫描为 0；
- 业务数据页和现有样式未被重排。

## 4. VM 固定 SHA 签核

VM 环境：

```text
CentOS 7.9
Python 3.8.13
GCC/G++ 11.2.1
Verdi/NPI O-2018.09-SP2
NPI_PLATFORM=LINUX64
DISPLAY=:0（由实际桌面进程解析，不依赖 GNOME）
```

在 VM 上执行的完整命令：

```bash
bash /root/rscheck_bootstrap.2IAa7Gmi/repo/scripts/test_vm_fresh_checkout.sh \
  --commit 7570ed09879abc85c7b5e40de57fde735898e47f
```

GitHub clone 第 1 次成功，detached HEAD 精确等于目标 SHA，运行时版本为 `0.9.2`，工作树干净。最终现场：

```text
RUN_ROOT=/root/rscheck_fresh.QF6en6iY
FULL_LOG=/root/rscheck_fresh.QF6en6iY/full_vm_test.log
ARTIFACT_ROOT=/root/rscheck_fresh.QF6en6iY/artifacts
TEST_ROOT=/root/rscheck_fresh.QF6en6iY/artifacts/verdi_gui_test.K69W7uzU
Verified commit=7570ed09879abc85c7b5e40de57fde735898e47f
Fresh-checkout test exit code=0
```

## 5. VM 完整结果

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh clone | 第 1 次成功；verified SHA 为目标 40 位提交 | PASS |
| Linux 全量测试 | `Ran 248 tests in 4.325s`，`OK`，无 skip | PASS |
| 版本一致性 | `rscheck.__version__=0.9.2`，与 `pyproject.toml` 一致 | PASS |
| collector 构建 | NPI Language Model 与 L1 头文件/库编译、链接和 `ldd` 完整 | PASS |
| partial KDB | 预期 elaboration error 转为 `NPI_LOAD_PARTIAL`；top 与所需 RTL 证据可查；GUI 1 warning | PASS |
| clean KDB / Verdi | 新生成 `kdb.elab++`；nTrace 标题严格匹配 elaborated top `top`；Verdi 可见 | PASS |
| 普通在线 GUI | 真实 collector/KDB 连续 3 轮，2 行全 PASS，窗口 mapped | PASS |
| 自定义端口在线 GUI | `clock_i/reset_ni`，1 行 PASS | PASS |
| clk 存在/rst 缺失 | 连续 20 轮均为预期 FAIL，finding 精确为 `RST_PORT_MISSING` | PASS |
| 在线反例 | 1 行预期 FAIL，2 errors，0 warning | PASS |
| 未登记模块默认规则 | `has_rs_cfg_en=true`、`clk/rst_n`、physical/effective=`2/2` | PASS |
| 旧 don't-care GUI | `has_rs_cfg_en=false` 专项 20 轮，任意文本保留，1 行 PASS | PASS |
| `RS_CFG_EN=NA` GUI | 专项 20 轮，RTL `RS_CRG_EN=1`，1 行 PASS，0 error，0 warning | PASS |
| 离线 GUI 稳定性 | 同一 mapped Tk 窗口连续 100 轮，2 行全 PASS | PASS |
| GUI 大表负载 | 单轮渲染 10,000 行，全部 PASS | PASS |
| 十日志门禁 | 十份日志逐一命中列映射、五根配置 round-trip、端口保留和 report/inventory schema marker | PASS |

partial KDB 中的 elaboration error 是测试夹具故意制造的兼容场景。collector 正确输出
`NPI_LOAD_PARTIAL` 并继续 fail-closed 取证，不是本轮失败。

## 6. `NA` GUI 硬证据

专项输入故意选择非零门控参数：

```text
RS_module=rs_pipe
RS_inst=NA_RS
module_rule.has_rs_cfg_en=true
Excel/internal RS_CFG_EN=NA
parameters.RS_CRG_EN=1
step_parameters=[rs_mode]
rs_mode=1
expected/effective step=1/1
clk/rst connection=matched
```

连续 20 轮后的固定 marker：

```text
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0
mode=offline case=rs-cfg-na iterations=20 window=mapped
rs-cfg-en=NA check=skipped rtl-rs-crg-en=1 findings=none
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules
module-rule-ports=preserved schemas=report-v3/inventory-v2
```

smoke 在输出该 marker 前已逐项校验 GUI 结果表、证据面板、finding 树、JSON report 和 CSV report。若 `NA` 丢失、RTL 参数证据不是 `1`、步数/端口证据不完整，或出现任何 finding，脚本都会非零退出。VM 总驱动还会拒绝该日志中的任何 `RS_CFG_EN_*` finding code。

## 7. 主签核文件哈希

```text
65475527b9068f155f109bda8da3bd1c6b3e377f66c353c4b56da8c6c39f500b  full_vm_test.log
f6ec43baf91af429a6a47fbd6f8af4980c849a487b8e6508d0dee504b16d3119  python_tests.log
80f087f79f593f5bb55f8ec2b13766bafd17b8727b7e7e94b13b347b3cf94e19  partial_load_gui.log
cc7b37a04255126ebedfe5cd3735d1c7fd0303c1754bb8a8aa4e0b1f0b81322d  online_gui_positive.log
4dfad5d6a52061b6699e4e876e4aeeeb226da9cc5d5369865b207ec352255f55  online_gui_custom_port.log
00f0174f0f22d9c47e3db9e5c008f77b4be5cddcb6432cd6d5c1feeeac2f2abc  online_gui_clk_present_rst_missing.log
0b58c0d3131110e834c80095145d144780c2ed2dc254b7c7ec646a30716fe8c4  online_gui_negative.log
8447e264d4ff40a97b07135cdecdc422054d2db803062088d4ac4098b23e546a  offline_gui_default_rule.log
0010ce47eae7e7b06b64bf144ae6643c6785717831d159dad1b44b3d898dc153  offline_gui_rs_cfg_dontcare.log
cd58922627df03dd2485f3c2750ae692b8800c86beda095791196c86a43e8674  offline_gui_rs_cfg_na.log
1fd443a7a1fda76404163331d3cffb07d2d0cd78c6a21a6b1888c868215bb05c  offline_gui_100_rounds.log
91dc89a157f3f22f5dcb8836226297de433cfb9a4424089ce91085f88bd13668  offline_gui_10000_rows.log
a90406e2f7514e423ba2fe75441645ae2a548713d1beb19f3db5bd9ac31e837d  positive_report.json
12d301041a8562ac575ff6499180042002903f53e2117c2916572b9632c08bf2  partial_load_report.json
481bd4c5033684fb996b3aca0ea55c01279c9025e4b4fcd12422d2ad7f7e0dfa  positive_inventory.json
b2c2828a25084a7dabfe8723f0125049da63b3ded4f6c4b74863f308484f9ea9  partial_load_inventory.json
bf71aceb2234c226ed89a040f02962833ed2cc3cf2baa6423ca53dd5ebfc026c  rs_npi_collector
```

## 8. 结论

解析后精确大写 `NA` 已成为可审计的逐行门控检查跳过值。它不受模块数据库
`has_rs_cfg_en` 真假影响，可同时跳过标签、参数存在性、意外存在、非零和未知值检查；其他行级 RTL 检查继续执行。严格大小写边界、报告保留、原有 true/false 合同、模板说明和版本一致性均已由自动测试覆盖。

该行为已通过 Windows 回归、本机可见 Tk GUI 20 轮、CentOS 无 skip 回归、GitHub fresh clone、partial/clean elaborated KDB、真实 Verdi/NPI 和完整 GUI 压测。
