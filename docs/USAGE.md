# RTL 打拍例化检查工具使用手册

本文档说明如何通过命令行（CLI）或工具自带的 Tkinter 桌面 GUI 准备规格表、配置检查规则、验证输入、使用可信 inventory 做离线检查，以及使用 Verdi elaborated KDB 做在线 NPI 检查。

> **重要约束：在线 NPI 只接受 `elabcom` 生成的 elaborated KDB 目录。**
>
> `vericom` 生成的 `work.lib++` 只是编译库，不能直接作为在线输入。工具不会接收 `-f filelist.f`、`-sv source.sv`、`-lib work` 或任何 `--` 后的 Verdi 参数透传。collector 内部传给 `npi_load_design` 的设计参数固定为 `-elab <KDB路径>`。

## 1. 安装与依赖

### 1.1 Python CLI 和工具自带 GUI

Python 前端负责读取 Excel/CSV、加载配置、执行规则检查和生成报告，同时提供 CLI 与工具自带桌面 GUI。

- Python 版本：3.8 或更高。
- CLI 没有第三方 Python 运行时依赖。
- GUI 使用标准库 Tkinter；最小化 Linux 安装通常要另装与当前 Python 解释器匹配的 Tk 包。
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

本文后续 CLI 统一使用 `python -m rscheck`；安装后可等价替换为 `rtl-rs-check`。GUI 可使用 `python -m rscheck gui` 或安装入口 `rtl-rs-check-gui`。

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

### 1.3 Linux 图形环境与 Tkinter

工具自带 GUI 和 Verdi GUI 都需要一个能通过 `xdpyinfo` 访问的 X11 DISPLAY。以下方式均可：

- 在 Linux 本地图形桌面的终端中运行；
- 在 VNC/XRDP 桌面内的终端中运行；
- 从已启动 X server 的客户端使用 `ssh -Y` 登录，并保持 SSH 连接开启。

GNOME 不是必需条件。KDE、Xfce、MATE、Cinnamon、LXQt 等 X11 会话都可使用；Wayland 会话必须启用 Xwayland。按系统安装依赖，并确保 Tk 包与实际启动 GUI 的 Python 版本匹配：

Debian/Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y python3-tk x11-utils
```

RHEL/CentOS 的系统 Python：

```bash
sudo yum install -y python3-tkinter xorg-x11-utils
```

CentOS/RHEL Software Collections 的 Python 3.8：

```bash
sudo yum install -y rh-python38-python-tkinter xorg-x11-utils
source /opt/rh/rh-python38/enable
python3 -c 'import tkinter; print(tkinter.TkVersion)'
```

若发行版使用带版本号的包名，应安装与 `python3 --version` 和 `python3 -c 'import sys; print(sys.executable)'` 对应的 `python3-tkinter` 包。`x11-utils`/`xorg-x11-utils` 提供 GUI 探测和测试所需的 `xdpyinfo`、`xprop` 与 `xwininfo`。

应优先在图形会话所属用户的终端中运行，并将仓库和 KDB 放在该用户可读取的位置。root 读取其他用户 `/proc/<PID>/environ` 的跨用户探测仅作为旧部署兼容回退，不保证在启用 SELinux、`hidepid` 或严格 Xauthority 权限时可用。Windows 和 macOS 可直接运行工具自带 GUI；macOS 只支持 Excel 验证和离线 inventory 检查，真实 NPI collector/Verdi KDB 在线检查只支持 Linux。

## 2. 规格表要求

### 2.1 九个映射字段

规格表必须映射以下九个字段，名称区分大小写。前八个字段的数据单元格始终必填；`RS_CFG_EN` 的表头和列映射必需，但数据单元格按 RTL 参数是否存在而条件填写：

| 字段 | 含义 | 检查方式 |
|---|---|---|
| `Intf_type` | 该组打拍 interface 的业务标签 | 当前仅写入报告，不参与 RTL 判定 |
| `RS_module` | 打拍实例预期的模块定义名 | 与实例的 NPI `npiDefName` 精确比较 |
| `RS_inst` | 一组打拍实例的本地实例名前缀 | 对 `position` 直接子实例做前缀和后缀规则匹配 |
| `position` | 该组实例的直接父 scope 完整 NPI 层次路径 | 必须能在 elaborated 层次中找到；首尾 `.` 会被去除 |
| `step` | 当前组预期的总拍数 | 等于符合前缀和后缀规则的实例数量；必须为正整数 |
| `clk` | 每个匹配实例的预期 clk 连线 | 与配置的 clk formal port 的 high connection 比较 |
| `rst` | 每个匹配实例的预期 rst 连线 | 与配置的 rst formal port 的 high connection 比较 |
| `CRG_source` | clk 预期的唯一上游来源 | 按 `rtl.crg_match` 与上游 module definition 或实例名比较 |
| `RS_CFG_EN` | 该组是否预期为假门控 | 逐实例读取 elaborated/effective 参数；存在时必须为 `0` 且本格精确填写 `假门控`，不存在时本格必须留空 |

一行定义一组，唯一组键是 `(position, RS_inst)`。同一个 `position` 下可以有多组，每组占一行；相同组键重复出现会被拒绝。

### 2.2 任意额外列和非连续列映射

规格表可以包含任意数量的额外列。九个映射字段可以乱序、彼此不连续，并位于任意正数列号。列号从 **1** 开始，九个映射必须互不重复；未映射列会被忽略，即使其中包含公式也不会参与读取。

例如，一个 13 列工作表可以这样排列：

| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Owner | position | Review_note | rst | Intf_type | RS_CFG_EN | CRG_source | RS_inst | step | Ticket | RS_module | clk | Comment |
| alice | top.u_tile | checked | rst_n | OUT_IF | 假门控 | crg_core | AAAA_BBB | 2 | HW-101 | rs_pipe | clk_rs | first group |

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
    "CRG_source": 7,
    "RS_CFG_EN": 6
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
  --column RS_CFG_EN=6 \
  --column CRG_source=7 \
  --column RS_inst=8 \
  --column step=9 \
  --column RS_module=11 \
  --column clk=12
```

