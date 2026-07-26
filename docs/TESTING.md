# RTL 打拍例化检查工具测试指南

本文档给出从本地单元测试、`rscheck` 自带 Tkinter GUI 可见 smoke 和压力测试，到 CentOS/Verdi 在线 NPI 检查的完整验证流程。除非某一节明确说明可以独立执行，Linux 在线测试各节应在同一个 shell 中按顺序执行，以复用 `PROJECT_ROOT`、`TEST_ROOT`、`ELAB_DB` 等变量。

> **生产契约：在线检查的设计输入只能是 `--elab-db <Verdi elaborated KDB 目录>`。**
>
> `-- -f ...`、`-- -sv ...`、`-- -lib ...` 及其他任意 NPI/Verdi 参数透传均不受支持，并且必须返回退出码 `2`。`vericom` 生成的 `work.lib++` 是编译库，不是 elaborated KDB，也不能作为 `--elab-db` 输入。生产 RTL 应由已有编译流程生成 elab 库，本工具不负责在检查过程中重新编译 RTL。

## 1. 测试矩阵

| 编号 | 环境 | 测试目标 | 预期退出码 | 关键预期结果 |
|---|---|---|---:|---|
| L1 | Windows / Linux / macOS | 全量 Python 自动测试 | `0` | 当前测试全部运行并输出 `OK` |
| L2 | Windows + Microsoft Excel | 真实 XLSX 九字段乱序、额外列及列覆盖 | `0` | `VALID: 1 specification row(s)` |
| L3 | 通用本地环境 | position 简写 + schema v2 离线正例 inventory + report v3 | `0` | `tile_core` 解析为 `top.u_tile`，NPI/inventory 只使用全路径；6 个物理实例贡献 `[1,1,0,1,1,1]`，两组均 PASS |
| L4 | 通用本地环境 | 离线反例 inventory | `1` | 1 行 FAIL，至少包含 `STEP_MISMATCH` 和 `RS_CFG_EN_LABEL_MISMATCH` |
| L5 | 通用本地环境 | position 映射、默认/显式模块规则、动态 step、RTL `RS_CRG_EN`、Excel/internal `RS_CFG_EN` 和旧 inventory | `0` | 映射命中/直通/重复组、隐式默认规则、显式覆盖优先、0/非0/缺失/`null`/X/Z、多 parameter AND 语义、step 0 及 schema v1 拒绝均有独立用例 |
| R0 | Linux + X11/Xwayland + Tk | GUI “验证 Excel”路径 | `0` | 20 轮均为 2 行 VALID、0 error、0 warning |
| R1 | Linux + X11/Xwayland + Tk | 工具自带 GUI 可见正例/反例和 100 轮稳定性 | `0` | 正例 100 轮均为 2 行 PASS、0 error、0 warning；反例显示 FAIL |
| R2 | Linux + X11/Xwayland + Tk | 工具自带 GUI 10,000 行负载 | `0` | 单轮 10,000 行、0 error、0 warning |
| R3 | Linux + Verdi/NPI + Tk | 工具自带 GUI 在线 KDB smoke | `0` | 3 轮均为 2 行 PASS；report 保留 `tile_core`，collector/inventory 只出现 `top.u_tile`，并保留动态拍数证据 |
| R4 | 通用 Python 环境 | 取消发生在后台进程启动阶段 | `0` | 100 轮全部通过，不遗留子进程 |
| R5 | Windows / Linux / macOS + Tk | GUI 完整配置导出、导入与事务回归 | `0` | 五个根对象完整往返；副本、dirty、搜索过滤、同路径拒绝及无效/取消/拒绝原子性均通过，输出固定 config-io marker |
| C1 | Linux + Verdi/NPI | C++ NPI collector 构建 | `0` | 生成可执行文件且 `libNPI.so` 可解析 |
| K1 | Linux + Verdi | `vericom` 编译示例 RTL | `0` | 生成 `work.lib++` |
| K2 | Linux + Verdi | `elabcom` 生成测试 KDB | `0` | 生成 `kdb.elab++` 目录 |
| V0 | Linux + X11/Xwayland | 无 Verdi/license 的两个 GUI 环境探测 | `0` | `rscheck GUI probe PASS` / `GUI probe PASS` |
| V1 | Linux + Verdi + X11/Xwayland | Verdi GUI 加载同一 KDB | `0` | 新窗口标题匹配 `VERDI_READY_REGEX`，明确显示 elaborated top `top` |
| N1 | Linux + Verdi/NPI | 在线正例 | `0` | 2 行通过；首组 physical=6、effective=5、expected=5，report schema v3 |
| N2 | Linux + Verdi/NPI | 在线反例 | `1` | 1 行失败、非零 error，包含动态拍数和 Excel/internal `RS_CFG_EN` 标签差异；实际 RTL parameter 为 `RS_CRG_EN` |
| N3 | Linux + Verdi/NPI + Tk | 带 elaboration error 但 top 可查询的 partial KDB | `0` | collector/CLI/GUI 继续；2 行 PASS、0 error、1 个 `NPI_LOAD_PARTIAL` warning |
| G1 | 任意 Python 环境 | 旧 filelist passthrough 防回归 | `2` | argparse 报 `unrecognized arguments` |
| G2 | 任意 Python 环境 | 把 `work.lib++` 错当 elab 输入 | `2` | Python runner 在启动 collector 前拒绝，不生成 PASS 报告 |
| G3 | 任意 Python 环境 | `--inventory` 与 `--elab-db` 冲突 | `2` | 报 `--elab-db requires --collector` |

退出码定义：

- `0`：检查通过；允许存在不影响判定的 warning。
- `1`：RTL 与规格不一致，或检查结果包含硬错误。
- `2`：命令行、配置、Excel、inventory、collector、Verdi/NPI 环境或设计加载失败。

## 2. 本地测试前提

- Python 3.8 或更高版本。
- 当前目录是仓库根目录，能够看到 `pyproject.toml`、`rscheck/`、`tests/`。
- Python 自动测试和离线检查不需要 Verdi、NPI license 或第三方 Python 包。
- 可见 GUI 测试需要 Tkinter；Linux 还需要可访问的 X11/Xwayland DISPLAY、`xdpyinfo`、`xprop` 和 `xwininfo`。
- 建议设置 `PYTHONDONTWRITEBYTECODE=1`，避免测试产生 `__pycache__`。

## 3. Windows 本地测试

### 3.1 全量测试

在 PowerShell 中执行。先把占位符改为实际仓库路径：

```powershell
$ProjectRoot = "C:\path\to\suhua_rs_tool"
Set-Location $ProjectRoot
$env:PYTHONDONTWRITEBYTECODE = "1"

python --version
python -m unittest discover -v
if ($LASTEXITCODE -ne 0) {
    throw "Python test suite failed with exit code $LASTEXITCODE"
}
```

预期末尾输出；测试总数以当前分支实际发现的用例为准，不固定为历史数字：

```text
Ran ... tests in ...

OK
```

这些测试覆盖配置校验、XLSX/CSV/TSV 九字段解析、position 映射命中与完整路径直通、`step=0`、实例分组、模块规则库、单/多 parameter 动态拍数、未知值 fail-closed、clk/rst、CRG、多源、逐实例 RTL `RS_CRG_EN` 与 Excel/internal `RS_CFG_EN` 标签、inventory schema v2 的 `warnings/notices`、report schema v3、partial-load warning、旧 schema 拒绝、elab-only CLI 契约、GUI 五根完整配置导入/导出的事务与副本语义、未编辑纯数字 sheet 名的字符串类型保持、GUI 命令构造与生命周期、进程组取消和跨桌面 GUI 会话发现。Windows/macOS 可以跳过明确标记为 Linux Bash/X11 或 POSIX-only 的用例；Linux 上适用用例不得意外 skipped。

实例分组回归必须同时覆盖非空 `RS_inst` 前缀和完整本地例化名：空 remainder 应合法，非空 remainder 仍按 `rtl.suffix_regex` 完整匹配。空后缀实例必须继续执行 module/parameter/step/clk/rst/CRG 检查并计入物理实例数及规则计算后的有效 `step`，但不得进入 tag/index/连续编号判断。还应覆盖同一 scope 中 `PFX` 与 `PFX_C0` 会被 `RS_inst=PFX` 同时匹配，以及重叠 Excel 组仍产生 `AMBIGUOUS_GROUP_MATCH`；当前没有 exact-only 模式。

### 3.2 Windows 工具自带 GUI 启动和布局检查

Tkinter 可用性和 GUI 启动命令：

```powershell
$ProjectRoot = "C:\path\to\suhua_rs_tool"
Set-Location $ProjectRoot

python -c "import tkinter; print(tkinter.TkVersion)"
if ($LASTEXITCODE -ne 0) { throw "Tkinter is unavailable" }

python -m rscheck gui
```

