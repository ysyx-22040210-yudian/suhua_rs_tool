# 任意 Excel 表头与列号映射 GUI/NPI 验证记录（2026-07-25）

本记录对应 `rtl-rs-check 0.7.0`，被测功能提交为
`25e941d15a08a9f20d5feb9dafc5dce90fb5c3a6`。本轮验证确认：

- `Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、
  `CRG_source`、`RS_CFG_EN` 是工具内部属性键，不是 Excel 固定表头；
- 每个属性只由用户配置的 1-based `columns` 列号取值，允许业务表头、乱序列、非连续列和额外列；
- 默认 `validate_headers=false`，GUI 默认不勾选“严格校验表头（可选）”；
- 显式 `validate_headers=true`、`--header-check` 或 GUI 勾选时，仍可把标准内部属性名作为严格诊断表头；
- NPI 和 Verdi GUI 仍只接收 `elabcom` 生成的 elaborated KDB，不接收 filelist、RTL 或 `work.lib++`。

## 1. Windows 本机发布门禁

环境：Windows，工作区 `D:\suhua_rs_tool`，Python 3.11。

实际执行：

```powershell
Set-Location D:\suhua_rs_tool
python -B -m unittest discover -s tests -v
python -m compileall -q rscheck scripts tests
python -B -m build
python -B scripts/test_rscheck_gui_smoke.py `
  --project-root . `
  --iterations 1 `
  --visible-seconds 3 `
  --visible-tab results
git diff --check
```

结果：`Ran 191 tests in 2.136s`，`OK (skipped=44)`；147 项执行通过，44 项仅因
Windows 不适用 Linux Bash/X11/POSIX 合同而按预期跳过。源码编译、补丁检查、wheel 和 sdist
构建均通过。可见 Tk GUI 显示 2 行 PASS、0 error、0 warning，并输出：

```text
GUI_SMOKE_PASS: state=PASS rows=行数 2 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=1 window=mapped ... header-map=column-index strict-header=false position-map=tile_core->top.u_tile npi-positions=full-path-only schemas=report-v3/inventory-v2
```

构建产物 SHA-256：

```text
cfe78ee540df2b2105e52237083a1cf683aa55723ee1c54196355dd25b842f4e  rtl_rs_check-0.7.0-py3-none-any.whl
f4ea3a036962373aab50117dbc31033ecc4423d8bb294042fe9265787f0b6f70  rtl_rs_check-0.7.0.tar.gz
```

示例 CSV 和 XLSX 均使用“接口分类”“模块类型”等中文业务表头。默认配置分别解析出相同的两行；
对同一文件显式传 `--header-check` 时返回退出码 2，并逐列报告业务表头与内部属性名不一致。

Excel 模板通过 artifact-tool 重新导入、两张工作表渲染、关键范围检查和公式错误扫描。两个 Excel
table 的 `TableColumn` 元数据与可见中文表头一致；公式错误为 0；`E2:E3` 保留非负整数验证，
`I2:I3` 保留 `假门控` 列表验证。模板 SHA-256：

```text
54b8fa9f73a429c95cda4c6122ceac5812592f45ca4273de17c303a127991d4f  RS_Check_Excel_Template.xlsx
```

## 2. CentOS/Verdi VM fresh-checkout

环境：CentOS 7.9、Python 3.8.13、G++ 11.2.1、Verdi/NPI O-2018.09-SP2。
非交互 root shell 没有预设 `DISPLAY`；GUI resolver 从实际桌面进程选中 `DISPLAY=:0`，不要求
GNOME 或 `gnome-session-binary`。license 值没有进入命令行、仓库或测试记录。

从可信 bootstrap checkout 执行以下等价命令；fresh 驱动重新从 GitHub clone，checkout 并核对
完整提交 SHA，然后只运行新 clone 中的脚本：

```bash
cd /root/rscheck_fresh.iRv3TMgE/repo_attempt1/repository
GUI_ONLINE_ITERATIONS=3 \
GUI_STRESS_ITERATIONS=100 \
GUI_LOAD_ROWS=10000 \
GUI_VISIBLE_SECONDS=10 \
bash scripts/test_vm_fresh_checkout.sh \
  --commit 25e941d15a08a9f20d5feb9dafc5dce90fb5c3a6