覆盖后仍会检查九个列号是否为正数且互不重复。

### 2.3 单元格和行规则

- 映射字段会去除首尾空白。
- 除 `RS_CFG_EN` 外的八个字段均为必填；这些字段和 `RS_CFG_EN` 全部为空时跳过整行，八个必填字段中只有部分为空时报错。
- `RS_CFG_EN` 可以为空或填写文本。是否应为空、是否必须精确为 `假门控` 只有加载 schema v2 inventory 或在线采集 RTL 后才能判定；其他非空文本会保留到 RTL 检查阶段，再按参数是否存在和值是否为零产生对应 finding。
- `step` 接受 `2` 或 Excel 常见的 `2.0`，但不接受 `0`、负数、小数或科学计数法。
- 默认会在 `excel.header_row` 对九个映射单元格做精确表头校验，包括即使数据格允许留空也必须存在的 `RS_CFG_EN` 表头。
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

### 3.4 `RS_CFG_EN` effective 参数

`RS_CFG_EN` 按组内匹配实例逐一检查，读取 elaboration 后的 effective 参数值，因此实例级 parameter override 优先于模块声明中的默认值。collector 会枚举每个匹配 `RS_module` 实例可访问的全部 effective parameters；inventory schema v2 把它们记录在实例的 `parameters` 对象中，而当前通过/失败规则消费其中的 `RS_CFG_EN`。键存在表示实例具有该参数，值类型为 `string|null`；字符串保留 collector 得到的值表示，`null` 表示 collector 找到了该参数但无法可靠解析其 effective 值。检查器把十进制有符号零、全零宽值、`'0` 和 Verilog 二/八/十六进制的全零形式都视为数值 `0`。

| 实例参数状态 | Excel `RS_CFG_EN` | 结果 |
|---|---|---|
| 不存在 `RS_CFG_EN` 键 | 空白 | 通过该实例检查 |
| 不存在 `RS_CFG_EN` 键 | 任意非空文本 | `RS_CFG_EN_PARAMETER_MISSING` |
| 字符串表示数值 `0` | 精确 `假门控` | 通过该实例检查 |
| 字符串表示数值 `0` | 空白或其他文本 | `RS_CFG_EN_LABEL_MISMATCH` |
| 字符串表示非零/非数值 | 精确 `假门控` | `RS_CFG_EN_VALUE_MISMATCH` |
| 字符串表示非零/非数值 | 空白或其他文本 | `RS_CFG_EN_LABEL_MISMATCH` 和 `RS_CFG_EN_VALUE_MISMATCH` |
| 值为 `null` | 精确 `假门控` | `RS_CFG_EN_VALUE_UNRESOLVED` |
| 值为 `null` | 空白或其他文本 | `RS_CFG_EN_LABEL_MISMATCH` 和 `RS_CFG_EN_VALUE_UNRESOLVED` |