也可先安装项目，再从 PowerShell 或开始菜单快捷方式调用安装入口：

```powershell
python -m pip install -e .
rtl-rs-check-gui
```

窗口出现后手工缩放到允许的最小尺寸 `980x680`，检查九个列映射、在线/离线数据源、报告路径和“导入/加载/导出”等操作按钮无重叠，并逐一检查五个页签：“检查配置”“模块规则库”“Position 映射库”“检查结果”“运行日志”。在映射页搜索 `tile_core`，确认其全路径为 `top.u_tile`，并用临时配置完成一次映射新建、修改、保存、重载和删除；规则页同样验收 `rs_pipe` 的兼容配置键 `has_rs_cfg_en=true`、`step_parameters=rs_mode`，并确认界面将该规则说明为 RTL `RS_CRG_EN` parameter。最后修改 GUI Excel 设置和两个内存数据库，导出完整配置副本并重新导入，确认五个根对象完整恢复。截图必须基于当前版本重新验收，且不提交仓库。

从仓库根目录可直接复制运行可见 GUI smoke。脚本使用临时配置验证 position 映射和模块规则的新建/保存/重载/删除，并把当前 Excel/列号、完整 `rtl`、包含未单独保存修改的两个数据库导出为副本后重新导入；它不会修改仓库配置，成功后把“Position 映射库”页保留 10 秒：

```powershell
$ProjectRoot = (Get-Location).Path
python scripts/test_rscheck_gui_smoke.py `
  --project-root $ProjectRoot `
  --iterations 1 `
  --visible-tab positions `
  --visible-seconds 10
if ($LASTEXITCODE -ne 0) { throw "Visible GUI smoke failed" }
```

终端末行必须包含：

```text
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules
```

该 marker 表示导出副本保留五个根对象、当前 GUI Excel/列号、已加载 `rtl` 及两个完整内存数据库，导出没有切换 active path 或清除 dirty，随后导入整体替换配置、切换路径并清除 dirty。搜索过滤不得影响数据库导出。取消、首次/复核无效配置、拒绝丢弃 Excel 表单或任一数据库、导出到当前配置同一路径、配置路径输入框已变化但未加载，以及未编辑的纯数字 sheet 名在导出和运行时保持字符串类型，由自动测试单独覆盖；任何失败都必须保持原配置状态。“导入”必须拒绝缺少任一根对象的快照；兼容性“加载”仍执行相同的 Excel/数据库确认和二次读取。

### 3.3 使用 Excel 生成“乱序列 + 自定义表头 + 额外列”XLSX

本节需要安装桌面版 Microsoft Excel。脚本生成一个真实 `.xlsx`，其中九个内部属性映射到乱序列，实际表头全部使用与内部属性名不同的业务名称，并在首尾各加入一个无关列。默认 `validate_headers=false`，因此属性归属只由 1-based 列号决定。

```powershell
$ProjectRoot = "C:\path\to\suhua_rs_tool"
$TestRoot = Join-Path $ProjectRoot "output\manual_testing"
$XlsxPath = Join-Path $TestRoot "shuffled_columns.xlsx"
New-Item -ItemType Directory -Force -Path $TestRoot | Out-Null

$excel = New-Object -ComObject Excel.Application
$workbook = $null
$worksheet = $null
try {
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $workbook = $excel.Workbooks.Add()
    $worksheet = $workbook.Worksheets.Item(1)
    $worksheet.Name = "RS_Check"

    $headers = @(
        "unrelated", "Instance selector", "Clock connection", "Interface label",
        "Clock source", "Expected stages", "Scope alias", "Reset connection",
        "Module definition", "Gating class", "notes"
    )
    $values = @(
        "ignore", "PIPE_X", "clk_i", "IN_IF", "my_crg",
        1, "top.u", "rst_n", "rs_pipe", "假门控", "extra column"
    )

    for ($column = 1; $column -le $headers.Count; $column++) {
        $worksheet.Cells.Item(1, $column) = $headers[$column - 1]
        $worksheet.Cells.Item(2, $column) = $values[$column - 1]
    }

    $workbook.SaveAs($XlsxPath, 51)
    $workbook.Close($false)
}
finally {
    if ($excel -ne $null) { $excel.Quit() }
    if ($worksheet -ne $null) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($worksheet)
    }
    if ($workbook -ne $null) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($workbook)
    }
    if ($excel -ne $null) {
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

Set-Location $ProjectRoot
$ValidateArgs = @(
    "validate",
    "--excel", $XlsxPath,
    "--config", (Join-Path $ProjectRoot "config\rscheck.example.json"),
    "--sheet", "RS_Check",
    "--column", "Intf_type=4",
    "--column", "RS_module=9",
    "--column", "RS_inst=2",
    "--column", "position=7",
    "--column", "step=6",
    "--column", "clk=3",
    "--column", "rst=8",
    "--column", "CRG_source=5",
    "--column", "RS_CFG_EN=10"
)

python -m rscheck @ValidateArgs
if ($LASTEXITCODE -ne 0) {
    throw "Shuffled XLSX validation failed with exit code $LASTEXITCODE"
}

# 可选严格诊断要求实际表头等于内部属性名，因此同一自定义表头文件应返回基础设施错误 2。
python -m rscheck @ValidateArgs --header-check
if ($LASTEXITCODE -ne 2) {
    throw "Strict header diagnostic should fail with exit code 2"
}
```

预期输出包含：

```text
VALID: 1 specification row(s)
row 2: top.u / PIPE_X module=rs_pipe step=1
```

显式开启严格诊断的第二次运行退出码为 `2`，标准错误包含：

```text
ERROR: header validation failed at row 1
```

第一次运行证明内部属性只按用户定义的列号读取：实际表头任意，额外列不会参与解析。第二次运行证明精确表头比较仍作为 opt-in 诊断保留，只有显式传入 `--header-check` 时才执行。

配置示例中没有 `top.u` 的 position 简写，因此本用例也同时证明：未命中 `position_mappings` 的 Excel 值会按完整 RTL 路径直通，`validate` 不会要求所有路径都登记数据库。

### 3.4 本地离线正例和反例

```powershell
Set-Location $ProjectRoot
python -m rscheck check `
  --excel tests/fixtures/specs.csv `
  --config config/rscheck.example.json `
  --sheet 1 `
  --inventory tests/fixtures/inventory.json
if ($LASTEXITCODE -ne 0) {
    throw "Offline positive check failed with exit code $LASTEXITCODE"
}
```

预期首行：

```text
RESULT: PASS | rows=2 errors=0 warnings=0
```

继续运行离线反例；返回码 `1` 是预期结果：

```powershell
$NegativeReport = Join-Path $ProjectRoot "output\manual_testing\negative.json"
python -m rscheck check `
  --excel tests/fixtures/specs_negative.csv `
  --config config/rscheck.example.json `
  --sheet 1 `
  --inventory tests/fixtures/inventory.json `
  --json-report $NegativeReport
if ($LASTEXITCODE -ne 1) {
    throw "Offline negative check did not return 1"
}

