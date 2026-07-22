# RTL 打拍例化检查工具测试指南

本文档给出从本地单元测试到 CentOS/Verdi 在线 NPI 检查的完整验证流程。除非某一节明确说明可以独立执行，Linux 在线测试各节应在同一个 shell 中按顺序执行，以复用 `PROJECT_ROOT`、`TEST_ROOT`、`ELAB_DB` 等变量。

> **生产契约：在线检查的设计输入只能是 `--elab-db <Verdi elaborated KDB 目录>`。**
>
> `-- -f ...`、`-- -sv ...`、`-- -lib ...` 及其他任意 NPI/Verdi 参数透传均不受支持，并且必须返回退出码 `2`。`vericom` 生成的 `work.lib++` 是编译库，不是 elaborated KDB，也不能作为 `--elab-db` 输入。生产 RTL 应由已有编译流程生成 elab 库，本工具不负责在检查过程中重新编译 RTL。

## 1. 测试矩阵

| 编号 | 环境 | 测试目标 | 预期退出码 | 关键预期结果 |
|---|---|---|---:|---|
| L1 | Windows / Linux / macOS | 全量 Python 自动测试 | `0` | `Ran 47 tests`、`OK` |
| L2 | Windows + Microsoft Excel | 真实 XLSX 列乱序、额外列及列覆盖 | `0` | `VALID: 1 specification row(s)` |
| L3 | 通用本地环境 | 离线正例 inventory | `0` | 两个规格组均 PASS |
| C1 | CentOS + Verdi/NPI | C++ NPI collector 构建 | `0` | 生成可执行文件且 `libNPI.so` 可解析 |
| K1 | CentOS + Verdi | `vericom` 编译示例 RTL | `0` | 生成 `work.lib++` |
| K2 | CentOS + Verdi | `elabcom` 生成测试 KDB | `0` | 生成 `kdb.elab++` 目录 |
| V1 | CentOS + Verdi + X11 | Verdi GUI 加载同一 KDB | `0` | 检测到 Verdi X11 主窗口并显示 `top` |
| N1 | CentOS + Verdi/NPI | 在线正例 | `0` | 2 行通过、0 error、0 warning |
| N2 | CentOS + Verdi/NPI | 在线反例 | `1` | 1 行失败、9 error、5 类核心 finding |
| G1 | 任意 Python 环境 | 旧 filelist passthrough 防回归 | `2` | argparse 报 `unrecognized arguments` |
| G2 | CentOS + Verdi/NPI | 把 `work.lib++` 错当 elab 输入 | `2` | collector/NPI 加载失败，不生成 PASS 报告 |
| G3 | 任意 Python 环境 | `--inventory` 与 `--elab-db` 冲突 | `2` | 报 `--elab-db requires --collector` |

退出码定义：

- `0`：检查通过；允许存在不影响判定的 warning。
- `1`：RTL 与规格不一致，或检查结果包含硬错误。
- `2`：命令行、配置、Excel、inventory、collector、Verdi/NPI 环境或设计加载失败。

## 2. 本地测试前提

- Python 3.8 或更高版本。
- 当前目录是仓库根目录，能够看到 `pyproject.toml`、`rscheck/`、`tests/`。
- Python 自动测试和离线检查不需要 Verdi、NPI license 或第三方 Python 包。
- 建议设置 `PYTHONDONTWRITEBYTECODE=1`，避免测试产生 `__pycache__`。

## 3. Windows 本地测试

### 3.1 全量 47 项测试

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
Ran 47 tests in ...

OK
```

这些测试覆盖配置校验、XLSX/CSV/TSV 解析、列映射、实例分组、clk/rst、CRG、多源、报告、elab-only CLI 契约及 runner 固定命令构造。

### 3.2 使用 Excel 生成“乱序列 + 额外列”XLSX

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

### 3.3 本地离线正例

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

预期同样是 `Ran 47 tests` 和 `OK`。

可单独运行 elab-only 契约测试：

```bash
cd "$PROJECT_ROOT"
python3 -m unittest tests.test_npi_runner tests.test_cli -v
```

预期 15 项全部通过。

## 5. CentOS/Verdi 测试环境

以下变量全部是占位符。不要把 SSH 密码、真实主机/IP、license 地址或其他凭据写入仓库、报告或命令日志。

```bash
# 可选：先登录测试机。主机名和用户均使用环境实际值替换。
ssh <REMOTE_USER>@<CENTOS_HOST>

