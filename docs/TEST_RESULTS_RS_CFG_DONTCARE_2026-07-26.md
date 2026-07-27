# RS_CFG_EN don't-care 与 VM GUI 压测验证记录（2026-07-26）

> 复现边界：本文保留所列提交中的历史测试命令。复跑时必须 checkout 该提交并使用其中的旧 fresh driver；当前默认 kdebug 的 driver 需要额外固定 kverif commit 和 ELF SHA-256。

本记录对应 `rtl-rs-check 0.9.1`，被测功能与门禁提交为
`37ccef3bbd15a1191e85664a00e165a296699d12`。核心判定首先在提交
`396b3ee17162d00228e5027984ddaa0dbc62eb92` 中实现，随后在被测提交中加入可复现的
20 轮可见 GUI 专项和九日志门禁。两个提交都已推送到 GitHub `main`，最终签核从 GitHub
重新 clone 被测提交，并在 detached HEAD 上运行。

本记录已脱敏，不包含 VM 地址、密码、license 值、Xauthority 路径、KDB 内容、collector
二进制或完整运行日志。

## 1. 被测行为

最终规则矩阵如下：

| 模块规则 | Excel/internal `RS_CFG_EN` | RTL effective `RS_CRG_EN` | 结果 |
|---|---|---|---|
| `has_rs_cfg_en=true` | 精确 `假门控` | 存在且为数值 0 | 通过门控检查 |
| `has_rs_cfg_en=true` | 其他文本或空白 | 任意 | `RS_CFG_EN_LABEL_MISMATCH` |
| `has_rs_cfg_en=false` | 任意字面文本或空白 | 不存在 | 标签不参与 PASS/FAIL，解析后的文本仍进入报告 |
| `has_rs_cfg_en=false` | 任意字面文本或空白 | 实际存在 | `RS_CFG_EN_PARAMETER_UNEXPECTED`，不另报标签 mismatch |

映射列中的公式和 Excel 错误值仍按通用解析合同拒绝。模板 `RS_Check!I2:I3` 不再有
“假门控”列表验证，`E2:E3` 的非负整数验证保留；字段说明明确区分规则 true/false。

## 2. Windows 发布门禁

环境为 Windows、Python 3.11，工作区为本机 Git checkout。执行：

```powershell
python -B -m unittest discover -s tests -v
python -m compileall -q rscheck scripts tests
python -m build
git diff --check
```

结果为 `Ran 240 tests in 2.733s`、`OK (skipped=45)`：195 项执行通过，45 项仅因
Windows 不适用 Linux Bash/X11/POSIX 合同而按预期跳过。`compileall`、wheel/sdist 构建
和补丁检查全部通过。

本轮本地构建产物 SHA-256：

```text
e665e12aef5be3e051ad813fd807b27910bf6df672645c410ae544f368ca0ada  rtl_rs_check-0.9.1-py3-none-any.whl
15c8d1a171b2c5a45cbf33ce04a23748181ee0da920c614aad9cc9e72cdb18ba  rtl_rs_check-0.9.1.tar.gz
```

## 3. VM 固定 SHA 签核

VM 环境：

```text
CentOS 7.9
Python 3.8.13
GCC/G++ 11.2.1
Verdi/NPI O-2018.09-SP2
NPI_PLATFORM=LINUX64
DISPLAY=:0（由实际桌面进程解析，不依赖 GNOME）
```

执行命令：

```bash
bash scripts/test_vm_fresh_checkout.sh \
  --commit 37ccef3bbd15a1191e85664a00e165a296699d12
```

第一次 fresh 驱动现场 `/root/rscheck_fresh.0YMY6vfu` 的三次 GitHub clone 分别发生
HTTPS EOF、443 超时和 clone timeout。该轮没有进入产品测试，驱动按设计返回 1 并保留
三个独立失败目录。

随后从已经推送的完整 Git 历史生成 bundle。Windows 与 VM 两端 bundle SHA-256 均为：

```text
53b25520d41195dfb5ab2c2752fac44d7f7ad3c8869d3cf469c7c92c919447f7  suhua_rs_tool_37ccef3.bundle
```

VM 在 `/root/rscheck_bundle.qcwoloyq` 独立 clone，HEAD 精确等于被测 SHA且工作树干净；完整
Verdi/NPI/GUI 驱动返回 0。网络恢复后重新执行正式 fresh 驱动，GitHub clone 第 1 次成功，
最终主签核现场为：

```text
RUN_ROOT=/root/rscheck_fresh.rr7roOyi
FULL_LOG=/root/rscheck_fresh.rr7roOyi/full_vm_test.log
ARTIFACT_ROOT=/root/rscheck_fresh.rr7roOyi/artifacts
TEST_ROOT=/root/rscheck_fresh.rr7roOyi/artifacts/verdi_gui_test.6Erk4Gms
Verified commit=37ccef3bbd15a1191e85664a00e165a296699d12
Fresh-checkout test exit code=0
```