同一 Excel 行匹配多个实例时，每个实例都必须通过；任一实例失败都会使该行 FAIL。参数不存在与参数值无法解析是两种不同状态，旧 inventory 缺少参数证据时不得按“不存在”处理。

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
    "CRG_source": 7,
    "RS_CFG_EN": 6
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
| `excel.validate_headers` | JSON 布尔值 | `true` | 是否要求映射表头精确等于九个字段名 |

### 4.2 `columns`

`columns` 必须恰好包含九个字段：`Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`、`RS_CFG_EN`。每个值都是正整数形式的 1-based 列号；也接受只包含十进制数字的 JSON 字符串。列号必须唯一，不要求连续或按字段顺序排列。

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
| `--header-check` | 本次运行显式开启表头校验；GUI 会按复选框状态传入该参数或 `--no-header-check` |
| `--no-header-check` | 本次运行关闭表头校验 |

`--no-header-check` 只应在表头不可控且列映射已经独立确认时使用；它会降低发现错列的能力。

## 5. 工具自带桌面 GUI

### 5.1 启动方式

Windows PowerShell 和 macOS 终端在仓库根目录执行：

```bash
python -m rscheck gui
```

执行 `python -m pip install -e .` 后也可使用安装入口：

```bash
rtl-rs-check-gui
```

macOS 可使用 GUI 验证 Excel 和执行离线 inventory 检查，但 Synopsys NPI collector、`libNPI.so` 和真实 elaborated KDB 在线加载只支持 Linux。

Linux 不建议直接猜测 `DISPLAY` 或硬编码登录时生成的 Xauthority 路径。启动器与 Verdi launcher 共用 X11/Xwayland 会话发现逻辑：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_rscheck_gui.sh --probe-only
bash scripts/launch_rscheck_gui.sh
```

`--probe-only` 只检查有效显示会话，不导入 Tkinter、不启动窗口，也不要求 Verdi 或 license。无参数启动会确认 Tkinter 可导入，然后以前台方式执行 `python -m rscheck gui`。若使用非默认 Python，可设置 `PYTHON_BIN`；SCL Python 3.8 默认会自动 source `/opt/rh/rh-python38/enable`，也可用 `PYTHON_ENABLE` 指定其他 enable 脚本。

### 5.2 “检查配置”页

| 区域 | GUI 字段 | 说明 |
|---|---|---|
| 规格输入 | `Excel / CSV` | `.xlsx`、`.xlsm`、`.csv` 或 `.tsv` 规格路径 |
| 规格输入 | `配置 JSON`、`加载` | 选择配置；“加载”把 sheet、行号及九列映射载入界面 |
| 规格输入 | `工作表` | 工作表名或 1-based 序号；CSV/TSV 不使用 sheet |
| 规格输入 | `表头行`、`数据起始行` | 均为 1-based 正整数，数据起始行必须晚于表头行 |
| 规格输入 | `校验映射表头` | 开启时九个映射列的表头必须与字段名精确一致 |
| Excel 列映射 | 九个列号 | `Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`、`RS_CFG_EN`；均从 1 开始、必须互不重复，其他列忽略 |
| 报告输出 | `JSON` | 必填；结构化检查报告，GUI 也从它读取结果 |
| 报告输出 | `CSV` | 可选；UTF-8 BOM 明细报告，可直接由 Excel 打开 |

数据源使用单选项切换：

- `在线 NPI（elaborated KDB）`：填写 `Collector`、`Elab KDB`，可选填写 `NPI 库目录`、`保存 Inventory`，并设置正整数 `超时（秒）`。
- `离线 Inventory`：只选择已有 `Inventory JSON`。它适合回归和问题复现，但不证明 inventory 与当前 RTL 同步。

在线模式严格构造现有 CLI 的 `--collector ... --elab-db ...` 命令。GUI 没有 RTL、filelist、top、编译选项或 passthrough 输入框，也不会在检查时调用 `vericom/elabcom`；`Elab KDB` 必须是生产流程预先生成的 elaborated KDB，不能是 `work.lib++`。

### 5.3 执行、结果和取消

- `验证 Excel`：在后台运行 `validate --json`，只检查配置、表头、列映射和规格行；成功后切到“检查结果”页并显示 `VALID`。
- `运行 RTL 检查`：在后台运行与当前界面等价的 `check` 命令；返回 `0` 显示 `PASS`，返回 `1` 显示 `FAIL` 和差异，基础设施错误显示 `ERROR`。
- `取消`：终止整组后台进程。Linux 先向 CLI 及 collector 所在进程组发送终止信号，超时后强制结束；Windows 终止完整子进程树。关闭仍在运行的窗口时也会先询问是否取消。
- `打开报告目录`：使用系统文件管理器打开 JSON/CSV 所在目录。

“检查结果”页顶部显示状态、总行数、通过/失败行数、error 和 warning 数；主表显示 Excel 行、`Intf_type`、`position`、`RS_inst`、`RS_CFG_EN`、实例数和 finding 数。选中一行后，下方列出 finding 的级别、代码、实例和说明，证据面板同时显示该行规格及 matched instances 的端口、clk 来源、effective `parameters` 和源文件/行号；再选具体 finding 会切换为它的 expected/actual。由此可同时核对 Excel 标签、具体失败实例和实际参数值。全局 finding 会作为 `GLOBAL` 行显示。“运行日志”页记录实际执行命令、stdout、stderr 和退出码，便于复现 GUI 问题。

GUI 在后台调用同一 CLI，不改变第 2 至 4 节定义的数据语义，也不改变报告 schema 或退出码。完整可见 smoke、100 轮稳定性、10,000 行负载、取消竞态和 VM 在线测试见 [测试指南](TESTING.md) 与 [VM GUI 复现指南](VM_GUI_TEST.md)。

## 6. 先运行 `validate`

`validate` 只检查配置和规格表，不加载 inventory、不启动 NPI，也不检查 RTL。因此它可以确认 `RS_CFG_EN` 映射和表头存在，但不能判断数据格应为空还是应填写 `假门控`；该判断只在 `check` 阶段执行。

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

## 7. 可信离线 inventory 模式

离线模式从已有的 schema v2 inventory 读取 elaborated 层次快照和逐实例 effective 参数，不启动 collector：

```bash
python -m rscheck check \
  --excel specs.xlsx \
  --config rscheck.json \
  --inventory trusted_inventory.json \
  --json-report output/rs_report.json \
  --csv-report output/rs_report.csv