$Negative = Get-Content -Raw -Encoding utf8 $NegativeReport | ConvertFrom-Json
if ($Negative.summary.passed -ne $false -or $Negative.summary.failed_rows -ne 1) {
    throw "Unexpected negative report summary"
}
```

预期 finding 至少包含 `STEP_MISMATCH` 和 `RS_CFG_EN_LABEL_MISMATCH`。当前反例的模块名、clk/rst 和 CRG 故意保持正确，因此不得要求旧版本的五类无关差异。

### 3.5 模块规则、动态 step、RTL `RS_CRG_EN` 和兼容标签合同回归

全量测试必须独立覆盖下列情况；不能只依赖一个同时触发多种 finding 的反例：

| 规则/实例条件 | 预期 |
|---|---|
| 没有模块专属显式规则 | 使用默认 `has_rs_cfg_en=true`、`step_parameters=[]`；兼容规则键实际要求 RTL `RS_CRG_EN`；`validate` 接受输入 |
| 默认规则，6 个物理实例 | 每个实例贡献 1，有效拍数 6 |
| 存在大小写精确匹配的显式规则 | 显式 `has_rs_cfg_en` 和 `step_parameters` 覆盖默认值；前者控制 RTL `RS_CRG_EN` |
| `step_parameters=["rs_mode"]`，值为 `1,1,0,1,1,1` | 贡献 `[1,1,0,1,1,1]`，有效拍数 5 |
| 两个 step parameters 且值都已解析 | 只有全部非零的实例贡献 1，至少一个为 0 贡献 0 |
| step parameter 缺失 | `STEP_PARAMETER_MISSING` + `STEP_CALCULATION_UNRESOLVED` |
| step parameter 为 `null`、X/Z、`?` 或非法值 | `STEP_PARAMETER_VALUE_UNRESOLVED` + `STEP_CALCULATION_UNRESOLVED` |
| 全部实例贡献 0，Excel `step=0` | PASS |
| 没有物理匹配实例，Excel `step=0` | `GROUP_NOT_FOUND`，不能 PASS |
| `has_rs_cfg_en=true`，RTL `RS_CRG_EN` 为 0，Excel/internal `RS_CFG_EN` 为 `假门控` | PASS |
| `has_rs_cfg_en=true`，RTL `RS_CRG_EN` 缺失/非零/未知 | 对应兼容 code `MISSING`/`VALUE_MISMATCH`/`VALUE_UNRESOLVED` |
| `has_rs_cfg_en=true`，实例只有同名 RTL `RS_CFG_EN=0`、没有 `RS_CRG_EN` | `RS_CFG_EN_PARAMETER_MISSING`；不得回退匹配旧 parameter 名 |
| `has_rs_cfg_en=false`，RTL `RS_CRG_EN` 不存在且 Excel `RS_CFG_EN` 留空 | PASS |
| `has_rs_cfg_en=false`，RTL 实际存在 `RS_CRG_EN` | `RS_CFG_EN_PARAMETER_UNEXPECTED` |
| `step_parameters` 包含 `RS_CRG_EN` | 配置错误；该 parameter 由专门逻辑处理 |
| `step_parameters` 包含另一个真实 RTL parameter `RS_CFG_EN` | 作为普通动态拍参数接受，不与 Excel/internal 标签字段混淆 |

还必须断言 report `schema_version=3`，每行都包含最终采用的默认或显式 `module_rule`，`step_check.physical_instances` 等于 matched instances 数量，`effective_step` 等于所有已知贡献之和。inventory 仍必须是 schema v2；schema v1、缺少实例 `parameters` 或值不是 `string|null` 的 inventory 必须被拒绝。

Position 映射必须有独立合同测试：`tile_core -> top.u_tile` 命中后 `SpecRow.position` 为全路径、`position_alias` 为简写；未登记的 `top.u_tile` 直通且 alias 为空；映射后相同 `(position, RS_inst)` 仍判重复；CLI/GUI report v3 和 CSV 保留 alias；交给 NPI runner 的唯一 positions 只能包含完整路径，绝不能包含 `tile_core`。

`RS_inst` 匹配也必须有独立合同测试：空单元格仍按必填字段拒绝；填写 `CTRL_RS_D0` 能以空 remainder 匹配同名本地实例；完整层次名不应被文档或 GUI 引导为 `RS_inst`；空后缀实例的 `rs_mode=0` 时贡献为 `0`，非零时贡献为 `1`；空后缀与 indexed 成员混合时，只对 indexed 成员检查 tag/index/连续性。

## 4. 通用 POSIX 本地测试

Linux 或 macOS 无需 Verdi 即可运行 Python 测试：

```bash
PROJECT_ROOT="/path/to/suhua_rs_tool"
cd "$PROJECT_ROOT"
export PYTHONDONTWRITEBYTECODE=1

python3 --version
python3 -m unittest discover -v
test "$?" -eq 0
```

当前预期是全量发现的测试均完成并输出 `OK`；不要把历史 `89` 当成固定门槛。Windows/macOS 可以跳过明确的 Linux Bash/X11 或 POSIX-only 用例，Linux 上 GUI 会话、launcher 和进程组回归不应 skipped。

macOS 可直接启动工具自带 GUI，验证 Excel 或使用 inventory 做离线检查：

```bash
cd "$PROJECT_ROOT"
python3 -c 'import tkinter; print(tkinter.TkVersion)'
python3 -m rscheck gui
```

macOS 不支持真实 NPI 在线采集；在线 collector、`libNPI.so` 和 elaborated KDB 加载测试必须在 Linux/Verdi 环境执行。

可单独运行 elab-only 契约测试：

```bash
cd "$PROJECT_ROOT"
python3 -m unittest tests.test_npi_runner tests.test_cli -v
```

预期全部通过。

### 4.1 Linux 工具自带 GUI 可见 smoke、稳定性与负载测试

先安装与 Python 匹配的 Tkinter 和 X11 工具。按发行版选一组命令：

```bash
# Debian/Ubuntu
sudo apt-get update
sudo apt-get install -y python3-tk x11-utils

# RHEL/CentOS 系统 Python
sudo yum install -y python3-tkinter xorg-x11-utils

# RHEL/CentOS SCL Python 3.8
sudo yum install -y rh-python38-python-tkinter xorg-x11-utils
```

在图形终端、VNC/XRDP 终端或保持连接的 `ssh -Y` shell 中执行。SCL 环境使用 `source /opt/rh/rh-python38/enable`；其他环境删掉该行：

```bash
PROJECT_ROOT="/path/to/suhua_rs_tool"
cd "$PROJECT_ROOT"

if [ -f /opt/rh/rh-python38/enable ]; then
  source /opt/rh/rh-python38/enable
fi
export PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" -c 'import tkinter; print("Tk", tkinter.TkVersion)'
bash scripts/launch_rscheck_gui.sh --probe-only

# 让本 shell 使用与 launcher 相同的、已验证的 DISPLAY/Xauthority。
source scripts/lib/gui_session.sh
gui_session_resolve
xdpyinfo >/dev/null
```

先验收“Position 映射库”页、映射/模块规则保存与重新加载，以及五根完整配置的导出和导入。命令只修改 smoke 自己创建的临时配置：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --iterations 1 \
  --visible-tab positions \
  --visible-seconds 10
```

窗口保留期间应看到 `tile_core -> top.u_tile`，映射表、搜索框和编辑区无重叠或截断；smoke 同时已在临时配置完成 position 映射和模块规则的新增、修改、搜索、保存、重载、删除，以及五根配置副本导出和原子导入。终端末行应为 `GUI_SMOKE_PASS`，并包含 `config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules`。

先单独覆盖 GUI 的“验证 Excel”按钮。该路径不运行 inventory 或 NPI 检查；下面连续验证 20 轮：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --validate-only \
  --iterations 20 \
  --visible-seconds 5
```

预期末行包含：

```text
GUI_SMOKE_PASS: rows=行数 2 errors=错误 0 warnings=警告 0 mode=validate case=positive iterations=20 window=mapped ... header-map=column-index strict-header=false position-map=tile_core->top.u_tile config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules
```

2026-07-24 的 20 轮结果仅是动态 step 之前的历史基线；`0.7.1` fresh 验收也早于完整配置导入/导出。若把本节 20 轮 validate-only 作为当前 `0.8.1` 设备门禁，必须在目标设备重跑并确认 config-io marker，不能沿用旧结果。

离线正例会真正创建可见 Tk 窗口、触发“运行 RTL 检查”并更新结果 Treeview。smoke 内部要求自己的 Tk 窗口处于 mapped/viewable 状态，并输出 Tk client 的 `window_id`；下面连续运行 100 轮，全部完成后保留结果页 10 秒供人工查看。外部证据把这个 ID 交给 `xwininfo -tree -stats`，要求 client 为 `IsViewable`，并在同一 X11 树中找到标题为 `RTL RS Check GUI Smoke` 的 Tk wrapper。该方式不依赖 EWMH `_NET_CLIENT_LIST` 或旧 Tk 缺失的 `_NET_WM_PID`：

```bash
(
set -e
cd "$PROJECT_ROOT"
POS_GUI_LOG="$(mktemp /tmp/rscheck_gui_positive.XXXXXX.log)"
GUI_WINDOW_INFO="$(mktemp /tmp/rscheck_window_info.XXXXXX)"
cleanup_positive_window_files() {
  rm -f "$GUI_WINDOW_INFO"
}
trap cleanup_positive_window_files EXIT

"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --iterations 100 \
  --visible-seconds 10 \
  >"$POS_GUI_LOG" 2>&1 &
POS_GUI_PID=$!

WINDOW_DETECTED=0
GUI_WINDOW_ID=""
for _ in $(seq 1 50); do
  GUI_WINDOW_ID="$(
    sed -n 's/.*window_id=\(0x[0-9a-fA-F][0-9a-fA-F]*\).*/\1/p' "$POS_GUI_LOG" \
      | tail -n 1
  )"
  if [ -n "$GUI_WINDOW_ID" ] && \
     xwininfo -id "$GUI_WINDOW_ID" -tree -stats >"$GUI_WINDOW_INFO" 2>&1 && \
     grep -Fq 'Map State: IsViewable' "$GUI_WINDOW_INFO" && \
     grep -Fq 'RTL RS Check GUI Smoke' "$GUI_WINDOW_INFO"; then
    WINDOW_DETECTED=1
    break
  fi
  kill -0 "$POS_GUI_PID" 2>/dev/null || break
  sleep 0.2
