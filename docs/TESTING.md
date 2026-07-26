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
| L3 | 通用本地环境 | position/CRG_source 简写 + schema v3 离线正例 inventory + report v4 | `0` | 两种简称均解析为完整路径；CRG trace 精确命中、报告保留 depth/path；6 个物理实例贡献 `[1,1,0,1,1,1]`，两组均 PASS |
| L4 | 通用本地环境 | 离线反例 inventory | `1` | 1 行 FAIL，至少包含 `STEP_MISMATCH` 和 `RS_CFG_EN_LABEL_MISMATCH` |
| L5 | 通用本地环境 | 模块规则、动态 step、门控和 CRG 有界追踪合同 | `0` | 规则 clk formal、多分支任一命中、精确排除 `clk/rst_n`、模块跳数、深度截断、unavailable、v2 legacy 兼容及 warning-only 退出码均有独立用例 |
| R0 | Linux + X11/Xwayland + Tk | GUI “验证 Excel”路径 | `0` | 20 轮均为 2 行 VALID、0 error、0 warning |
| R1 | Linux + X11/Xwayland + Tk | 工具自带 GUI 可见正例/反例和 100 轮稳定性 | `0` | 正例 100 轮均为 2 行 PASS、0 error、0 warning；反例显示 FAIL |
| R2 | Linux + X11/Xwayland + Tk | 工具自带 GUI 10,000 行负载 | `0` | 单轮 10,000 行、0 error、0 warning |
| R3 | Linux + Verdi/NPI + Tk | 工具自带 GUI 在线 KDB smoke | `0` | 3 轮均为 2 行 PASS；report 保留 `tile_core`，collector/inventory 只出现 `top.u_tile`，并保留动态拍数证据 |
| R4 | 通用 Python 环境 | 取消发生在后台进程启动阶段 | `0` | 100 轮全部通过，不遗留子进程 |
| R5 | Windows / Linux / macOS + Tk | GUI 完整配置导出、导入与事务回归 | `0` | 六个根对象和三个数据库完整往返；副本、dirty、搜索过滤、同路径拒绝及无效/取消/拒绝原子性均通过，输出固定 config-io marker |
| R6 | Linux + Verdi/NPI + Tk | 有 clk、无 rst 的 GUI 在线隔离回归 | `0` | 默认 20 轮；每轮被测行均为预期 FAIL、1 error，finding 精确为 `RST_PORT_MISSING`，不得出现 `CLK_PORT_MISSING`/`CLK_UNCONNECTED` |
| R7 | Linux + X11/Xwayland + Tk | `has_rs_cfg_en=false` 的 Excel 任意文本 GUI 专项 | `0` | 默认 20 轮；每轮 1 行 PASS、0 error、0 warning，原始 `RS_CFG_EN` 文本保留，RTL 无 `RS_CRG_EN` 且 findings 为空 |
| R8 | Linux + X11/Xwayland + Tk | Excel/internal `RS_CFG_EN=NA` 逐行豁免 GUI 专项 | `0` | 默认 20 轮；RTL `RS_CRG_EN=1` 仍不产生门控 finding，其他 module/step/clk/rst 检查继续且整行 PASS |
| R9 | Linux + X11/Xwayland + Tk | CRG_source 简称映射 GUI/JSON/CSV 专项 | `0` | 默认 20 轮；alias+full 保留，三层 witness 精确命中，`crg-source-check=pass trace-depth=3` |
| R10 | Linux + Verdi/NPI + Tk | CRG trace 深度上限 GUI 在线压测 | `0` | 默认 20 轮；最大深度 2 时六个多层实例产生 6 个 `CRG_TRACE_DEPTH_LIMIT` warning，direct depth 1 分支仍 PASS，行和 CLI 仍通过 |
| C1 | Linux + Verdi/NPI | C++ NPI collector 构建 | `0` | 生成可执行文件，`libNPI.so`/`libnpiL1.so` 均可解析；Language Model 与 L1 fallback 合并出全部 formal ports |
| K1 | Linux + Verdi | `vericom` 编译示例 RTL | `0` | 生成 `work.lib++` |
| K2 | Linux + Verdi | `elabcom` 生成测试 KDB | `0` | 生成 `kdb.elab++` 目录 |
| V0 | Linux + X11/Xwayland | 无 Verdi/license 的两个 GUI 环境探测 | `0` | `rscheck GUI probe PASS` / `GUI probe PASS` |
| V1 | Linux + Verdi + X11/Xwayland | Verdi GUI 加载同一 KDB | `0` | 新窗口标题匹配 `VERDI_READY_REGEX`，明确显示 elaborated top `top` |
| N1 | Linux + Verdi/NPI | 在线正例 | `0` | 2 行通过；首组 CRG depth 3 命中且分支任一命中，inventory v3/report v4 |
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

这些测试覆盖配置、九列解析、两套路径映射、实例分组、模块规则、逐模块 clk/rst、动态 step、RTL `RS_CRG_EN` 与 Excel 标签，并覆盖从规则 clk formal 出发的有界 CRG 上游追踪：多分支、`clk/rst_n` 精确排除、模块跳数、环路/对象预算、深度边界、完整 hierarchy 精确匹配、三个 warning code、inventory v2 legacy/v3、report v4、GUI `CRG Trace` 与 CSV trace 证据。Windows/macOS 可以跳过明确标记为 Linux Bash/X11 或 POSIX-only 的用例；Linux 上适用用例不得意外 skipped。

实例分组回归必须同时覆盖非空 `RS_inst` 前缀和完整本地例化名：空 remainder 应合法，非空 remainder 仍按 `rtl.suffix_regex` 完整匹配。空后缀实例必须继续执行 module/parameter/step/逐模块 clk/rst 检查并计入物理实例数及规则计算后的有效 `step`；其解析后的 `CRG_source` 和可选 alias 只保留证据，不得进入 tag/index/连续编号、NPI positions 或 PASS/FAIL 判断。还应覆盖同一 scope 中 `PFX` 与 `PFX_C0` 会被 `RS_inst=PFX` 同时匹配，以及重叠 Excel 组仍产生 `AMBIGUOUS_GROUP_MATCH`；当前没有 exact-only 模式。

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

