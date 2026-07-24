# 动态 step 与模块规则库验证记录（2026-07-25）

本记录对应新增 `module_rules`、`has_rs_cfg_en`、`step_parameters`、逐实例有效拍贡献、report schema v3 和 GUI“模块规则库”页的版本。Windows 与 Linux VM 结果均来自 2026-07-25 对当前工作区的实际执行；2026-07-24 的历史数据不作为本版本通过证据。

在线 NPI 的设计输入契约没有改变：只能把 `elabcom` 生成的 Verdi elaborated KDB 交给 collector。不得向 NPI 传 RTL、filelist、top、`work.lib++` 或任意 Verdi 参数透传。

模块规则采用“显式覆盖优先”的解析方式：存在大小写精确匹配的 `module_rules` 项时使用该项；否则使用隐式默认 `has_rs_cfg_en=true`、`step_parameters=[]`，即要求假门控且每个匹配物理实例贡献 `1` 拍。缺少模块专属项本身不是错误。

## 1. 本地已验证

环境：Windows，工作区 `D:\suhua_rs_tool`，Python 3.11.5。

### 1.1 全量 Python 测试

实际执行：

```powershell
Set-Location D:\suhua_rs_tool
python -m unittest discover -v
```

实际结果：

```text
Ran 120 tests in 2.651s
OK (skipped=22)
```

22 项 skip 均为 Windows 不适用的 Linux Bash/X11 或 POSIX 进程组用例；其余 98 项通过。已执行用例覆盖 `step=0`、隐式默认规则、显式规则优先、无 step parameter、单/多 parameter、参数缺失、`null`、X/Z、非法值、`RS_CFG_EN` 数据库/RTL 一致性、report v3 结构校验、GUI 规则保存和配置路径同步逻辑。

### 1.2 离线正例和动态拍证据

实际执行了以下等价命令，并把报告写入系统临时目录：

```powershell
python -m rscheck check `
  --excel examples/specs.csv `
  --config config/rscheck.example.json `
  --sheet 1 `
  --inventory tests/fixtures/inventory.json `
  --json-report $env:TEMP\rscheck_dynamic_report.json `
  --csv-report $env:TEMP\rscheck_dynamic_report.csv
```

实际结果：

```text
RESULT: PASS | rows=2 errors=0 warnings=0
[PASS] row 2 OUT_IF | top.u_tile / AAAA_BBB physical=6 effective=5 expected=5 RS_CFG_EN=假门控
[PASS] row 3 CTRL_IF | top.u_tile / CTRL_RS physical=1 effective=1 expected=1 RS_CFG_EN=假门控
```

JSON 断言结果：report `schema_version=3`；首组 `physical_instances=6`、`effective_step=5`、`expected=5`，贡献为 `[1,1,0,1,1,1]`。输入 inventory 仍为 schema v2。

### 1.3 离线反例

使用 `tests/fixtures/specs_negative.csv` 实际运行后返回 `1`：

```text
RESULT: FAIL | rows=1 errors=2 warnings=0
[FAIL] row 2 OUT_IF | top.u_tile / AAAA_BBB physical=6 effective=5 expected=6 RS_CFG_EN=真门控
```

report v3 中实际 finding 为 `RS_CFG_EN_LABEL_MISMATCH` 和 `STEP_MISMATCH`。这证明反例按有效拍数 5 与 Excel 期望 6 比较，没有把 6 个物理实例误当成 6 拍。

### 1.4 Windows 可见 GUI smoke

实际执行：

```powershell
Set-Location D:\suhua_rs_tool
python scripts/test_rscheck_gui_smoke.py `
  --project-root D:\suhua_rs_tool `
  --iterations 1 `
  --visible-tab rules `
  --visible-seconds 1
```

实际结果：Tk 窗口处于 mapped 状态，脚本完成临时模块规则的新建、保存和重载，离线正例 2 行 PASS、0 error、0 warning，并输出：

```text
GUI_SMOKE_PASS: state=PASS rows=行数 2 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=1 window=mapped ... schemas=report-v3/inventory-v2
```

VM 端另行完成了 `980×680` 的规则库页和结果页截图验收，见下一节。

## 2. Linux VM 已验证

