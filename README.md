# RTL 打拍例化检查工具

该工具把 Excel 中的打拍规格与 NPI 展开后的 RTL 层次做对比，当前检查：

- `position` 是否存在；
- 同一 `RS_inst` 前缀组的实例数量是否等于 `step`；
- 每个实例的模块定义名是否等于 `RS_module`；
- 每个实例的 clk/rst formal port 是否存在、已连接且符合 Excel；
- clk 是否可追到唯一上游模块，且模块定义名等于 `CRG_source`；
- 前缀重叠导致同一实例匹配多个 Excel 组时，明确报错。

`Intf_type` 在 v1 中作为业务标签进入报告，不参与 RTL 判定。仅凭当前八列无法可靠检查各拍之间的数据串接，详见“当前边界”。

## 文档

- [详细使用文档](docs/USAGE.md)
- [完整测试指南](docs/TESTING.md)
- [Verdi GUI 端到端复现指南](docs/VM_GUI_TEST.md)
- [Excel 输入模板](examples/RS_Check_Excel_Template.xlsx)

## 工程结构

```text
rscheck/                 Python 前端、XLSX 解析、检查与报告
npi/                     C++ NPI 采集器及 Makefile
config/rscheck.example.json
examples/                可运行的 CSV 与 SystemVerilog 示例
docs/                    详细使用与测试文档
tests/                   无 NPI license 也能运行的离线测试
scripts/                 跨设备 Verdi GUI 启动器和端到端测试脚本
```

Python 端要求 3.8 或更高版本，没有第三方运行时依赖。支持 `.xlsx`、`.xlsm`、`.csv`、`.tsv`；旧二进制 `.xls` 需先另存为 `.xlsx`。宏不会执行，映射列中的公式会被拒绝，以免读取过期缓存值。

## 列映射

复制并修改 [config/rscheck.example.json](config/rscheck.example.json)。Excel 允许包含任意其他列；工具只读取映射的八列。列号从 1 开始，八列可以任意排列、无需连续，但必须为正数且互不重复：

```json
"columns": {
  "Intf_type": 1,
  "RS_module": 2,
  "RS_inst": 3,
  "position": 4,
  "step": 5,
  "clk": 6,
  "rst": 7,
  "CRG_source": 8
}
```

也可以在命令行临时覆盖：

```bash
python -m rscheck validate \
  --excel specs.xlsx \
  --config config/rscheck.example.json \
  --column Intf_type=5 \
  --column RS_module=9
```

默认会校验表头，防止列号填错。工作表、表头行和数据起始行可在 JSON 中配置，也可通过 `--sheet`、`--header-row`、`--data-start-row` 覆盖。

## 分组规则

一行 Excel 定义一组，组键为 `(position, RS_inst)`。`position` 是组成员直接父 scope 的完整 NPI 路径。

若 `RS_inst=AAAA_BBB`，本地实例名必须满足：

```text
AAAA_BBB + 非空字符串 + 末尾阿拉伯数字
```

因此 `AAAA_BBB_C0`、`AAAA_BBB_C12` 匹配，`AAAA_BBB0`、`AAAA_BBB_C` 不匹配。相同前缀但不同 suffix stem 仍属于同一组，只产生 warning。数字是否从 0 连续默认不影响 PASS；需要时把 `require_contiguous_indices` 设为 `true`。

Excel 中的简单 `clk`/`rst` 名称相对 `position` 解析，例如 `position=top.u_tile`、`clk=clk_rs` 对应 `top.u_tile.clk_rs`。formal port 名默认是 `clk`、`rst`，可通过 `rtl.clk_port`、`rtl.rst_port` 修改。

## 先验证 Excel

```bash
python -m rscheck validate \
  --excel examples/specs.csv \
  --config config/rscheck.example.json \
  --sheet 1
```

## 离线检查

已有可信 NPI inventory 时，无需 Verdi 环境即可检查并生成 JSON/CSV 报告：

```bash
python -m rscheck check \
  --excel examples/specs.csv \
  --config config/rscheck.example.json \
  --sheet 1 \
  --inventory tests/fixtures/inventory.json \
  --json-report output/rs_report.json \
  --csv-report output/rs_report.csv
```

CSV 报告使用 UTF-8 BOM，可直接用 Excel 打开。JSON 报告包含匹配实例的完整路径、模块名、源文件/行号、clk/rst 实际连线及 CRG 驱动证据。

`--inventory` 是面向测试和问题复现的离线模式，不证明 inventory 与当前 RTL 同步。生产签核应使用 `--collector --elab-db` 从当前 Verdi elaborated KDB 重新采集。