窗口出现后手工缩放到允许的最小尺寸 `980x680`，检查九个列映射、在线/离线数据源、报告路径和“导入/加载/导出”等操作按钮无重叠，并逐一检查六个页签：“检查配置”“模块规则库”“Position 映射库”“CRG Source映射库”“检查结果”“运行日志”。在两套映射页分别确认 `tile_core -> top.u_tile` 和 CRG_source 简称到完整 RTL 路径，并用临时配置完成新建、修改、保存、重载和删除；规则页同样验收 `rs_pipe` 的 `has_rs_cfg_en=true`、`step_parameters=rs_mode` 和 `clk_port=clk/rst_port=rst`。新建规则时把两个端口输入留空，应保存为 `clk/rst_n`。最后修改 GUI Excel 设置和三个内存数据库，导出完整配置副本并重新导入，确认六个根对象及所有端口规则完整恢复。截图必须基于当前版本重新验收，且不提交仓库。

从仓库根目录可直接复制运行可见 GUI smoke。脚本使用临时配置验证 position、CRG Source 映射和模块规则的新建/保存/重载/删除，并把当前 Excel/列号、完整 `rtl`、包含未单独保存修改的三个数据库导出为副本后重新导入；它不会修改仓库配置，成功后把“Position 映射库”页保留 10 秒：

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
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules
```

该 marker 表示导出副本保留六个根对象、当前 GUI Excel/列号、已加载 `rtl` 及三个完整内存数据库，导出没有切换 active path 或清除 dirty，随后导入整体替换配置、切换路径并清除 dirty。成功行还必须包含 `crg-source-db=crud-complete`。搜索过滤不得影响数据库导出。取消、首次/复核无效配置、拒绝丢弃 Excel 表单或任一数据库、导出到当前配置同一路径、配置路径输入框已变化但未加载，以及未编辑的纯数字 sheet 名在导出和运行时保持字符串类型，由自动测试单独覆盖；任何失败都必须保持原配置状态。“完整导入”必须拒绝缺少任一根对象的快照；兼容性“加载”允许旧配置省略 `crg_source_mappings` 并按空库处理，仍执行相同的 Excel/数据库确认和二次读取。

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
  --excel examples/specs.csv `
  --config config/rscheck.example.json `
  --sheet 1 `
  --inventory examples/inventory.json
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
  --inventory examples/inventory.json `
  --json-report $NegativeReport
if ($LASTEXITCODE -ne 1) {
    throw "Offline negative check did not return 1"
}

$Negative = Get-Content -Raw -Encoding utf8 $NegativeReport | ConvertFrom-Json
if ($Negative.summary.passed -ne $false -or $Negative.summary.failed_rows -ne 1) {
    throw "Unexpected negative report summary"
}
```

预期硬 finding 至少包含 `STEP_MISMATCH` 和 `RS_CFG_EN_LABEL_MISMATCH`。CRG trace finding 只允许是 warning，不应掩盖这些硬错误。

### 3.5 模块端口规则、动态 step、RTL `RS_CRG_EN` 和兼容标签合同回归

全量测试必须独立覆盖下列情况；不能只依赖一个同时触发多种 finding 的反例：

| 规则/实例条件 | 预期 |
|---|---|
| 没有模块专属显式规则 | 使用默认 `has_rs_cfg_en=true`、`step_parameters=[]`、`clk_port=clk`、`rst_port=rst_n`；兼容规则键实际要求 RTL `RS_CRG_EN`；`validate` 接受输入 |
| 默认规则，6 个物理实例 | 每个实例贡献 1，有效拍数 6 |
| 存在大小写精确匹配的显式规则 | 显式 `has_rs_cfg_en`、`step_parameters`、`clk_port`、`rst_port` 覆盖默认值；前者控制 RTL `RS_CRG_EN` |
| GUI 新建/修改规则时 clk/rst 输入留空 | 规范化为 `clk`、`rst_n`，导出/保存后显式写入 JSON |
| 旧显式规则缺少 `clk_port/rst_port` | 分别继承同一配置的 `rtl.clk_port/rst_port`；保存/导出时显式化且语义不变 |
| 自定义 `clk_port/rst_port` | checker 只按该模块规则选择端口；不同 `RS_module` 可使用不同 formal port 名 |
| RTL 模块存在并连接规则指定的 clk，但没有规则指定的 rst formal port | 行 FAIL，finding 精确为 `RST_PORT_MISSING`；不得出现 `CLK_PORT_MISSING`/`CLK_UNCONNECTED`；当前没有跳过 rst 检查的规则 |
| `step_parameters=["rs_mode"]`，值为 `1,1,0,1,1,1` | 贡献 `[1,1,0,1,1,1]`，有效拍数 5 |
| 两个 step parameters 且值都已解析 | 只有全部非零的实例贡献 1，至少一个为 0 贡献 0 |
| step parameter 缺失 | `STEP_PARAMETER_MISSING` + `STEP_CALCULATION_UNRESOLVED` |
| step parameter 为 `null`、X/Z、`?` 或非法值 | `STEP_PARAMETER_VALUE_UNRESOLVED` + `STEP_CALCULATION_UNRESOLVED` |
| 全部实例贡献 0，Excel `step=0` | PASS |
| 没有物理匹配实例，Excel `step=0` | `GROUP_NOT_FOUND`，不能 PASS |
| `has_rs_cfg_en=true`，RTL `RS_CRG_EN` 为 0，Excel/internal `RS_CFG_EN` 为 `假门控` | PASS |
| `has_rs_cfg_en=true`，RTL `RS_CRG_EN` 缺失/非零/未知 | 对应兼容 code `MISSING`/`VALUE_MISMATCH`/`VALUE_UNRESOLVED` |
| `has_rs_cfg_en=true`，实例只有同名 RTL `RS_CFG_EN=0`、没有 `RS_CRG_EN` | `RS_CFG_EN_PARAMETER_MISSING`；不得回退匹配旧 parameter 名 |
| `has_rs_cfg_en=false`，RTL `RS_CRG_EN` 不存在，Excel `RS_CFG_EN` 为空、`假门控`、数字或除 `NA` 外的任意其他字面内容 | PASS；字段仍解析并进入报告，不得出现 `RS_CFG_EN_LABEL_MISMATCH` |
| `has_rs_cfg_en=false`，RTL 实际存在 `RS_CRG_EN`，Excel `RS_CFG_EN` 为除 `NA` 外的任意字面内容 | `RS_CFG_EN_PARAMETER_UNEXPECTED`；不得另报标签 mismatch |
| 任意模块规则和任意 RTL `RS_CRG_EN` 状态，Excel `RS_CFG_EN` 去空白后精确为大写 `NA` | 跳过该行全部门控检查；module、实例、step、clk/rst 检查继续；`na`/`N/A` 不等价 |
| `step_parameters` 包含 `RS_CRG_EN` | 配置错误；该 parameter 由专门逻辑处理 |
| `step_parameters` 包含另一个真实 RTL parameter `RS_CFG_EN` | 作为普通动态拍参数接受，不与 Excel/internal 标签字段混淆 |
| v3 完整 trace 精确命中目标完整 hierarchy | `crg_source_check.status=pass`，matched node 保存 depth/path，无 CRG finding |
| v3 完整 trace 未命中 | `CRG_SOURCE_NOT_FOUND` warning；行 PASS/FAIL 和退出码只由其他硬检查决定 |
| 最大模块深度截断 | `CRG_TRACE_DEPTH_LIMIT` warning；depth N 节点仍可匹配但不继续展开 input |
| trace 缺失/无法解析，或 v2 `clk_sources` 未精确 legacy 命中 | `CRG_TRACE_UNAVAILABLE` warning |
| 中间模块 input 名精确为 `clk` 或 `rst_n` | 不展开；大小写或其他名字不受该排除规则影响 |

