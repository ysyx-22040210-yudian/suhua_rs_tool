# RTL 打拍例化检查工具使用手册

本文档说明如何准备规格表、配置检查规则、验证输入、使用可信 inventory 做离线检查，以及使用 Verdi elaborated KDB 做在线 NPI 检查。

> **重要约束：在线 NPI 只接受 `elabcom` 生成的 elaborated KDB 目录。**
>
> `vericom` 生成的 `work.lib++` 只是编译库，不能直接作为在线输入。工具不会接收 `-f filelist.f`、`-sv source.sv`、`-lib work` 或任何 `--` 后的 Verdi 参数透传。collector 内部传给 `npi_load_design` 的设计参数固定为 `-elab <KDB路径>`。

## 1. 安装与依赖

### 1.1 Python 前端

Python 前端负责读取 Excel/CSV、加载配置、执行规则检查和生成报告。

- Python 版本：3.8 或更高。
- Python 运行时依赖：无第三方依赖。
- 支持的规格文件：`.xlsx`、`.xlsm`、`.csv`、`.tsv`。
- 不支持旧二进制 `.xls`；请先另存为 `.xlsx` 或 CSV。
- 离线 inventory 模式不需要 Verdi、NPI license 或 C++ 编译器。

在仓库根目录安装命令行入口：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
rtl-rs-check --help
```

Windows PowerShell 做离线检查时，虚拟环境激活命令为：

```powershell
.\.venv\Scripts\Activate.ps1
```

也可以不安装，直接在仓库根目录运行：

```bash
python -m rscheck --help
```

本文后续统一使用 `python -m rscheck`；安装后可等价替换为 `rtl-rs-check`。

### 1.2 在线 NPI 环境

在线采集还需要：

- Linux 环境；
- Synopsys Verdi/NPI 安装和可用 license；
- 与目标 KDB 兼容的 Verdi/NPI 版本；
- 支持 C++11 的 `g++`；
- GNU Make；
- `VERDI_HOME` 或 `NOVAS_INST_DIR` 指向 Verdi 安装根目录；
- `NPI_PLATFORM` 指向对应平台库目录，默认 `LINUX64`。

已验证组合为 CentOS 7.9、Python 3.8.13、G++ 11.2.1、Verdi/NPI O-2018.09-SP2、`NPI_PLATFORM=LINUX64`。

### 1.3 Verdi GUI 环境

Verdi GUI 需要一个能通过 `xdpyinfo` 访问的 X11 DISPLAY。以下方式均可：

- 在 Linux 本地图形桌面的终端中运行；
- 在 VNC/XRDP 桌面内的终端中运行；
- 从已启动 X server 的客户端使用 `ssh -Y` 登录，并保持 SSH 连接开启。

GNOME 不是必需条件。KDE、Xfce、MATE、Cinnamon、LXQt 等 X11 会话都可使用；Wayland 会话必须启用 Xwayland。Debian/Ubuntu 需安装 `x11-utils`，RHEL/CentOS 需安装 `xorg-x11-utils`，以提供 GUI 探测所需的 `xdpyinfo` 和完整测试所需的 `xwininfo`。

应优先在图形会话所属用户的终端中运行，并将仓库和 KDB 放在该用户可读取的位置。root 读取其他用户 `/proc/<PID>/environ` 的跨用户探测仅作为旧部署兼容回退，不保证在启用 SELinux、`hidepid` 或严格 Xauthority 权限时可用。

## 2. 规格表要求

### 2.1 八个必需字段

每个有效数据行必须提供以下八个字段，名称区分大小写：

| 字段 | 含义 | 检查方式 |
|---|---|---|
| `Intf_type` | 该组打拍 interface 的业务标签 | v1 仅写入报告，不参与 RTL 判定 |
| `RS_module` | 打拍实例预期的模块定义名 | 与实例的 NPI `npiDefName` 精确比较 |
| `RS_inst` | 一组打拍实例的本地实例名前缀 | 对 `position` 直接子实例做前缀和后缀规则匹配 |
| `position` | 该组实例的直接父 scope 完整 NPI 层次路径 | 必须能在 elaborated 层次中找到；首尾 `.` 会被去除 |
| `step` | 当前组预期的总拍数 | 等于符合前缀和后缀规则的实例数量；必须为正整数 |
| `clk` | 每个匹配实例的预期 clk 连线 | 与配置的 clk formal port 的 high connection 比较 |
| `rst` | 每个匹配实例的预期 rst 连线 | 与配置的 rst formal port 的 high connection 比较 |
| `CRG_source` | clk 预期的唯一上游来源 | 按 `rtl.crg_match` 与上游 module definition 或实例名比较 |

一行定义一组，唯一组键是 `(position, RS_inst)`。同一个 `position` 下可以有多组，每组占一行；相同组键重复出现会被拒绝。

### 2.2 任意额外列和非连续列映射

规格表可以包含任意数量的额外列。八个必需字段可以乱序、彼此不连续，并位于任意正数列号。列号从 **1** 开始，八个映射必须互不重复；未映射列会被忽略，即使其中包含公式也不会参与读取。

例如，一个 13 列工作表可以这样排列：

| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Owner | position | Review_note | rst | Intf_type | Spare | CRG_source | RS_inst | step | Ticket | RS_module | clk | Comment |
| alice | top.u_tile | checked | rst_n | OUT_IF | - | crg_core | AAAA_BBB | 2 | HW-101 | rs_pipe | clk_rs | first group |

对应配置为：

```json
{
  "columns": {
    "Intf_type": 5,
    "RS_module": 11,
    "RS_inst": 8,
    "position": 2,
    "step": 9,
    "clk": 12,
    "rst": 4,
    "CRG_source": 7
  }
}
```

也可以临时覆盖部分列号；未覆盖项继续使用配置文件：

```bash
python -m rscheck validate \
  --excel specs.xlsx \
  --config rscheck.json \
  --column position=2 \
  --column rst=4 \
  --column Intf_type=5 \
  --column CRG_source=7 \
  --column RS_inst=8 \
  --column step=9 \
  --column RS_module=11 \
  --column clk=12
