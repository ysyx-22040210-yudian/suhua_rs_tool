# RTL 打拍例化检查工具测试指南

本文档给出从本地单元测试、`rscheck` 自带 Tkinter GUI 可见 smoke 和压力测试，到 CentOS/Verdi 在线 NPI 检查的完整验证流程。除非某一节明确说明可以独立执行，Linux 在线测试各节应在同一个 shell 中按顺序执行，以复用 `PROJECT_ROOT`、`TEST_ROOT`、`ELAB_DB` 等变量。

> **生产契约：在线检查的设计输入只能是 `--elab-db <Verdi elaborated KDB 目录>`。**
>
> `-- -f ...`、`-- -sv ...`、`-- -lib ...` 及其他任意 NPI/Verdi 参数透传均不受支持，并且必须返回退出码 `2`。`vericom` 生成的 `work.lib++` 是编译库，不是 elaborated KDB，也不能作为 `--elab-db` 输入。生产 RTL 应由已有编译流程生成 elab 库，本工具不负责在检查过程中重新编译 RTL。

## 1. 测试矩阵

| 编号 | 环境 | 测试目标 | 预期退出码 | 关键预期结果 |
|---|---|---|---:|---|
| L1 | Windows / Linux / macOS | 全量 Python 自动测试 | `0` | `Ran 89 tests`、`OK` |
| L2 | Windows + Microsoft Excel | 真实 XLSX 列乱序、额外列及列覆盖 | `0` | `VALID: 1 specification row(s)` |
| L3 | 通用本地环境 | 离线正例 inventory | `0` | 两个规格组均 PASS |
| L4 | 通用本地环境 | 离线反例 inventory | `1` | 1 行 FAIL，包含五类核心 finding |
| R0 | Linux + X11/Xwayland + Tk | GUI “验证 Excel”路径 | `0` | 20 轮均为 2 行 VALID、0 error、0 warning |
| R1 | Linux + X11/Xwayland + Tk | 工具自带 GUI 可见正例/反例和 100 轮稳定性 | `0` | 正例 100 轮均为 2 行 PASS、0 error、0 warning；反例显示 FAIL |
| R2 | Linux + X11/Xwayland + Tk | 工具自带 GUI 10,000 行负载 | `0` | 单轮 10,000 行、0 error、0 warning |
| R3 | Linux + Verdi/NPI + Tk | 工具自带 GUI 在线 KDB smoke | `0` | 3 轮均为 2 行 PASS、0 error、0 warning |
| R4 | 通用 Python 环境 | 取消发生在后台进程启动阶段 | `0` | 100 轮全部通过，不遗留子进程 |
| C1 | Linux + Verdi/NPI | C++ NPI collector 构建 | `0` | 生成可执行文件且 `libNPI.so` 可解析 |
| K1 | Linux + Verdi | `vericom` 编译示例 RTL | `0` | 生成 `work.lib++` |
| K2 | Linux + Verdi | `elabcom` 生成测试 KDB | `0` | 生成 `kdb.elab++` 目录 |
| V0 | Linux + X11/Xwayland | 无 Verdi/license 的两个 GUI 环境探测 | `0` | `rscheck GUI probe PASS` / `GUI probe PASS` |
| V1 | Linux + Verdi + X11/Xwayland | Verdi GUI 加载同一 KDB | `0` | 检测到新的 Verdi X11 窗口 |
| N1 | Linux + Verdi/NPI | 在线正例 | `0` | 2 行通过、0 error、0 warning |
| N2 | Linux + Verdi/NPI | 在线反例 | `1` | 1 行失败、9 error、5 类核心 finding |
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

### 3.1 全量 89 项测试

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

预期末尾输出：

```text
Ran 89 tests in ...

OK (skipped=22)
```

这些测试覆盖配置校验、XLSX/CSV/TSV 解析、列映射、实例分组、clk/rst、CRG、多源、报告、elab-only CLI 契约、GUI 命令构造与生命周期、包含忽略 SIGTERM 后代的进程组取消、跨桌面 GUI 会话发现，以及严格的 rscheck/Verdi launcher 参数。Windows 实测总共发现 89 项，其中 21 项仅适用于 Linux Bash/X11，另有 1 项仅适用于 POSIX 进程组，按预期 skipped，其余 67 项全部通过；macOS 预期只跳过 21 项 Linux-only 测试。CentOS 应运行全部 89 项并显示 `OK`，不能跳过 Linux launcher 回归。

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