还必须断言 report `schema_version=4`，每行包含 `crg_source_check`；当前 collector inventory 必须为 schema v3，匹配 RS 实例的 `clock_trace` 必须记录规则 `clock_port`、配置深度、固定排除列表、状态和 module witness。loader 继续接受 v2，但 schema v1、缺少实例 `parameters` 或非法 v3 trace 必须被拒绝。

clk/rst 必须独立取证和判定，不能把“某一个端口缺失”实现为“整组端口信息不可用”。本地定向用例 `test_present_clk_and_missing_rst_reports_only_rst_missing` 对 finding 顺序和值做精确断言；VM 真实 NPI 回归则要求 `rs_clk_only.CLK_ONLY_RS` 的 inventory formal ports 精确为 `{clk,d,q}`，且 `clk.connection=top.u_tile.clk_rs`。

Position 映射必须有独立合同测试：`tile_core -> top.u_tile` 命中后 `SpecRow.position` 为全路径、`position_alias` 为简写；未登记的 `top.u_tile` 直通且 alias 为空；映射后相同 `(position, RS_inst)` 仍判重复；CLI/GUI report v4 和 CSV 保留 alias；交给 NPI runner 的唯一 positions 只能包含完整路径，绝不能包含 `tile_core`。

CRG Source 映射也必须有独立合同测试：`core_clock_source -> top.u_soc.u_crg_core` 命中后 `SpecRow.crg_source` 为全路径、`crg_source_alias` 为简写；未登记的完整路径直通且 alias 为空；validate JSON、report v4、CSV 和 GUI 同时保留 alias+full，并用该完整路径精确匹配 trace。CRG 路径不得加入 NPI positions。

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

macOS 不支持真实 NPI 在线采集；在线 collector、`libNPI.so`、`libnpiL1.so` 和 elaborated KDB 加载测试必须在 Linux/Verdi 环境执行。

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

先验收“Position 映射库”页、两套映射/模块规则保存与重新加载，以及六根完整配置的导出和导入。命令只修改 smoke 自己创建的临时配置：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --iterations 1 \
  --visible-tab positions \
  --visible-seconds 10
```

窗口保留期间应看到 `tile_core -> top.u_tile`，映射表、搜索框和编辑区无重叠或截断；smoke 同时已在临时配置完成 position、CRG Source 映射和模块规则的新增、修改、搜索、保存、重载、删除，以及六根配置副本导出和原子导入。终端末行应为 `GUI_SMOKE_PASS`，并包含 `config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules` 和 `crg-source-db=crud-complete`。

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
GUI_SMOKE_PASS: rows=行数 2 errors=错误 0 warnings=警告 0 mode=validate case=positive iterations=20 window=mapped ... header-map=column-index strict-header=false position-map=tile_core->top.u_tile config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules
```