```

覆盖后仍会检查八个列号是否为正数且互不重复。

### 2.3 单元格和行规则

- 映射字段会去除首尾空白。
- 八个字段均为必填。整行八个字段都为空时跳过；部分为空时报错。
- `step` 接受 `2` 或 Excel 常见的 `2.0`，但不接受 `0`、负数、小数或科学计数法。
- 默认会在 `excel.header_row` 对八个映射单元格做精确表头校验。
- XLSX/XLSM 映射字段中的公式单元格会被拒绝，防止使用未刷新的 Excel 缓存值；CSV/TSV 只有文本，没有可验证的 Excel 公式元数据。
- `.xlsm` 中的宏不会执行。
- CSV 优先按 UTF-8 BOM/UTF-8 读取，失败后尝试 GB18030；CSV 可探测逗号、分号或制表符，`.tsv` 固定使用制表符。
- 至少要有一行有效规格。

## 3. 分组和信号匹配语义

### 3.1 实例组

`RS_inst` 只定义前缀。实例名减去该前缀后的剩余字符串，必须完整匹配 `rtl.suffix_regex`。

默认规则：

```regex
(?P<tag>.+?)(?P<index>[0-9]+)
```

若 `RS_inst=AAAA_BBB`：

- `AAAA_BBB_C0`、`AAAA_BBB_C12`：匹配；
- `AAAA_BBB0`：不匹配，因为缺少非空 tag；
- `AAAA_BBB_C`：不匹配，因为末尾没有数字 index。

默认只检查匹配数量是否等于 `step`，不会要求 index 从 0 连续。启用 `require_contiguous_indices` 后，index 必须等于从 `index_base` 开始、长度为 `step` 的连续序列，且所有实例必须使用同一个 tag。

### 3.2 clk/rst 连线

formal port 名由 `rtl.clk_port` 和 `rtl.rst_port` 指定，默认分别是 `clk`、`rst`。Excel 中的 `clk`/`rst` 是这些 formal port 的预期实际连线，不是 formal port 名。

比较时会忽略信号字符串中的空白，并接受：

1. Excel 值与 NPI 完整连线名完全相同；
2. NPI 连线名等于 `position + "." + Excel值`。

例如 `position=top.u_tile`、`clk=clk_rs` 可以匹配 `top.u_tile.clk_rs`。若 `allow_leaf_signal_match=true`，还允许只比较最后一级信号名，但这可能把不同 scope 的同名信号误判为一致，默认关闭。

### 3.3 CRG 来源

collector 使用 Netlist Model 的 `npiNlDriver` 逆向追踪 clk。只有找到唯一有效的上游 module cell 时才可通过；无来源、多来源和采集 warning 都会 fail-closed。

`rtl.crg_match` 决定 `CRG_source` 的比较对象：

| 值 | 比较对象 |
|---|---|
| `module` | module definition 名，默认值 |
| `instance` | 上游实例完整路径或本地叶子实例名 |
| `module_or_instance` | 上述任一种 |

推断出的 primitive gate/buffer 会继续向上穿透；只有它在 Netlist 中表现为 module cell 时才会作为 `CRG_source` 候选。

## 4. 配置文件完整说明

配置文件是 UTF-8 JSON，根对象只允许 `excel`、`columns`、`rtl` 三个键。未知键、错误类型和未知列名都会被拒绝。

完整示例：

```json
{
  "excel": {
    "sheet": "RS_Check",
    "header_row": 1,
    "data_start_row": 2,
    "validate_headers": true
  },
  "columns": {
    "Intf_type": 5,
    "RS_module": 11,
    "RS_inst": 8,
    "position": 2,
    "step": 9,
    "clk": 12,
    "rst": 4,
    "CRG_source": 7
  },
  "rtl": {
    "clk_port": "clk",
    "rst_port": "rst",
    "suffix_regex": "(?P<tag>.+?)(?P<index>[0-9]+)",
    "index_base": 0,
    "require_contiguous_indices": false,
    "allow_leaf_signal_match": false,
    "crg_match": "module"
  }
}
```

### 4.1 `excel`

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `excel.sheet` | 非空字符串或正整数 | `1` | XLSX/XLSM 的工作表名或 1-based 工作表序号；CSV/TSV 没有工作表 |
| `excel.header_row` | 正整数 | `1` | 表头所在行 |
| `excel.data_start_row` | 正整数 | `2` | 第一条数据所在行，必须大于 `header_row` |
| `excel.validate_headers` | JSON 布尔值 | `true` | 是否要求映射表头精确等于八个字段名 |

### 4.2 `columns`

`columns` 必须恰好包含八个字段：`Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`。每个值都是正整数形式的 1-based 列号；也接受只包含十进制数字的 JSON 字符串。列号必须唯一，不要求连续或按字段顺序排列。

### 4.3 `rtl`

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `rtl.clk_port` | 非空字符串 | `clk` | 打拍模块的 clk formal port 名 |
| `rtl.rst_port` | 非空字符串 | `rst` | 打拍模块的 rst formal port 名 |
| `rtl.suffix_regex` | 非空正则字符串 | `(?P<tag>.+?)(?P<index>[0-9]+)` | 匹配实例名减去 `RS_inst` 后的完整 remainder；必须含命名组 `index` |
| `rtl.index_base` | 非负整数 | `0` | 连续 index 检查的起点 |
| `rtl.require_contiguous_indices` | JSON 布尔值 | `false` | 是否强制相同 tag 和连续 index |
| `rtl.allow_leaf_signal_match` | JSON 布尔值 | `false` | 是否允许 clk/rst 仅按最后一级信号名匹配 |
| `rtl.crg_match` | 枚举字符串 | `module` | `module`、`instance` 或 `module_or_instance` |

`suffix_regex` 会被工具自动按完整字符串匹配，无需自行添加 `^`/`$`。`index` 组在运行时必须只产生 ASCII 数字，推荐固定写成 `(?P<index>[0-9]+)`。`tag` 组不是语法必需，但要使用 tag 一致性检查时应保留。

### 4.4 命令行覆盖

| 参数 | 作用 |
|---|---|
| `--sheet NAME_OR_INDEX` | 覆盖 `excel.sheet`；全数字值解释为 1-based 序号 |
| `--header-row N` | 覆盖表头行 |
| `--data-start-row N` | 覆盖数据起始行 |
| `--column FIELD=INDEX` | 覆盖一个字段的列号，可重复 |
| `--no-header-check` | 本次运行关闭表头校验 |

`--no-header-check` 只应在表头不可控且列映射已经独立确认时使用；它会降低发现错列的能力。

## 5. 先运行 `validate`

`validate` 只检查配置和规格表，不加载 inventory、不启动 NPI，也不检查 RTL。

```bash
python -m rscheck validate \
  --excel specs.xlsx \
  --config rscheck.json