done
printf 'rscheck GUI window ID: %s\n' "$GUI_WINDOW_ID"
cat "$GUI_WINDOW_INFO"
set +e
wait "$POS_GUI_PID"
POS_GUI_RC=$?
set -e
cat "$POS_GUI_LOG"
test "$WINDOW_DETECTED" -eq 1
test "$POS_GUI_RC" -eq 0
grep -F 'Width:' "$GUI_WINDOW_INFO"
grep -F 'Height:' "$GUI_WINDOW_INFO"
grep -F 'rows=行数 2 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=100 window=mapped' "$POS_GUI_LOG"
grep -F 'header-map=column-index strict-header=false' "$POS_GUI_LOG"
grep -F 'config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules' "$POS_GUI_LOG"
grep -F 'position-map=tile_core->top.u_tile npi-positions=full-path-only' "$POS_GUI_LOG"
)
```

每轮都会解析示例 Excel 的 `tile_core`，要求 GUI/report 中完整路径为 `top.u_tile`、`position_alias=tile_core`，并要求 inventory positions 只有 `top.u_tile`。`0.7.1` 的 100 轮和 10,000 行结果是导入/导出功能之前的历史性能基线，见 [NPI partial-load 兼容与 GUI 压测验证记录](TEST_RESULTS_NPI_PARTIAL_LOAD_2026-07-25.md)；当前 `0.8.1` 必须重新运行并出现 config-io marker。

离线反例使用相同 inventory，但规格故意写错。脚本自身预期 GUI 显示 `FAIL`，因此 smoke 成功仍返回 `0`：

```bash
(
set -e
cd "$PROJECT_ROOT"
NEG_GUI_LOG="$(mktemp /tmp/rscheck_gui_negative.XXXXXX.log)"
set +e
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --negative \
  --iterations 1 \
  --visible-seconds 5 \
  >"$NEG_GUI_LOG" 2>&1
NEG_GUI_RC=$?
set -e
cat "$NEG_GUI_LOG"
test "$NEG_GUI_RC" -eq 0
grep -F 'mode=offline case=negative iterations=1 window=mapped' "$NEG_GUI_LOG"
)
```

10,000 行负载测试会在临时目录生成规格和对应 inventory，通过 GUI 后台进程、JSON 报告解析及 10,000 行 Treeview 渲染路径。命令使用 GNU `time` 同时记录墙钟时间和最大常驻内存：

```bash
(
set -e
cd "$PROJECT_ROOT"
LOAD_GUI_LOG="$(mktemp /tmp/rscheck_gui_load.XXXXXX.log)"
set +e
/usr/bin/time -f 'GUI_LOAD wall=%e sec max_rss=%M KiB' \
  "$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --generated-rows 10000 \
  --iterations 1 \
  --timeout 180 \
  --visible-seconds 0 \
  >"$LOAD_GUI_LOG" 2>&1
LOAD_GUI_RC=$?
set -e
cat "$LOAD_GUI_LOG"
test "$LOAD_GUI_RC" -eq 0
grep -F 'rows=行数 10000 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=1 window=mapped' "$LOAD_GUI_LOG"
grep -F 'config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules' "$LOAD_GUI_LOG"
grep -F 'GUI_LOAD wall=' "$LOAD_GUI_LOG"
)
```

2026-07-24 九列版本的 2.12 秒墙钟时间、最大 RSS 175,612 KiB，以及 0.7.1 fresh run 的 10,000 行结果都只是完整配置导入/导出之前的历史性能数据；旧数字不是当前 `0.8.1` 性能结论，也不是不同设备的硬门槛。若需要本机时间/RSS 基线，应在目标设备重跑本节命令并确认 config-io marker；缺少 `/usr/bin/time` 时先安装发行版的 `time` 包。

取消启动竞态的自动回归可单独重复 100 轮。它覆盖“用户在后台 CLI 尚未完成启动时点击取消”的窗口，确保取消请求不会丢失：

```bash
(
set -e
cd "$PROJECT_ROOT"
CANCEL_START_SECONDS="$(date +%s)"
for _ in $(seq 1 100); do
  "$PYTHON_BIN" -m unittest \
    tests.test_gui_backend.ProcessControllerTests.test_cancel_during_process_start_is_not_lost \
    >/dev/null 2>&1
done
CANCEL_ELAPSED="$(( $(date +%s) - CANCEL_START_SECONDS ))"
printf 'cancel-during-start: 100/100 PASS in %s seconds\n' "$CANCEL_ELAPSED"
)
```

CentOS 实测连续 100 轮通过，总耗时约 15.3 秒。完整测试套件还覆盖运行中取消，以及组长先退出、后代忽略 SIGTERM 时的三秒后进程组强制清理，验证不会只结束 Python CLI 而遗留 collector 子进程。

手工验收时可在较慢的在线检查开始后点击“取消”，结果状态应变为 `CANCELLED`，日志停止增长，`pgrep -af rs_npi_collector` 不应出现本次 collector。不要用 `kill -9` 代替 GUI 取消按钮进行这项功能验收。

## 5. Linux/Verdi 测试环境

先通过组织批准的方式进入 Linux 设备，并在当前 shell 或受控环境模块中设置仓库、Verdi 和 license 环境。不要把 SSH 密码、真实主机/IP、license 地址或其他凭据写入仓库、报告或命令日志。以下检查命令不包含这些值，可直接复制：

```bash
: "${PROJECT_ROOT:?set PROJECT_ROOT in the current shell}"
: "${VERDI_HOME:?set VERDI_HOME in the current shell}"
: "${LM_LICENSE_FILE:?set LM_LICENSE_FILE in the current shell}"

export NPI_PLATFORM="${NPI_PLATFORM:-LINUX64}"
export NOVAS_INST_DIR="$VERDI_HOME"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-$LM_LICENSE_FILE}"
export PYTHON_BIN="${PYTHON_BIN:-python3}"
export CXX="${CXX:-g++}"
export NPI_INC_DIR="${NPI_INC_DIR:-$VERDI_HOME/share/NPI/inc}"
export NPI_LIB_DIR="${NPI_LIB_DIR:-$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM}"

# 若系统有多个 Python/GCC 工具链，在这里 source 对应的 enable 脚本。
# source <PYTHON_TOOLCHAIN_ENABLE>
# source <GCC_TOOLCHAIN_ENABLE>

cd "$PROJECT_ROOT"
"$PYTHON_BIN" --version
"$CXX" --version
test -x "$VERDI_HOME/bin/vericom"
test -x "$VERDI_HOME/bin/elabcom"
test -f "$NPI_INC_DIR/npi.h"
test -f "$NPI_LIB_DIR/libNPI.so"
```

若任一 `test` 返回非零，先修复安装路径或环境变量，不要继续构建。

## 6. 构建 C++ NPI collector

```bash
cd "$PROJECT_ROOT"

make -C npi \
  VERDI_HOME="$VERDI_HOME" \
  NPI_PLATFORM="$NPI_PLATFORM" \
  NPI_INC="$NPI_INC_DIR" \
  NPI_LIB="$NPI_LIB_DIR" \
  CXX="$CXX"

COLLECTOR="$PROJECT_ROOT/npi/build/rs_npi_collector"
test -x "$COLLECTOR"
file "$COLLECTOR"
ldd "$COLLECTOR" | grep 'libNPI.so'
```

仓库路径、`NPI_INC_DIR` 和 `NPI_LIB_DIR` 不得包含空白；GNU Make 会拆分目标名，Makefile 会在构建前明确拒绝这类路径。

预期：

- `make` 返回 `0`。
- `$COLLECTOR` 是当前 CentOS 架构的可执行文件。
- `ldd` 中 `libNPI.so` 指向 `$NPI_LIB_DIR`，且不是 `not found`。

若 NPI 库不在标准目录，在 `rscheck check` 命令中增加 `--npi-lib-dir "$NPI_LIB_DIR"`。

该选项只配置 collector 子进程的动态库搜索路径，不是设计参数，也不会传给 `npi_load_design`。

## 7. 使用 vericom 和 elabcom 生成测试 KDB

本节只针对仓库内的小型示例 RTL。生产工程应复用其正式编译流程已经生成的 elaborated KDB。

```bash
cd "$PROJECT_ROOT"

TEST_ROOT="$PROJECT_ROOT/output/manual_verdi_test_$(date +%Y%m%d_%H%M%S)"
ELAB_ROOT="$TEST_ROOT/example_elab"
ELAB_DB="$ELAB_ROOT/kdb.elab++"
mkdir -p "$ELAB_ROOT"
cd "$ELAB_ROOT"

"$VERDI_HOME/bin/vericom" \
  -sv "$PROJECT_ROOT/examples/rtl/rs_example.sv"
VERICOM_RC=$?
if [ "$VERICOM_RC" -ne 0 ]; then
  echo "vericom failed: $VERICOM_RC" >&2
  exit "$VERICOM_RC"
fi
test -d "$ELAB_ROOT/work.lib++"

"$VERDI_HOME/bin/elabcom" \
  -top top \
  -elab "$ELAB_DB"