2026-07-24 的 20 轮结果仅是动态 step 之前的历史基线；`0.7.1` fresh 验收也早于完整配置导入/导出。若把本节 20 轮 validate-only 作为当前 `0.11.0` 设备门禁，必须在目标设备重跑并确认六根 config-io marker，不能沿用旧结果。

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
grep -F 'config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules' "$POS_GUI_LOG"
grep -F 'position-map=tile_core->top.u_tile npi-positions=full-path-only' "$POS_GUI_LOG"
)
```

每轮都会解析示例 Excel 的 `tile_core`，要求 GUI/report 中完整路径为 `top.u_tile`、`position_alias=tile_core`，并要求 inventory positions 只有 `top.u_tile`。`0.7.1` 的 100 轮和 10,000 行结果是历史性能基线；当前 `0.11.0` 必须重新运行并出现六根 config-io 和 report-v4/inventory-v3 marker。

`has_rs_cfg_en=false` 的 GUI 专项故意在 Excel/internal `RS_CFG_EN` 中写入任意非标准文本，并让离线 inventory 中的实例不含 `RS_CRG_EN`。下面默认连续运行 20 轮并保留“检查结果”页；每轮都必须 PASS，GUI/JSON report 必须保留解析后的 Excel 文本，同时不得产生 `RS_CFG_EN_LABEL_MISMATCH` 或其他 finding：

```bash
(
set -e
set -o pipefail
cd "$PROJECT_ROOT"
RS_CFG_DONTCARE_GUI_LOG="$(mktemp /tmp/rscheck_gui_rs_cfg_dontcare.XXXXXX.log)"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --rs-cfg-dontcare \
  --iterations 20 \
  --visible-tab results \
  --visible-seconds 10 \
  2>&1 | tee "$RS_CFG_DONTCARE_GUI_LOG"
grep -F 'mode=offline case=rs-cfg-dontcare iterations=20 window=mapped' "$RS_CFG_DONTCARE_GUI_LOG"
grep -F 'has-rs-cfg-en=false label=dont-care rs-crg-en=absent findings=none' "$RS_CFG_DONTCARE_GUI_LOG"
grep -F 'parsed-rs-cfg-en=任意非标准文本' "$RS_CFG_DONTCARE_GUI_LOG"
grep -F 'config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules' "$RS_CFG_DONTCARE_GUI_LOG"
)
```

一键 VM 脚本把相同专项写入 `offline_gui_rs_cfg_dontcare.log`，默认轮次由 `GUI_RS_CFG_DONTCARE_ITERATIONS=20` 控制；该变量必须是十进制正整数。专项仍执行完整配置导入/导出、report v4/inventory v3 和结果页可见性门禁，而不是只调用 checker。

精确大写 `NA` 的逐行豁免使用另一个可见离线 GUI 专项。解析器先去除首尾空白，因此 ` NA ` 会规范化为 `NA`；`na`、`N/A` 和 `Na` 均不等价。该夹具故意保留 `RTL RS_CRG_EN=1`，用来证明只跳过本行全部 `RS_CFG_EN` 标签和 `RS_CRG_EN` 参数存在性/值检查；`RS_module`、实例分组、动态 `step`、clk 和 rst 仍必须执行并通过：

```bash
(
set -e
set -o pipefail
cd "$PROJECT_ROOT"
RS_CFG_NA_GUI_LOG="$(mktemp /tmp/rscheck_gui_rs_cfg_na.XXXXXX.log)"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --rs-cfg-na \
  --iterations 20 \
  --visible-tab results \
  --visible-seconds 10 \
  2>&1 | tee "$RS_CFG_NA_GUI_LOG"
grep -F 'mode=offline case=rs-cfg-na iterations=20 window=mapped' "$RS_CFG_NA_GUI_LOG"
grep -F 'rs-cfg-en=NA check=skipped rtl-rs-crg-en=1 findings=none' "$RS_CFG_NA_GUI_LOG"
grep -F 'config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules' "$RS_CFG_NA_GUI_LOG"
grep -F 'schemas=report-v4/inventory-v3' "$RS_CFG_NA_GUI_LOG"
)
```

一键 VM 脚本把该专项写入 `offline_gui_rs_cfg_na.log`，默认轮次由 `GUI_RS_CFG_NA_ITERATIONS=20` 控制；该变量必须是十进制正整数。每轮必须为 1 行 PASS、0 error、0 warning，报告保留规范化后的 `RS_CFG_EN=NA`，门控 findings 为空，并且非门控检查证据仍完整。

CRG_source 简称映射专项使用独立的一行规格，证明 GUI、JSON 和 CSV 同时保留简称与完整路径，并用三层 trace witness 精确命中：

```bash
(
set -e
set -o pipefail
cd "$PROJECT_ROOT"
CRG_SOURCE_MAPPING_GUI_LOG="$(mktemp /tmp/rscheck_gui_crg_source_mapping.XXXXXX.log)"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --crg-source-mapping \
  --iterations 20 \
  --visible-tab results \
  --visible-seconds 10 \
  2>&1 | tee "$CRG_SOURCE_MAPPING_GUI_LOG"
grep -F 'state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=offline case=crg-source-mapping iterations=20' "$CRG_SOURCE_MAPPING_GUI_LOG"
grep -F 'crg-source-map=core_clock_source->top.u_soc.u_crg_core gui-json-csv=alias+full crg-source-check=pass trace-depth=3 findings=none' "$CRG_SOURCE_MAPPING_GUI_LOG"
grep -F 'config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules' "$CRG_SOURCE_MAPPING_GUI_LOG"
grep -F 'schemas=report-v4/inventory-v3' "$CRG_SOURCE_MAPPING_GUI_LOG"
)
```

一键 VM 脚本把该专项写入 `offline_gui_crg_source_mapping.log`，默认轮次由正整数 `GUI_CRG_SOURCE_MAPPING_ITERATIONS=20` 控制。该用例必须在 `clock_trace.modules` 中保存 depth 3 matched witness；inventory positions 仍只来自 `position`。

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
grep -F 'config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules' "$LOAD_GUI_LOG"
grep -F 'GUI_LOAD wall=' "$LOAD_GUI_LOG"
)
```

2026-07-24 九列版本的性能数字只是历史数据，不是当前 `0.11.0` 或其他设备的硬门槛。需要本机基线时应重跑并确认 report-v4/inventory-v3 与六根 config-io marker。

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
export NPI_L1_INC_DIR="${NPI_L1_INC_DIR:-$VERDI_HOME/share/NPI/L1/C/inc}"
if [ -z "${NPI_L1_LIB_DIR:-}" ]; then
  if [ -f "$NPI_LIB_DIR/libnpiL1.so" ]; then
    NPI_L1_LIB_DIR="$NPI_LIB_DIR"
  else
    NPI_PLATFORM_LOWER="$(printf '%s' "$NPI_PLATFORM" | tr '[:upper:]' '[:lower:]')"
    NPI_L1_LIB_DIR="$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM_LOWER"
  fi
fi
export NPI_L1_LIB_DIR

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
test -f "$NPI_L1_INC_DIR/npi_L1.h"
test -f "$NPI_L1_LIB_DIR/libnpiL1.so"
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
  NPI_L1_INC="$NPI_L1_INC_DIR" \
  NPI_L1_LIB="$NPI_L1_LIB_DIR" \
  CXX="$CXX"