```

成功时输出规范化后的组摘要：

```text
VALID: 2 specification row(s)
row 2: top.u_tile / AAAA_BBB module=rs_pipe step=2
row 3: top.u_tile / CTRL_RS module=rs_pipe step=1
```

输出完整规范化 JSON：

```bash
python -m rscheck validate \
  --excel specs.xlsx \
  --config rscheck.json \
  --json
```

建议在每次调整列映射、sheet、表头行、数据起始行或 suffix 规则后先执行 `validate`。注意：`validate` 成功只说明规格输入合法，不代表 RTL 检查会通过。

## 6. 可信离线 inventory 模式

离线模式从已有的 schema v1 inventory 读取 elaborated 层次快照，不启动 collector：

```bash
python -m rscheck check \
  --excel specs.xlsx \
  --config rscheck.json \
  --inventory trusted_inventory.json \
  --json-report output/rs_report.json \
  --csv-report output/rs_report.csv
```

`--inventory` 与 `--collector` 互斥。离线模式不能使用 `--elab-db`、`--keep-inventory` 或 `--npi-lib-dir`。

### 6.1 信任边界和新鲜度警告

> **离线 inventory 是受信任输入，不提供 KDB 来源或新鲜度证明。**

schema v1 不记录 RTL commit、elabcom 命令、top、宏、include 路径、KDB 哈希、生成时间或 collector 版本。工具只验证 JSON 结构和部分内部一致性；手工修改、由旧流程生成或已经过期的 inventory 仍可能产生看似正常的 PASS。

只应在以下条件全部满足时复用 inventory：

- inventory 来自本工具当前版本 collector 的成功在线运行；
- RTL commit、依赖库、宏、include 路径和 elaboration top 未变化；
- elaborated KDB 未重新生成或替换；
- `position`、`rtl.clk_port`、`rtl.rst_port` 及相关配置未变化；
- inventory 文件未被手工编辑；
- 使用者明确接受离线快照的信任边界。

任一条件无法确认时，应重新运行在线采集。建议通过 `--keep-inventory` 生成离线文件，并在文件名或外部制品元数据中记录 RTL commit 和 KDB 生成时间，例如：

```text
inventory.top.<rtl-commit>.<kdb-timestamp>.json
```

### 6.2 inventory 基本结构

```json
{
  "schema_version": 1,
  "positions": {
    "top.u_tile": {
      "found": true,
      "instances": [
        {
          "name": "AAAA_BBB_C0",
          "full_name": "top.u_tile.AAAA_BBB_C0",
          "module": "rs_pipe",
          "file": "rs_top.sv",
          "line": 10,
          "ports": {
            "clk": {
              "connection": "top.u_tile.clk_rs",
              "type": "npiNet"
            },
            "rst": {
              "connection": "top.u_tile.rst_n",
              "type": "npiNet"
            }
          },
          "clk_sources": [
            {
              "instance": "top.u_tile.u_crg",
              "module": "crg_core"
            }
          ]
        }
      ]
    }
  },
  "warnings": []
}
```

加载器要求 `schema_version=1`、`positions` 为对象、`found` 为布尔值、`instances` 为数组，并检查实例 `name/full_name` 一致性和 `full_name` 唯一性。inventory 中任何 `warnings` 都会转换为全局 `NPI_UNRESOLVED` 硬错误，避免不完整采集误报 PASS。

## 7. 构建 NPI collector

从仓库根目录执行，避免改变后续命令的工作目录：

```bash
make -C npi VERDI_HOME=/path/to/verdi NPI_PLATFORM=LINUX64
```

默认产物：

```text
npi/build/rs_npi_collector
```

Makefile 使用 C++11，包含 `$VERDI_HOME/share/NPI/inc`，并从以下目录链接 `libNPI.so`：

```text
$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM
```

安装布局不符合上述结构时，可直接覆盖 Makefile 的目录和编译器：

```bash
: "${VERDI_HOME:?set VERDI_HOME in the current shell}"
: "${NPI_INC_DIR:?set NPI_INC_DIR in the current shell}"
: "${NPI_LIB_DIR:?set NPI_LIB_DIR in the current shell}"
make -C npi \
  VERDI_HOME="$VERDI_HOME" \
  NPI_INC="$NPI_INC_DIR" \
  NPI_LIB="$NPI_LIB_DIR" \
  CXX="${CXX:-g++}"