环境：CentOS 7.9，Python 3.8.13，GCC/G++ 11.2.1，Verdi/NPI O-2018.09-SP2，`NPI_PLATFORM=LINUX64`。测试副本位于 `/root/suhua_rs_tool_dynamic_step_20260725`；最终 fresh-KDB 产物目录为 `output/verdi_gui_test.bLFQ2Ppv`。

完整脚本实际执行：

```bash
cd /root/suhua_rs_tool_dynamic_step_20260725
bash scripts/test_vm_verdi_gui.sh
```

| 项目 | 实际结果 | 状态 |
|---|---|---|
| Linux 全量测试 | `Ran 120 tests`，`OK`，无 skip | PASS |
| NPI collector 构建 | 实际 `npi.h`/`libNPI.so`，G++ 11 构建和 `ldd` 均通过 | PASS |
| fresh Verdi KDB | 当前示例 RTL `vericom` 0 error/0 warning；`elabcom -top top -elab` 0 error/0 warning | PASS |
| Verdi GUI | 仅执行 `verdi -elab <fresh KDB>`；top 窗口 `1143×745` 可见，压力结束前再次确认仍存活且日志无失败标记 | PASS |
| 在线 CLI 正例 | 2 行 PASS，0 error/0 warning；首组 physical=6、effective/expected=5/5 | PASS |
| 在线 rscheck GUI 正例 | 同一 fresh KDB 和真实 collector 连续 3 轮；report v3/inventory v2 | PASS |
| 在线 rscheck GUI 反例 | 1 轮 FAIL、2 errors；同时断言 `STEP_MISMATCH` 与 `RS_CFG_EN_LABEL_MISMATCH` | PASS |
| 模块规则库布局 | X11 client 为 `IsViewable`、`980×680`；搜索、表格、编辑和保存控件无重叠或截断 | PASS |
| 结果页布局 | X11 client 为 `IsViewable`、`980×680`；首组显示匹配实例 `6`、实际/期望拍 `5/5` | PASS |
| 离线 GUI 稳定性 | 可见 Tk 窗口连续 100 轮，2 行 PASS、0 error/0 warning | PASS |
| 10,000 行 GUI 负载 | 10,000 行 PASS、0 error/0 warning；墙钟 3.52 秒，最大 RSS 272,636 KiB，无 swap | PASS |
| 进程和日志清理 | collector/Verdi 日志扫描通过；只按本次精确 KDB 路径清理进程，既有其他 Verdi 未被终止，测试 KDB 无残留进程 | PASS |

在线 inventory 中 6 个 `AAAA_BBB` 实例的 `rs_mode` 为五个 `1`、一个 `0`，report 的贡献顺序为 `[1,1,0,1,1,1]`。无关 `WIDTH=1` 仍保留在逐实例参数证据中。NPI 检查命令只包含 collector 与 `--elab-db`；未传 filelist、RTL、top、`work.lib++` 或 passthrough 参数。

## 3. VM 直接执行入口

在已安装 Tk/Verdi/NPI、当前 shell 已能正常启动 Verdi，且位于图形桌面/VNC/XRDP/`ssh -Y` 会话时，从标准克隆目录直接执行。脚本不要求特定 license 环境变量名；若站点需要专用初始化脚本，请在此命令块之前 source：

```bash
cd "$HOME/suhua_rs_tool"
if [ -f /opt/rh/rh-python38/enable ]; then
  source /opt/rh/rh-python38/enable
fi
bash scripts/launch_rscheck_gui.sh --probe-only
bash scripts/test_vm_verdi_gui.sh
```

完整的规则页、正反例、100 轮、10,000 行和单独在线 KDB smoke 命令见 [测试指南](TESTING.md) 与 [VM GUI 复现指南](VM_GUI_TEST.md)。测试输出不得包含 VM 密码、真实 license 地址或会话认证文件。

## 4. 当前结论

动态 step 版本已经通过 Windows 离线回归，以及 Linux VM 上的 fresh KDB、真实 NPI、Verdi GUI、在线正负例、三轮在线 GUI、100 轮稳定性、10,000 行压力和两个最小窗口布局验收。当前结论仅覆盖仓库示例与记录的工具版本；生产设计仍需使用其正式流程生成的 elaborated KDB 运行同一检查。