COLLECTOR="$PROJECT_ROOT/npi/build/rs_npi_collector"
test -x "$COLLECTOR"
file "$COLLECTOR"
ldd "$COLLECTOR" | grep 'libNPI\.so'
ldd "$COLLECTOR" | grep 'libnpiL1\.so'
```

仓库路径及 `NPI_INC_DIR`、`NPI_LIB_DIR`、`NPI_L1_INC_DIR`、`NPI_L1_LIB_DIR` 不得包含空白；GNU Make 会拆分目标名，Makefile 会在构建前明确拒绝这类路径。

预期：

- `make` 返回 `0`。
- `$COLLECTOR` 是当前 CentOS 架构的可执行文件。
- `ldd` 中 `libNPI.so` 和 `libnpiL1.so` 都指向预期目录，且都不是 `not found`。

该 collector 会合并 Language Model `npiPort` 遍历和 NPI L1 `npi_mod_inst_get_port` fallback，采集每个直接子 module instance 的全部 formal ports。Python runner 还会传 `--trace-rules` 和 `--trace-max-depth`，让 collector 从每种 RS module 的有效 clk formal 做有界 Netlist 上游追踪并写出 inventory v3 `clock_trace`。`--clk-port/--rst-port` 仍保留为兼容接口。

若 NPI 库不在标准目录，在 `rscheck check` 命令中增加 `--npi-lib-dir "$NPI_LIB_DIR"`。

该选项只配置 collector 子进程的主要 NPI 动态库搜索路径，不是设计参数，也不会传给 `npi_load_design`。`libnpiL1.so` 位于其他目录时由构建时的 rpath 和当前 `LD_LIBRARY_PATH` 定位。

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

`--elab-db` 不强制目录名必须以 `.elab++` 结尾，因为 `elabcom -elab <path>` 允许自定义名称；但该路径必须存在且必须是目录。collector 调用 `npi_load_design -elab <path>`；返回 0 时还会按手册示例枚举 top。存在可查询 top 才继续，并由后续 position、实例、formal port、parameter 和 CRG trace 取证；无 top 才退出 11。CRG trace 不完整只产生专用 warning，CRG_source 路径不加入 collector positions。

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
  --crg-trace-max-depth 16 \
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
[PASS] row 2 OUT_IF | tile_core -> top.u_tile / AAAA_BBB physical=6 effective=5 expected=5 CRG_source=crg_core -> top.u_tile.u_crg CRG_trace=PASS RS_CFG_EN=假门控
[PASS] row 3 CTRL_IF | tile_core -> top.u_tile / CTRL_RS_D0 physical=1 effective=1 expected=1 CRG_source=crg_aux -> top.u_tile.u_aux_crg CRG_trace=PASS RS_CFG_EN=假门控
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

if report.get("schema_version") != 4:
    raise SystemExit("report must use schema_version 4")
if inventory.get("schema_version") != 3:
    raise SystemExit("inventory must use schema_version 3")

positions = inventory.get("positions")
if not isinstance(positions, dict) or set(positions) != {"top.u_tile"}:
    raise SystemExit("collector must receive only the resolved full path: {!r}".format(positions))
if "tile_core" in positions:
    raise SystemExit("position alias leaked into NPI inventory positions")
if set(positions) & {"crg_core", "crg_aux", "top.u_tile.u_crg", "top.u_tile.u_aux_crg"}:
    raise SystemExit("CRG source leaked into NPI inventory positions: {!r}".format(positions))
expected_crg = {
    "AAAA_BBB": ("top.u_tile.u_crg", "crg_core", 3),
    "CTRL_RS_D0": ("top.u_tile.u_aux_crg", "crg_aux", 1),
}
if len(csv_rows) != 2:
    raise SystemExit("CSV report row count changed: {!r}".format(csv_rows))
for csv_row in csv_rows:
    expected_full, expected_alias, expected_depth = expected_crg[csv_row["RS_inst"]]
    evaluations = json.loads(csv_row["crg_trace_evidence"])
    if (
        csv_row.get("position") != "top.u_tile"
        or csv_row.get("position_alias") != "tile_core"
        or csv_row.get("CRG_source") != expected_full
        or csv_row.get("crg_source_alias") != expected_alias
        or csv_row.get("crg_trace_status") != "pass"
        or csv_row.get("crg_trace_max_depth") != "16"
        or not evaluations
        or any(item.get("status") != "matched" for item in evaluations)
        or any(item.get("matched", {}).get("depth") != expected_depth for item in evaluations)
    ):
        raise SystemExit("CSV report lost CRG trace evidence: {!r}".format(csv_row))

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
    ports = instances[name].get("ports")
    if not isinstance(ports, dict) or set(ports) != {"clk", "rst", "d", "q"}:
        raise SystemExit("collector did not retain all rs_pipe formal ports for {}: {!r}".format(name, ports))
    trace = instances[name].get("clock_trace")
    if (
        not isinstance(trace, dict)
        or trace.get("clock_port") != "clk"
        or trace.get("max_depth") != 16
        or trace.get("excluded_inputs") != ["clk", "rst_n"]
        or trace.get("status") != "complete"
    ):
        raise SystemExit("invalid inventory v3 clock_trace for {}: {!r}".format(name, trace))
    modules = {item["instance"]: item for item in trace.get("modules", [])}
    if name == "CTRL_RS_D0":
        required = {"top.u_tile.u_aux_crg": 1}
    else:
        required = {
            "top.u_tile.u_occ": 1,
            "top.u_tile.u_clk_mux": 2,
            "top.u_tile.u_crg": 3,
            "top.u_tile.u_aux_crg": 3,
        }
    for expected_instance, expected_depth in required.items():
        node = modules.get(expected_instance)
        if (
            not isinstance(node, dict)
            or node.get("depth") != expected_depth
            or node.get("path", [])[-1:] != [expected_instance]
        ):
            raise SystemExit("missing CRG witness {} for {}: {!r}".format(expected_instance, name, trace))

custom = instances.get("CUSTOM_RS")
if not isinstance(custom, dict) or custom.get("module") != "rs_custom":
    raise SystemExit("custom-port fixture instance is missing: {!r}".format(custom))
if set(custom.get("ports", {})) != {"clock_i", "reset_ni", "d", "q"}:
    raise SystemExit("rs_custom formal ports are incomplete: {!r}".format(custom))
if custom.get("clock_trace") is not None:
    raise SystemExit("unrequested rs_custom trace must be null: {!r}".format(custom))

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
    expected_full, expected_alias, expected_depth = expected_crg[row["spec"]["RS_inst"]]
    if (
        row["spec"].get("CRG_source") != expected_full
        or row["spec"].get("crg_source_alias") != expected_alias
    ):
        raise SystemExit("report lost CRG_source alias/full evidence: {!r}".format(row["spec"]))
    crg_check = row.get("crg_source_check")
    if (
        not isinstance(crg_check, dict)
        or crg_check.get("expected") != expected_full
        or crg_check.get("status") != "pass"
        or not crg_check.get("instances")
        or any(item.get("status") != "matched" for item in crg_check["instances"])
        or any(item.get("matched", {}).get("depth") != expected_depth for item in crg_check["instances"])
    ):
        raise SystemExit("report lost CRG matched witness: {!r}".format(crg_check))
    rule = row.get("module_rule")
    if rule != {
        "name": "rs_pipe",
        "has_rs_cfg_en": True,
        "step_parameters": ["rs_mode"],
        "clk_port": "clk",
        "rst_port": "rst",
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
print("positive multi-level CRG trace inventory-v3/report-v4 evidence OK:", actual)
PY
```

预期输出 `positive multi-level CRG trace inventory-v3/report-v4 evidence OK`，Python 返回 `0`。这证明首组从 RS 经过 `u_occ` depth 1、`u_clk_mux` depth 2，分支到 `u_crg`/`u_aux_crg` depth 3，目标分支任一命中即可通过；同时证明 `clk_occ.clk/rst_n` 没有被展开为追踪分支。第二组 `u_aux_crg` 为 direct depth 1。两种 Excel 简写仍保留 alias+full，inventory positions 只包含 position。