窗口出现后手工缩放到允许的最小尺寸 `980x680`，检查以下内容均可见、文字未截断且控件不重叠：八个 1-based 列映射、在线/离线数据源选择、JSON/CSV 输出、“验证 Excel”“运行 RTL 检查”“取消”和三个页签。2026-07-24 已在 Windows 通过该最小尺寸截图检查；截图是本地验收证据，不提交仓库。

### 3.3 使用 Excel 生成“乱序列 + 额外列”XLSX

本节需要安装桌面版 Microsoft Excel。脚本生成一个真实 `.xlsx`，其中八个目标字段被打乱，并在首尾各加入一个无关列。

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
        "unused", "RS_inst", "clk", "Intf_type", "CRG_source",
        "step", "position", "rst", "RS_module", "notes"
    )
    $values = @(
        "ignore", "PIPE_X", "clk_i", "IN_IF", "my_crg",
        1, "top.u", "rst_n", "my_pipe", "extra column"
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
    "--column", "CRG_source=5"
)

python -m rscheck @ValidateArgs
if ($LASTEXITCODE -ne 0) {
    throw "Shuffled XLSX validation failed with exit code $LASTEXITCODE"
}
```

预期输出包含：

```text
VALID: 1 specification row(s)
row 2: top.u / PIPE_X module=my_pipe step=1
```

这证明列号可由用户定义，额外列不会参与解析，表头校验仍会按照覆盖后的列号执行。

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

预期 finding 至少包含 `STEP_MISMATCH`、`RS_MODULE_MISMATCH`、`CLK_CONNECTION_MISMATCH`、`RST_CONNECTION_MISMATCH` 和 `CRG_SOURCE_MISMATCH`。

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

当前预期是 `Ran 89 tests` 和 `OK`。Windows 实测有 21 项 Linux Bash/X11 测试及 1 项 POSIX 进程组测试 skipped；macOS 预期只跳过 21 项 Linux-only 测试；CentOS 应执行全部 89 项，GUI 会话和 launcher 回归不应 skipped。

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
GUI_SMOKE_PASS: rows=行数 2 errors=错误 0 warnings=警告 0 mode=validate case=positive iterations=20 window=mapped
```

2026-07-24 的 CentOS 可见窗口实测中，20 轮全部通过。

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
)
```

2026-07-24 的 CentOS 可见窗口实测中，100 轮全部通过，最终结果为 2 行 PASS、0 error、0 warning。

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
grep -F 'GUI_LOAD wall=' "$LOAD_GUI_LOG"
)
```

CentOS 最终测量的墙钟时间为 1.98–2.08 秒，最大 RSS 为 150060–150148 KiB，结果均为 10,000 行、0 error、0 warning。该区间用于记录已验证基线，不应作为不同 CPU、存储或桌面环境上的硬性性能门槛。若缺少 `/usr/bin/time`，先安装发行版的 `time` 包。

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

`--elab-db` 不强制目录名必须以 `.elab++` 结尾，因为 `elabcom -elab <path>` 允许自定义名称；但该路径必须存在且必须是目录。真正的 KDB 有效性由 `npi_load_design -elab <path>` 判定。

## 8. 在线正例

命令中没有 `--` 尾部参数，也没有 `-f`、`-sv` 或 `-lib`。唯一设计输入是 `$ELAB_DB`。

```bash
cd "$PROJECT_ROOT"

POS_INVENTORY="$TEST_ROOT/positive_inventory.json"
POS_REPORT="$TEST_ROOT/positive_report.json"
POS_CSV="$TEST_ROOT/positive_report.csv"

set +e
python3 -m rscheck check \
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
[PASS] row 2 OUT_IF | top.u_tile / AAAA_BBB (2/2 instances)
[PASS] row 3 CTRL_IF | top.u_tile / CTRL_RS (1/1 instances)
```

### 8.1 正例 JSON 摘要断言

```bash
python3 - "$POS_REPORT" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as stream:
    report = json.load(stream)

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
print("positive summary OK:", actual)
PY
```

预期输出 `positive summary OK`，Python 返回 `0`。

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