```

`--inventory` 与 `--collector` 互斥。离线模式不能使用 `--elab-db`、`--keep-inventory` 或 `--npi-lib-dir`。

### 7.1 信任边界和新鲜度警告

> **离线 inventory 是受信任输入，不提供 KDB 来源或新鲜度证明。**

schema v2 不记录 RTL commit、elabcom 命令、top、宏、include 路径、KDB 哈希、生成时间或 collector 版本。工具只验证 JSON 结构和部分内部一致性；手工修改、由旧流程生成或已经过期的 inventory 仍可能产生看似正常的 PASS。schema v1 没有逐实例参数证据，会被当前加载器拒绝；不能把其缺失的 `parameters` 当作“模块没有 `RS_CFG_EN`”。

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

### 7.2 inventory 基本结构

```json
{
  "schema_version": 2,
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
          "parameters": {
            "RS_CFG_EN": "0"
          },
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

加载器要求 `schema_version=2`、`positions` 为对象、`found` 为布尔值、`instances` 为数组，并要求每个实例的 `parameters` 为对象、每个参数值只能是 JSON 字符串或 `null`。它还会检查实例 `name/full_name` 一致性和 `full_name` 唯一性。`parameters` 中没有某个键表示 collector 确认该实例没有该参数；键存在且值为 `null` 表示参数存在但 effective 值无法可靠解析。inventory 中任何 `warnings` 都会转换为全局 `NPI_UNRESOLVED` 硬错误，避免不完整采集误报 PASS。

## 8. 构建 NPI collector

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

## 9. 生产 elaborated KDB 输入

### 9.1 接受和拒绝的输入

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

### 9.2 为仓库示例生成 KDB

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

## 10. 在线 `check`

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
6. collector 遍历层次、端口、逐实例 effective parameters 和 clk driver，生成 schema v2 inventory；
7. Python 加载 inventory，执行组、拍数、模块、clk/rst、CRG 和 `RS_CFG_EN` 检查；
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

### 10.1 打开同一个 KDB 的 Verdi GUI

这里启动的是 Verdi GUI，不是第 5 节的 `rscheck` 自带 GUI。需要人工查看 hierarchy、实例或连线时，使用严格的跨设备启动器；它只接受现存 elaborated KDB 目录，并且实际调用固定为 `verdi -elab <KDB>`。

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

完整端到端测试 `scripts/test_vm_verdi_gui.sh` 还支持 `PYTHON_BIN`、`CXX`、`NPI_INC_DIR` 和 `NPI_LIB_DIR`，分别覆盖 Python、C++ 编译器、`npi.h` 所在目录和 `libNPI.so` 所在目录。脚本默认在退出时关闭本次启动的 Verdi；仅在需要保留窗口人工检查时设置 `KEEP_VERDI_GUI=1`。详见 [Verdi GUI 端到端复现指南](VM_GUI_TEST.md)。

## 11. 报告和退出码

### 11.1 控制台

控制台首先输出整体结果，再逐行输出匹配数和 findings：

```text
RESULT: PASS | rows=2 errors=0 warnings=0
[PASS] row 2 OUT_IF | top.u_tile / AAAA_BBB (2/2 instances) RS_CFG_EN=假门控
[PASS] row 3 CTRL_IF | top.u_tile / CTRL_RS (1/1 instances) RS_CFG_EN=假门控
```

### 11.2 JSON 报告

`--json-report` 写 UTF-8 JSON，包含：

- `schema_version`：固定为 `2`；旧 report schema 不包含当前参数证据；
- `summary`：是否通过、总行数、通过/失败行数、error/warning 数；
- `global_findings`：例如 `NPI_UNRESOLVED`、`AMBIGUOUS_GROUP_MATCH`；
- `rows[].spec`：规范化后的九字段规格和源行号，其中 `RS_CFG_EN` 可以是空字符串；
- `rows[].matched_instances`：实例路径、模块、文件/行号、`parameters`、端口连线和 clk 来源证据；`parameters` 的值类型为 `string|null`；
- `rows[].findings`：code、message、expected、actual 等诊断信息。

输出目录不存在时会自动创建。

### 11.3 CSV 报告

`--csv-report` 写 UTF-8 BOM CSV，可直接用 Excel 打开。列包括：

```text
status,row,position,RS_inst,RS_CFG_EN,instance,code,message,expected,actual
```

无 finding 的规格行写一条 `PASS`；有 finding 时每个 finding 写一条记录。
`RS_CFG_EN` 失败沿用通用的 `expected` / `actual` 列，其中包含 Excel 标签、参数存在状态和 effective 参数值；逐实例完整 `parameters` 证据保存在 JSON 报告中。

### 11.4 Python CLI 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 没有 error；可以存在 warning-only finding |
| `1` | RTL/规格检查失败，或出现 fail-closed 全局错误 |
| `2` | 命令参数、配置、规格表、inventory、collector、KDB、NPI 加载或报告输出失败 |

### 11.5 直接运行 collector 的退出码

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

## 12. 常见错误与处理

| 错误或 finding | 常见原因 | 处理建议 |
|---|---|---|
| `header validation failed` | 列号错、表头行错、字段大小写不一致 | 用 `validate` 和 `--json` 检查规范化结果；修正映射，不要优先关闭表头检查 |
| `missing/unknown column mappings` | 九字段映射不完整或拼写错误 | `columns` 必须恰好包含九个规定字段 |
| `column mappings must be unique` | 两个字段映射到同一列 | 调整为互不重复的 1-based 列号 |
| `blank required fields` | 除 `RS_CFG_EN` 外的八个必填字段只填写了一部分 | 补齐八个始终必填字段，或清空整行使其被跳过；不要仅为消除此错误而填写 `RS_CFG_EN` |
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
| `npi_load_design failed` | KDB 损坏、版本不兼容、未完成 elaboration 或 top 错误 | 确认输入是 `elabcom -elab` 产物；用相同 Verdi 流程重新生成 KDB 并验证 top；`work.lib++` 会在启动 collector 前被拒绝 |
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
| `RS_CFG_EN_PARAMETER_MISSING` | Excel 非空，但匹配实例没有 `RS_CFG_EN` 参数 | 无该参数的组应把 Excel 单元格留空，或核对是否匹配了错误模块/实例 |
| `RS_CFG_EN_LABEL_MISMATCH` | 实例有该参数，但 Excel 规范化后的文本不是精确的 `假门控` | 使用文本 `假门控`，不要使用布尔值、数字或别名；参数非零时还会同时报告 value mismatch |
| `RS_CFG_EN_VALUE_MISMATCH` | 实例有 `RS_CFG_EN`，但 effective 字符串不表示数值 `0` | 核对实例 parameter override 和 elaborated KDB；不能仅修改 Excel 标签规避非零或非数值状态 |
| `RS_CFG_EN_VALUE_UNRESOLVED` | collector 找到参数但无法可靠解析 effective 值 | 查看 inventory 中该实例的 `parameters.RS_CFG_EN=null`，检查 NPI/KDB 和参数表达式；该状态 fail-closed |
| `AMBIGUOUS_GROUP_MATCH` | 同一实例同时匹配多个重叠 `RS_inst` 前缀 | 重新设计互不重叠的组前缀 |
| `NPI_UNRESOLVED` | collector 产生 traversal/driver warning | 视为硬错误；检查 KDB、层次和 Netlist driver 信息，不要忽略 |
| `no usable X11 display` | 当前 DISPLAY 不可用，或无法发现/认证其他会话 | 从本地/VNC/XRDP 图形终端运行，或使用带客户端 X server 的 `ssh -Y`；再执行 `--probe-only` |
| `multiple usable X11 displays` | 自动探测到多个有效 DISPLAY | 设置 `GUI_DISPLAY`，必要时同时设置 `GUI_USER` 或 `GUI_XAUTHORITY` |
| `no active gnome-session-binary session` | 使用了仓库旧版或外部旧启动脚本 | 更新仓库；工具 GUI 用 `scripts/launch_rscheck_gui.sh --probe-only`，Verdi 用 `scripts/launch_verdi_gui.sh --probe-only`；当前实现不要求 GNOME |

## 13. 当前边界

- `Intf_type` 仅作为业务标签，不验证 interface 的 input/output 数据链。
- 当前九字段不足以证明每一拍的数据端口按顺序串接，也不检查首拍输入和末拍输出。
- 默认只按数量检查 `step`；只有启用 `require_contiguous_indices` 才检查数字 stage 连续性。
- collector 只收集 `position` 的直接 module children；会穿过 `npiGenScope`，但不会进入已经遇到的普通子模块，也不会穿过 interface/program 等其他层次边界。
- SystemVerilog instance array 名如 `u[0]` 不符合默认 `AAAA_BBB_C0` 风格，需要定制 `suffix_regex` 和前缀策略。
- clk/rst 的复杂表达式不做字符串猜测，统一按 `UNSUPPORTED_CONNECTION` 处理。
- CRG 追踪要求唯一 module cell 来源；顶层输入、多驱动和无法解析的 primitive 网络会 fail-closed。
- `RS_CFG_EN` 只按逐实例 effective 值判定；schema v1 或缺少 `parameters` 的 inventory 不具备所需证据，不能用于当前检查。
- `allow_leaf_signal_match=true` 会放宽层次比较，可能让不同 scope 的同名信号误匹配。
- 任意 collector warning 都会升级为 `NPI_UNRESOLVED` error。
- 在线模式只消费现有 elaborated KDB，不接收 RTL/filelist，也不负责 RTL 编译或 elaboration。
- 离线 inventory 是可信快照例外，不包含来源证明；它不能替代新鲜 KDB 的在线采集。
- NPI 真实构建和在线运行依赖特定 Synopsys 版本、平台动态库和 license；离线 Python 测试不能覆盖这些环境因素。

## 14. 推荐操作顺序

1. 固定 RTL commit、宏、库、include 和 top，生成新的 elaborated KDB。
2. 配置九字段的 1-based 列映射，并按 RTL 预期为 `RS_CFG_EN` 留空或填写精确文本 `假门控`。
3. 执行 `validate`，确认 sheet、表头、列和规范化内容。
4. 使用 `--collector + --elab-db` 做在线检查。
5. 同时输出 JSON/CSV，并用 `--keep-inventory` 保存可审计快照。
6. 只有在来源和新鲜度都可证明时，才使用该 inventory 做离线复查。
7. RTL、KDB 或相关配置变化后立即重新在线采集。
8. 需要人工核对时，用 `launch_verdi_gui.sh --elab-db "$ELAB_DB"` 打开同一个 KDB。