```

GNU Make 无法可靠表示带空白的目标路径，因此仓库路径、`NPI_INC_DIR` 和 `NPI_LIB_DIR` 不得包含空白；Makefile 会在解析阶段给出明确错误。该约束不影响 `launch_verdi_gui.sh`，后者支持带空格的 KDB 路径。

运行前设置：

```bash
export VERDI_HOME=/path/to/verdi
export NPI_PLATFORM=LINUX64
```

Python runner 会尝试把标准 NPI 平台目录加入 collector 子进程的 `LD_LIBRARY_PATH`。若安装布局非标准，传入的 `--npi-lib-dir` 必须是**直接包含 `libNPI.so` 的目录**，例如：

```bash
--npi-lib-dir /tools/verdi/share/NPI/lib/LINUX64
```

清理构建产物：

```bash
make -C npi clean
```

通常不需要直接运行 collector。直接运行时，它要求一个每行一个 `position` 的文本文件，并且只接受 `--elab-db` 作为设计输入：

```bash
npi/build/rs_npi_collector \
  --positions positions.txt \
  --output inventory.json \
  --clk-port clk \
  --rst-port rst \
  --elab-db /absolute/path/to/kdb.elab++
```

## 8. 生产 elaborated KDB 输入

### 8.1 接受和拒绝的输入

在线模式接受：

- `elabcom -elab <path>` 生成的、当前可访问的 KDB 目录；
- 默认名 `kdb.elab++` 或自定义目录名。

在线模式不接受：

- `vericom` 生成的 `work.lib++`；
- `filelist.f`；
- `.v`、`.sv`、`.vhd` 等 RTL 源文件；
- `-f`、`-F`、`-sv`、`-verilog`、`-vhdl`、`-lib`、`-path`、`-top` 等任意 Verdi 导入参数；
- `--` 后的 passthrough 参数。

生产环境应优先复用芯片工程已有、受控的 Verdi 编译/elaboration 流程。生成 KDB 时使用的 RTL、宏、include、库和 top 必须与待检查设计一致。本工具只读取 KDB，不负责重新编译或 elaboration RTL。

即使生产准备流程内部使用 filelist，它也只能用于工具外部的 `vericom/elabcom` 阶段，不能传给本工具的 `check` 或 collector 命令。

### 8.2 为仓库示例生成 KDB

以下命令从仓库根目录开始，并使用独立临时输出目录，避免旧 `work.lib++` 污染结果：

```bash
set -eu
: "${VERDI_HOME:?VERDI_HOME must point to the Verdi installation}"