## 构建 NPI 采集器

在安装了 Verdi/NPI 的 Linux 环境中构建：

```bash
make -C npi VERDI_HOME=/path/to/verdi NPI_PLATFORM=LINUX64
```

非标准安装布局可显式指定头文件和库目录：

```bash
: "${VERDI_HOME:?set VERDI_HOME in the current shell}"
: "${NPI_INC_DIR:?set NPI_INC_DIR in the current shell}"
: "${NPI_LIB_DIR:?set NPI_LIB_DIR in the current shell}"
make -C npi \
  VERDI_HOME="$VERDI_HOME" \
  NPI_INC="$NPI_INC_DIR" \
  NPI_LIB="$NPI_LIB_DIR"
```

GNU Make 会按空白拆分目标名，因此仓库路径、`NPI_INC_DIR` 和 `NPI_LIB_DIR` 不得包含空白；Makefile 会对此提前报错。该限制只影响 collector/端到端构建，独立 GUI 启动器仍支持 KDB 路径包含空格。

构建产物默认位于 `npi/build/rs_npi_collector`。采集器使用手册中的 `npi_init`、`npi_load_design`、`npi_handle_by_name`、`npiInternalScope`、`npiPort`、`npiHighConn`，并用 Netlist Model 的 `npiNlDriver` 追踪 clk 驱动。`npi_load_design` 只接收 `-elab <path>`，不会接收源码、filelist 或任意 Verdi 参数透传。

运行前设置 `VERDI_HOME`。Python runner 会自动把对应 NPI library 目录加入采集器子进程的 `LD_LIBRARY_PATH`：

```bash
export VERDI_HOME=/path/to/verdi
export NPI_PLATFORM=LINUX64
```

非标准安装可在 `check` 命令中使用 `--npi-lib-dir /path/to/NPI/lib/LINUX64` 覆盖；该目录必须直接包含 `libNPI.so`。

## 生成 Verdi elab 库

在线检查的输入必须是 `elabcom` 生成的 Verdi elaborated KDB。`vericom` 生成的 `work.lib++` 只是编译库，不能直接作为本工具的在线输入。以下命令为示例 RTL 生成默认形式的 `kdb.elab++`：

```bash
PROJECT_ROOT=$(pwd)
ELAB_ROOT="$PROJECT_ROOT/output/example_elab"
mkdir -p "$ELAB_ROOT"
cd "$ELAB_ROOT"

"$VERDI_HOME/bin/vericom" -sv "$PROJECT_ROOT/examples/rtl/rs_example.sv"
"$VERDI_HOME/bin/elabcom" -top top -elab "$ELAB_ROOT/kdb.elab++"

cd "$PROJECT_ROOT"
```

生产项目通常由现有 Verdi 编译流程提供 elab 库，本工具不负责重新编译 RTL。

## 在线 NPI 检查

`--elab-db` 必须指向一个已存在的 Verdi elaborated KDB 目录。工具内部只会把它转换为 `-elab <path>` 交给 `npi_load_design`：

```bash
python -m rscheck check \
  --excel specs.xlsx \
  --config rscheck.json \
  --collector npi/build/rs_npi_collector \
  --elab-db output/example_elab/kdb.elab++ \
  --keep-inventory output/npi_inventory.json \
  --json-report output/rs_report.json \
  --csv-report output/rs_report.csv
```

旧的 `-- -f ...`、`-- -sv ...`、`-- -lib ...` 和其他任意参数透传会被拒绝，防止在线检查绕过 elab 库直接重新编译 RTL。

## 跨设备启动 Verdi GUI

`rscheck` 本身是 CLI；需要查看层次时，用仓库内的启动器打开同一个 elaborated KDB。启动器只执行 `verdi -elab <KDB>`，不接受 RTL、filelist、`work.lib++`、`-top` 或任意参数透传：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_verdi_gui.sh --probe-only

export ELAB_DB=/absolute/path/to/kdb.elab++
bash scripts/launch_verdi_gui.sh --elab-db "$ELAB_DB"
```

默认以前台方式运行，关闭 Verdi 后命令才返回。需要让命令立即返回时使用后台模式：

```bash
bash scripts/launch_verdi_gui.sh \
  --elab-db "$ELAB_DB" \
  --background