ELABCOM_RC=$?
if [ "$ELABCOM_RC" -ne 0 ]; then
  echo "elabcom failed: $ELABCOM_RC" >&2
  exit "$ELABCOM_RC"
fi
test -d "$ELAB_DB"

cd "$PROJECT_ROOT"
printf 'TEST_ROOT=%s\nELAB_DB=%s\n' "$TEST_ROOT" "$ELAB_DB"
```

预期同时存在：

```text
$ELAB_ROOT/work.lib++       # vericom 编译库，不可传给生产工具
$ELAB_ROOT/kdb.elab++       # elabcom elaborated KDB，可传给 --elab-db
```

`--elab-db` 不强制目录名必须以 `.elab++` 结尾，因为 `elabcom -elab <path>` 允许自定义名称；但该路径必须存在且必须是目录。collector 调用 `npi_load_design -elab <path>`；返回 0 时还会按手册示例枚举 top。存在可查询 top 才继续，并由后续 position/实例/端口/parameter/CRG 检查保持 fail-closed；无 top 才退出 11。

## 8. 在线正例

命令中没有 `--` 尾部参数，也没有 `-f`、`-sv` 或 `-lib`。唯一设计输入是 `$ELAB_DB`。

```bash
cd "$PROJECT_ROOT"

POS_INVENTORY="$TEST_ROOT/positive_inventory.json"
POS_REPORT="$TEST_ROOT/positive_report.json"
POS_CSV="$TEST_ROOT/positive_report.csv"

set +e
"$PYTHON_BIN" -m rscheck check \
  --excel "$PROJECT_ROOT/examples/specs.csv" \
  --config "$PROJECT_ROOT/config/rscheck.example.json" \
  --sheet 1 \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --npi-timeout 180 \
  --keep-inventory "$POS_INVENTORY" \
  --json-report "$POS_REPORT" \
  --csv-report "$POS_CSV"
POS_RC=$?
set -e

if [ "$POS_RC" -ne 0 ]; then
  echo "online positive check returned $POS_RC, expected 0" >&2
  exit 1
fi
test -s "$POS_INVENTORY"
test -s "$POS_REPORT"
test -s "$POS_CSV"
```

预期控制台摘要：

```text
RESULT: PASS | rows=2 errors=0 warnings=0
[PASS] row 2 OUT_IF | tile_core -> top.u_tile / AAAA_BBB physical=6 effective=5 expected=5 RS_CFG_EN=假门控
[PASS] row 3 CTRL_IF | tile_core -> top.u_tile / CTRL_RS_D0 physical=1 effective=1 expected=1 RS_CFG_EN=假门控
```

### 8.1 正例 schema、摘要和参数证据断言

```bash
"$PYTHON_BIN" - "$POS_REPORT" "$POS_INVENTORY" "$POS_CSV" <<'PY'
import csv
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    report = json.load(stream)
with open(sys.argv[2], "r", encoding="utf-8") as stream:
    inventory = json.load(stream)
with open(sys.argv[3], "r", encoding="utf-8-sig", newline="") as stream:
    csv_rows = list(csv.DictReader(stream))

if report.get("schema_version") != 3:
    raise SystemExit("report must use schema_version 3")
if inventory.get("schema_version") != 2:
    raise SystemExit("inventory must use schema_version 2")

positions = inventory.get("positions")
if not isinstance(positions, dict) or set(positions) != {"top.u_tile"}:
    raise SystemExit("collector must receive only the resolved full path: {!r}".format(positions))
if "tile_core" in positions:
    raise SystemExit("position alias leaked into NPI inventory positions")
if len(csv_rows) != 2 or any(
    row.get("position") != "top.u_tile"
    or row.get("position_alias") != "tile_core"
    for row in csv_rows
):
    raise SystemExit("CSV report lost resolved position/alias evidence: {!r}".format(csv_rows))

instances = {
    instance["name"]: instance
    for instance in inventory["positions"]["top.u_tile"]["instances"]
}
expected_modes = {
    "AAAA_BBB_C0": "1",
    "AAAA_BBB_C1": "1",
    "AAAA_BBB_C2": "0",
    "AAAA_BBB_C3": "1",
    "AAAA_BBB_C4": "1",
    "AAAA_BBB_C5": "1",
    "CTRL_RS_D0": "1",
}
for name, expected_mode in expected_modes.items():
    parameters = instances[name].get("parameters")
    if (
        not isinstance(parameters, dict)
        or parameters.get("RS_CRG_EN") != "0"
        or parameters.get("rs_mode") != expected_mode
    ):
        raise SystemExit("inventory lost effective parameters for {}: {!r}".format(name, parameters))

expected = {
    "passed": True,
    "rows": 2,
    "passed_rows": 2,
    "failed_rows": 0,
    "errors": 0,
    "warnings": 0,
}
actual = report["summary"]
if actual != expected:
    raise SystemExit("unexpected positive summary: {!r}".format(actual))
for row in report["rows"]:
    if row["spec"].get("position") != "top.u_tile":
        raise SystemExit("report did not use resolved position: {!r}".format(row["spec"]))
    if row["spec"].get("position_alias") != "tile_core":
        raise SystemExit("report lost Excel position alias: {!r}".format(row["spec"]))
    if row["spec"].get("RS_CFG_EN") != "假门控":
        raise SystemExit("report lost RS_CFG_EN Excel evidence: {!r}".format(row["spec"]))
    rule = row.get("module_rule")
    if rule != {
        "name": "rs_pipe",
        "has_rs_cfg_en": True,
        "step_parameters": ["rs_mode"],
    }:
        raise SystemExit("unexpected module rule evidence: {!r}".format(rule))
    for instance in row["matched_instances"]:
        parameters = instance.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("RS_CRG_EN") != "0":
            raise SystemExit("report lost effective parameter evidence: {!r}".format(instance))

out_row = next(row for row in report["rows"] if row["spec"]["RS_inst"] == "AAAA_BBB")
step_check = out_row["step_check"]
contributions = [item["contribution"] for item in step_check["contributions"]]
if step_check["physical_instances"] != 6:
    raise SystemExit("expected 6 physical instances: {!r}".format(step_check))
if step_check["effective_step"] != 5 or step_check["expected"] != 5:
    raise SystemExit("expected effective/expected step 5/5: {!r}".format(step_check))
if contributions != [1, 1, 0, 1, 1, 1]:
    raise SystemExit("unexpected step contributions: {!r}".format(contributions))

ctrl_row = next(row for row in report["rows"] if row["spec"]["RS_inst"] == "CTRL_RS_D0")
ctrl_names = [instance["name"] for instance in ctrl_row["matched_instances"]]
if ctrl_names != ["CTRL_RS_D0"]:
    raise SystemExit("full local RS_inst did not match exactly one empty-suffix instance: {!r}".format(ctrl_names))
ctrl_step = ctrl_row["step_check"]
if ctrl_step["physical_instances"] != 1 or ctrl_step["effective_step"] != 1:
    raise SystemExit("empty-suffix instance lost normal step evaluation: {!r}".format(ctrl_step))