PROJECT_ROOT=$(pwd -P)
mkdir -p "$PROJECT_ROOT/output"
ELAB_ROOT=$(mktemp -d "$PROJECT_ROOT/output/example_elab.XXXXXX")

(
  cd "$ELAB_ROOT"
  "$VERDI_HOME/bin/vericom" -sv "$PROJECT_ROOT/examples/rtl/rs_example.sv"
  "$VERDI_HOME/bin/elabcom" -top top -elab "$ELAB_ROOT/kdb.elab++"
)

test -d "$ELAB_ROOT/kdb.elab++"
printf 'KDB: %s\n' "$ELAB_ROOT/kdb.elab++"
```

`vericom` 在当前目录生成默认编译库 `work.lib++`；紧接着在同一目录运行 `elabcom`，它读取该编译库并生成真正的 elaborated KDB。`-elab` 可指定自定义 KDB 目录名，collector 不强制 `.elab++` 后缀。

## 9. 在线 `check`

在上一步同一个 shell 中，可直接使用 `$ELAB_ROOT`：

```bash
python -m rscheck check \
  --excel examples/specs.csv \
  --config config/rscheck.example.json \
  --collector npi/build/rs_npi_collector \
  --elab-db "$ELAB_ROOT/kdb.elab++" \
  --keep-inventory output/inventory.current.json \
  --json-report output/rs_report.json \
  --csv-report output/rs_report.csv \
  --npi-timeout 600
```

在线流程为：

1. Python 解析规格表和配置；
2. 提取所有唯一 `position` 写入临时文本文件；
3. 确认 collector 和 KDB 目录存在；
4. 启动 C++ collector；
5. collector 构造且只构造 `{程序名, "-elab", KDB路径}` 交给 `npi_init/npi_load_design`；
6. collector 遍历层次、端口和 clk driver，生成 schema v1 inventory；
7. Python 加载 inventory，执行组、拍数、模块、clk/rst 和 CRG 检查；
8. 可选保存 inventory 和 JSON/CSV 报告。

在线专用参数：

| 参数 | 说明 |
|---|---|
| `--collector PATH` | C++ collector 可执行文件；与 `--inventory` 互斥 |
| `--elab-db DIR` | 必需，现存 Verdi elaborated KDB 目录 |
| `--keep-inventory PATH` | 保存本次实时采集结果，供审计或可信离线复用 |
| `--npi-timeout SECONDS` | 可选，collector 超时秒数，必须大于等于 1 |
| `--npi-lib-dir DIR` | 可选，直接包含 `libNPI.so` 的目录 |

不要在命令末尾添加 `-- -f ...`、`-- -sv ...` 或其他 Verdi 参数；argparse 会直接拒绝这些参数。

### 9.1 打开同一个 KDB 的 Verdi GUI

`rscheck` 是 CLI。需要人工查看 hierarchy、实例或连线时，使用严格的跨设备启动器；它只接受现存 elaborated KDB 目录，并且实际调用固定为 `verdi -elab <KDB>`。

先在当前图形 shell 中验证 GUI：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_verdi_gui.sh --probe-only
```