```

GUI 可以来自本地图形终端、VNC/XRDP 桌面，或客户端已运行 X server 的 `ssh -Y` 会话。GNOME 不是必需条件；KDE、Xfce、MATE、Cinnamon、LXQt 等 X11 桌面均可使用。Wayland 桌面必须启用 Xwayland，因为 Verdi 是 X11 应用。Debian/Ubuntu 安装 `x11-utils`，RHEL/CentOS 安装 `xorg-x11-utils`，以提供 `xdpyinfo`/`xwininfo`。

应优先由图形会话所属用户运行，并确保该用户能读取仓库和 KDB。root 从其他用户进程恢复 GUI 环境只作为旧部署兼容回退，可能受 SELinux、`hidepid` 和 Xauthority 权限限制。

Verdi 路径可通过 `VERDI_BIN`、`VERDI_HOME`、`NOVAS_INST_DIR` 覆盖；GUI 选择可通过 `GUI_USER`、`GUI_DISPLAY`、`GUI_XAUTHORITY`、`GUI_SESSION_PID` 覆盖。完整测试脚本还支持 `PYTHON_BIN`、`CXX`、`NPI_INC_DIR`、`NPI_LIB_DIR`。详细矩阵和可复制命令见 [Verdi GUI 端到端复现指南](docs/VM_GUI_TEST.md)。

## 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 所有硬检查通过；可能有 warning |
| `1` | RTL 与规格不一致，或存在无法可靠解析的连接/驱动 |
| `2` | 配置、Excel、inventory、NPI 启动或设计加载失败 |

## 当前边界

- v1 仅把 `Intf_type` 作为报告标签。若要检查 interface 数据链，需要补充 input/output formal port 映射、首尾预期信号和 stage 顺序定义。
- clk/rst 的复合表达式（concat、运算、mux 等）不会做字符串猜测，而是报 `UNSUPPORTED_CONNECTION`。
- `CRG_source` 默认与第一个唯一上游模块的 `npiDefName` 精确比较；以 NPI module cell 表示的 clock gate/buffer 会被视作 source，primitive gate/buffer 会继续向上追踪。多驱动或顶层输入等无法确定来源的场景会 fail-closed。
- 采集器产生的任何 NPI traversal/driver warning 都按 `NPI_UNRESOLVED` 硬错误处理，避免层次截断后误报 PASS。
- 默认只收集 `position` 下的直接 module children；为了兼容 generate，采集器会穿过非 module 的 generate scope，但不会下钻进已经遇到的普通子模块。
- SystemVerilog instance array 的名字形如 `u[0]`，与 `AAAA_BBB_C0` 这类后缀命名不是同一种分组格式。

## 测试

```bash
python -m unittest discover -v
```

离线测试覆盖 XLSX/CSV 解析、列映射、公式/空字段/重复组、实例分组、拍数、模块名、clk/rst、CRG、多源、前缀歧义和报告导出。真实 NPI 编译与设计加载必须在有对应 Synopsys 安装和 license 的 Linux 环境中执行。

在已登录图形桌面并安装 Verdi/NPI 的 Linux 设备上，可运行完整 GUI 正向链路：

```bash
: "${LM_LICENSE_FILE:?set LM_LICENSE_FILE in the current shell}"
export LM_LICENSE_FILE
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-$LM_LICENSE_FILE}"
bash scripts/launch_verdi_gui.sh --probe-only
bash scripts/test_vm_verdi_gui.sh
```

GUI 探测优先使用当前 shell 已可访问的 `DISPLAY`，否则扫描常见桌面/Xwayland 进程和可读的进程环境；不要求固定桌面用户名、GNOME 或 `gnome-session-binary`。`scripts/test_vm_verdi_gui.sh --gui-probe-only` 也可执行同一探测。完整脚本随后运行全部 Python 测试、构建 collector、生成新的 `kdb.elab++`、启动 `verdi -elab`，再让检查工具只通过 `--elab-db` 使用同一 KDB。

## 已验证环境

2026-07-24 已在以下环境完成真实构建与端到端验证：

```text
CentOS 7.9
Python 3.8.13
GCC/G++ 11.2.1
Verdi/NPI O-2018.09-SP2
NPI_PLATFORM=LINUX64
```

64 项全量测试和 17 项 GUI 定向测试全部通过。未设置 GUI 选择变量时自动发现 `DISPLAY=:0`，Verdi 在 2 秒内出现窗口，独立 launcher 成功打开真实 elaborated KDB；同一 KDB 的在线 NPI 检查为 2 行 PASS、0 error、0 warning。错误规格按预期返回退出码 `1`，并报告 `STEP_MISMATCH`、`RS_MODULE_MISMATCH`、`CLK_CONNECTION_MISMATCH`、`RST_CONNECTION_MISMATCH` 和 `CRG_SOURCE_MISMATCH`。