print("positive position-mapping/inventory-v2/report-v3 evidence OK:", actual)
PY
```

预期输出 `positive position-mapping/inventory-v2/report-v3 evidence OK`，Python 返回 `0`。这同时证明 Excel 使用简写、报告保留简写、collector/inventory 只处理解析后的完整路径。

### 8.2 VM 上工具自带 GUI 的在线 KDB smoke

以下块不包含主机、密码或真实 license。先把前三个占位变量替换为本机受控路径；`ELAB_DB` 必须是已经由 `elabcom` 生成的目录。命令只把 collector 和该 KDB 交给 GUI，不传 RTL、filelist 或 top：

```bash
(
set -e
export PROJECT_ROOT="/path/to/suhua_rs_tool"
export VERDI_HOME="/path/to/verdi"
export ELAB_DB="/absolute/path/to/kdb.elab++"

: "${LM_LICENSE_FILE:?set LM_LICENSE_FILE through the approved site environment}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-$LM_LICENSE_FILE}"
export NPI_PLATFORM="${NPI_PLATFORM:-LINUX64}"
export PYTHON_BIN="${PYTHON_BIN:-python3}"
export CXX="${CXX:-g++}"
export NPI_INC_DIR="${NPI_INC_DIR:-$VERDI_HOME/share/NPI/inc}"
export NPI_LIB_DIR="${NPI_LIB_DIR:-$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM}"
export COLLECTOR="$PROJECT_ROOT/npi/build/rs_npi_collector"

cd "$PROJECT_ROOT"
if [ -f /opt/rh/rh-python38/enable ]; then
  source /opt/rh/rh-python38/enable
fi

make -C npi \
  VERDI_HOME="$VERDI_HOME" \
  NPI_PLATFORM="$NPI_PLATFORM" \
  NPI_INC="$NPI_INC_DIR" \
  NPI_LIB="$NPI_LIB_DIR" \
  CXX="$CXX"
test -x "$COLLECTOR"
test -d "$ELAB_DB"
test -f "$NPI_LIB_DIR/libNPI.so"

bash scripts/launch_rscheck_gui.sh --probe-only
source scripts/lib/gui_session.sh
gui_session_resolve

ONLINE_GUI_LOG="$(mktemp /tmp/rscheck_gui_online.XXXXXX.log)"
ONLINE_WINDOW_INFO="$(mktemp /tmp/rscheck_online_window_info.XXXXXX)"
cleanup_online_window_files() {
  rm -f "$ONLINE_WINDOW_INFO"
}
trap cleanup_online_window_files EXIT

"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --timeout 180 \
  --iterations 3 \
  --visible-seconds 15 \
  >"$ONLINE_GUI_LOG" 2>&1 &
ONLINE_GUI_PID=$!

WINDOW_DETECTED=0
ONLINE_WINDOW_ID=""
for _ in $(seq 1 100); do
  ONLINE_WINDOW_ID="$(
    sed -n 's/.*window_id=\(0x[0-9a-fA-F][0-9a-fA-F]*\).*/\1/p' "$ONLINE_GUI_LOG" \
      | tail -n 1
  )"
  if [ -n "$ONLINE_WINDOW_ID" ] && \
     xwininfo -id "$ONLINE_WINDOW_ID" -tree -stats >"$ONLINE_WINDOW_INFO" 2>&1 && \
     grep -Fq 'Map State: IsViewable' "$ONLINE_WINDOW_INFO" && \
     grep -Fq 'RTL RS Check GUI Smoke' "$ONLINE_WINDOW_INFO"; then
    WINDOW_DETECTED=1
    break
  fi
  kill -0 "$ONLINE_GUI_PID" 2>/dev/null || break
  sleep 0.2
done
printf 'rscheck GUI window ID: %s\n' "$ONLINE_WINDOW_ID"
cat "$ONLINE_WINDOW_INFO"
set +e
wait "$ONLINE_GUI_PID"
ONLINE_GUI_RC=$?
set -e
cat "$ONLINE_GUI_LOG"
test "$WINDOW_DETECTED" -eq 1
test "$ONLINE_GUI_RC" -eq 0
grep -F 'Width:' "$ONLINE_WINDOW_INFO"
grep -F 'Height:' "$ONLINE_WINDOW_INFO"
grep -F 'rows=行数 2 errors=错误 0 warnings=警告 0 mode=online case=positive iterations=3 window=mapped window_id=0x' "$ONLINE_GUI_LOG"
grep -F 'contract=elab-only' "$ONLINE_GUI_LOG"
)
```

成功标准是 `wait` 返回 `0`；`xwininfo` 显示 `IsViewable` 和有效 Width/Height；最后一行 `GUI_SMOKE_PASS` 带 `window=mapped`、`window_id=0x...`、`header-map=column-index strict-header=false` 和 `contract=elab-only`。运行日志中的命令必须包含 `--collector` 和 `--elab-db`，不得包含 inventory、filelist、top 或 passthrough。每轮都必须加载同一个 fresh elaborated KDB；最终结果为 2 行 PASS，首组显示 6 个物理实例、有效/期望拍 `5/5`。inventory 必须是 schema v2，report 必须是 schema v3，贡献必须为 `[1,1,0,1,1,1]`。在线 smoke 的报告和临时 inventory 位于系统临时目录，退出后自动清理。2026-07-24 记录不包含动态 step，不能作为本项证据。

## 9. 在线反例

反例复用同一个 `$ELAB_DB`，只把规格替换为 `tests/fixtures/specs_negative.csv`。该规格保留正确模块、clk/rst 和 CRG；RTL `RS_CRG_EN=0` 保持不变，只把有效拍数 `5` 写成 `6`，并把 Excel/internal `RS_CFG_EN` 标签写成 `真门控`。

```bash
cd "$PROJECT_ROOT"

NEG_REPORT="$TEST_ROOT/negative_report.json"
NEG_LOG="$TEST_ROOT/negative_console.log"

set +e
"$PYTHON_BIN" -m rscheck check \
  --excel "$PROJECT_ROOT/tests/fixtures/specs_negative.csv" \
  --config "$PROJECT_ROOT/config/rscheck.example.json" \
  --sheet 1 \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --npi-timeout 180 \
  --json-report "$NEG_REPORT" \
  >"$NEG_LOG" 2>&1
NEG_RC=$?
set -e

if [ "$NEG_RC" -ne 1 ]; then
  echo "online negative check returned $NEG_RC, expected 1" >&2
  sed -n '1,160p' "$NEG_LOG" >&2
  exit 1
fi
test -s "$NEG_REPORT"
sed -n '1,160p' "$NEG_LOG"
```

预期：

- 退出码严格为 `1`，不是 `0` 或 `2`。
- 控制台首行为 `RESULT: FAIL | rows=1 errors=<正整数> warnings=0`；不要固定历史 error 总数。
- finding 至少包含 `STEP_MISMATCH` 和 `RS_CFG_EN_LABEL_MISMATCH`；不要要求当前反例没有故意制造的模块/连线/CRG 错误。

### 9.1 反例 JSON 摘要和 finding 断言

```bash
"$PYTHON_BIN" - "$NEG_REPORT" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as stream:
    report = json.load(stream)

if report.get("schema_version") != 3:
    raise SystemExit("negative report must use schema_version 3")

actual_summary = report["summary"]
expected_fields = {
    "passed": False,
    "rows": 1,
    "passed_rows": 0,
    "failed_rows": 1,
    "warnings": 0,
}
if any(actual_summary.get(key) != value for key, value in expected_fields.items()):
    raise SystemExit("unexpected negative summary: {!r}".format(actual_summary))
if actual_summary.get("errors", 0) <= 0:
    raise SystemExit("negative report must contain at least one error: {!r}".format(actual_summary))

expected_codes = {
    "STEP_MISMATCH",
    "RS_CFG_EN_LABEL_MISMATCH",
}
actual_codes = {
    finding["code"]
    for row in report["rows"]
    for finding in row["findings"]
}
missing = expected_codes - actual_codes
if missing:
    raise SystemExit("missing negative findings: {!r}".format(sorted(missing)))
step_check = report["rows"][0]["step_check"]
if step_check["physical_instances"] != 6 or step_check["effective_step"] != 5:
    raise SystemExit("negative dynamic-step evidence is wrong: {!r}".format(step_check))
if step_check["expected"] != 6:
    raise SystemExit("negative expected step must be 6: {!r}".format(step_check))
print("negative summary/findings OK:", actual_summary, sorted(actual_codes))
PY
```

## 10. 安全门禁：旧 filelist passthrough 必须返回 2

下面命令故意使用已经删除的旧接口。即使同时提供合法的 `$ELAB_DB`，`-- -f ...` 也必须在 argparse 阶段被拒绝，collector 不应启动。

```bash
cd "$PROJECT_ROOT"

LEGACY_LOG="$TEST_ROOT/legacy_filelist.log"
set +e
"$PYTHON_BIN" -m rscheck check \
  --excel "$PROJECT_ROOT/examples/specs.csv" \
  --config "$PROJECT_ROOT/config/rscheck.example.json" \
  --sheet 1 \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  -- -f "$PROJECT_ROOT/examples/rtl/filelist.f" \
  >"$LEGACY_LOG" 2>&1
LEGACY_RC=$?
set -e

if [ "$LEGACY_RC" -ne 2 ]; then
  echo "legacy passthrough returned $LEGACY_RC, expected 2" >&2
  cat "$LEGACY_LOG" >&2
  exit 1
fi
grep -q 'unrecognized arguments' "$LEGACY_LOG"
cat "$LEGACY_LOG"
```

预期日志包含：

```text
rtl-rs-check: error: unrecognized arguments: -- -f ...
```

把 `-f ...` 分别替换为 `-sv <file>` 或 `-lib <library>`，也必须返回 `2`。

## 11. 安全门禁：work.lib++ 不能作为 elab 输入

Python runner 会在启动 collector 前拒绝名为 `work.lib++` 的编译库；解析后的最终路径同样检查，因此把它改名为符号链接也不能绕过门禁。这验证工具不会把编译库误当 elaborated KDB。

```bash
cd "$PROJECT_ROOT"

WORKLIB_LOG="$TEST_ROOT/work_lib_as_elab.log"
set +e
"$PYTHON_BIN" -m rscheck check \
  --excel "$PROJECT_ROOT/examples/specs.csv" \
  --config "$PROJECT_ROOT/config/rscheck.example.json" \
  --sheet 1 \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_ROOT/work.lib++" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --npi-timeout 180 \
  >"$WORKLIB_LOG" 2>&1
WORKLIB_RC=$?
set -e