前台启动，关闭 Verdi 后 shell 才返回：

```bash
: "${ELAB_DB:?export ELAB_DB to an elaborated KDB directory}"
bash scripts/launch_verdi_gui.sh --elab-db "$ELAB_DB"
```

后台启动并打印 PID 和日志路径：

```bash
: "${ELAB_DB:?export ELAB_DB to an elaborated KDB directory}"
bash scripts/launch_verdi_gui.sh \
  --elab-db "$ELAB_DB" \
  --background
```

`--probe-only` 不能与 `--elab-db` 或 `--background` 同时使用。启动器拒绝 `work.lib++`、普通文件、RTL/filelist、`-f`、`-sv`、`-lib`、`-top`、位置参数和 `--` passthrough。

Verdi 可执行文件按 `VERDI_BIN`、`VERDI_HOME/bin/verdi`、`NOVAS_INST_DIR/bin/verdi`、`PATH` 的顺序查找。GUI 默认先使用当前可访问的 `DISPLAY`，再扫描常见桌面/Xwayland 进程；可用以下变量消除设备差异：

| 变量 | 用途 |
|---|---|
| `VERDI_BIN` | 直接指定 Verdi 可执行文件 |
| `VERDI_HOME` / `NOVAS_INST_DIR` | 指定 Verdi 安装根目录 |
| `GUI_USER` | 自动扫描时限定进程用户；配合 `GUI_DISPLAY` 时标识目标图形用户 |
| `GUI_DISPLAY` | 显式指定 DISPLAY，或在多个可用 DISPLAY 中选择 |
| `GUI_XAUTHORITY` | 为显式 DISPLAY 指定 Xauthority 文件 |
| `GUI_SESSION_PID` | 从指定图形进程读取 DISPLAY 和会话环境 |

完整端到端测试 `scripts/test_vm_verdi_gui.sh` 还支持 `PYTHON_BIN`、`CXX`、`NPI_INC_DIR` 和 `NPI_LIB_DIR`，分别覆盖 Python、C++ 编译器、`npi.h` 所在目录和 `libNPI.so` 所在目录。详见 [Verdi GUI 端到端复现指南](VM_GUI_TEST.md)。

## 10. 报告和退出码

### 10.1 控制台

控制台首先输出整体结果，再逐行输出匹配数和 findings：

```text
RESULT: PASS | rows=2 errors=0 warnings=0
[PASS] row 2 OUT_IF | top.u_tile / AAAA_BBB (2/2 instances)
[PASS] row 3 CTRL_IF | top.u_tile / CTRL_RS (1/1 instances)
```

### 10.2 JSON 报告

`--json-report` 写 UTF-8 JSON，包含：

- `summary`：是否通过、总行数、通过/失败行数、error/warning 数；
- `global_findings`：例如 `NPI_UNRESOLVED`、`AMBIGUOUS_GROUP_MATCH`；
- `rows[].spec`：规范化后的八字段规格和源行号；
- `rows[].matched_instances`：实例路径、模块、文件/行号、端口连线和 clk 来源证据；
- `rows[].findings`：code、message、expected、actual 等诊断信息。

输出目录不存在时会自动创建。

### 10.3 CSV 报告

`--csv-report` 写 UTF-8 BOM CSV，可直接用 Excel 打开。列包括：

```text
status,row,position,RS_inst,instance,code,message,expected,actual
```

无 finding 的规格行写一条 `PASS`；有 finding 时每个 finding 写一条记录。

### 10.4 Python CLI 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 没有 error；可以存在 warning-only finding |
| `1` | RTL/规格检查失败，或出现 fail-closed 全局错误 |
| `2` | 命令参数、配置、规格表、inventory、collector、KDB、NPI 加载或报告输出失败 |