export PROJECT_ROOT="<PROJECT_ROOT>"
export VERDI_HOME="<VERDI_HOME>"
export NPI_PLATFORM="LINUX64"
export NOVAS_INST_DIR="$VERDI_HOME"
export LM_LICENSE_FILE="<LICENSE_PORT>@<LICENSE_HOST>"
export SNPSLMD_LICENSE_FILE="<LICENSE_PORT>@<LICENSE_HOST>"

# 若系统有多个 Python/GCC 工具链，在这里 source 对应的 enable 脚本。
# source <PYTHON_TOOLCHAIN_ENABLE>
# source <GCC_TOOLCHAIN_ENABLE>

cd "$PROJECT_ROOT"
python3 --version
g++ --version
test -x "$VERDI_HOME/bin/vericom"
test -x "$VERDI_HOME/bin/elabcom"
test -f "$VERDI_HOME/share/NPI/inc/npi.h"
test -f "$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM/libNPI.so"
```

若任一 `test` 返回非零，先修复安装路径或环境变量，不要继续构建。

## 6. 构建 C++ NPI collector

```bash
cd "$PROJECT_ROOT"

make -C npi \
  VERDI_HOME="$VERDI_HOME" \
  NPI_PLATFORM="$NPI_PLATFORM" \
  CXX=g++

COLLECTOR="$PROJECT_ROOT/npi/build/rs_npi_collector"
test -x "$COLLECTOR"
file "$COLLECTOR"
ldd "$COLLECTOR" | grep 'libNPI.so'
```

预期：

- `make` 返回 `0`。
- `$COLLECTOR` 是当前 CentOS 架构的可执行文件。
- `ldd` 中 `libNPI.so` 指向 `$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM/`，且不是 `not found`。

若 NPI 库不在标准目录，运行在线检查时可显式增加：

```text
--npi-lib-dir <DIRECTORY_CONTAINING_libNPI.so>
```

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

Python runner 会确认路径存在且为目录，因此 `work.lib++` 会进入 collector；随后 `npi_load_design -elab <work.lib++>` 必须失败。这验证工具不会把编译库误当 elaborated KDB。

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
  --npi-timeout 180 \
  >"$WORKLIB_LOG" 2>&1
WORKLIB_RC=$?
set -e

if [ "$WORKLIB_RC" -ne 2 ]; then
  echo "work.lib++ input returned $WORKLIB_RC, expected 2" >&2
  cat "$WORKLIB_LOG" >&2
  exit 1
fi
grep -Eq 'NPI_LOAD|npi_load_design|collector exited' "$WORKLIB_LOG"
cat "$WORKLIB_LOG"
```

预期：

- Python CLI 返回 `2`，表示设计加载失败。
- 日志包含 `NPI_LOAD`、`npi_load_design failed` 或 collector 非零退出信息。
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

### 14.1 本地测试数量不是 47

- 确认位于正确仓库根目录。
- 执行 `python -m unittest discover -v`，不要只运行单个测试文件。
- 检查 Python 是否为 3.8 或更高版本。
- 若仓库后续合法增加测试，测试数可能增长；此时应核对新增测试名称，而不是强行保持 47。

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
- 必要时使用 `--npi-lib-dir "$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM"`。
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

- 最常见原因是错误地传入 `work.lib++`。
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

## 15. 一键复现 Verdi GUI 正向链路

仓库提供 `scripts/test_vm_verdi_gui.sh`，用于在已登录 GNOME/X11 桌面且安装 Verdi/NPI 的 Linux 设备上自动执行：

1. 全量 Python 测试；
2. NPI collector 构建和动态库检查；
3. 在全新目录运行 `vericom` 和 `elabcom`；
4. 通过 `verdi -elab <kdb.elab++>` 启动 GUI并检测 X11 窗口；
5. 检查工具只用 `--elab-db <同一kdb.elab++>` 运行在线正例；
6. 断言 JSON summary 为 2 行通过、0 error、0 warning。

最小运行命令：

```bash
cd /path/to/suhua_rs_tool
export VERDI_HOME=/path/to/verdi
export LM_LICENSE_FILE=<port>@<license-host>
export SNPSLMD_LICENSE_FILE="$LM_LICENSE_FILE"
export GUI_USER=<logged-in-desktop-user>
bash scripts/test_vm_verdi_gui.sh
```

脚本不会把 RTL 或 filelist 传给 NPI 检查。`vericom` 仅在准备阶段为仓库示例生成 `work.lib++`，随后 `elabcom` 生成真正的 elaborated KDB；检查命令的设计输入只有 `--elab-db`。

完整的设备变量、预期输出、人工 hierarchy 检查点和 2026-07-23 实测记录见 [Verdi GUI 端到端复现指南](VM_GUI_TEST.md)。生成的 KDB、日志、collector 和报告位于被 `.gitignore` 排除的目录，不应提交仓库。