成功标准是 `wait` 返回 `0`；`xwininfo` 对 smoke 自己输出的 client `window_id` 显示 `IsViewable`、标题 wrapper 和有效 Width/Height；最后一行 `GUI_SMOKE_PASS` 带 `window=mapped`、`window_id=0x...` 和 `contract=elab-only`。smoke 还直接读取“运行日志”，要求实际命令包含 `--collector` 和 `--elab-db`，并拒绝 inventory、filelist、top 和 passthrough 选项。3 轮中的每一轮都使用真实 collector 加载同一个 elaborated KDB；最终 GUI 结果页应显示 `PASS`、行数 2、通过 2、失败 0、错误 0、警告 0。2026-07-24 的 CentOS/Verdi 实测满足该结果。在线 smoke 创建的报告和临时 inventory 位于系统临时目录，脚本退出后自动清理。

## 9. 在线反例

反例复用同一个 `$ELAB_DB`，只把 Excel/CSV 规格替换为 `tests/fixtures/specs_negative.csv`。该规格故意写错拍数、模块名、clk、rst 和 CRG source。

```bash
cd "$PROJECT_ROOT"

NEG_REPORT="$TEST_ROOT/negative_report.json"
NEG_LOG="$TEST_ROOT/negative_console.log"

set +e
python3 -m rscheck check \
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
- 控制台首行为 `RESULT: FAIL | rows=1 errors=9 warnings=0`。
- finding 至少包含以下五类：
  - `STEP_MISMATCH`
  - `RS_MODULE_MISMATCH`
  - `CLK_CONNECTION_MISMATCH`
  - `RST_CONNECTION_MISMATCH`
  - `CRG_SOURCE_MISMATCH`

### 9.1 反例 JSON 摘要和 finding 断言

```bash
python3 - "$NEG_REPORT" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as stream:
    report = json.load(stream)

expected_summary = {
    "passed": False,
    "rows": 1,
    "passed_rows": 0,
    "failed_rows": 1,
    "errors": 9,
    "warnings": 0,
}
actual_summary = report["summary"]
if actual_summary != expected_summary:
    raise SystemExit("unexpected negative summary: {!r}".format(actual_summary))

expected_codes = {
    "STEP_MISMATCH",
    "RS_MODULE_MISMATCH",
    "CLK_CONNECTION_MISMATCH",
    "RST_CONNECTION_MISMATCH",
    "CRG_SOURCE_MISMATCH",
}
actual_codes = {
    finding["code"]
    for row in report["rows"]
    for finding in row["findings"]
}
missing = expected_codes - actual_codes
if missing:
    raise SystemExit("missing negative findings: {!r}".format(sorted(missing)))
print("negative summary/findings OK:", actual_summary, sorted(actual_codes))
PY
```

## 10. 安全门禁：旧 filelist passthrough 必须返回 2

下面命令故意使用已经删除的旧接口。即使同时提供合法的 `$ELAB_DB`，`-- -f ...` 也必须在 argparse 阶段被拒绝，collector 不应启动。

```bash
cd "$PROJECT_ROOT"

LEGACY_LOG="$TEST_ROOT/legacy_filelist.log"
set +e
python3 -m rscheck check \
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
python3 -m rscheck check \
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
python3 -m rscheck check \
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

### 14.1 本地测试数量不是 89

- 确认位于正确仓库根目录。
- 执行 `python -m unittest discover -v`，不要只运行单个测试文件。
- 检查 Python 是否为 3.8 或更高版本。
- Windows 和 macOS 允许 Linux Bash/X11 测试 skipped；Linux 上应确认这些测试实际运行。
- 若仓库后续合法增加测试，测试数可能增长；此时应核对新增测试名称，而不是强行保持 89。

### 14.2 `header validation failed`

- 检查 `--sheet`、`header_row` 和 `data_start_row`。
- 检查八个 `--column FIELD=INDEX` 是否全部为 1-based 正整数且互不重复。
- 确认映射后的表头精确为 `Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`。

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

### 14.7 `error[NPI_LOAD]` / `npi_load_design failed`