### 10.5 直接运行 collector 的退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 采集成功并写出 inventory |
| `2` | collector 参数错误或出现额外参数 |
| `3` | positions 文件不可读或没有有效路径 |
| `4` | elaborated KDB 路径不存在、不可访问或不是目录 |
| `10` | `npi_init` 失败 |
| `11` | `npi_load_design` 加载 KDB 失败 |
| `12` | `npi_end` 失败 |
| `13` | inventory 输出失败 |
| `14` | 未预期内部异常 |

通过 Python runner 使用 collector 时，collector 的非零退出会被转换为 Python CLI 的基础设施错误，通常返回 `2`。

## 11. 常见错误与处理

| 错误或 finding | 常见原因 | 处理建议 |
|---|---|---|
| `header validation failed` | 列号错、表头行错、字段大小写不一致 | 用 `validate` 和 `--json` 检查规范化结果；修正映射，不要优先关闭表头检查 |
| `missing/unknown column mappings` | 八字段映射不完整或拼写错误 | `columns` 必须恰好包含八个规定字段 |
| `column mappings must be unique` | 两个字段映射到同一列 | 调整为互不重复的 1-based 列号 |
| `blank required fields` | 数据行只填写了部分映射字段 | 补齐八字段，或清空整行使其被跳过 |
| `step must be a positive integer` | `step` 为 0、负数、小数或科学计数法 | 改为正整数，如 `2` |
| `duplicate group` | 同一 `(position, RS_inst)` 出现多行 | 合并或修改重复组 |
| `sheet ... not found` | sheet 名/序号不对 | 用实际工作表名或 1-based 序号覆盖 `--sheet` |
| `legacy .xls is not supported` | 输入是旧二进制 Excel | 另存为 `.xlsx` 或 CSV |
| `formula cells are not supported` | XLSX/XLSM 的映射字段是公式单元格 | 将值固化为文本/数字；额外未映射列不受影响 |
| collector executable not found | 未构建或路径不对 | 执行 `make -C npi ...` 并检查 `npi/build/rs_npi_collector` |
| `libNPI.so` 找不到 | `VERDI_HOME`、`NPI_PLATFORM` 或运行库目录错误 | 设置环境变量，或用 `--npi-lib-dir` 指向直接包含 `.so` 的平台目录 |
| elaborated database not found/must be a directory | `--elab-db` 路径不存在，或传入了普通文件 | 传入现存的 `elabcom -elab` KDB 目录 |
| `unexpected argument` / `unrecognized arguments` | 仍在使用旧 `-- -f/-sv/-lib` 透传 | 删除透传，先在外部流程生成 KDB，再只传 `--elab-db` |
| `npi_init failed` | NPI 环境、license 或版本问题 | 检查 license、Verdi 安装和平台库 |
| `npi_load_design failed` | 传入了 `work.lib++`、KDB 损坏、版本不兼容、未完成 elaboration 或 top 错误 | 确认输入是 `elabcom -elab` 产物；用相同 Verdi 流程重新生成 KDB 并验证 top |
| collector timeout | KDB 很大或 NPI 卡住 | 调高 `--npi-timeout`，同时检查 license/存储/设计状态 |
| `POSITION_NOT_FOUND` | Excel 路径不属于当前 elaborated top，或层次名错误 | 在同一 KDB 中核对完整实例层次和 `position` |
| `GROUP_NOT_FOUND` | 没有实例同时满足前缀和 suffix regex | 核对 `RS_inst` 和 `rtl.suffix_regex` |
| `INSTANCE_SUFFIX_INVALID` | 有前缀相同实例，但 suffix 不符合规则 | 修正命名或 regex；该 finding 默认是 warning |
| `STEP_MISMATCH` | 匹配实例数与 `step` 不同 | 核对漏例化、多例化和规格拍数 |
| `SUFFIX_TAG_MISMATCH` | 同组实例的 tag 不同 | 核对命名；默认是 warning，连续 index 模式下还会导致硬错误 |
| `STAGE_INDEX_MISMATCH` | index 不连续、不从 `index_base` 开始或 tag 不唯一 | 修正实例命名或关闭不需要的连续检查 |
| `RS_MODULE_MISMATCH` | 实例 definition 与 `RS_module` 不同 | 核对模块替换、wrapper 和规格模块名 |
| `CLK_PORT_MISSING` / `RST_PORT_MISSING` | 配置的 formal port 不存在 | 修正 `rtl.clk_port/rst_port` 或 RTL |
| `CLK_UNCONNECTED` / `RST_UNCONNECTED` | formal port 没有 high connection | 修正例化连线 |
| `CLK_CONNECTION_MISMATCH` / `RST_CONNECTION_MISMATCH` | Excel 预期信号与实际连接不一致 | 使用相对 `position` 的简单名或完整层次名，并核对连接 |
| `UNSUPPORTED_CONNECTION` | clk/rst 经 concat、运算、mux 等复杂表达式连接 | 改为可追踪的直接信号，或扩展采集/规则模型 |
| `CRG_SOURCE_UNRESOLVED` | 顶层输入、无 driver、无法到达 module cell | 补充可识别来源或接受该 fail-closed 结果 |
| `MULTIPLE_CLK_SOURCES` | clk 存在多个唯一上游 module source | 消除多驱动或修正结构 |
| `CRG_SOURCE_MISMATCH` | 唯一来源与 `CRG_source` 不一致 | 核对 `rtl.crg_match` 和 Excel 来源字段 |
| `AMBIGUOUS_GROUP_MATCH` | 同一实例同时匹配多个重叠 `RS_inst` 前缀 | 重新设计互不重叠的组前缀 |
| `NPI_UNRESOLVED` | collector 产生 traversal/driver warning | 视为硬错误；检查 KDB、层次和 Netlist driver 信息，不要忽略 |
| `no usable X11 display` | 当前 DISPLAY 不可用，或无法发现/认证其他会话 | 从本地/VNC/XRDP 图形终端运行，或使用带客户端 X server 的 `ssh -Y`；再执行 `--probe-only` |
| `multiple usable X11 displays` | 自动探测到多个有效 DISPLAY | 设置 `GUI_DISPLAY`，必要时同时设置 `GUI_USER` 或 `GUI_XAUTHORITY` |
| `no active gnome-session-binary session` | 使用了仓库旧版或外部旧启动脚本 | 更新仓库并改用 `scripts/launch_verdi_gui.sh --probe-only`；当前实现不要求 GNOME |