完整 VM 脚本还会基于同一 KDB 临时生成一行 `rs_custom/CUSTOM_RS` 规格和模块规则 `clk_port=clock_i`、`rst_port=reset_ni`，并故意把 `CRG_source` 写成错误值。clk/rst 和其他硬检查仍必须 PASS，CRG 子检查产生一个 `CRG_SOURCE_NOT_FOUND` warning；CLI 仍返回 `0`：

```text
custom module clk/rst formal-port rule evidence OK: clock_i/reset_ni
crg-source-check=warning finding-code=CRG_SOURCE_NOT_FOUND
```

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
export NPI_L1_INC_DIR="${NPI_L1_INC_DIR:-$VERDI_HOME/share/NPI/L1/C/inc}"
if [ -z "${NPI_L1_LIB_DIR:-}" ]; then
  if [ -f "$NPI_LIB_DIR/libnpiL1.so" ]; then
    NPI_L1_LIB_DIR="$NPI_LIB_DIR"
  else
    NPI_PLATFORM_LOWER="$(printf '%s' "$NPI_PLATFORM" | tr '[:upper:]' '[:lower:]')"
    NPI_L1_LIB_DIR="$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM_LOWER"
  fi
fi
export NPI_L1_LIB_DIR
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
  NPI_L1_INC="$NPI_L1_INC_DIR" \
  NPI_L1_LIB="$NPI_L1_LIB_DIR" \
  CXX="$CXX"
test -x "$COLLECTOR"
test -d "$ELAB_DB"
test -f "$NPI_LIB_DIR/libNPI.so"
test -f "$NPI_L1_INC_DIR/npi_L1.h"
test -f "$NPI_L1_LIB_DIR/libnpiL1.so"

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

成功标准是 `wait` 返回 `0`；`xwininfo` 显示 `IsViewable` 和有效 Width/Height；最后一行 `GUI_SMOKE_PASS` 带 `window=mapped`、`contract=elab-only` 和 `schemas=report-v4/inventory-v3`。运行日志中的命令必须包含 `--collector`、`--elab-db` 和 `--crg-trace-max-depth 16`，不得包含 inventory、filelist、top 或 passthrough。每轮都必须加载同一个 fresh elaborated KDB；最终结果为 2 行 PASS、0 warning。smoke 内部必须断言 inventory v3 的多层 `clock_trace` 和 report v4 的 matched depth/path；首组贡献为 `[1,1,0,1,1,1]`。

在线深度上限 GUI 专项使用同一 KDB，把最大模块跳数设为 `2` 并连续运行 20 轮：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --timeout 180 \
  --crg-depth-limit \
  --iterations 20 \
  --visible-tab results \
  --visible-seconds 15
```

首组目标在 depth 3，因此 6 个物理实例各产生一个 `CRG_TRACE_DEPTH_LIMIT` warning；depth 2 的 `u_clk_mux` 仍保存在证据中，但不再展开 input。第二组 direct `u_aux_crg` 在 depth 1 命中。GUI 总体仍为 PASS、2 passed rows、0 error、6 warning，第一行显示 `WARNING`/`CRG Trace=WARNING`，第二行显示 `PASS`。smoke 自身返回 `0`，末行必须包含：

```text
mode=online case=crg-depth-limit iterations=20
crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6
schemas=report-v4/inventory-v3
```

一键 VM 脚本用正整数 `GUI_CRG_TRACE_DEPTH_ITERATIONS` 控制轮数，默认 `20`，并把专项保存为 `online_gui_crg_trace_depth_limit.log`。

有 clk、无 rst 的精确复现使用同一 fresh elaborated KDB，并通过可见 GUI 默认连续执行 20 轮：

```bash
cd "$PROJECT_ROOT"

"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --timeout 180 \
  --clk-without-rst \
  --iterations 20 \
  --visible-tab results \
  --visible-seconds 15
```

该 smoke 工具本身应返回 `0`，因为它验证的是预期失败是否被正确隔离；GUI 中被测行必须显示 FAIL、1 error、0 warning。成功 marker 必须同时包含：

```text
case=clk-present-rst-missing iterations=20
rule-ports=clk/rst_n
clk-port-evidence=present rst-port-evidence=missing finding-codes=RST_PORT_MISSING
```

每一轮都重新启动 collector 并加载 `$ELAB_DB`。inventory 中 `rs_clk_only.CLK_ONLY_RS` 的 formal ports 必须精确为 `clk/d/q`，`clk.connection` 必须是 `top.u_tile.clk_rs`；report finding 集合必须精确为 `{RST_PORT_MISSING}`，不得出现 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`。

## 9. 在线反例

反例复用同一个 `$ELAB_DB`，只把规格替换为 `tests/fixtures/specs_negative.csv`。该规格保留正确模块、clk/rst 和 CRG_source；RTL `RS_CRG_EN=0` 保持不变，只把有效拍数 `5` 写成 `6`，并把 Excel/internal `RS_CFG_EN` 标签写成 `真门控`。

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
- finding 至少包含 `STEP_MISMATCH` 和 `RS_CFG_EN_LABEL_MISMATCH`；不得出现 CRG finding。

### 9.1 反例 JSON 摘要和 finding 断言