## 4. 完整结果

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh clone | 第 1 次成功；verified SHA、detached HEAD 均为目标 40 位提交；工作树干净 | PASS |
| Linux 全量测试 | `Ran 240 tests in 4.901s`，`OK`，无 skip | PASS |
| collector 构建 | NPI Language Model 与 L1 头文件/库编译、链接和 `ldd` 完整 | PASS |
| partial KDB | `npi_load_design` 报预期 elaboration error，但 top 和所需 RTL 证据仍可查询；GUI 1 warning | PASS |
| clean KDB / Verdi | 新生成 `kdb.elab++`；nTrace 标题严格匹配 elaborated top `top`，窗口 mapped | PASS |
| 普通在线 GUI | 同一 mapped Tk 窗口、真实 collector/KDB 连续 3 轮，2 行全 PASS | PASS |
| 自定义端口在线 GUI | `clock_i/reset_ni`，1 行 PASS | PASS |
| clk 存在/rst 缺失在线 GUI | 连续 20 轮均为预期 FAIL，finding 精确为 `RST_PORT_MISSING` | PASS |
| 在线反例 | 1 行预期 FAIL，2 errors，0 warning | PASS |
| 未登记模块默认规则 | `has_rs_cfg_en=true`、`clk/rst_n`、physical/effective=`2/2` | PASS |
| RS_CFG_EN don't-care GUI | 连续 20 轮，1 行 PASS，0 error，0 warning，窗口 mapped | PASS |
| 离线 GUI 稳定性 | 同一 mapped Tk 窗口连续 100 轮，2 行全 PASS | PASS |
| GUI 大表负载 | 单轮渲染 10,000 行，全部 PASS | PASS |
| 九日志门禁 | 九份日志逐一命中列映射、五根配置 round-trip、端口和 report/inventory schema marker | PASS |

partial KDB 中的 elaboration error 是测试夹具故意制造的兼容场景。collector 正确输出
`NPI_LOAD_PARTIAL` 并继续 fail-closed 取证，不是本轮失败。

## 5. don't-care GUI 证据

专项输入具有以下事实：

```text
RS_module=rs_pipe
RS_inst=DONTCARE_RS
module_rule.has_rs_cfg_en=false
Excel/internal RS_CFG_EN=任意非标准文本
parameters.RS_CRG_EN=absent
step_parameters=[rs_mode]
rs_mode=1
expected/effective step=1/1
```

连续 20 轮后的硬门禁 marker 为：

```text
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0
mode=offline case=rs-cfg-dontcare iterations=20 window=mapped
has-rs-cfg-en=false label=dont-care rs-crg-en=absent findings=none
parsed-rs-cfg-en=任意非标准文本
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules
module-rule-ports=preserved schemas=report-v3/inventory-v2
```

smoke 同时检查 GUI 结果表、JSON report、证据面板和 finding 树。若 Excel 文本丢失、规则
回退为 true、RTL 证据意外含 `RS_CRG_EN`，或出现任何 finding，脚本都会非零退出。

## 6. 主签核文件哈希

```text
0933a47288ff812b6bd93e9d00a9bd13f873ad7193c097393aefc438ec46abec  full_vm_test.log
1412fa524cf4da436887d77307dcadc2ec43aa51a8e1b8f65ca396304fa077bb  python_tests.log
80f087f79f593f5bb55f8ec2b13766bafd17b8727b7e7e94b13b347b3cf94e19  partial_load_gui.log
cc7b37a04255126ebedfe5cd3735d1c7fd0303c1754bb8a8aa4e0b1f0b81322d  online_gui_positive.log
4dfad5d6a52061b6699e4e876e4aeeeb226da9cc5d5369865b207ec352255f55  online_gui_custom_port.log
00f0174f0f22d9c47e3db9e5c008f77b4be5cddcb6432cd6d5c1feeeac2f2abc  online_gui_clk_present_rst_missing.log
0b58c0d3131110e834c80095145d144780c2ed2dc254b7c7ec646a30716fe8c4  online_gui_negative.log
8447e264d4ff40a97b07135cdecdc422054d2db803062088d4ac4098b23e546a  offline_gui_default_rule.log
0010ce47eae7e7b06b64bf144ae6643c6785717831d159dad1b44b3d898dc153  offline_gui_rs_cfg_dontcare.log
1fd443a7a1fda76404163331d3cffb07d2d0cd78c6a21a6b1888c868215bb05c  offline_gui_100_rounds.log
91dc89a157f3f22f5dcb8836226297de433cfb9a4424089ce91085f88bd13668  offline_gui_10000_rows.log
1473155f798bacea2e7e7d7907902cdc65f823f4205f285cbe724d00c8141e4e  positive_report.json
1d51184798ef75ec1ca7c18b8c79a7a817f2f17073aa63f258daa1b5aa263738  partial_load_report.json
3dd5fe2eda72bfdec407ecca0860ebbecc048221befa407fd1869668cefda456  positive_inventory.json
e5b69b87616b1f23c86ebb120d809390ab585ed5addd178256391ed91c2884f7  partial_load_inventory.json
8370286e0d3eee50dfb1432f72f98fa3fa7435b574f8b27a3c0e85ba30b4f445  rs_npi_collector
```

## 7. 结论

当显式模块规则设置 `has_rs_cfg_en=false` 时，Excel/internal `RS_CFG_EN` 已成为真正的
don't-care 标签：任意字面文本都不会产生标签 finding，解析后的内容仍可在 GUI 和报告中
查看。RTL 若实际仍存在 `RS_CRG_EN`，独立的参数存在性检查仍会报
`RS_CFG_EN_PARAMETER_UNEXPECTED`；规则 true 的严格 `假门控 + RS_CRG_EN=0` 合同未改变。

该行为已在 Windows 回归、CentOS 无 skip 回归、独立 bundle clone、GitHub fresh clone、
可见 Tk GUI 20 轮专项以及完整 Verdi/NPI/GUI 压测中通过。