## 12. 当前边界

- `Intf_type` 仅作为业务标签，不验证 interface 的 input/output 数据链。
- 当前八字段不足以证明每一拍的数据端口按顺序串接，也不检查首拍输入和末拍输出。
- 默认只按数量检查 `step`；只有启用 `require_contiguous_indices` 才检查数字 stage 连续性。
- collector 只收集 `position` 的直接 module children；会穿过 `npiGenScope`，但不会进入已经遇到的普通子模块，也不会穿过 interface/program 等其他层次边界。
- SystemVerilog instance array 名如 `u[0]` 不符合默认 `AAAA_BBB_C0` 风格，需要定制 `suffix_regex` 和前缀策略。
- clk/rst 的复杂表达式不做字符串猜测，统一按 `UNSUPPORTED_CONNECTION` 处理。
- CRG 追踪要求唯一 module cell 来源；顶层输入、多驱动和无法解析的 primitive 网络会 fail-closed。
- `allow_leaf_signal_match=true` 会放宽层次比较，可能让不同 scope 的同名信号误匹配。
- 任意 collector warning 都会升级为 `NPI_UNRESOLVED` error。
- 在线模式只消费现有 elaborated KDB，不接收 RTL/filelist，也不负责 RTL 编译或 elaboration。
- 离线 inventory 是可信快照例外，不包含来源证明；它不能替代新鲜 KDB 的在线采集。
- NPI 真实构建和在线运行依赖特定 Synopsys 版本、平台动态库和 license；离线 Python 测试不能覆盖这些环境因素。

## 13. 推荐操作顺序

1. 固定 RTL commit、宏、库、include 和 top，生成新的 elaborated KDB。
2. 配置八字段的 1-based 列映射。
3. 执行 `validate`，确认 sheet、表头、列和规范化内容。
4. 使用 `--collector + --elab-db` 做在线检查。
5. 同时输出 JSON/CSV，并用 `--keep-inventory` 保存可审计快照。
6. 只有在来源和新鲜度都可证明时，才使用该 inventory 做离线复查。
7. RTL、KDB 或相关配置变化后立即重新在线采集。
8. 需要人工核对时，用 `launch_verdi_gui.sh --elab-db "$ELAB_DB"` 打开同一个 KDB。