```bash
"$PYTHON_BIN" - "$NEG_REPORT" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as stream:
    report = json.load(stream)

if report.get("schema_version") != 4:
    raise SystemExit("negative report must use schema_version 4")

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
  --excel examples/specs.csv \
  --config config/rscheck.example.json \
  --sheet 1 \
  --inventory examples/inventory.json \
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

### 14.4 `libNPI.so` / `libnpiL1.so` 无法加载

- 检查 `VERDI_HOME` 和 `NPI_PLATFORM`。
- 检查 `NPI_INC_DIR`、`NPI_LIB_DIR`、`NPI_L1_INC_DIR`、`NPI_L1_LIB_DIR`；头文件必须分别包含 `npi.h`、`npi_L1.h`。
- 执行 `ldd "$COLLECTOR" | grep -E 'libNPI|libnpiL1'`，两项都不能是 `not found`。
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
- `warning[NPI_LOAD_PARTIAL]` 表示 load 返回 0，但 NPI 仍能枚举 top。该 warning 本身不阻止 PASS；继续核对报告中所有目标证据。CRG trace 若受 partial KDB 影响应另见 `CRG_TRACE_UNAVAILABLE`，不能用 partial notice 忽略硬错误。
- `error[NPI_LOAD]` / 退出 11 表示 load 返回 0 且没有任何 top 可查询。返回第 7 节重建 KDB，并确认 `-top` 与 Excel 层次根一致。
- 执行 `ldd "$COLLECTOR" | grep -E 'libNPI|libnpiL1'`，确认两个运行时库、`VERDI_HOME`、PATH 中的 Verdi 与生成 KDB 的版本/平台一致。
- 直接运行 collector 时分别保存 stdout/stderr，并查看当前工作目录下 `rs_npi_collectorLog/compiler.log`。Python runner 在失败时会同时保留两个输出流的首尾诊断。

### 14.8 正例出现 `POSITION_NOT_FOUND`

- 确认 KDB 的顶层模块是 `top`。
- 示例 Excel 应填写 `position=tile_core`；确认配置 `position_mappings.tile_core=top.u_tile`，report 中应同时出现 `position_alias=tile_core` 和 `position=top.u_tile`。
- 若 `position_alias` 为空，说明 Excel 值未命中映射而被按完整路径直通；检查简写大小写和配置文件是否为当前 GUI/CLI 实际加载的文件。
- 确认解析后的 `top.u_tile` 与展开后的完整 NPI 层次一致。
- 确认没有拿到其他工程或旧版本 RTL 的 KDB。

### 14.9 `CLK_PORT_MISSING` / `RST_PORT_MISSING` 或 CRG 值不同

- 先查看 report v4 的 `module_rule.clk_port/rst_port`，确认规则键精确匹配 `RS_module`。未知模块默认使用 `clk/rst_n`；旧显式规则缺键时继承 `rtl.clk_port/rst_port`。
- 查看 JSON inventory 中对应实例的 `ports`。当前 collector 应包含全部 formal ports；若是升级前生成、只含旧全局 clk/rst 的 offline inventory，请用当前 collector 重新在线采集。
- partial KDB 中 Language Model 端口遍历为空时，collector 应由 NPI L1 `npi_mod_inst_get_port` fallback 补齐；若仍为空，核对 `libnpiL1.so`、实例完整路径和 collector 日志。
- clk/rst 由 checker 独立判定。模块存在且连接了规则指定的 clk、但没有规则指定的 rst formal port 时，行必须 FAIL 且 finding 只能是 `RST_PORT_MISSING`；若同时看到 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`，先核对 inventory 中 clk 的 formal port/connection 证据，并按回归缺陷处理。当前没有跳过 rst 检查的开关。
- `CRG_SOURCE_NOT_FOUND`：v3 trace 完整但没有模块完整 hierarchy 精确命中；核对 alias 映射和 `clock_trace.modules/path`。
- `CRG_TRACE_DEPTH_LIMIT`：目标在本次最大模块跳数之外；确认后提高 `rtl.crg_trace_max_depth` 并重采。
- `CRG_TRACE_UNAVAILABLE`：trace 缺失/未解析、formal 不一致，或 v2 legacy 证据未精确命中。三个 code 都是 warning，不改变行的硬 PASS/FAIL 或退出码。

### 14.10 RTL `RS_CRG_EN` 检查失败（兼容 code 为 `RS_CFG_EN_*`）

- 先检查本行 Excel/internal `RS_CFG_EN` 去除首尾空白后是否精确等于大写 `NA`。只有 `NA` 会跳过本行全部 `RS_CFG_EN_*`/RTL `RS_CRG_EN` 判定；`na`、`N/A` 等写法仍按下列模块规则检查。该特殊值不会跳过 module、实例、动态 `step`、clk 或 rst，因此这些 finding 仍需分别修复。
- `RS_CFG_EN_PARAMETER_MISSING`：非 `NA` 行的兼容规则键 `has_rs_cfg_en=true`，但实例证据没有 `RS_CRG_EN`；修正规则或 RTL/KDB。
- `RS_CFG_EN_PARAMETER_UNEXPECTED`：非 `NA` 行为 `has_rs_cfg_en=false`，但 RTL 实际仍有 `RS_CRG_EN`；除精确 `NA` 外，修改 Excel 标签不能规避该错误。
- `RS_CFG_EN_LABEL_MISMATCH`：仅适用于非 `NA` 且 `has_rs_cfg_en=true` 的行，表示 Excel/internal `RS_CFG_EN` 不是精确文本 `假门控`；`false` 时任意非 `NA` 字面内容均不产生该 finding。
- `RS_CFG_EN_VALUE_MISMATCH`：非 `NA` 行的实例 `RS_CRG_EN` 字符串不表示数值 `0`；检查实例 override 和本次 elaborated KDB，不能只改 Excel 标签。
- `RS_CFG_EN_VALUE_UNRESOLVED`：非 `NA` 行的 inventory 中 `parameters.RS_CRG_EN` 为 `null`；检查 collector/NPI 参数遍历和 KDB，不能把 `null` 当作参数不存在。
- schema v1 或实例缺少整个 `parameters` 对象属于输入契约错误，应重新使用当前 collector 生成 schema v3 inventory。旧 v2 仍可加载，但 CRG trace 能力有限。
- 同组多个实例时检查 finding 的实例路径；每个实例使用自己的 effective 值，任一失败都会使整行 FAIL。

### 14.11 模块规则或动态 step 检查失败

- 显式规则没有生效：规则键与 `RS_module` 大小写不完全一致。工具会采用默认 `has_rs_cfg_en=true`、`step_parameters=[]`、`clk_port=clk`、`rst_port=rst_n`；核对 report v4 的 `module_rule` 和 `clock_trace.clock_port`。
- `STEP_PARAMETER_MISSING`：规则中的 parameter 不存在于该实例；核对拼写、模块类型和 KDB。
- `STEP_PARAMETER_VALUE_UNRESOLVED`：parameter 为 `null`、X/Z/`?` 或非法值；该实例贡献未知。
- `STEP_CALCULATION_UNRESOLVED`：至少一个实例贡献未知；查看 report v4 的 `step_check.contributions`，先解决根因。
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

`scripts/test_vm_verdi_gui.sh` 自动执行：全量 Python 测试、NPI L0/L1 collector 构建、partial/clean elaborated KDB、可见 Verdi、普通在线 GUI、自定义 `clock_i/reset_ni`、有 clk/无 rst、两个 RS_CFG_EN 专项、CRG Source 映射、在线 CRG depth-limit 20 轮、离线 GUI 100 轮和 10,000 行。真实正例必须证明 `RS -> u_occ -> u_clk_mux -> {u_crg,u_aux_crg}` 的三层/分支 trace、`clk/rst_n` input 排除和 direct depth 1；深度专项必须证明 max depth 2 只产生 warning。每次 GUI smoke 都完成六根配置和三个数据库往返，并生成 inventory v3/report v4。NPI 唯一设计输入始终是 `--elab-db`；CRG_source 不会成为 collector positions。