- 当前 Python runner 会在 collector 启动前拒绝 `work.lib++` 及其符号链接别名；若看到对应错误，改传真正的 elaborated KDB。
- 对真正进入 `npi_load_design` 后的失败，检查 KDB 是否损坏、未完成 elaboration，或与当前 Verdi/NPI 版本不兼容。
- 返回第 7 节重新执行 `elabcom -top <top> -elab <path>`。
- 确认生成 KDB 时使用的 Verdi 版本与运行 collector 时兼容。
- 确认 `-top` 与 Excel 中 `position` 的层次根一致。

### 14.8 正例出现 `POSITION_NOT_FOUND`

- 确认 KDB 的顶层模块是 `top`。
- 确认规格中的 `position=top.u_tile` 与展开后的完整 NPI 层次一致。
- 确认没有拿到其他工程或旧版本 RTL 的 KDB。

### 14.9 正例出现 `NPI_UNRESOLVED`、`CRG_SOURCE_UNRESOLVED` 或多驱动

- 查看 JSON inventory 中对应实例的 `ports` 和 `clk_sources`。
- collector 的 traversal/driver warning 会 fail-closed，不应通过忽略 warning 来获得 PASS。
- 检查 clock gate/buffer 后实际被追踪到的第一个模块是否与 `CRG_source` 一致。

### 14.10 反例返回 2，而不是 1

退出码 `2` 说明检查尚未进入 RTL 差异判定，通常是 KDB、collector、动态库、license、Excel 或配置错误。先解决日志中的基础设施错误，再验证反例的五类 finding。

### 14.11 旧 `-- -f` 命令没有返回 2

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

`scripts/test_vm_verdi_gui.sh` 自动执行：89 项 Python 测试、collector 构建和动态库检查、示例 `vericom`/`elabcom`、`verdi -elab <kdb.elab++>` GUI 窗口检测、同一 KDB 的在线正例及 JSON summary 断言。`work.lib++` 仅在准备阶段供 `elabcom` 使用；NPI 检查的唯一设计输入始终是 `--elab-db`。

在已设置 Verdi 和 license 环境的图形 shell 中执行：

```bash
cd "$HOME/suhua_rs_tool"
: "${LM_LICENSE_FILE:?set LM_LICENSE_FILE in the current shell}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-$LM_LICENSE_FILE}"
bash scripts/launch_verdi_gui.sh --probe-only
bash scripts/test_vm_verdi_gui.sh
```

可移植覆盖项：

| 分类 | 环境变量 |
|---|---|
| Verdi 定位 | `VERDI_BIN`、`VERDI_HOME`、`NOVAS_INST_DIR` |
| GUI 选择 | `GUI_USER`、`GUI_DISPLAY`、`GUI_XAUTHORITY`、`GUI_SESSION_PID` |
| 测试工具链 | `PYTHON_BIN`、`CXX` |
| 非标准 NPI 布局 | `NPI_INC_DIR`、`NPI_LIB_DIR` |

端到端脚本构建时将 `NPI_INC_DIR`/`NPI_LIB_DIR` 传给 Makefile，并在在线检查中显式使用 `--npi-lib-dir "$NPI_LIB_DIR"`。完整默认值、SSH/VNC/XRDP 命令、成功输出和故障排查见 [Verdi GUI 端到端复现指南](VM_GUI_TEST.md)。生成的 KDB、日志、collector 和报告位于 `.gitignore` 排除的目录，不应提交仓库。

2026-07-24 实测：CentOS 89 项全量测试通过；清空 `DISPLAY`、`XAUTHORITY`、`DBUS_SESSION_BUS_ADDRESS` 和 `XDG_RUNTIME_DIR` 后自动发现 `DISPLAY=:0`；“验证 Excel”20 轮均为 2 行 VALID、0 error、0 warning；可见离线正例 100 轮均为 2 行 PASS、0 error、0 warning；10,000 行 GUI 负载最终测量为 1.98–2.08 秒、最大 RSS 150060–150148 KiB、0 error、0 warning；取消启动竞态 100 轮约 15.3 秒；真实 elaborated KDB 在线 GUI smoke 3 轮均为 2 行 PASS、0 error、0 warning。Windows 实测发现 89 项，其中 21 项 Linux Bash/X11 测试和 1 项 POSIX 进程组测试按预期 skipped，其余 67 项通过；macOS 预期只跳过 21 项 Linux-only 测试。工具自带 GUI 在 Windows 最小窗口 `980x680` 完成截图布局验收，截图不纳入仓库。