if [ "$WORKLIB_RC" -ne 2 ]; then
  echo "work.lib++ input returned $WORKLIB_RC, expected 2" >&2
  cat "$WORKLIB_LOG" >&2
  exit 1
fi
grep -Fq 'not an elaborated KDB' "$WORKLIB_LOG"
cat "$WORKLIB_LOG"
```

预期：

- Python CLI 返回 `2`，表示输入被拒绝。
- 日志包含 `work.lib++ is a compiled Verdi library, not an elaborated KDB`。
- collector 不启动。
- 不得出现 `RESULT: PASS`。

## 12. inventory 模式互斥检查

离线 `--inventory` 模式不允许提供 `--elab-db`：

```bash
cd "$PROJECT_ROOT"

set +e
"$PYTHON_BIN" -m rscheck check \
  --excel tests/fixtures/specs.csv \
  --config config/rscheck.example.json \
  --sheet 1 \
  --inventory tests/fixtures/inventory.json \
  --elab-db "$ELAB_DB"
MODE_RC=$?
set -e

test "$MODE_RC" -eq 2
```

预期错误：

```text
ERROR: --elab-db requires --collector
```

同一命令同时指定 `--inventory` 和 `--collector` 时，argparse 也必须返回 `2`，并报告两者互斥。

## 13. 测试产物检查

完成在线测试后，`$TEST_ROOT` 至少应包含：

```text
example_elab/work.lib++
example_elab/kdb.elab++
partial_load_elab/partial.elab++
partial_load_inventory.json
partial_load_report.json
partial_load_check.log
partial_load_gui.log
positive_inventory.json
positive_report.json
positive_report.csv
negative_report.json
negative_console.log
legacy_filelist.log
work_lib_as_elab.log
```

建议保留这些文件用于审计。确认不再需要后，只删除本次打印出的 `$TEST_ROOT`，不要对未展开、为空或根目录变量执行递归删除。

## 14. 故障排查

### 14.1 本地测试数量与当前分支不一致

- 确认位于正确仓库根目录。
- 执行 `python -m unittest discover -v`，不要只运行单个测试文件。
- 检查 Python 是否为 3.8 或更高版本。
- Windows 和 macOS 允许 Linux Bash/X11 测试 skipped；Linux 上应确认这些测试实际运行。
- 测试总数会随合法回归用例增长；应核对当前分支发现的测试名称和 skipped 原因，不要强行保持历史 `89`。

### 14.2 `header validation failed`

- 该错误只会在配置 `validate_headers=true`、命令行传入 `--header-check` 或 GUI 勾选“严格校验表头（可选）”时出现；默认解析不比较表头文字。
- 检查 `--sheet`、`header_row` 和 `data_start_row`。
- 检查九个 `--column FIELD=INDEX` 是否全部为 1-based 正整数且互不重复。
- 若有意使用严格诊断，确认映射后的表头精确为 `Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`、`RS_CFG_EN`。
- 若实际表头本来就是自定义业务名称，关闭该可选诊断；内部属性仍由九个列号映射，不由表头文字决定。

### 14.3 `NPI collector executable not found`

- 重新执行第 6 节构建。
- 确认 `COLLECTOR` 指向文件而不是目录。
- 执行 `test -x "$COLLECTOR"` 和 `file "$COLLECTOR"`。

### 14.4 `libNPI.so: cannot open shared object file`

- 检查 `VERDI_HOME` 和 `NPI_PLATFORM`。
- 执行 `ldd "$COLLECTOR" | grep libNPI`。
- 必要时使用 `--npi-lib-dir "$NPI_LIB_DIR"`。
- 不要把动态库路径伪装成设计参数；它与 `--elab-db` 是不同用途。

### 14.5 Verdi 或 license 初始化失败

- 确认 `VERDI_HOME`、`NOVAS_INST_DIR`、`LM_LICENSE_FILE`、`SNPSLMD_LICENSE_FILE` 已在当前 shell 中设置。
- 使用组织内部批准的 license 地址；不要把真实地址、密码或 token 写入本仓库。
- 向 EDA 管理员确认工具版本、平台目录和 license feature 是否可用。

### 14.6 `Verdi elaborated database not found` 或 `must be a directory`

- `--elab-db` 必须是已经存在的目录。
- 相对路径按运行命令时的当前目录解析；自动化中建议使用绝对路径。
- `.elab++` 不是强制后缀，但路径内容必须是 `elabcom` 生成的 elaborated KDB。

### 14.7 `NPI_LOAD_PARTIAL` 或 `error[NPI_LOAD]`

- 当前 Python runner 会在 collector 启动前拒绝 `work.lib++` 及其符号链接别名；若看到对应错误，改传真正的 elaborated KDB。
- `warning[NPI_LOAD_PARTIAL]` 表示 load 返回 0，但 NPI 仍能枚举 top。该 warning 本身不阻止 PASS；继续核对报告中所有目标证据，不能用它忽略 `POSITION_NOT_FOUND`、端口、parameter 或 CRG 错误。
- `error[NPI_LOAD]` / 退出 11 表示 load 返回 0 且没有任何 top 可查询。返回第 7 节重建 KDB，并确认 `-top` 与 Excel 层次根一致。
- 执行 `ldd "$COLLECTOR" | grep libNPI.so`，确认运行时库、`VERDI_HOME`、PATH 中的 Verdi 与生成 KDB 的版本/平台一致。
- 直接运行 collector 时分别保存 stdout/stderr，并查看当前工作目录下 `rs_npi_collectorLog/compiler.log`。Python runner 在失败时会同时保留两个输出流的首尾诊断。

### 14.8 正例出现 `POSITION_NOT_FOUND`

- 确认 KDB 的顶层模块是 `top`。
- 示例 Excel 应填写 `position=tile_core`；确认配置 `position_mappings.tile_core=top.u_tile`，report 中应同时出现 `position_alias=tile_core` 和 `position=top.u_tile`。
- 若 `position_alias` 为空，说明 Excel 值未命中映射而被按完整路径直通；检查简写大小写和配置文件是否为当前 GUI/CLI 实际加载的文件。
- 确认解析后的 `top.u_tile` 与展开后的完整 NPI 层次一致。
- 确认没有拿到其他工程或旧版本 RTL 的 KDB。

### 14.9 正例出现 `NPI_UNRESOLVED`、`CRG_SOURCE_UNRESOLVED` 或多驱动

- 查看 JSON inventory 中对应实例的 `ports` 和 `clk_sources`。
- collector 的 traversal/driver warning 会 fail-closed，不应通过忽略 warning 来获得 PASS。
- 检查 clock gate/buffer 后实际被追踪到的第一个模块是否与 `CRG_source` 一致。

### 14.10 RTL `RS_CRG_EN` 检查失败（兼容 code 为 `RS_CFG_EN_*`）

- `RS_CFG_EN_PARAMETER_MISSING`：兼容规则键 `has_rs_cfg_en=true`，但实例证据没有 `RS_CRG_EN`；修正规则或 RTL/KDB。
- `RS_CFG_EN_PARAMETER_UNEXPECTED`：`has_rs_cfg_en=false`，但 RTL 实际仍有 `RS_CRG_EN`；不能仅靠 Excel `RS_CFG_EN` 留空规避。
- `RS_CFG_EN_LABEL_MISMATCH`：Excel/internal `RS_CFG_EN` 没有按模块规则填写精确 `假门控` 或空白。
- `RS_CFG_EN_VALUE_MISMATCH`：实例 `RS_CRG_EN` 字符串不表示数值 `0`；检查实例 override 和本次 elaborated KDB，不能只改 Excel 标签。
- `RS_CFG_EN_VALUE_UNRESOLVED`：inventory 中 `parameters.RS_CRG_EN` 为 `null`；检查 collector/NPI 参数遍历和 KDB，不能把 `null` 当作参数不存在。
- schema v1 或实例缺少整个 `parameters` 对象属于输入契约错误，应重新使用当前 collector 生成 schema v2 inventory。
- 同组多个实例时检查 finding 的实例路径；每个实例使用自己的 effective 值，任一失败都会使整行 FAIL。

### 14.11 模块规则或动态 step 检查失败

- 显式规则没有生效：规则键与 `RS_module` 大小写不完全一致。工具会采用默认 `has_rs_cfg_en=true`、`step_parameters=[]`，即要求 RTL `RS_CRG_EN`；核对 report v3 的 `module_rule`。
- `STEP_PARAMETER_MISSING`：规则中的 parameter 不存在于该实例；核对拼写、模块类型和 KDB。
- `STEP_PARAMETER_VALUE_UNRESOLVED`：parameter 为 `null`、X/Z/`?` 或非法值；该实例贡献未知。
- `STEP_CALCULATION_UNRESOLVED`：至少一个实例贡献未知；查看 report v3 的 `step_check.contributions`，先解决根因。
- `STEP_MISMATCH`：所有贡献已知，但总和与 Excel 不同；不要直接用物理实例数替代有效拍数。

### 14.12 反例返回 2，而不是 1

退出码 `2` 说明检查尚未进入 RTL 差异判定，通常是 KDB、collector、动态库、license、Excel 或配置错误。缺少模块专属规则不会导致退出码 `2`；这种情况使用默认规则。先解决日志中的基础设施错误，再验证反例 finding。

### 14.13 旧 `-- -f` 命令没有返回 2

- 确认运行的是当前仓库中的 `rscheck`，而不是系统中已安装的旧版本。
- 执行 `python3 -c 'import rscheck; print(rscheck.__file__)'` 检查导入位置。
- 当前生产 CLI 不存在 passthrough；任何恢复任意 NPI 参数转发的改动都应视为安全回归。

## 15. Verdi GUI 与一键正向链路

本节只测试 Verdi GUI。工具自带的 Tkinter GUI 使用第 4.1 节和第 8.2 节命令；两者共享显示会话发现，但窗口、用途和启动命令不同。

### 15.1 GUI 前提和独立探测

GUI 可来自本地图形会话、VNC/XRDP 桌面，或客户端已运行 X server 的 `ssh -Y` 会话。GNOME 不是必需条件；Wayland 必须同时启用 Xwayland。按系统选择一组安装命令。

Debian/Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y x11-utils
```