成功输出至少应包含这些稳定 marker：

```text
custom module clk/rst formal-port rule evidence OK: clock_i/reset_ni
clk-present/rst-missing CLI evidence OK: ports=clk,d,q
finding isolation OK: RST_PORT_MISSING only; CLK_PORT_MISSING absent
GUI_SMOKE_PASS: ... mode=online case=positive ... schemas=report-v4/inventory-v3 ...
GUI_SMOKE_PASS: state=PASS ... warnings=警告 1 mode=online case=custom-port ... crg-source-check=warning finding-code=CRG_SOURCE_NOT_FOUND ...
GUI_SMOKE_PASS: state=PASS ... warnings=警告 6 mode=online case=crg-depth-limit iterations=20 ... crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6 ...
GUI_SMOKE_PASS: state=FAIL rows=行数 1 errors=错误 1 warnings=警告 0 mode=online case=clk-present-rst-missing iterations=20 ... clk-port-evidence=present rst-port-evidence=missing finding-codes=RST_PORT_MISSING ...
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=offline case=rs-cfg-dontcare iterations=20 ... has-rs-cfg-en=false label=dont-care rs-crg-en=absent findings=none parsed-rs-cfg-en=任意非标准文本 ...
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=offline case=rs-cfg-na iterations=20 ... rs-cfg-en=NA check=skipped rtl-rs-crg-en=1 findings=none ...
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=offline case=crg-source-mapping iterations=20 ... crg-source-map=core_clock_source->top.u_soc.u_crg_core gui-json-csv=alias+full crg-source-check=pass trace-depth=3 findings=none ...
```

VM 脚本对以下十二份 GUI 日志逐一执行硬断言；缺少任意一份日志中的固定 marker 都会使脚本非零退出：

```text
online_gui_positive.log
online_gui_crg_trace_depth_limit.log
online_gui_custom_port.log
online_gui_clk_present_rst_missing.log
partial_load_gui.log
online_gui_negative.log
offline_gui_default_rule.log
offline_gui_rs_cfg_dontcare.log
offline_gui_rs_cfg_na.log
offline_gui_crg_source_mapping.log
offline_gui_100_rounds.log
offline_gui_10000_rows.log
```

固定 marker 为：

```text
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules
```

十二份日志还必须包含 `module-rule-ports=preserved`、`crg-source-db=crud-complete`、`dirty-copy=export-preserved/import-cleared` 和 `schemas=report-v4/inventory-v3`。`offline_gui_crg_source_mapping.log` 必须包含 `crg-source-check=pass trace-depth=3 findings=none`；`online_gui_crg_trace_depth_limit.log` 必须包含 `case=crg-depth-limit` 和 `crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6`。后者仍必须是 2 passed rows、0 error，证明 CRG 截断仅为 warning。

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
| 非标准 NPI 布局 | `NPI_INC_DIR`、`NPI_LIB_DIR`、`NPI_L1_INC_DIR`、`NPI_L1_LIB_DIR` |
| GUI 压测规模 | `GUI_ONLINE_ITERATIONS`、`GUI_CRG_TRACE_DEPTH_ITERATIONS`、`GUI_CLK_WITHOUT_RST_ITERATIONS`、`GUI_RS_CFG_DONTCARE_ITERATIONS`、`GUI_RS_CFG_NA_ITERATIONS`、`GUI_CRG_SOURCE_MAPPING_ITERATIONS`、`GUI_STRESS_ITERATIONS`、`GUI_LOAD_ROWS`、`GUI_VISIBLE_SECONDS` |
| fresh 运行目录 | `VM_RUN_BASE`（绝对路径） |
| 站点环境/license | `VERDI_ENV_FILE`（绝对路径）、`VERDI_AUTO_LICENSE_IMPORT` |

`VERDI_WINDOW_REGEX` 只用于预筛 Verdi 相关窗口，不能决定就绪；`VERDI_READY_REGEX` 必须匹配包含 elaborated top 的窗口标题。普通在线正例默认 3 轮；CRG depth-limit、有 clk/无 rst、don't-care、精确 `NA` 和 CRG Source 映射五个专项默认各 20 轮，分别由对应 `GUI_*_ITERATIONS` 变量控制。六个轮次变量都必须是十进制正整数。

当前代码版本为 `0.11.0`。现有 [CRG_source 映射库与 VM GUI 压测验证记录](TEST_RESULTS_CRG_SOURCE_MAPPING_2026-07-26.md) 固定到 `0.10.0` 提交 `366c54114bc23f2878e0715357f7ab40f2ef7ea5`，是尚未启用 CRG 来源追踪时的十一日志历史基线，不能作为 inventory v3/report v4 和 depth-limit 专项的当前证据。

[RS_CFG_EN don't-care 与 VM GUI 压测验证记录](TEST_RESULTS_RS_CFG_DONTCARE_2026-07-26.md) 是上一版 `0.9.1` 的固定基线，对应提交 `37ccef3bbd15a1191e85664a00e165a296699d12`：GitHub fresh clone 第一次成功，CentOS/Python 3.8 的 240 项全部通过且无 skip；partial/clean KDB、mapped Verdi/Tk GUI、`has_rs_cfg_en=false` / Excel 任意文本专项 20 轮、clk 存在/rst 缺失专项 20 轮、普通在线 3 轮、离线 100 轮、10,000 行负载和九份 GUI 日志门禁全部通过。更早的 [clk 存在、rst 缺失 finding 隔离记录](TEST_RESULTS_CLK_PRESENT_RST_MISSING_2026-07-26.md) 固定到历史功能提交 `b3d701c2b95a4941fae398b4c2490c7f630127c3`。

下述 [逐模块 clk/rst 端口与 CRG 暂停判定验证记录](TEST_RESULTS_MODULE_PORTS_2026-07-26.md) 是本轮 finding 隔离修复之前的 `0.9.0` 历史基线，固定到功能提交 `2e90d6636accee3d5450a1feac64dc2f36edc608`。[RTL RS_CRG_EN 匹配与 VM GUI 压测验证记录](TEST_RESULTS_RS_CRG_EN_2026-07-26.md) 是 `0.8.1` 历史基线，固定到提交 `a9a26869b99d69d3826ffb0071e967cfedbf5c92`。`TEST_RESULTS_CONFIG_IO_2026-07-26.md` 是 `0.8.0` 历史基线，其余 `TEST_RESULTS_*.md` 是更早功能阶段的历史基线。