```

本轮实际路径：

```text
RUN_ROOT=/root/rscheck_fresh.PcXzP33s
ARTIFACT_ROOT=/root/rscheck_fresh.PcXzP33s/artifacts
TEST_ROOT=/root/rscheck_fresh.PcXzP33s/artifacts/verdi_gui_test.NS3P5A2m
```

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh clone | 第 1 次 clone 成功，完整 HEAD 等于被测提交 | PASS |
| Linux 全量测试 | `Ran 191 tests in 4.056s`，`OK`，无 skip | PASS |
| 任意表头前置证据 | 中文业务表头与内部属性名无交集；9 个属性按 1-based 列号映射；strict 默认关闭 | PASS |
| NPI collector | 使用实际 `npi.h`/`libNPI.so` 构建，`ldd` 指向 Verdi NPI 库 | PASS |
| fresh elaborated KDB | `vericom`、`elabcom` 均 0 error/0 warning，设计输入为 `kdb.elab++` | PASS |
| Verdi GUI | 2 秒后新窗口严格匹配已加载 elaborated top `top` | PASS |
| 在线 CLI 正例 | 2 行 PASS，0 error/0 warning；position 映射与动态 step 证据正确 | PASS |
| 在线 GUI 正例 | 真实 collector/KDB 连续 3 轮，2 行 PASS | PASS |
| 在线 GUI 预期反例 | 1 行 FAIL、2 errors、0 warning；GUI 正确显示失败，smoke 返回成功 | PASS |
| 默认模块规则 | 未登记模块 physical/effective=`2/2`，贡献 `[1,1]` | PASS |
| 离线 GUI 稳定性 | 同一可见 Tk 窗口连续 100 轮，2 行 PASS | PASS |
| GUI 大表负载 | 同一可见 Tk 窗口完成 10,000 行，10,000 行 PASS | PASS |
| 表头合同日志 | 在线正例/反例、默认规则、100 轮、10,000 行五份日志均含列号映射证据 | PASS |
| 清理 | fresh 驱动退出码 0；本轮 Verdi 进程已关闭 | PASS |

关键 GUI 输出：

```text
GUI_SMOKE_PASS: state=PASS rows=行数 2 errors=错误 0 warnings=警告 0 mode=online case=positive iterations=3 window=mapped ... header-map=column-index strict-header=false contract=elab-only position-map=tile_core->top.u_tile npi-positions=full-path-only schemas=report-v3/inventory-v2
GUI_SMOKE_PASS: state=FAIL rows=行数 1 errors=错误 2 warnings=警告 0 mode=online case=negative iterations=1 window=mapped ... header-map=column-index strict-header=false contract=elab-only schemas=report-v3/inventory-v2
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=offline case=default-rule iterations=1 window=mapped ... header-map=column-index strict-header=false rule=unregistered-default has-rs-cfg-en=true step-parameters=[] physical=2 effective=2 contributions=1,1 schemas=report-v3/inventory-v2
GUI_SMOKE_PASS: state=PASS rows=行数 2 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=100 window=mapped ... header-map=column-index strict-header=false position-map=tile_core->top.u_tile npi-positions=full-path-only schemas=report-v3/inventory-v2
GUI_SMOKE_PASS: state=PASS rows=行数 10000 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=1 window=mapped ... header-map=column-index strict-header=false schemas=report-v3/inventory-v2
```

所有在线命令只构造 `--collector`、`--elab-db <.../kdb.elab++>` 和 NPI 库路径。脚本同时检查
GUI 日志不得出现 inventory 作为在线设计输入，也不得出现 RTL、filelist、`-top` 或任意透传参数。

## 3. VM 审计文件

以下文件保留在本轮 `RUN_ROOT`，SHA-256 为：

```text
5455703dafae985b88afe25d65e9668005c889ca1ec4c8def2e7bde59351acd8  full_vm_test.log
fd5e7e44a9cc2004a852e6cda97c858150a69664389764a9f370d15c9dab1ddc  python_tests.log
d2e5f83866389c0a19f86c90832af7b1a9e2d043910d1798e55ffa28e051a558  collector_ldd.txt
c1b4c5a30958e5abe308368c06d6a161dfd335b973521eb2b11f5b25525f5cc1  positive_report.json
1d9fedfe63124b631fc7b16fe3df45445302d69c863b099f324b1f93e5581c27  positive_report.csv
2da23bea8e2429b209763d00147834cd24800b8366aaf29c7f6f637ad4afe775  positive_inventory.json
a6163699bfa0618aee6dd35c428a240064f431f73618e66dfe18a5218eb55278  verdi_gui.log
aaa1cd234ff2f7b35ba9d926e509e1bce04dddd4190a4d478abc56db16d89596  online_gui_positive.log
206162f0cca15025226824a9d3f6b62dca1a62136e95a20c0bd28f2812270406  online_gui_negative.log
8a9e925af1a8799e73f610143c8c9dc850755877b29919e4a0c00b0343adca06  offline_gui_default_rule.log
6dae80f2b20ddeceb4aa38dbd0c8aa886f195ebe658ca7ccdf80d93befee2688  offline_gui_100_rounds.log
67b23e6ccf7dabcbb46ab833f4783a6cab815b36cbe07c56264064cdc2647952  offline_gui_10000_rows.log
```

仓库提交生成、执行和断言这些结果的脚本、示例、测试与本记录；不提交 license、X11 认证数据、
生成的 KDB 或大体积临时产物。

## 4. 结论

`rtl-rs-check 0.7.0` 已在 Windows 和 CentOS/Verdi VM 上证明：Excel 表头文字不决定属性归属，
用户配置的 1-based 列号才是唯一映射依据。默认 GUI/CLI 路径可以直接处理中文或其他业务表头；
严格同名表头检查仍可显式启用。该变化没有削弱 elaborated KDB 输入边界、NPI 检查、模块规则、
Position 映射、完整实例名、动态 step、报告 schema 或可见 GUI 压力测试。