RHEL/CentOS：

```bash
sudo yum install -y xorg-x11-utils
```

应从图形用户自己的终端运行，并确保该用户能读取仓库和 KDB。root 跨用户读取会话环境仅是兼容回退。进入仓库后，独立探测不需要 Verdi 或 license：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_verdi_gui.sh --probe-only
```

也可验证端到端脚本使用的同一探测库：

```bash
bash scripts/test_vm_verdi_gui.sh --gui-probe-only
```

### 15.2 独立启动已有 elaborated KDB

前台模式：

```bash
: "${ELAB_DB:?export ELAB_DB to an elaborated KDB directory}"
bash scripts/launch_verdi_gui.sh --elab-db "$ELAB_DB"
```

后台模式：

```bash
: "${ELAB_DB:?export ELAB_DB to an elaborated KDB directory}"
bash scripts/launch_verdi_gui.sh \
  --elab-db "$ELAB_DB" \
  --background
```

启动器只调用 `verdi -elab "$ELAB_DB"`。它拒绝 `work.lib++`、普通文件、RTL/filelist、`-f`、`-sv`、`-lib`、`-top`、位置参数和 `--` passthrough。SSH X11 转发的前台或后台 Verdi 都依赖当前隧道，使用期间必须保持 SSH 连接。

### 15.3 一键端到端测试

`scripts/test_vm_verdi_gui.sh` 自动执行：全量 Python 测试、collector 构建、故意带 elaboration error 但 top 可查询的 partial KDB CLI/GUI 回归、clean 示例 `vericom/elabcom`、`verdi -elab <kdb.elab++>` 严格 top 窗口检测、同一 fresh KDB 的在线 GUI 正例和反例、离线 GUI 100 轮/10,000 行，以及 `tile_core -> top.u_tile`、NPI positions 仅全路径、inventory v2/report v3、6 个物理实例、`rs_mode` 和 `[1,1,0,1,1,1]` 贡献证据断言。每次 GUI smoke 还会完成 `excel`、`columns`、`rtl`、`position_mappings`、`module_rules` 五根配置的完整导出/导入往返。partial KDB 必须为 2 行 PASS、0 error、1 个 `NPI_LOAD_PARTIAL` warning，GUI 的 GLOBAL 行显示 PASS。启动阶段和 PASS 前复核都要求新窗口标题匹配 `VERDI_READY_REGEX`；任意新 Verdi 窗口加固定等待不能通过。脚本还确认精确 KDB 对应进程仍存活，并扫描 Verdi/collector 日志。`work.lib++` 仅供 `elabcom` 准备 KDB；NPI 检查的唯一设计输入始终是 `--elab-db`。脚本默认只按本次 KDB 路径关闭它启动的 Verdi；设置 `KEEP_VERDI_GUI=1` 才在成功后保留窗口。

VM 脚本对以下六份 GUI 日志逐一执行硬断言；缺少任意一份日志中的固定 marker 都会使脚本非零退出：

```text
online_gui_positive.log
partial_load_gui.log
online_gui_negative.log
offline_gui_default_rule.log
offline_gui_100_rounds.log
offline_gui_10000_rows.log
```

固定 marker 为：

```text
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules
```

在当前 shell 已能正常启动 Verdi 的图形 shell 中执行。正式 VM 复现推荐使用仓库外层 fresh-checkout 驱动；它默认测试克隆时的 `origin/main`，运行根目录位于当前用户 `$HOME`，也可用 `VM_RUN_BASE` 的绝对路径指向其他可写目录：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_verdi_gui.sh --probe-only
bash scripts/test_vm_fresh_checkout.sh
```

固定到完整提交号或静默加载站点环境文件。`VERDI_ENV_FILE` 必须是可信的绝对路径；它在隔离子进程中加载，输出/xtrace 被抑制，返回非零时不启动正式测试：

```bash
bash scripts/test_vm_fresh_checkout.sh --commit FULL_SHA
VERDI_ENV_FILE=/path/to/site_env.sh bash scripts/test_vm_fresh_checkout.sh --commit FULL_SHA
```

fresh 驱动只支持 `--commit REV` 和 `--help`。每次运行在 `${VM_RUN_BASE:-$HOME}` 生成唯一根目录，最多三次带 TERM/KILL 上限的 clone 并保留每个 `repo_attemptN`；完整输出为 `full_vm_test.log`，正式产物固定在 `artifacts`，PASS/FAIL 后均不自动删除。root 且未设置 license 时，正式脚本会自动读取已选中桌面用户的登录初始化，但只导入 `LM_LICENSE_FILE`/`SNPSLMD_LICENSE_FILE`，不导入 PATH 等其他内容，也不打印值；已有 license 值优先，`VERDI_AUTO_LICENSE_IMPORT=0` 可禁用。只有已信任、已核对且位于 VM 本机文件系统的 checkout 才直接运行 `bash scripts/test_vm_verdi_gui.sh`。

可移植覆盖项：

| 分类 | 环境变量 |
|---|---|
| Verdi 定位 | `VERDI_BIN`、`VERDI_HOME`、`NOVAS_INST_DIR` |
| GUI 选择 | `GUI_USER`、`GUI_DISPLAY`、`GUI_XAUTHORITY`、`GUI_SESSION_PID` |
| Verdi 就绪 | `GUI_START_TIMEOUT`、`VERDI_WINDOW_REGEX`、`VERDI_READY_REGEX` |
| 测试工具链 | `PYTHON_BIN`、`CXX` |
| 非标准 NPI 布局 | `NPI_INC_DIR`、`NPI_LIB_DIR` |
| GUI 压测规模 | `GUI_ONLINE_ITERATIONS`、`GUI_STRESS_ITERATIONS`、`GUI_LOAD_ROWS`、`GUI_VISIBLE_SECONDS` |
| fresh 运行目录 | `VM_RUN_BASE`（绝对路径） |
| 站点环境/license | `VERDI_ENV_FILE`（绝对路径）、`VERDI_AUTO_LICENSE_IMPORT` |

`VERDI_WINDOW_REGEX` 只用于预筛 Verdi 相关窗口，不能决定就绪；`VERDI_READY_REGEX` 必须匹配包含 elaborated top 的窗口标题。端到端脚本构建时将 `NPI_INC_DIR`/`NPI_LIB_DIR` 传给 Makefile，并在在线检查中显式使用 `--npi-lib-dir "$NPI_LIB_DIR"`。完整默认值、SSH/VNC/XRDP 命令、成功输出和故障排查见 [Verdi GUI 端到端复现指南](VM_GUI_TEST.md)。直接运行时产物位于 `.gitignore` 排除的目录；fresh 驱动产物位于仓库外的本轮 `${VM_RUN_BASE:-$HOME}/rscheck_fresh.*`，两者均不提交 Git。

当前 `0.8.0` 验收见 [GUI 完整配置导入导出与 VM 压测验证记录](TEST_RESULTS_CONFIG_IO_2026-07-26.md)，固定到提交 `d416493648aaffd446ca46bad2f994e5a131d06d`：Windows `Ran 220 tests`、`OK (skipped=44)`；CentOS/Python 3.8 的 220 项全部通过且无 skip；partial/clean KDB、Verdi GUI、在线正反例、默认规则、离线 100 轮和 10,000 行负载均通过，上述六份 GUI 日志全部命中五根配置 round-trip marker。`TEST_RESULTS_NPI_PARTIAL_LOAD_2026-07-25.md` 是 `0.7.1` 历史基线，其余 `TEST_RESULTS_*.md` 是更早功能阶段的历史基线。
