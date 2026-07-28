# kdebug RTL inventory 后端

本文说明 `suhua_rs_tool` 如何通过二次开发后的
[`kverif`](https://github.com/ysyx-22040210-yudian/kverif) `kdebug` 采集
inventory v3，而不让 rscheck 的 Python 代码直接导入、链接或调用 NPI 函数。

## 1. 结论和适用版本

原版 kdebug **不能直接替换** `rs_npi_collector`，原因有两点：

1. 原版没有 `rscheck.inventory` JSON action，不能一次返回 positions、实例、全部
   formal ports、effective parameters 和有界 `clock_trace`；
2. kdebug 自身的命令行不是 rscheck collector 的
   `--positions/--output/--trace-rules/...` 接口，不能直接填入 GUI 的 Collector 字段。

必须同时使用以下两个二次开发分支：

| 仓库 | 分支 | 职责 |
|---|---|---|
| `ysyx-22040210-yudian/kverif` | `codex/rscheck-elab-inventory` | 提供 `kdebug.v1` 的 `rscheck.inventory` action 和配套 engine |
| `ysyx-22040210-yudian/suhua_rs_tool` | `codex/kdebug-npi-backend` | 提供兼容 collector CLI 的无 NPI Python adapter、GUI、checker 和压测脚本 |

需要固定提交复现时，可分别把两个分支切换到 `<KVERIF_SHA>` 和
`<RSCHECK_SHA>`；不要把一个仓库的 SHA 用到另一个仓库。

## 2. 架构边界

```text
rscheck GUI / CLI
        |
        | existing --collector protocol
        v
scripts/rs_kdebug_collector.py
        |
        | one JSON request: kdebug.v1 / rscheck.inventory
        v
kverif/kdebug/kdebug + kdebug/libexec/*
        |
        | one Verdi load/session for all requested positions and rules
        v
Verdi elaborated KDB directory -> inventory v3 -> rscheck checker/report
```

`rscheck/kdebug_collector.py` 和 `scripts/rs_kdebug_collector.py` 不包含 NPI
header、NPI 动态库、`ctypes` 或 `cffi`。它们只通过 `subprocess` 向独立 kdebug
进程发送 JSON。NPI/Verdi 相关实现和兼容性留在 kdebug engine 边界内。

**rscheck 的执行边界只有 `$KDEBUG_BIN --json -`。rscheck 不执行、不 source
`kdebug_npi.tcl` 或 `rscheck_inventory.tcl`，也不会执行 kverif 的 C++ 源文件。**
这两个 Tcl 文件由编译后的 kdebug ELF 在启动私有 engine 后间接交给 Verdi 使用，
属于 kdebug 构建产物，不是 rscheck collector 入口。

这项隔离不等于移除 Verdi 依赖。在线检查仍然需要：

- Linux 上合法安装且与 KDB 兼容的 Verdi；
- 站点批准的可用 license 环境；
- `elabcom` 已经生成的 elaborated KDB 目录；
- 完整保留同一次 kdebug 构建的 frontend、engine、Python/Tcl 文件。

旧的 `npi/rs_npi_collector.cpp` 只保留为历史行为和 A/B 回归 baseline，不再是
GUI 新会话的默认后端。不要用 baseline 的通过结果代替 kdebug 后端签核。

`kverif` 源码使用 MIT License，二次分发源码或构建包时必须保留其 `LICENSE`。
不要把 Synopsys 的共享库、头文件、安装目录 Tcl、license 信息、生产 KDB 或项目
RTL 提交到 GitHub；目标设备使用其本机合法安装的 Verdi。可搬运的最小 kdebug
构建树包括 frontend、`libexec/kdebug-engine`、`libexec/tcl_engine/*` 和 `LICENSE`。

## 3. 固定 JSON 协议

adapter 固定启动：

```text
$KDEBUG_BIN --json -
```

stdin 只写入一个 UTF-8 JSON object。结构为：

```json
{
  "api_version": "kdebug.v1",
  "action": "rscheck.inventory",
  "target": {
    "elab_db": "/absolute/path/to/TB.elab++"
  },
  "args": {
    "positions": ["top.u_tile"],
    "trace_rules": {
      "rs_pipe": "clk",
      "rs_custom": "clock_i"
    },
    "trace_max_depth": 16,
    "clk_port": "clk",
    "rst_port": "rst_n"
  },
  "output": {
    "format": "json"
  }
}
```

所有 positions 和 module rules 必须在同一个 action 中提交。kdebug 必须只启动一次
Verdi load/session，不能按 Excel 行、position 或实例重复加载 KDB。
上层设置 `--npi-timeout` 时，runner 通过受控环境变量把秒数交给 adapter；adapter
会在请求中增加略短的 `limits.timeout_ms`，让 kdebug 先终止 Verdi 并清理临时目录，
避免只结束 adapter 后留下后台进程。

成功响应必须为一个 JSON object，inventory 位于 `data.inventory`：

```json
{
  "api_version": "kdebug.v1",
  "action": "rscheck.inventory",
  "ok": true,
  "data": {
    "inventory": {
      "schema_version": 3,
      "positions": {},
      "warnings": [],
      "notices": []
    }
  }
}
```

失败响应使用 `ok=false` 和 `error.code/error.message`。adapter 在写入目标文件之前
使用 rscheck 现有 loader 完整校验 schema v3，并通过同目录临时文件原子替换；无效
response、schema v2 或缺字段不会覆盖已有 inventory。

设置绝对路径 `RSCHECK_KDEBUG_HOME` 后，adapter 只向 kdebug 子进程导出同值
`KDEBUG_HOME`，把 work、registry 和日志隔离到该目录；不会修改运行用户的 `HOME`。

## 4. 设计输入边界

唯一设计输入是 `--elab-db <目录>`，该目录必须是 `elabcom -elab` 生成的 Verdi
elaborated KDB，通常命名为 `TB.elab++` 或 `kdb.elab++`。

下列输入不受支持：

- `vericom` 生成的 `work.lib++` 编译库及其符号链接别名；
- filelist；
- `.v`、`.sv`、`.vhd` 等 RTL 文件；
- `-f`、`-sv`、`-lib`、`-top` 或 `--` 后的任意 Verdi 参数透传；
- 普通文件、缺失路径或非 elaborated 数据库目录。

生产工程即使使用 filelist，也只能在 rscheck 之外的正式编译/elaboration 流程中使用。
rscheck 和 kdebug action 消费的是最终 elaborated KDB，不负责重新编译 RTL。

## 5. 双仓库克隆和构建

下面命令不包含主机地址、密码、token 或 license 值。在一个已经能运行 Verdi 的
Linux shell 中设置实际 `VERDI_HOME` 后执行：

只需构建 kdebug 时，推荐直接使用本仓库脚本。它固定 kverif 仓库、分支和提交，完成
clone、编译、kverif `test-fast`、ELF/NPI 动态依赖检查、运行包展开复检，并生成包含
`kdebug + libexec + LICENSE + BUILD_INFO + SHA256SUMS` 的运行包：

```bash
set -euo pipefail
cd /absolute/path/to/suhua_rs_tool
OUTPUT_BASE="$HOME/rscheck-kdebug-builds" \
bash scripts/build_kdebug_from_kverif.sh
```

已有真实 elaborated KDB 时，建议在构建阶段直接验证打包后的 ELF、私有 engine 和
Verdi KDB 链路：

```bash
OUTPUT_BASE="$HOME/rscheck-kdebug-builds" \
bash scripts/build_kdebug_from_kverif.sh \
  --smoke-elab-db /absolute/path/to/kdb.elab++ \
  --smoke-position top.u_tile
```

脚本输出中的 `RUNTIME_KDEBUG_BIN` 才是目标设备应设置的 `KDEBUG_BIN`。运行包不能只留下
ELF，必须整体携带相邻 `libexec`。下面是需要同时准备两个源码 checkout 时的手工流程：

```bash
set -euo pipefail

: "${VERDI_HOME:?export VERDI_HOME to the approved Verdi installation}"
export WORK_ROOT="${WORK_ROOT:-$HOME/rscheck_kdebug_repro}"
export PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "$WORK_ROOT"
git clone \
  --branch codex/rscheck-elab-inventory \
  --single-branch \
  https://github.com/ysyx-22040210-yudian/kverif.git \
  "$WORK_ROOT/kverif"
git clone \
  --branch codex/kdebug-npi-backend \
  --single-branch \
  https://github.com/ysyx-22040210-yudian/suhua_rs_tool.git \
  "$WORK_ROOT/suhua_rs_tool"

export KVERIF_HOME="$WORK_ROOT/kverif"
export RSCHECK_ROOT="$WORK_ROOT/suhua_rs_tool"
export PYTHON="$(command -v "$PYTHON_BIN")"
export PATH="$VERDI_HOME/bin:$KVERIF_HOME/tools:$PATH"

(cd "$KVERIF_HOME" && \
  git checkout --detach 2b43b799c8f7f8586a9e6c2128335e74d971e633 && \
  test "$(git rev-parse HEAD)" = 2b43b799c8f7f8586a9e6c2128335e74d971e633)

make -C "$KVERIF_HOME/kdebug" clean
make -C "$KVERIF_HOME/kdebug" -j2 all
PYTHON="$PYTHON_BIN" make -C "$KVERIF_HOME/kdebug" test-fast

export KDEBUG_BIN="$KVERIF_HOME/kdebug/kdebug"
test -x "$KDEBUG_BIN"
test -x "$KVERIF_HOME/kdebug/libexec/kdebug-engine"
test -f "$KVERIF_HOME/kdebug/libexec/tcl_engine/kdebug_engine.py"
test -f "$KVERIF_HOME/kdebug/libexec/tcl_engine/kdebug_npi.tcl"
test -f "$KVERIF_HOME/kdebug/libexec/tcl_engine/rscheck_inventory.tcl"
test -x "$RSCHECK_ROOT/scripts/rs_kdebug_collector.py"

"$PYTHON_BIN" -m pip install -e "$RSCHECK_ROOT"
(cd "$KVERIF_HOME" && git rev-parse HEAD)
(cd "$RSCHECK_ROOT" && git rev-parse HEAD)
```

`KDEBUG_BIN` 必须是绝对路径，并指向 `make -C kdebug all` 构建出的 Linux ELF
`$KVERIF_HOME/kdebug/kdebug`。不能填写 `.c/.cc/.cpp/.cxx` 原始 C++ 文件，也不接受
shell/Python/Tcl 包装脚本；也不能填写 `tools/kdebug`。adapter 不再从 PATH 猜测
kdebug，必须显式设置 `KDEBUG_BIN`，并在加载 KDB 前检查常规文件、执行权限和 ELF 文件头，
失败时返回 `error[KDEBUG_EXEC]`。`tools/kdebug` 可用于人工调用，但 rscheck 与 VM
压测统一使用 ELF 路径，并依靠相邻目录定位配套 engine。不要只复制一个 kdebug
文件到其他目录；必须保留同一构建树的 `kdebug/libexec`。

`kdebug_npi.tcl` 是编译后 kdebug 的私有运行时资源，不是 rscheck 入口。若要求整个
kdebug 运行时彻底不包含 Tcl，重新编译现有工程并不能做到，必须重写 kverif 的 Verdi
访问后端；若直接改成 C++ NPI，则会违背本分支隔离直接 NPI 函数调用的目标。

## 6. 生成示例 elaborated KDB

生产使用已有受控 KDB。为了验证双仓库，可用 rscheck 示例 RTL 生成独立 KDB：

```bash
set -euo pipefail
: "${VERDI_HOME:?VERDI_HOME is required}"
: "${RSCHECK_ROOT:?RSCHECK_ROOT is required}"

export RUN_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/rscheck_kdebug.XXXXXX")"
export ELAB_ROOT="$RUN_ROOT/example_elab"
export ELAB_DB="$ELAB_ROOT/kdb.elab++"
mkdir -p "$ELAB_ROOT"

(
  cd "$ELAB_ROOT"
  "$VERDI_HOME/bin/vericom" -sv "$RSCHECK_ROOT/examples/rtl/rs_example.sv"
  "$VERDI_HOME/bin/elabcom" -top top -elab "$ELAB_DB"
)

test -d "$ELAB_ROOT/work.lib++"
test -d "$ELAB_DB"
printf 'RUN_ROOT=%s\nELAB_DB=%s\n' "$RUN_ROOT" "$ELAB_DB"
```

`work.lib++` 只供同目录的 `elabcom` 使用。后续命令只能传 `$ELAB_DB`。

## 7. CLI 验证

### 7.1 直接验证 adapter 合同

```bash
set -euo pipefail
: "${KDEBUG_BIN:?KDEBUG_BIN is required}"
: "${RSCHECK_ROOT:?RSCHECK_ROOT is required}"
: "${ELAB_DB:?ELAB_DB is required}"

printf '%s\n' 'top.u_tile' >"$RUN_ROOT/positions.txt"
printf 'rs_pipe\tclk\nrs_custom\tclock_i\n' >"$RUN_ROOT/trace_rules.tsv"

"$PYTHON_BIN" "$RSCHECK_ROOT/scripts/rs_kdebug_collector.py" \
  --positions "$RUN_ROOT/positions.txt" \
  --output "$RUN_ROOT/adapter_inventory.json" \
  --trace-rules "$RUN_ROOT/trace_rules.tsv" \
  --trace-max-depth 16 \
  --clk-port clk \
  --rst-port rst_n \
  --elab-db "$ELAB_DB"

"$PYTHON_BIN" - "$RUN_ROOT/adapter_inventory.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    inventory = json.load(stream)
assert inventory["schema_version"] == 3
assert set(inventory["positions"]) == {"top.u_tile"}
print("adapter inventory v3 PASS")
PY
```

### 7.2 运行完整 rscheck CLI

```bash
set -euo pipefail
cd "$RSCHECK_ROOT"
mkdir -p "$RUN_ROOT/reports"

KDEBUG_BIN="$KDEBUG_BIN" "$PYTHON_BIN" -m rscheck check \
  --excel "$RSCHECK_ROOT/examples/specs.csv" \
  --config "$RSCHECK_ROOT/config/rscheck.example.json" \
  --collector "$RSCHECK_ROOT/scripts/rs_kdebug_collector.py" \
  --elab-db "$ELAB_DB" \
  --npi-timeout 180 \
  --crg-trace-max-depth 16 \
  --keep-inventory "$RUN_ROOT/reports/inventory.json" \
  --json-report "$RUN_ROOT/reports/report.json" \
  --csv-report "$RUN_ROOT/reports/report.csv"
```

不要把 `$KDEBUG_BIN` 直接传给 `--collector`。`--collector` 接收兼容 adapter；
adapter 再从环境变量 `KDEBUG_BIN` 定位 kdebug ELF。kdebug 后端通常不需要填写
`--npi-lib-dir`；该旧参数仅为需要显式运行库目录的兼容 collector 保留。

## 8. 启动 GUI

源码 checkout 中 GUI 新会话默认把 Collector 填为
`scripts/rs_kdebug_collector.py`；pip/wheel 安装会解析同环境的
`rs-kdebug-collector` console entry point。可以直接在 GUI 的 `kdebug ELF` 行浏览选择
编译后的绝对路径；也可以在启动前导出 `KDEBUG_BIN`，让新会话自动带入：

```bash
set -euo pipefail
cd "$RSCHECK_ROOT"

# 可选：export KDEBUG_BIN=/absolute/path/to/kdebug-runtime/kdebug
bash scripts/launch_rscheck_gui.sh --probe-only
bash scripts/launch_rscheck_gui.sh
```

在“检查配置”页保持默认 `RTL Collector Adapter`，分别选择编译后的 `kdebug ELF` 和
`$ELAB_DB`，再运行 RTL 检查。GUI 会校验 ELF 的绝对路径、执行权限和文件头，并只通过
子进程环境变量 `KDEBUG_BIN` 传给 adapter；不会把 ELF 误当作 collector，也不会把设备
路径写入配置导出。`运行库目录（可选）` 对 kdebug 后端通常留空。GUI 只需要有效
X11/Xwayland DISPLAY 和 Tkinter，不要求 GNOME 或 `gnome-session-binary`。

## 9. 完整 VM/GUI 压测

已有 clean/partial elaborated KDB 时，先运行可复现的 focused backend 压测：

```bash
set -euo pipefail
: "${KDEBUG_BIN:?KDEBUG_BIN is required}"
: "${CLEAN_ELAB_DB:?clean elab++ directory is required}"
: "${PARTIAL_ELAB_DB:?partial elab++ directory is required}"
cd "$RSCHECK_ROOT"

KDEBUG_BIN="$KDEBUG_BIN" \
CLEAN_ELAB_DB="$CLEAN_ELAB_DB" \
PARTIAL_ELAB_DB="$PARTIAL_ELAB_DB" \
PRESSURE_ITERATIONS=20 \
bash scripts/test_kdebug_backend_pressure.sh
```

该脚本只接收两个 `elab++` 目录，执行 raw JSON action、adapter clean/partial、20
轮真实 Verdi KDB 重载、执行中 frontend cancellation 和 1 秒强制超时，拒绝
inventory/临时目录/进程泄漏，并把
frontend、engine、Python/Tcl 和 adapter 哈希保存到唯一 `RUN_ROOT`。root 的非交互
shell 没有 license 时，可显式增加 `VERDI_LICENSE_USER=<已批准的桌面用户>`；脚本只
导入该用户登录环境中的 `LM_LICENSE_FILE`/`SNPSLMD_LICENSE_FILE`，不会打印值或导入
其他环境。

当前 checkout 已位于 VM 本机文件系统并且可信时，直接运行：

```bash
set -euo pipefail
: "${KDEBUG_BIN:?KDEBUG_BIN is required}"
cd "$RSCHECK_ROOT"

RSCHECK_COLLECTOR_BACKEND=kdebug \
KDEBUG_BIN="$KDEBUG_BIN" \
KVERIF_EXPECTED_COMMIT="${KVERIF_EXPECTED_COMMIT-}" \
KDEBUG_EXPECTED_SHA256="${KDEBUG_EXPECTED_SHA256-}" \
bash scripts/test_vm_verdi_gui.sh
```

该命令运行全量 Python 测试、partial/clean KDB、Verdi GUI、在线 GUI、多层 CRG
trace、深度上限 20 轮、clk/rst 隔离、门控专项、100 轮 GUI 稳定性和 10,000 行
负载。输出必须包含：

```text
Collector backend: kdebug JSON action rscheck.inventory
COLLECTOR_BACKEND=kdebug
KDEBUG_MANIFEST=.../kdebug_build_manifest.txt
```

要从 suhua bootstrap checkout 重新克隆并固定 kdebug 分支，执行：

```bash
set -euo pipefail
: "${KDEBUG_BIN:?KDEBUG_BIN is required}"
cd "$RSCHECK_ROOT"

RSCHECK_COLLECTOR_BACKEND=kdebug \
KDEBUG_BIN="$KDEBUG_BIN" \
KVERIF_EXPECTED_COMMIT=<KVERIF_SHA> \
KDEBUG_EXPECTED_SHA256=<KDEBUG_ELF_SHA256> \
bash scripts/test_vm_fresh_checkout.sh \
  --commit origin/codex/kdebug-npi-backend
```

分支推送后也可以把最后一项换成完整 `<RSCHECK_SHA>`。正式复现应同时把
`KVERIF_EXPECTED_COMMIT` 设为完整 `<KVERIF_SHA>`，把 `KDEBUG_EXPECTED_SHA256`
设为 `sha256sum "$KDEBUG_BIN"` 的第一列。fresh driver 保留 `RUN_ROOT`、`FULL_LOG`
和 `ARTIFACT_ROOT`，在 artifacts 中写入 `kdebug_build_manifest.txt` 和隔离的
`kdebug_home` 日志，不在日志中输出 license 值。kdebug 模式不要求旧 NPI
C/L1 headers、直接链接库、`make` 或 C++ compiler；这些只在显式 `npi` baseline 下检查。

## 10. Partial KDB 语义

kdebug 加载 KDB 时即使 Verdi 报 elaboration error，也不能只凭 compiler log 判失败：

- 若同一次 action 中至少一个 top 仍可查询，kdebug 返回 `ok=true`，继续 fail-closed
  采集，并在 `inventory.notices` 写入 `NPI_LOAD_PARTIAL`；
- adapter 把该 notice 以 `warning[NPI_LOAD_PARTIAL]` 写到 stderr，Python checker 将其
  作为非致命 warning；
- position、实例、formal ports、parameters 或 trace 证据仍按各自规则检查，partial
  notice 不会掩盖硬错误；
- 若没有任何可查询 top，kdebug 返回 action failure，adapter 不生成成功 inventory，
  rscheck CLI 返回环境/采集错误；
- trace 本身不完整时另报 `CRG_TRACE_UNAVAILABLE`，不能伪造 `complete`。

## 11. 旧 C++ collector baseline

需要 A/B 定位时才构建旧 collector：

```bash
cd "$RSCHECK_ROOT"
make -C npi VERDI_HOME="$VERDI_HOME" NPI_PLATFORM="${NPI_PLATFORM:-LINUX64}"
test -x npi/build/rs_npi_collector
```

baseline 压测必须显式声明，避免与 kdebug 结果混淆：

```bash
cd "$RSCHECK_ROOT"
RSCHECK_COLLECTOR_BACKEND=npi bash scripts/test_vm_verdi_gui.sh
```

baseline 直接链接 `libNPI.so/libnpiL1.so`，不满足“rscheck 后端开发不直接调用
NPI”的新架构目标。它只用于比较历史证据、定位版本差异和防止行为回归。

## 12. 常见故障

- `kdebug executable not found`：设置绝对 `KDEBUG_BIN`，确认文件可执行，并保留同一
  build tree 的 `kdebug/libexec`。
- `KDEBUG_ACTION`：查看 response 的 `error.code/error.message` 和 kdebug stderr；先用
  第 7.1 节直接 adapter 命令复现。
- `KDEBUG_RESPONSE`：kdebug stdout 必须只有一个 JSON object；普通日志必须走
  stderr。确认两个仓库使用上述配套分支。
- `legacy inventory schema_version 2`：在线 kdebug action 必须返回 v3，不能用旧
  inventory 冒充实时采集。
- `warning[NPI_LOAD_PARTIAL]`：按第 10 节继续检查所有硬证据，不要只删除日志。
- `work.lib++ is ... not an elaborated KDB`：改传同一编译流程生成的 `*.elab++`
  目录。
- GUI 能启动但在线检查找不到 kdebug：在 `kdebug ELF` 行选择运行包内的绝对 ELF 路径，
  或在启动 GUI 的同一个 shell 中导出 `KDEBUG_BIN`；配置导入/导出不会保存设备相关
  可执行路径和环境变量。
