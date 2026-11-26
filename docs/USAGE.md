# RTL 打拍例化检查工具使用手册

本文对应工具版本 `0.14.0`，当前在线采集格式为 inventory v4，检查报告格式为 report v4。

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

#### 用 Python 3.8 直接运行

从仓库根目录执行。下面的 Linux 命令指定解释器，不会误用设备上另一个版本的 `python3`：

```bash
python3.8 --version
python3.8 -m rscheck gui
```

Windows 如果用 Python Launcher 安装了 3.8，可用 `py -3.8 -m rscheck gui`。若使用虚拟环境或自定义安装，也可以用该环境的 Python 可执行文件全路径代替 `python3.8`。

Linux 的 `bash scripts/launch_rscheck_gui.sh` 和 VM 测试脚本会先检查默认 `python3`，若版本低于 3.8 或找不到它，则尝试 `python3.8`。也可显式指定 `PYTHON_BIN=python3.8 bash scripts/launch_rscheck_gui.sh`；显式指定的解释器不满足要求时会提示错误，不会悄悄换用别的版本。

如需区分解释器、Tk 与设计数据的问题，可先跑两个独立自检：

```bash
python3.8 -c 'import sys, tkinter as tk; r=tk.Tk(); r.update(); print(sys.version.split()[0], "Tk", r.tk.call("info","patchlevel"), "mapped", r.winfo_ismapped()); r.destroy()'
python3.8 -m rscheck check --excel examples/RS_Check_Excel_Template.xlsx --config config/rscheck.example.json --inventory examples/inventory.json
```

第一条应能打开再关闭一个窗口并打印 `mapped 1`；第二条不需要显示或 Verdi，应得到 `RESULT: PASS`、2 行、0 错误。在线检查仍沿用已编译的 NPI collector 和 Verdi elaborated KDB，换用 Python 3.8 不改变其输入格式。

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

### 2.1 内部属性与列映射

工具使用以下十个区分大小写的内部属性键。它们只用于 JSON、CLI 和 GUI 中标识属性，不要求 Excel 对应列使用同名表头；实际表头可以是任意业务名称。每个内部属性的 1-based 列号唯一决定从哪一列读取。其中四项决定整行是否检查，其他字段可逐项跳过（见下文）；兼容内部属性 `RS_CFG_EN` 的列号映射始终必需，数据单元格可以为空或填写字面值。注意：`RS_CFG_EN` 是 Excel/config/report 中保留的标签字段名，不是当前匹配的 RTL parameter 名：

| 内部属性 | 含义 | 检查方式 |
|---|---|---|
| `Intf_type` | 该组打拍 interface 的业务标签 | 当前仅写入报告，不参与 RTL 判定 |
| `RS_module` | 打拍实例预期的模块定义名 | 与实例的 NPI `npiDefName` 精确比较 |
| `RS_inst` | 一个或多个本地实例名或组前缀；可由公式生成 | 逐项匹配完整名、合法前缀组或唯一最近名。没有有效项不使用两列 route；只命中一部分时整行失败，不补选 |
| `RS_route` | 可选第一参考名，示例为第 10 列 | 只在非空的 `RS_inst` 所有项都未选中时启用；若同表的 `RS_module`、解析后 `position`、`RS_route` 组合唯一，用整格选一个实例 |
| `RS_route2` | 可选第二参考名，示例为第 11 列；可含汉字和英文 | 同一组合在多行相同时改用本行第二参考名；按整串比较，不拆分、不翻译。其余情况不参与检查 |
| `position` | 配置库中的 position 简写，或该组实例的直接父 scope 完整 NPI 层次路径 | 简写命中后转换为全路径；未命中时兼容完整路径输入；最终必须能在 elaborated 层次中找到 |
| `step` | 当前组预期的有效总拍数 | 按解析后的默认或显式规则汇总逐实例贡献；必须为非负整数 |
| `clk` | 目标 CRG 的时钟输出实际所接的线名 | 从配置的 RS 时钟口反追，只比较这条路径抵达的目标输出的外侧连接 |
| `rst` | 目标 CRG 的复位输出实际所接的线名 | 从配置的 RS 复位口独立反追，比较该目标输出的外侧连接 |
| `CRG_source` | 配置库中的来源简写，或时钟和复位来源模块的完整 RTL instance hierarchy | 简写命中后转换为全路径，未命中时兼容完整路径输入；有界追踪任一分支精确命中该全路径时 CRG 子检查 PASS，否则只报告 warning |
| `RS_CFG_EN` | 该组是否预期为假门控的兼容标签字段 | 去除首尾空白后留空或精确为大写 `NA` 时，仅跳过该行全部 `RS_CFG_EN`/RTL `RS_CRG_EN` 检查；否则 `has_rs_cfg_en=true` 时必须精确填写 `假门控`，`false` 时任意字面内容都不参与标签判定；内容均解析并写入报告 |

一个 `position` 可有多组，每组一行。没有参考名时，同模块、同一解析后层次、等价 `RS_inst` 名字集合不能重复。有参考名的行可留到 RTL 匹配后区分；任意两行最终指向同一物理实例，仍会报告冲突。

### 2.2 任意额外列和非连续列映射

规格表可以包含任意数量的额外列。已映射属性可以映射到乱序、彼此不连续的任意正数列号。列号从 **1** 开始，各属性的列号必须互不重复；未映射列会被忽略，即使其中包含公式也不会参与读取。实际表头文字不参与属性归属判断。

例如，一个 13 列工作表可以这样排列：

| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Owner | Scope label | Review_note | Reset net | Interface role | Gate policy | Clock origin | Instance selector | Pipeline depth | Ticket | Module kind | Clock net | Comment |
| alice | tile_core | checked | rst_n | OUT_IF | 假门控 | crg_core | AAAA_BBB | 5 | HW-101 | rs_pipe | clk_rs | first group |

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

覆盖后仍会检查所有映射的列号是否为正数且互不重复。上表示例的实际表头与内部属性名不同，但默认解析仍会按这些列号得到正确数据。

### 2.3 单元格和行规则

- 映射数据单元格会去除首尾空白。
- 数据行的取舍按下方「空值与 NA」规则；没有映射的业务列不参与读值和警告。
- `RS_inst` 可以填写组前缀，也可以填写完整本地名（例如 `CTRL_RS_D0`），不要填写 `top.u_tile.CTRL_RS_D0` 这样的层次全路径。如果这一格没有有效名字，即使填写了两列 route，也不会选择实例。
- `RS_CFG_EN` 有效值按模块规则核对 RTL `RS_CRG_EN`；留空或 `NA` 则不检查该标签及其对应 RTL 参数。
- `step` 必须是非负整数，接受 `0`、`2` 或 `2.0`。数值公式即使在文件中以 `2E+00` 保存，也按 2 拍处理；文本 `"2e0"`、负数、非整数和布尔值不作为拍数。
- 默认 `excel.validate_headers=false`，不检查或比较映射列的表头文字。只有显式开启严格表头诊断时，才会在 `excel.header_row` 要求已映射列的表头精确等于内部属性名。
- XLSX/XLSM 的映射列可以是公式；读取的是已保存的结果。CSV/TSV 只有文本，其中的 `=...` 不会被计算。
- CSV 优先按 UTF-8 BOM/UTF-8 读取，失败后尝试 GB18030；CSV 可探测逗号、分号或制表符，`.tsv` 固定使用制表符。
- 只有表头、没有数据行时提示没有规格数据；空白分隔行不计数。如果规格行全部被跳过或数据区只有空白行，RTL 检查结果为 `SKIPPED`，不运行 NPI。

### 空值与 NA

| 字段 | 留空（包括纯空白） | 填大写文本 `NA` |
|---|---|---|
| `RS_module`、`Intf_type`、`position`、`step` | 任一项为空即跳过整行，不报警 | 同样跳过整行，不报警 |
| `RS_inst`、`clk`、`rst`、`CRG_source`、`RS_CFG_EN` | 跳过该项，每行每字段报一次 `EXCEL_FIELD_BLANK` | 跳过该项，不报空值警告 |
| 两个可选参考名 | `RS_inst` 有项时，已映射的 `RS_route` 留空按普通空项提示；`RS_route2` 仅在确实需要它区分重复行时提示 | 不报空值警告；需要第二参考名却没有可用值，仍报不能确定实例的错误 |

先判整行，再解析简写、匹配例化、检查拍数。跳过的行不与其他行争用例化，也不进入 collector 请求。前后空白忽略，`0` 是有效拍数；`na`、`N/A` 和 Excel 错误值 `#N/A` 都不是这个标记。公式已保存的空字符串和文本 `NA` 与手填等效；已被明确跳过的行不要求修复其余格的公式。没有映射的参考名列不报空字段警告。

- `rst=NA`、`clk=clk_core`：不检查复位端口，时钟仍从 RS 的时钟端口追到 CRG，核对源端的 `clk_core`。交换两个字段，复位同样可以独立检查。若只填写了 `CRG_source` 而没有期望时钟或复位线名，则只用时钟路径判断该 CRG 是否可达，不比对线名。
- `CRG_source=NA`：没有可比对的目标，源端线名显示未检查；已填写的 `clk`、`rst` 仍检查对应 RS 端口是否存在、是否已连接。没有目标不会导致虚假的线名不匹配。
- `RS_inst=NA`、空字符串或仅含分隔符：即使两列 route 已填写，也只核对层次和模块是否存在，不合并整层实例，不把未知拍数当作 0。没有其他错误时计入“部分检查”，而不是完整通过。文本 `NA` 不报空值警告。

GUI 的“验证 Excel”和“运行 RTL 检查”都会显示检查行数、跳过行数及空字段警告；选择一行可看具体字段。JSON 报告的 `skipped_rows` 列出每个整行跳过的原始行号、字段和原因；`rows[].spec.ignored_fields` 区分其他字段的 `blank` 与 `NA`。CSV 的整行状态为 `SKIPPED`。仅验证 Excel 时可用 `validate --json --include-skipped` 同时取得两类行；不加此选项的 `--json` 保持原有的规格行数组格式。

### 公式单元格

公式可以用在任意映射列。GUI 的“验证 Excel”和“运行 RTL 检查”与命令行使用同一读取方式，无需修改配置。可用[公式样表](../examples/specs_formulas.xlsx)和项目示例配置体验：

| 样表位置 | 公式产生的值 | 检查行为 |
|---|---|---|
| `RS_Check!C2` 的 `RS_inst` | `="AAAA"&"_BBB"` 得到组前缀 `AAAA_BBB` | 用这个前缀匹配，不把公式表达式当成例化名 |
| `RS_Check!E2` 的 `step` | 对“输入”表中的 2 和 3 求和，得到 5 | 与该组实际有效拍数比较；结果 0 也是合法拍数 |
| `RS_Check!D2` 的 `position` | 拼接出 `tile_core` | 继续查位置库，得到 `top.u_tile`；`CRG_source` 简称同理 |
| `RS_Check!C3` / `J3` 的 `RS_inst` / `RS_route` | 前者算出空字符串，后者跨表取到 `CTRL_RS_DO` | 提示一次 `RS_inst` 空值警告；不选择实例或计算拍数。若公式返回文本 `"NA"`，同样不选实例，但不报空值警告 |
| `RS_Check!I2:I3` 的 `RS_CFG_EN` | `IF` 分别返回文本 `假门控`、`NA` | 前者照常检查门控，后者只跳过该行门控检查；`=NA()` 返回的错误 `#N/A` 不等于文本 `NA` |

`RS_inst` 也可以一次算出多个名字。例如 [换行样表](../examples/specs_multiline_inst.xlsx) 的 C4 使用 `="CTRL_RS_D0"&CHAR(10)&"CTRL_RS_DO"`：第一项精确命中，第二项模糊命中同一个实际例化，所以只计 **1 拍**。公式产生的其他分隔符也按手填规则处理。无须把公式粘贴为值。

在 GUI 选中该行，打开**匹配明细**：可以对照公式单元格地址（如 `RS_Check!C4`）、检查器读到的已保存字符串、每项匹配到的实际路径。地址取自用户映射的列，不固定为 C 列。`validate --json` 和 RTL JSON 报告的规格记录用可选字段 `RS_inst_formula_cell` 保存该地址；`RS_inst` 保留供匹配的结果，不导出公式表达式。

**以最后一次保存的计算结果为准。** 修改过输入、使用手动计算或文件由脚本生成时，请在 Excel 或 LibreOffice 中重新计算整本工作簿并保存，再交给检查机。工具不运行公式或宏、不刷新外部链接、不改写原文件，也无法从一个看似有效的旧值（包括某些生成器写入的占位 0）判断它是否过期。检查机本身不需要安装 Office。

成功时，GUI 运行日志或 CLI 标准错误会给出一条 `info[EXCEL_FORMULA_CACHE]`，包含读取的公式格数和示例地址。若映射格缺少结果，例如 `RS_Check!E2 (step): formula has no cached result`，请先重算并保存；若出现 `#REF!`、`#DIV/0!` 等，请先修复表内公式。在参与检查的行中，这两种情况都在 RTL 采集前停止，不会把该格当作 0、空值或豁免。未映射列、未选择的工作表以及未启用严格表头验证的表头不会单独触发这类错误。

## 3. 分组和信号匹配语义

### 3.1 实例组

#### 在一个 RS_inst 单元格填写多个名字

在 `RS_inst` 中，**空格（含全角空格）、制表符 Tab、实际换行、分号 `;` / `；`、逗号 `,` / `，`、顿号 `、` 和冒号 `:` / `：`** 都是分项标志。可以混用；首尾或连续出现的分隔符产生的空项忽略。例如 `AAAA_BBB_C3，： AAAA_BBB_C4` 只提取两个名字，仍是一条 Excel 规格，共用一个 `step`。下划线、点以及 `[0]` 不拆。RTL 转义标识符以反斜杠开始、以空白结束，内部标点是名字的一部分，例如 `\bus;A,1 ; B` 提取 `\bus;A,1` 和 `B`。CSV/TSV 的单元格若包含文件列分隔符或换行，请给整格加双引号，避免错列。

**冒号在不同字段中的意义：** `RS_inst=A::B` 提取 `A`、`B` 两项；启用的 `RS_route=A::B` 或 `RS_route2=通道：A::B` 则按整格查询一次。检查 Excel 时可预览 `RS_inst` 拆分结果；打开旧报告时显示的是生成报告时的依据。

在 Windows Excel 中，编辑 `RS_inst` 单元格时按 **Alt+Enter** 即可一行填一个例化名，不需要额外加分号。粘贴进同一格的多行文本、已计算并保存的 `="AAAA_BBB_C3"&CHAR(10)&"AAAA_BBB_C4"` 也可以；LF、CRLF 和单独的 CR 都按换行处理。仅开启「自动换行」造成的视觉折行、或手打反斜杠和字母 `n`（`\n`）不算分隔。可直接打开[单元格内换行样表](../examples/specs_multiline_inst.xlsx)，在 GUI 点「验证 Excel」查看每一格拆出的名字。

同一格内也可以用行尾反斜杠 `\` 续行，例如：

```text
AAAA_BBB_C3；\
AAAA_BBB_C4
```

这里只提取 `AAAA_BBB_C3`、`AAAA_BBB_C4` 两项。反斜杠自身不参与模糊匹配，也不把上下两行拼成一个名字。换行前允许留空格或制表符；若 `\` 开始的是实际转义标识符（如 `\bus[0]`），则完整保留。GUI「验证 Excel」可看提取后的两项，「运行 RTL 检查」可看每项的匹配路径；原始单元格文本保留在行证据里。

- 每个名字独立匹配，每个前缀组独立检查后缀编号。全部命中则使用并集；部分命中则保留逐项结果、整行 FAIL，不使用任一 route。只有非空 `RS_inst` 的所有项均未命中才尝试参考名。完全没有有效项时只核对层次和模块，不计算整行拍数。
- 一个 RTL 全路径的参数、clk/rst、CRG 和有效拍数只检查一次。即使名字重复，或前缀、完整名和错别字都选中同一实例，也不重复计拍。相反，**不同 Excel 行**争用同一个 RTL 实例仍报 `AMBIGUOUS_GROUP_MATCH`。
- 部分项有结果、其余项无候选或最近并列时，整行 FAIL。已选中项的实例和贡献仍可查，但不会把部分拍数当作整行有效拍数。仅验证 Excel 时可以先看 `RS_inst_items`；完成 RTL 检查后在行证据中查看逐项选择原因、完整路径、重叠和并列信息。公式返回的字符串也按相同规则拆分。

[多分隔符样表](../examples/specs_multi_inst.xlsx)可以直接用示例配置运行：第一条 `AAAA_BBB_C0  AAAA_BBB_X2` 用空格分为两项，前者精确命中 C0（1 拍），后者模糊命中 C2（0 拍），共 1 拍；第二条用连续的中文逗号、冒号分出 C3 和 C4，共 2 拍；第三条用顿号分开的两个拼写指向同一实例，只计 1 拍。

例如，在示例设计的 `tile_core` 填 `AAAA_BBB_X1；AAAA_BBB_X2`，两个名字都没有直接命中。工具把每个**完整输入名字**分别与同层的 `rs_pipe` 实例名比较。两项唯一最近的实例分别是 `top.u_tile.AAAA_BBB_C1` 和 `top.u_tile.AAAA_BBB_C2`，距离都是 1。前者贡献 1 拍、后者贡献 0 拍，故本行 `step=1` 通过。即使第一项直接填对 `AAAA_BBB_C1`，第二项的选择也不变。GUI 结果列显示「2 项模糊命中」或「1 项模糊命中」；选中该行，在「诊断摘要」可按输入顺序看每项的选中路径，以及模糊匹配的候选数和距离。如果某项并列而其他项命中，仍保留所有匹配证据，整行 FAIL，不用部分结果计算总拍数。

#### 每一项的匹配顺序

对每个提取出的名字，先用完整本地名或「前缀＋配置的合法后缀」找组。只要命中成员，就检查整个组；即使命中实例的模块类型、拍数或连线不符合预期，也照常报错，不因此换一个实例。只有组里一个成员都没找到时，才用**该项的原始拼写**，在同一解析后 `position` 的同类型 `RS_module` 中按编辑距离找唯一最近的本地例化名。选中后只检查这一个物理实例，不按新名字再扩成组。无需新配置。

例如，`RS_inst=CTRL_RS_DO` 在示例设计里找不到同名或合法前缀组；唯一最近的是 `top.u_tile.CTRL_RS_D0`（字母 O 与数字 0 相差一个字符）。点选「检查结果」中的该行，能看到「RS_inst 模糊回退」、Excel 原名、选中全路径、编辑距离和候选列表。如果把输入换成 `AAAA_BBB_X`，六个 `AAAA_BBB_C0…C5` 同样接近，没有可用参考名时，工具报 `RS_INST_AMBIGUOUS`，不任选一个。同层无该模块候选时报 `GROUP_NOT_FOUND`，层次本身不存在时仍报 `POSITION_NOT_FOUND`。

**用参考名选单个实例。** GUI「检查配置」可以独立指定 `RS_route`、`RS_route2` 的列号（示例为 J/K），或留空禁用。旧配置未映射新列仍可读取。CLI 可用 `--column RS_route=13 --column RS_route2=14` 临时改列，`--column RS_route2=` 禁用第二列。表头可以是任意业务名称，除非显式开启严格检查。

1. `RS_inst` 没有有效项：忽略两列 route。非空时每项先查完整名／前缀组，再按本项做模糊匹配；全部选中则只用这些结果，部分选中则整行报错、总拍数未确定，两种情况都不看 route。只有所有项都没选中且 `RS_route` 有值时才进入后续选择。
   - 同一工作表、`RS_inst` 有有效项的检查行中，按 **`RS_module`、解析后的 `position` 全路径、`RS_route` 整串** 判断重复。三项组合唯一，用 `RS_route`；有两行或更多相同，只用各行自己的 `RS_route2`。不同简写解析到同一全路径算同层；换模块或换层次不算重复。RTL 候选距离并列本身不触发第二列。
   - 示例：[两行参考名](../examples/specs_route2.csv)使用相同模块、同一层次以及 `RS_route=数据:总线`，两行的 `RS_inst=AAAA_BBB_X` 都未唯一命中。`数据：AAAA_BBB_C2` 选中 `AAAA_BBB_C2`（0 拍），`控制：CTRL_RS_D0` 选中 `CTRL_RS_D0`（1 拍）。
2. 按解析后的 `position` 限定直接子模块（沿用 collector 的 generate scope 展开规则），再按本行 `RS_module` 精确过滤。比较对象是**本地例化名**，不比较父路径、模块定义名、时钟连线或其他层次。参考名按普通文本、区分大小写处理，不支持通配符或正则。半角 `:`、全角 `：` 或连续的 `::` 可以直接填；它们参与整串比较，不会拆成多个名字，诊断中也保留原样。
3. 插入、删除、替换一个字符各计 1 次。选**编辑距离最小且唯一**的物理实例，不扩展成同前缀组，不用 `clk`、`step` 反推。精确匹配距离为 0；无额外分数门槛，较大的唯一最小距离也会被选中，请结合诊断复核。仅选中的实例参与后续参数、有效拍数、clk/rst、CRG 检查。
4. 无候选时报 `RS_ROUTE_NO_CANDIDATES` 或 `RS_ROUTE2_NO_CANDIDATES`，最近并列时报 `RS_ROUTE_AMBIGUOUS` 或 `RS_ROUTE2_AMBIGUOUS`；没有唯一答案时不选实例。需要第二参考名但未映射、为空或为 `NA`，报 `RS_ROUTE2_REQUIRED`，不再用重复的第一列猜。仅当实际用到的参考名公式有错误或无缓存，才报 `ROUTE_CELL_ERROR` 并给出单元格地址。两行选中同一实际例化仍报 `AMBIGUOUS_GROUP_MATCH`。

#### 人工复核实例匹配

运行「RTL 检查」→ 打开「检查结果」→ 选中目标 Excel 行 → 点击右下「匹配明细」。关闭「仅看问题」可检查 PASS 行。先核对输入原文、实际使用的字段、为什么回退和 `position` 简写指向的层次，再逐项比对 RTL 全路径。原文用带引号的文本显示：实际换行显示为 `\n`，原本写入的反斜杠会转义，以便区分。复核不修改选择、检查结果或 Excel。

- **完整名／前缀组：** 列出全部命中的物理实例，逐一注明是完整名相同还是哪个后缀符合配置规则，以及实际模块定义、可用的源文件和行号。发现后缀无效的告警也会列出。
- **`RS_inst` 模糊回退／`RS_route`／`RS_route2`：** 显示同层同模块候选总数、排名、编辑距离、选中全路径，或未选原因。新报告默认列前 5 名；最小距离并列超过 5 个时额外列出并列者，合计最多显示 50 个，并明确显示数量与总数。比较和并列计数始终覆盖全部候选；同距按全路径排序仅为稳定显示，不用于打破并列。旧报告未保存的候选不会补猜。
- **多项与计拍：** 按 Excel 输入顺序分别列证据。首次计入、贡献 0 拍、与前项重叠不重复计拍分别标明。存在未确定项时，可复核其他已选中的实例，但不把部分结果当成整行有效拍数。只做「验证 Excel」时尚无 RTL 匹配明细。

「复制匹配明细」复制当前行。导出 CSV 的 `match_details` 列是相同的中文说明，原有 `instance_match` 仍为结构化数据。JSON 的 `rows[].instance_match` 保留查询及结果，多项见 `items`；常规排名见 `top_candidates`，新增保存的并列候选见 `extra_tied_candidates`。也可在现有 `check` 命令后加 `--match-details` 直接打印，或加 `--match-report output/match-details.txt` 输出所有行（UTF-8，不要求 GUI）。

无需 Verdi 即可试用[参考名示例](../examples/specs_route.csv)。下面这条命令在仓库根目录运行：

```bash
python -m rscheck check --excel examples/specs_route.csv --config config/rscheck.example.json --inventory examples/inventory.json --json-report output/route.json --csv-report output/route.csv
```

两行应分别唯一选中 `top.u_tile.CTRL_RS_D0`（参考名 `CTRL_RS_DO`，距离 1，贡献 1 拍）和 `top.u_tile.AAAA_BBB_C2`（精确同名，贡献 0 拍）。将第一行参考名改成 `AAAA_BBB_C`，会出现六个距离同为 1 的候选，该行应失败。

非空的 `RS_inst` 可以定义一组实例的前缀，也可以就是一个实例的完整 NPI 本地例化名。这里的“本地”指 `position` 直接子实例的名字，不含 `top.u_tile.` 等父层次路径。

从实例本地名中去掉 `RS_inst` 后得到 remainder：

- remainder 为空时直接匹配，不要求 `rtl.suffix_regex` 接受空字符串；这就是完整本地例化名模式。
- remainder 非空时，必须完整匹配 `rtl.suffix_regex`。

默认规则：

```regex
(?P<tag>.+?)(?P<index>[0-9]+)
```

若 `RS_inst=AAAA_BBB`：

- `AAAA_BBB`：匹配，remainder 为空；
- `AAAA_BBB_C0`、`AAAA_BBB_C12`：匹配；
- `AAAA_BBB0`：不匹配，因为缺少非空 tag；
- `AAAA_BBB_C`：不匹配，因为末尾没有数字 index。

例如 RTL 本地例化名为 `CTRL_RS_D0` 时，可以在 Excel 直接填写 `RS_inst=CTRL_RS_D0`；此时 remainder 为空并匹配该实例。

所有匹配实例，包括空后缀实例，都会照常检查 `RS_module`、模块规则要求的 parameters、逐实例 `step` 贡献和 clk/rst 连接，并从规则指定的 clk formal 执行 CRG 有界追踪。没有选中物理实例时，即使 Excel `step=0` 也不通过；诊断区分层次不存在、无候选和最近并列。空后缀实例不具有 suffix tag/index，因此不参与 `SUFFIX_TAG_MISMATCH` 或连续编号检查。默认不要求 index 从 0 连续；启用 `require_contiguous_indices` 后，仅非空 remainder 且成功解析出 index 的成员必须从 `index_base` 开始连续并使用同一个 tag。连续性不按有效 `step` 截短。

完整名输入是对既有前缀匹配的补充，不是 exact-only 模式。若同一 scope 同时存在 `PFX` 和 `PFX_C0`，填写 `RS_inst=PFX` 会同时匹配前者的空 remainder 和后者符合默认 suffix 规则的 `_C0`；两行使用重叠 `RS_inst` 并命中同一实例时仍报 `AMBIGUOUS_GROUP_MATCH`。当前没有只匹配 `PFX` 的显式模式。

### 3.2 Position 简写与完整路径

可选配置 `position_mappings` 是 Excel 简写到 RTL 全路径的数据库。例如：

```json
"position_mappings": {
  "tile_core": "top.u_tile",
  "lsu_pipe": "top.u_core.u_lsu.u_pipe"
}
```

解析每行时按区分大小写的 Excel 文本精确查找：

- 命中 `tile_core` 时，`SpecRow.position`、组键、clk/rst 相对路径和 NPI collector 的 positions 输入均使用 `top.u_tile`；report v4 的 `spec.position_alias` 保留 `tile_core`。
- 未命中时，不把它当作数据库错误，而是直接把 Excel 文本当作完整 RTL 路径；此时 `position_alias` 为空。这保证原来填写 `top.u_tile` 的 Excel 向后兼容。
- 映射只替换 `position`，不会对路径做模糊、前缀或递归映射，也不会改写 `RS_inst`、`clk`、`rst` 等其他单元格。

因此映射拼写错误通常会在在线/离线检查阶段表现为 `POSITION_NOT_FOUND`。检查报告中的完整路径和简写证据，可以区分“映射已命中但 RTL 中路径不存在”和“Excel 值没有命中映射而被当作路径直通”。

**从 DUT 树选择映射值（position 和 CRG_source 通用）：**

1. 在“检查配置”选在线 NPI，填好 collector 和 Verdi elaborated KDB（例如 `top.elab++`）。离线 inventory 只记录检查用的局部信息，不能代替完整 DUT 树。
2. 到对应的映射库输入简称，点击完整路径右侧的“选择 DUT 层次…”。窗口自动读取顶层；用箭头逐层展开，可见各节点的实例名、完整路径、模块定义和类型。不要求先输入正则。兄弟节点多时，每次显示 200 个，双击“显示更多…”或选中它按回车继续。
3. 要过滤时，输入针对**完整路径**的 Python 正则并按回车或点“筛选”。命中项着色，祖先分支保留，方便区分不同父层下同名的实例；“清除筛选”返回同一棵树。例如 `(?i)\.u_crg$` 只找末级为 `u_crg` 的路径（忽略大小写）。筛选使用同一份层次索引，不会重新调用 NPI。
4. 选中一个节点点“使用所选路径”（或回车）；双击分支是展开/收起，双击叶节点可直接选用。只填回发起选择的那一个路径框，不动简称或另一套映射。先“应用修改”，再“保存映射库”，才会写入配置。

#### DUT 层次缓存

**再次打开：** 两个映射页共享当前用户的层次缓存。第一次成功读取 KDB 后，关闭选择窗口、切换映射页、修改 Excel 或简称都不会再次采集；同一 GUI 直接复用内存树。重启工具后，第一次打开会在后台读取本地索引，也不启动 collector。窗口标明「已从 KDB 加载」「命中内存缓存」或「命中本地缓存」。每次打开都从未筛选的顶层开始，不沿用上次选中的节点。

**换库与重载：** 以 KDB 的实际路径、目录身份和设计文件元数据识别版本。切换到从未缓存或已重建的库，会重新采集；切回未改变的旧库可使用它自己的索引。「清除层次缓存并重载」只清当前 KDB，并立刻重新采集，不删除 KDB 或任何映射。「清除筛选」只是恢复整树视图。失败或取消不会把半份索引当成成功缓存；保留的部分加载或截断状态仍会显示。缓存只用于选路径，**不缓存 RTL 检查结果**。

Linux 默认缓存位于 `${XDG_CACHE_HOME:-$HOME/.cache}/rscheck/dut-hierarchy-v1`，Windows 为 `%LOCALAPPDATA%\rscheck\dut-hierarchy-v1`。如需自定义，可在启动前设置 `RSCHECK_HIERARCHY_CACHE_DIR`，例如 `export RSCHECK_HIERARCHY_CACHE_DIR="$HOME/.cache/project-rscheck"`。请选自己可写、位于 KDB 外的私有目录（文件中含设计层次名）。磁盘缓存不可写时，日志会提示，此次 GUI 仍可在内存中复用。

正则使用 `re.search`，默认区分大小写：`u_lsu` 表示包含，`^top\.u_core\.` 限定路径开头。GUI 中留空表示浏览，非空正则限 256 字符；无效正则会提示，零匹配不会改动输入。**完整树浏览不受 500 条搜索上限限制**；正则筛选最多展示前 500 个命中，显示总数和截断提示。若 KDB 部分加载，或 collector 达到 256 层/1,000,000 次 scope 访问的保护上限，会提示索引不完整。后台读取或筛选可取消，不会清空上一次 RTL 检查结果。

### 3.3 clk/rst 连线

formal port 名由当前行匹配到的 `module_rules.<RS_module>.clk_port` 和 `rst_port` 指定。GUI 新建或修改规则时，这两个输入框留空会分别规范化为 `clk`、`rst_n`。这两个 formal port 是 **追踪起点**，Excel 的 `clk`/`rst` 却是 **目标 CRG 输出端实际所接的信号名**；不拿 RS 本地接线或 CRG 输出 formal 名代替。两条路径分别判断。

没有精确显式规则的未知模块使用隐式默认端口 `clk`、`rst_n`。为兼容旧 JSON，已有显式模块规则若缺少 `clk_port/rst_port`，加载时继承历史全局 `rtl.clk_port/rst_port`；规则在内存中始终具有明确端口名，下一次通过 GUI 保存规则库或导出配置时会把两项显式写回。`rtl.clk_port/rst_port` 因此只承担旧配置迁移和 collector 命令兼容，不再覆盖一个已经带端口名的模块规则。

模块规则中留空表示采用默认端口名，不是关闭检查。Excel 的 `rst` 填 `NA` 时则不检查该行复位，留空亦不检查、但提示一次 warning。若 `rst` 已填写期望值而 RTL 没有该 formal port，报告 `RST_PORT_MISSING`。若同一实例的规则所指 clk formal port 确实存在且连接正确，则该行 FAIL，但 finding 只能是 `RST_PORT_MISSING`，不得连带产生 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`。

比较时会忽略信号字符串中的空白，并接受：

1. Excel 值与 NPI 完整连线名完全相同；
2. NPI 连线名等于 `目标 CRG 的父层全路径 + "." + Excel值`。

例如目标为 `top.u_tile.u_crg`，其 `.clk_out(clk_core)` 经过门控成为 `clk_rs`，`.reset_out(rst_core)` 经过 `u_reset` 成为 `rst_rs`。Excel 填 `clk_core` / `rst_core`（或其完整路径 `top.u_tile.clk_core` / `top.u_tile.rst_core`）；填接收端 `clk_rs` / `rst_rs` 是错的。若 `allow_leaf_signal_match=true`，还允许只比较最后一级信号名，但这可能把不同 scope 的同名信号误判为一致，默认关闭。

运行后在「检查结果 → 诊断摘要 → CRG 源端连线 · clk / rst」可逐实例查看路径、输出口、实际信号和期望值。JSON/CSV 的 `source_signal_checks` 和 `source_signal_status` 是两条源线的结果；兼容字段 `crg_source_check` 仅描述时钟能否到达目标，不代表源线已全部验证。

### 3.4 CRG 来源简写与完整路径

可选配置 `crg_source_mappings` 是 Excel CRG_source 简写到 RTL 全路径的数据库。例如：

```json
"crg_source_mappings": {
  "crg_aux": "top.u_tile.u_aux_crg",
  "crg_core": "top.u_tile.u_crg"
}
```

解析每行时按区分大小写的 Excel 文本精确查找。命中时，`SpecRow.crg_source` 和 report/CSV 的 `CRG_source` 保存完整 RTL 路径，`crg_source_alias` 保留 Excel 简写；未命中时直接把 Excel 文本作为完整路径，alias 为空。和 position 映射一样，输入会先去除首尾空白，并去除路径首尾的 `.`；不做模糊、前缀或递归 alias 映射。

`CRG_source` 是必填内部属性，也是有界追踪的目标。对每个匹配 RS 实例，collector 从该实例最终模块规则指定的 `clk_port`、`rst_port` 两个 formal 各自出发，沿 Netlist driver 向上游查找：

1. 分别从 RS 时钟口、复位口穿过 net、assign、concat、slice 和 primitive，找到驱动它的上游模块 output；
2. 记录该上游模块的完整 instance hierarchy、module definition、模块深度和当前 witness path；
3. 若完整实例路径精确等于解析后的 `CRG_source`，检查当前有证据的驱动输出及它的 high connection，不枚举无关输出作为候选；
4. 枚举中间模块的 input：时钟路径精确排除 `clk` 和 `rst_n`；复位路径排除 `clk`，但允许继续沿 `rst_n` 输入追复位；
5. 每一项须同时命中指定实例和该项实际源端连线。已确定的线名不符为 FAIL；若目标未到达、达到深度上限，或目标输出不可解析，给出 warning，不猜测信号名。时钟命中不能代替复位命中。

深度按“经过的上游模块数”计算。RS 模块自身不计数，直接驱动 RS clk 的模块 A 为 depth 1；从 A 的 input 找到的模块 B 为 depth 2。depth N 的模块仍参与目标匹配，但达到配置上限后不再展开其 input。net、assign、concat、slice 和 primitive 不消耗模块深度。`rtl.crg_trace_max_depth` 默认 `16`，范围为 `1..256`；collector 还按对象已访问的最小模块深度终止环路，并对每个 trace 固定限制最多访问 100,000 个 Netlist 对象，因此不会无限递归。

每个实例的结果为：命中目标时 `matched`；完整遍历但未命中时 `not_found`；达到配置/采集深度边界时 `depth_limited`；端口不一致、缺少 v3 trace 或 NPI 无法可靠解析时 `unavailable`。后三者分别产生 warning `CRG_SOURCE_NOT_FOUND`、`CRG_TRACE_DEPTH_LIMIT`、`CRG_TRACE_UNAVAILABLE`。复位同样有独立的 RST 来源诊断。这些 warning 会显示在行和报告中，但不改变 `RowResult.passed`，也不把仅有 warning 的 CLI 退出码从 `0` 改为 `1`。起点端口缺失、未连接以及已知源线名不符仍是错误。

CRG_source 简称或完整路径不会加入 NPI collector 的 positions 请求；collector 仍只接收解析后的完整 `position`。Python runner 另行生成 `RS_module<TAB>clk_port<TAB>rst_port` trace rule 文件。兼容配置键 `rtl.crg_match` 仍可加载，但不参与新的完整 hierarchy 精确匹配。当前在线采集要求 inventory v4；v2/v3 快照没有足够的源输出证据，离线打开会给 `SOURCE_INVENTORY_LEGACY`，不拿以前的 RS 本地线名判为匹配。

### 3.5 模块规则库

配置中的 `module_rules` 是 `RS_module` 到检查策略的可选覆盖数据库。解析顺序固定为：先按区分大小写的模块名查找显式规则；精确匹配时使用显式规则，否则使用隐式默认规则 `has_rs_cfg_en=true`、`step_parameters=[]`、`clk_port="clk"`、`rst_port="rst_n"`。`has_rs_cfg_en` 为兼容既有配置保留，实际控制 RTL `RS_CRG_EN` parameter 检查。因此没有模块专属项时不会导致 `validate` 或 RTL 检查失败；默认行为是要求逐实例 `RS_CRG_EN=0`、Excel/internal `RS_CFG_EN` 精确填写 `假门控`，让每个匹配实例贡献 `1` 拍，并检查 formal ports `clk`、`rst_n`。

```json
"module_rules": {
  "rs_pipe": {
    "has_rs_cfg_en": true,
    "step_parameters": ["rs_mode"],
    "clk_port": "clk_i",
    "rst_port": "reset_n"
  },
  "rs_plain": {
    "has_rs_cfg_en": false,
    "step_parameters": [],
    "clk_port": "clk",
    "rst_port": "rst_n"
  }
}
```

显式规则名、parameter 名和端口名必须是无首尾空白的非空字符串；`step_parameters` 必须是无重复项的 JSON 数组，且不能包含由专门逻辑处理的 `RS_CRG_EN`。`RS_CFG_EN` 不再是保留的 RTL parameter 名；若 RTL 确实另有同名 parameter，可把它作为普通 `step_parameters` 项。显式规则优先于默认规则，其键必须与 Excel/RTL `RS_module` 大小写完全一致。RTL 中未列入 `step_parameters` 的其他 parameters 允许存在，它们仍作为证据写入 inventory/report，但不影响有效拍数。

仓库的 `examples/rtl/rs_example.sv` 提供 `rs_custom` / `CUSTOM_RS` 夹具，formal ports 为 `clock_i`、`reset_ni`。VM 真实 NPI 测试会临时加入对应模块规则，证明它不依赖全局 `clk/rst` 名称。同一文件还提供 `rs_clk_only` / `CLK_ONLY_RS`，其 formal ports 精确为 `clk/d/q`、`clk` 连接 `top.u_tile.clk_rs`，且不存在任何 rst formal port；该夹具专门验证 clk/rst 独立判定。

### 3.6 RTL `RS_CRG_EN` effective 参数与 Excel `RS_CFG_EN` 标签

`RS_CRG_EN` 按组内匹配实例逐一检查，读取 elaboration 后的 effective 参数值，因此实例级 override 优先于模块声明默认值。inventory v2/v3 都把 collector 枚举到的全部 effective parameters 记录在实例 `parameters` 对象中；字符串保留 NPI 值表示，`null` 表示参数存在但无法可靠解析。为兼容现有 Excel、配置和下游报告，标签字段、规则键及 finding code 仍分别使用 `RS_CFG_EN`、`has_rs_cfg_en` 和 `RS_CFG_EN_*`；这些兼容名字不改变实际 RTL parameter 为 `RS_CRG_EN` 的事实。

| 模块规则 | RTL 实例的 `RS_CRG_EN` | Excel/internal `RS_CFG_EN` | 结果 |
|---|---|---|---|
| 任意 | 任意，包括缺失、未知、`0` 或非零 | 去空白后精确大写 `NA` | 跳过该行全部 `RS_CFG_EN`/`RS_CRG_EN` 检查；其他检查继续 |
| `has_rs_cfg_en=true` | 参数存在且为数值 `0` | 精确 `假门控` | 通过该实例检查 |
| `has_rs_cfg_en=true` | 参数缺失 | `假门控` | `RS_CFG_EN_PARAMETER_MISSING` |
| `has_rs_cfg_en=true` | 参数为已知非零值 | `假门控` | `RS_CFG_EN_VALUE_MISMATCH` |
| `has_rs_cfg_en=true` | 参数为 `null`、X/Z 或非法值 | `假门控` | `RS_CFG_EN_VALUE_UNRESOLVED` |
| `has_rs_cfg_en=true` | 任意 | 空白或除 `NA` 外的其他文本 | 另报 `RS_CFG_EN_LABEL_MISMATCH` |
| `has_rs_cfg_en=false` | 参数不存在 | 除 `NA` 外的任意字面内容 | 通过该实例检查；内容仍进入报告 |
| `has_rs_cfg_en=false` | 参数实际存在 | 除 `NA` 外的任意字面内容 | `RS_CFG_EN_PARAMETER_UNEXPECTED`；不另报标签 mismatch |

### 3.7 `step_parameters` 和有效拍数

`step_parameters=[]` 时，每个模块名正确的匹配实例贡献 `1`；这也是没有显式模块规则时的默认行为。数组非空时，对每个实例读取所有列出的 parameter：所有值都能确定时，全部非零贡献 `1`，至少一个为零贡献 `0`。例如 6 个实例的 `rs_mode` 为 `1,1,0,1,1,1`，逐实例贡献为 `1,1,0,1,1,1`，因此有效拍数为 `5`。

多个参数采用“全部非零”语义。例如 `step_parameters=["rs_mode", "pipe_enable"]` 时，两个值都已解析且都非零才贡献 `1`；都已解析且至少一个为零时贡献 `0`。参数缺失报 `STEP_PARAMETER_MISSING`；`null`、X/Z/`?` 或非法值报 `STEP_PARAMETER_VALUE_UNRESOLVED`。任一所需参数未知时该实例贡献就是 `null`，即使另一个参数已知为零也不使用部分证据。任一实例贡献未知时，整行 `effective_step` 为 `null` 并报 `STEP_CALCULATION_UNRESOLVED`。只有全部贡献已知时才求和，并用 `STEP_MISMATCH` 报告与 Excel `step` 的差异。

同一 Excel 行匹配多个实例时，每个实例的模块、parameter 和按模块规则选定的 clk/rst 都必须通过；任一硬错误都会使该行 FAIL。`CRG_source` 当前不在硬检查集合中。

## 4. 配置文件完整说明

配置文件是 UTF-8 JSON，根对象只允许 `excel`、`columns`、`rtl`、`position_mappings`、`crg_source_mappings`、`module_rules` 六个键。普通加载兼容历史配置省略 `position_mappings` 或 `crg_source_mappings`，缺少时按空库处理；GUI“完整导入”严格要求六根全部存在。未知键、错误类型和未知内部属性名都会被拒绝。

完整示例：

```json
{
  "excel": {
    "sheet": "RS_Check",
    "header_row": 1,
    "data_start_row": 2,
    "validate_headers": false
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
    "crg_match": "module",
    "crg_trace_max_depth": 16
  },
  "position_mappings": {
    "tile_core": "top.u_tile",
    "lsu_pipe": "top.u_core.u_lsu.u_pipe"
  },
  "crg_source_mappings": {
    "crg_aux": "top.u_tile.u_aux_crg",
    "crg_core": "top.u_tile.u_crg"
  },
  "module_rules": {
    "rs_pipe": {
      "has_rs_cfg_en": true,
      "step_parameters": ["rs_mode"],
      "clk_port": "clk_i",
      "rst_port": "reset_n"
    },
    "rs_plain": {
      "has_rs_cfg_en": false,
      "step_parameters": [],
      "clk_port": "clk",
      "rst_port": "rst_n"
    }
  }
}
```

### 4.1 `excel`

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `excel.sheet` | 非空字符串或正整数 | `1` | XLSX/XLSM 的工作表名或 1-based 工作表序号；CSV/TSV 没有工作表 |
| `excel.header_row` | 正整数 | `1` | 表头所在行 |
| `excel.data_start_row` | 正整数 | `2` | 第一条数据所在行，必须大于 `header_row` |
| `excel.validate_headers` | JSON 布尔值 | `false` | 可选严格诊断；`true` 时要求映射表头精确等于对应内部属性名，`false` 时表头文字任意 |

0.6.0 及更早版本的示例配置曾显式使用 `true`。升级不会擅自覆盖已有配置中的显式值；若要采用当前默认的任意业务表头语义，请把该值改为 `false`、在 GUI 取消勾选，或对单次 CLI 运行传 `--no-header-check`。

### 4.2 `columns`

`columns` 支持两个可选键 `RS_route`、`RS_route2`，其余九项必须映射：`Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`、`RS_CFG_EN`。这些键区分大小写，但它们不是对 Excel 表头的命名要求。`RS_CFG_EN` 在这里是稳定的标签字段键；对应的 RTL parameter 是 `RS_CRG_EN`。每个值都是正整数形式的 1-based 列号；也接受只包含十进制数字的 JSON 字符串。列号唯一决定属性归属，必须互不重复，不要求连续或按属性顺序排列。

### 4.3 `rtl`

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `rtl.clk_port` | 非空字符串 | `clk` | 旧显式模块规则缺少 `clk_port` 时的兼容继承值；collector 命令仍传递该值，但不按它过滤端口 |
| `rtl.rst_port` | 非空字符串 | `rst` | 旧显式模块规则缺少 `rst_port` 时的兼容继承值；collector 命令仍传递该值，但不按它过滤端口 |
| `rtl.suffix_regex` | 非空正则字符串 | `(?P<tag>.+?)(?P<index>[0-9]+)` | 匹配实例名减去 `RS_inst` 后的非空 remainder；空 remainder 直接合法；正则必须含命名组 `index` |
| `rtl.index_base` | 非负整数 | `0` | 连续 index 检查的起点 |
| `rtl.require_contiguous_indices` | JSON 布尔值 | `false` | 是否强制相同 tag 和连续 index |
| `rtl.allow_leaf_signal_match` | JSON 布尔值 | `false` | 是否允许 clk/rst 仅按最后一级信号名匹配 |
| `rtl.crg_match` | 枚举字符串 | `module` | `module`、`instance` 或 `module_or_instance` |
| `rtl.crg_trace_max_depth` | `1..256` 的整数 | `16` | CRG 上游追踪允许经过的最大模块跳数；RS 模块自身不计数 |

非空 remainder 会由 `suffix_regex` 自动按完整字符串匹配，无需自行添加 `^`/`$`；空 remainder 会绕过该正则并作为合法完整名匹配。`index` 组在运行时必须只产生 ASCII 数字，推荐固定写成 `(?P<index>[0-9]+)`。`tag` 组不是语法必需，但要使用 tag 一致性检查时应保留。

`rtl.clk_port/rst_port` 和 `rtl.crg_match` 保留在配置 schema 中以保证旧文件可加载。新配置应在每个显式 `module_rules` 项中写 `clk_port/rst_port`；`crg_match` 当前不参与结果判定。有界追踪始终按上游模块完整 instance hierarchy 与解析后的 `CRG_source` 精确匹配。

### 4.4 `position_mappings`

`position_mappings` 可省略或设为空 JSON 对象。每个键是 Excel 可以填写的非空简写，每个值是对应的非空 RTL 完整层次路径；名称区分大小写。简写命中后，工具仅向 NPI 请求映射值，不向 collector 发送简写。没有命中的 Excel 值按完整路径直通，因此不需要把所有现有全路径重复登记到数据库。

多个简写可以指向同一完整路径。判断同层或参考名组合重复时使用解析后的全路径，不用原始简写；同一实例不会因为换了简写就能归属两行。

GUI 可视化维护之外，CLI 也提供持久化 CRUD 和单值解析：

```bash
python -m rscheck position-db set \
  --config config/rscheck.example.json \
  --alias core0_lsu \
  --rtl-path tb_top.dut.u_core0.u_lsu

python -m rscheck position-db list \
  --config config/rscheck.example.json \
  --json

python -m rscheck position-db resolve \
  --config config/rscheck.example.json \
  --position core0_lsu \
  --json

python -m rscheck position-db delete \
  --config config/rscheck.example.json \
  --alias core0_lsu
```

`set` 新增或覆盖映射，`delete` 要求简写已经存在，二者都通过临时文件加原子替换写回配置。
原子替换保证单次写入不会留下半份 JSON，但不提供跨进程合并或文件锁；同一配置必须遵守单写者
约束，不要同时用多个 CLI 进程或 GUI 与 CLI 修改它。GUI 保存前会重新读取磁盘配置并只替换当前
数据库，以保留其他配置项的外部修改；若外部修改的是正在编辑的同一数据库，仍应先重新加载。
`resolve` 只查询，不读取 Excel、不加载 inventory、不启动 NPI；未命中时会返回 direct 的完整
路径语义，即 `position_alias` 为空、`position` 等于输入值。

在无 GUI 环境中，可直接对 elaborated KDB 做同样的路径发现。该命令不读取 Excel 或配置，也不修改映射库：

```bash
python -m rscheck hierarchy-search \
  --collector /absolute/path/to/rs_npi_collector \
  --elab-db /absolute/path/to/top.elab++ \
  --regex '^top\.u_tile(_peer)?$' \
  --limit 500 \
  --npi-timeout 180 \
  --json
```

`--regex` 使用 Python 正则并只匹配完整 hierarchy path；`--limit` 范围为 `1..5000`，默认 `500`。collector 的 `--list-hierarchy` 是与 inventory 采集互斥的内部模式，只枚举可作为 position 的 scope，不采集全芯片端口、parameter 或时钟追踪，因此不能把 `--positions`、`--clk-port` 或 trace 参数混入该模式。输入仍只能是 Verdi elaborated KDB。

### 4.5 `crg_source_mappings`

`crg_source_mappings` 可省略或设为空 JSON 对象。每个键是 Excel 可以填写的非空 CRG_source 简写，每个值是对应的非空 RTL 完整层次路径；名称区分大小写。未命中的 Excel 值按完整路径直通，因此旧表无需修改。

映射影响 Excel 解析、GUI/CLI 展示、JSON/CSV 中的 alias+full 证据，并给出 CRG trace 要精确匹配的完整 instance hierarchy；它不会增加 NPI positions。GUI 可视化维护之外，CLI 提供对称的 CRUD 和单值解析：

```bash
python -m rscheck crg-source-db set \
  --config config/rscheck.example.json \
  --alias core0_crg \
  --rtl-path tb_top.dut.u_core0.u_crg

python -m rscheck crg-source-db list \
  --config config/rscheck.example.json \
  --json

python -m rscheck crg-source-db resolve \
  --config config/rscheck.example.json \
  --crg-source core0_crg \
  --json

python -m rscheck crg-source-db delete \
  --config config/rscheck.example.json \
  --alias core0_crg
```

`set/delete` 原子写回配置并遵守与 position 数据库相同的单写者约束。`resolve --json` 命中时返回完整 `CRG_source` 和 Excel `crg_source_alias`；未命中时返回输入路径和空 alias，不读取 Excel、inventory 或 NPI。

### 4.6 `module_rules`

`module_rules` 必须是 JSON 对象，可以为空。每个键是一个需要覆盖默认行为的精确模块定义名。当前格式的每个值使用以下四项；未知键会被拒绝：

| 配置项 | 类型 | 说明 |
|---|---|---|
| `has_rs_cfg_en` | JSON 布尔值 | 兼容配置键；Excel `RS_CFG_EN` 精确为 `NA` 时本行不应用门控检查；其他值下，`true` 要求 RTL `RS_CRG_EN` 存在、值为 0 且 Excel 填 `假门控`，`false` 要求 RTL 不存在 `RS_CRG_EN`，Excel 字段任意字面内容均不参与标签判定但仍进入报告 |
| `step_parameters` | 唯一字符串数组 | 决定逐实例拍贡献的 effective parameters；空数组表示每个实例贡献 1 |
| `clk_port` | 非空字符串 | 该模块的 clk formal port 名；GUI 留空时使用 `clk` |
| `rst_port` | 非空字符串 | 该模块的 rst formal port 名；GUI 留空时使用 `rst_n` |

没有精确显式项的模块自动采用 `has_rs_cfg_en=true`、`step_parameters=[]`、`clk_port="clk"`、`rst_port="rst_n"`。显式项优先，名称比较区分大小写。这里的兼容键 `has_rs_cfg_en` 始终控制 `RS_CRG_EN`，不会回退匹配 RTL `RS_CFG_EN`。

旧配置中的显式规则可以暂时省略 `clk_port/rst_port`：加载器分别从同一文件的 `rtl.clk_port/rst_port` 继承，保持升级前语义。配置一旦由 GUI 保存规则库、导出完整配置或经 `save_config` 写回，这两个键就会在每条规则中显式化。新手写配置应直接包含两项，不要依赖继承。

### 4.7 命令行覆盖

| 参数 | 作用 |
|---|---|
| `--sheet NAME_OR_INDEX` | 覆盖 `excel.sheet`；全数字值解释为 1-based 序号 |
| `--header-row N` | 覆盖表头行 |
| `--data-start-row N` | 覆盖数据起始行 |
| `--column FIELD=INDEX` | 覆盖一个内部属性的 1-based 列号，可重复；`--column RS_route=` 或 `--column RS_route2=` 仅在本次运行关闭相应参考名列 |
| `--header-check` | 本次运行显式开启严格表头诊断，要求映射表头等于内部属性名 |
| `--no-header-check` | 本次运行按列号解析且不比较表头文字；这是默认行为，也可覆盖配置中的 `true` |
| `--crg-trace-max-depth N` | 单次 `check` 覆盖 `rtl.crg_trace_max_depth`；只接受 `1..256` |

`position_mappings`、`crg_source_mappings` 和 `module_rules` 没有仅对单次检查生效的临时覆盖参数，检查始终读取当前配置 JSON。两套映射可分别通过 GUI 或 `position-db`、`crg-source-db` 子命令持久化维护；模块规则通过 GUI 维护。严格表头诊断默认关闭；仅当规格表主动采用标准内部属性名作为表头，并希望用它额外发现错列时，才使用 `--header-check`。

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

RS Studio 左侧的「工作区」用于配置、运行和查结果；「数据库」用于编辑规则及两类路径映射。底部操作栏在六个页面都可用。检查配置按规格与配置、RTL 数据来源、列映射和报告输出分区；缩窄窗口时表单会重新排列，向下滚动即可到达后面的字段。

| 区域 | GUI 字段 | 说明 |
|---|---|---|
| 规格输入 | `Excel / CSV` | `.xlsx`、`.xlsm`、`.csv` 或 `.tsv` 规格路径 |
| 规格输入 | `配置 JSON`、`导入`、`加载`、`导出` | “导入”选择并切换完整配置；“加载”重新读取输入框中的路径；“导出”把当前完整配置另存为副本 |
| 规格输入 | `工作表` | 工作表名或 1-based 序号；CSV/TSV 不使用 sheet |
| 规格输入 | `表头行`、`数据起始行` | 均为 1-based 正整数，数据起始行必须晚于表头行 |
| 规格输入 | `严格校验表头（可选）` | 默认未勾选；勾选后已映射列的表头必须与内部属性名精确一致 |
| 内部属性 -> Excel 列号 | 列号 | `Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`、`RS_CFG_EN`，以及可选 `RS_route`、`RS_route2`（各自留空禁用）；列号从 1 开始、必须互不重复，实际表头任意，其他列忽略 |
| RTL 数据来源 | `CRG Trace 最大层数` | 模块跳数上限，默认 `16`，范围 `1..256`；在线/离线检查都会应用，导入/导出随 `rtl` 根对象保存 |
| 报告输出 | `JSON` | 必填；结构化检查报告，GUI 也从它读取结果 |
| 报告输出 | `CSV` | 可选；UTF-8 BOM 明细报告，可直接由 Excel 打开 |

数据源使用单选项切换：

- `在线 NPI`：填写 `Collector`、`Elab KDB`。需要指定 `NPI 库目录`、`保存 Inventory` 或 `超时（秒）` 时，展开「高级采集设置」。收起不会清空已填的值。
- `离线 Inventory`：选择已有 `Inventory JSON`。首次试用可从仓库根目录启动，保留默认规格表和配置，点击「使用示例 Inventory」，再点击底栏的「运行 RTL 检查」。这个模式适合回归和问题复现，不证明 inventory 与当前 RTL 同步。

在线模式严格构造现有 CLI 的 `--collector ... --elab-db ...` 命令。GUI 没有 RTL、filelist、top、编译选项或 passthrough 输入框，也不会在检查时调用 `vericom/elabcom`；`Elab KDB` 必须是生产流程预先生成的 elaborated KDB，不能是 `work.lib++`。

### 5.3 完整配置导入和导出

可移植配置 JSON 由且仅由以下六个根对象组成：

| 根对象 | 导出来源 | 内容 |
|---|---|---|
| `excel` | 当前 GUI | 工作表、表头行、数据起始行、是否严格校验表头 |
| `columns` | 当前 GUI | 已映射内部属性的 1-based 列号 |
| `rtl` | 当前 GUI 与已加载配置 | suffix 规则、连续编号开关、`crg_trace_max_depth`，以及供旧规则继承的全局 clk/rst 等 RTL 兼容设置 |
| `position_mappings` | 当前 GUI 内存数据库 | 全部 position 简写到完整 RTL 路径的映射 |
| `crg_source_mappings` | 当前 GUI 内存数据库 | 全部 CRG_source 简写到完整 RTL 路径的映射 |
| `module_rules` | 当前 GUI 内存数据库 | 全部模块规则及其 `has_rs_cfg_en`、`step_parameters`、`clk_port`、`rst_port`；前者控制 RTL `RS_CRG_EN` |

“导出”是副本操作。它会先校验当前 GUI 的 Excel 选项和列号，再把上述六个根对象写入另一个 JSON。三个数据库中已经点击“应用新建/修改”但尚未点击各自“保存”按钮的修改也包含在副本中；页面搜索只改变表格显示，不影响导出范围。导出成功后仍使用原来的当前配置路径，三个数据库原有的“未保存”状态也保持不变，原配置文件不会被改写。工具会解析规范化路径并拒绝把导出目标设为当前配置自身；需要覆盖其他已有副本时由系统保存对话框确认。若用户只改了“配置 JSON”路径输入框而没有点击“加载”，导出会在打开保存对话框前拒绝，确保 `rtl` 始终来自界面实际加载的配置。

“导入”采用先校验、后确认、最后整体替换的顺序：

1. 选择候选 JSON 后，工具先完整解析并校验全部六个根对象；无效文件立即拒绝，此时不会询问丢弃本地修改。
2. 候选有效时，若当前 Excel/列号表单与已加载配置不同，先询问是否丢弃；若模块规则库、Position 映射库或 CRG Source 映射库存在未保存修改，再按数据库逐一询问。
3. 所有确认接受后，工具重新读取并校验候选文件，防止其在确认期间被替换；复核仍有效才一次性载入六个根对象、把候选 JSON 切换为当前配置路径，并清除三个数据库的未保存状态。

取消文件选择、首次/复核校验失败或拒绝任一 Excel/数据库丢弃确认，都不会部分导入；当前路径、GUI 字段、三个内存数据库和 dirty 状态保持原样。导入不同于“加载”：导入通过文件选择器选择配置并严格要求六个根对象全部存在；“加载”读取“配置 JSON”输入框当前路径，继续兼容第 4 节所述历史配置省略映射根字段的情况。两者都会执行首次 schema 校验、Excel/数据库确认和二次读取，成功后再更新界面。

`excel.sheet` 的 JSON 类型有语义：字符串表示工作表名，正整数表示 1-based 序号。若已加载配置使用字符串形式的纯数字工作表名（例如 `"123"`），GUI 中未编辑该字段时，导出和运行都继续使用字符串名称，不会静默改为序号 `123`；用户主动把工作表字段改成纯 ASCII 数字时，才按序号解析。

Excel/CSV 文件、collector、Elab KDB、NPI 库目录、离线/保存 inventory、JSON/CSV report 和超时等是单次运行输入或输出，不属于这六个配置根对象，也不会随导入/导出迁移。换设备后应在 GUI 中重新选择这些本机路径。

### 5.4 “Position 映射库”页

加载配置后，映射页列出 Excel position 简写和 RTL 完整路径。用户可以搜索、新建、修改、删除当前配置中的对应关系；“应用新建/修改”只更新 GUI 内存，“保存映射库”才原子写回当前配置 JSON 并重新加载校验。

简写和完整路径都必须为非空文本，简写不能重复。存在未保存映射时，“验证 Excel”和“运行 RTL 检查”都会被阻止；切换配置或关闭窗口也会先确认是否丢弃更改。保存后可直接用 Excel 中的简写运行，CLI/NPI 获得的是映射后的完整路径。

点击“选择 DUT 层次…”即可逐层浏览或用正则过滤。选定路径、关闭或取消都不自动修改数据库；导出的配置只含保存的映射，不含本机层次缓存及正则。

### 5.5 “CRG Source映射库”页

加载配置后，该页列出 Excel CRG_source 简写和 RTL 完整路径，支持搜索、新建、修改、删除和原子保存。操作、dirty 阻断、切换配置确认及保存冲突处理与 Position 映射库一致。

完整路径框旁同样提供“选择 DUT 层次…”。例如要给 `crg_core` 选源模块，可展开 `top → u_tile → u_crg`，或筛选 `^top\.u_tile\.u_crg$`，确认完整路径后选用。应选 **CRG 模块实例**，不是它的时钟或复位信号。

命中映射后，GUI 结果表、JSON report 和 CSV 同时显示或保存 `简称 -> 完整路径`；未命中时显示直通的完整路径。解析后的完整路径作为有界追踪目标，但不会加入 NPI positions。

### 5.6 “模块规则库”页

加载配置后，规则页列出模块名、是否具有 RTL `RS_CRG_EN`、clk/rst formal port 名和决定拍数的 parameters。配置文件中对应的布尔键仍为 `has_rs_cfg_en`。用户可以搜索规则，或通过“新建/修改”“删除”维护当前配置：

1. `RS_module` 填 RTL module definition 的精确名称；
2. 勾选“有 RS_CRG_EN parameter”或保持未勾选；
3. `clk 端口`、`rst 端口` 填该模块实际 formal port 名；留空时分别自动使用 `clk`、`rst_n`；
4. “决定 step 的 parameters”使用逗号分隔，例如 `rs_mode, pipe_enable`；无控制 parameter 时留空；
5. 点击“应用新建/修改”只修改 GUI 内存；点击“保存规则库”才原子写回当前配置 JSON并重新加载校验。

重复 parameter、`RS_CRG_EN` 被误放入 `step_parameters`、空模块名或含空白的端口名等会在应用时拒绝。存在“未保存”规则时，“验证 Excel”和“运行 RTL 检查”都会被阻止；切换配置或关闭窗口也会先确认是否丢弃更改。旧规则缺少端口键时会先显示继承后的实际值，保存后 JSON 中每条规则都会显式包含端口名。

### 5.7 执行、结果和取消

- `验证 Excel`：在后台运行 `validate --json`，检查配置、列映射和规格行；只有勾选可选严格诊断时才额外比较表头。成功后切到“检查结果”页并显示 `VALID`。
- `运行 RTL 检查`：在后台运行与当前界面等价的 `check` 命令；返回 `0` 显示 `PASS`，返回 `1` 显示 `FAIL` 和差异，基础设施错误显示 `ERROR`。
- `取消`：终止整组后台进程。Linux 先向 CLI 及 collector 所在进程组发送终止信号，超时后强制结束；Windows 终止完整子进程树。关闭仍在运行的窗口时也会先询问是否取消。
- `报告目录`：使用系统文件管理器打开 JSON/CSV 所在目录。

“检查结果”页顶部显示状态、总行数、通过/失败行数、error 和 warning 数；主表显示解析后的完整 position/CRG_source 及各自 Excel 简写、新增的 `CRG Trace` 状态、“匹配实例”和“实际/期望拍”。新 report v4 的 CRG 列显示 `PASS` 或 `WARNING`；旧 report v2/v3 没有该结构时显示 `N/A`。只有 warning 的通过行以 `WARNING` 行状态显示，但仍计入 passed rows，CLI 退出码仍为 `0`。

排查时可先勾选「仅看问题」：只显示失败、有警告的行及全局诊断，顶部六项统计仍针对整份报告。选中明细行后，左下显示诊断项，右下默认「诊断摘要」。使用 `RS_inst` 模糊回退或任一 route 的行可在这里看原字符串、同模块候选数、最小编辑距离，以及选中的完整实例名或无法唯一选中的原因。点击某条诊断可查看该问题的原因；拖动左右分隔条可给证据留出更多空间。

切到「匹配明细」后可逐项核对并复制[人工复核说明](#人工复核实例匹配)。「原始 JSON」保留 `spec`、包含 `clk_port/rst_port` 的 `module_rule`、`step_check`、report v4 的 `crg_source_check` 与逐实例参数、端口、`clock_trace` 及源文件/行号。`clock_trace.modules` 可核对完整 hierarchy、depth 和 witness `path`。点击「复制 JSON」可取得当前证据。全局诊断作为 `GLOBAL` 行显示；只验证 Excel 时显示 `VALID`，不代表已检查 RTL。「运行日志」保留实际 CLI 命令、stdout、stderr 和退出码，并支持复制。

GUI 在后台调用同一 CLI，不改变第 2 至 4 节定义的数据语义、报告或退出码。完整可见 smoke 会实际执行六根配置的导出、导入和往返等价性检查，成功标记为 `config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules`，并输出 `schemas=report-v4/inventory-v4`。CRG 映射专项固定 marker 为 `crg-source-check=pass trace-depth=3 findings=none`；在线深度上限专项固定 marker 为 `crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6`，由 `GUI_CRG_TRACE_DEPTH_ITERATIONS` 控制并写入 `online_gui_crg_trace_depth_limit.log`。端到端脚本对包含该专项在内的十二份 GUI 日志执行配置往返和 schema 门禁。完整命令见 [测试指南](TESTING.md) 与 [VM GUI 复现指南](VM_GUI_TEST.md)。

## 6. 先运行 `validate`

`validate` 只检查配置和规格表，不加载 inventory、不启动 NPI，也不检查 RTL。因此它会确认必需的九项列号及可选的 `RS_route`、`RS_route2` 映射，解析 `position_mappings` 和 `crg_source_mappings`，并在摘要/JSON 中保留两组完整路径与 alias；默认不判断实际表头文字，只有显式开启严格诊断时才比较表头。它不能判断 RTL 路径是否真实存在，也不会在此阶段应用 `has_rs_cfg_en=true` 的精确 `假门控` 标签要求；该判定只在 `check` 阶段执行，`false` 时标签内容始终 don't-care。精确大写 `NA` 的门控豁免也在 `check` 时生效，且不会关闭同一行的其他 RTL 检查。

```bash
python -m rscheck validate \
  --excel specs.xlsx \
  --config rscheck.json
```

成功时输出规范化后的组摘要：

```text
VALID: 2 specification row(s)
row 2: tile_core -> top.u_tile / AAAA_BBB module=rs_pipe step=5 CRG_source=crg_core -> top.u_tile.u_crg
row 3: tile_core -> top.u_tile / CTRL_RS_D0 module=rs_pipe step=1 CRG_source=crg_core -> top.u_tile.u_crg
```

输出完整规范化 JSON：

```bash
python -m rscheck validate \
  --excel specs.xlsx \
  --config rscheck.json \
  --json
```

建议在每次调整列映射、sheet、数据起始行或 suffix 规则后先执行 `validate`；若开启了严格表头诊断，调整表头行后也应重新执行。注意：`validate` 成功只说明规格输入合法，不代表 RTL 检查会通过。

## 7. 可信离线 inventory 模式

离线模式从 inventory v4 读取有界时钟和复位追踪证据，不启动 collector；旧 v2/v3 只兼容读取，无法校验源端连线：

```bash
python -m rscheck check \
  --excel specs.xlsx \
  --config rscheck.json \
  --inventory trusted_inventory.json \
  --json-report output/rs_report.json \
  --csv-report output/rs_report.csv
```

`--inventory` 与 `--collector` 互斥。离线模式不能使用 `--elab-db`、`--keep-inventory` 或 `--npi-lib-dir`。

仓库自带的当前格式样例可直接复现两行离线 PASS；它是 schema v4，主 RS 组的 `clock_trace` 与示例 RTL 的 `u_occ -> u_clk_mux -> {u_crg, u_aux_crg}` 三层分支一致：

```bash
python -m rscheck check \
  --excel examples/specs.csv \
  --config config/rscheck.example.json \
  --sheet 1 \
  --inventory examples/inventory.json \
  --json-report output/rs_report.json \
  --csv-report output/rs_report.csv
```

`tests/fixtures/inventory.json` 刻意保留为 schema v2，只用于自动化 legacy 兼容回归，不作为当前格式的示例或 GUI 压测输入。

### 7.1 信任边界和新鲜度警告

> **离线 inventory 是受信任输入，不提供 KDB 来源或新鲜度证明。**

inventory v2/v3 都不记录 RTL commit、elabcom 命令、top、宏、include 路径、KDB 哈希、生成时间或 collector 版本。工具只验证 JSON 结构和部分内部一致性；手工修改、由旧流程生成或已经过期的 inventory 仍可能产生看似正常的 PASS。schema v1 没有逐实例参数证据，会被当前加载器拒绝；不能把其缺失的 `parameters` 当作“模块没有 `RS_CRG_EN`”。

只应在以下条件全部满足时复用 inventory：

- inventory 来自本工具当前版本 collector 的成功在线运行；
- RTL commit、依赖库、宏、include 路径和 elaboration top 未变化；
- elaborated KDB 未重新生成或替换；
- `position`、匹配模块规则的 `clk_port/rst_port` 及相关配置未变化；
- inventory 文件未被手工编辑；
- 使用者明确接受离线快照的信任边界。

任一条件无法确认时，应重新运行在线采集。建议通过 `--keep-inventory` 生成离线文件，并在文件名或外部制品元数据中记录 RTL commit 和 KDB 生成时间，例如：

```text
inventory.top.<rtl-commit>.<kdb-timestamp>.json
```

### 7.2 inventory 基本结构

当前完整可运行样例是 [inventory v4](../examples/inventory.json)。每个实例有 `clock_trace` 和 `reset_trace`（可为 null）；非空 trace 的 `endpoints[]` 记录 `instance`、输出 `port`、实际 `connection`、`depth` 及该输出自己的 `path`。下方片段只说明旧 v3 的模块路径格式，不包含源端线名。

```json
{
  "schema_version": 3,
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
            "RS_CRG_EN": "0"
          },
          "ports": {
            "clk": {
              "connection": "top.u_tile.clk_rs",
              "type": "npiNet"
            },
            "reset_n": {
              "connection": "top.u_tile.rst_n",
              "type": "npiNet"
            }
          },
          "clk_sources": [
            {"instance": "top.u_tile.u_occ", "module": "clk_occ"},
            {"instance": "top.u_tile.u_clk_mux", "module": "clk_mux"},
            {"instance": "top.u_tile.u_crg", "module": "crg_core"}
          ],
          "clock_trace": {
            "clock_port": "clk",
            "max_depth": 16,
            "excluded_inputs": ["clk", "rst_n"],
            "status": "complete",
            "modules": [
              {
                "instance": "top.u_tile.u_occ",
                "module": "clk_occ",
                "depth": 1,
                "path": ["top.u_tile.u_occ"]
              },
              {
                "instance": "top.u_tile.u_clk_mux",
                "module": "clk_mux",
                "depth": 2,
                "path": ["top.u_tile.u_occ", "top.u_tile.u_clk_mux"]
              },
              {
                "instance": "top.u_tile.u_crg",
                "module": "crg_core",
                "depth": 3,
                "path": [
                  "top.u_tile.u_occ",
                  "top.u_tile.u_clk_mux",
                  "top.u_tile.u_crg"
                ]
              }
            ],
            "diagnostics": []
          }
        }
      ]
    }
  },
  "warnings": [],
  "notices": []
}
```

加载器接受 `schema_version=2`、`3` 或 `4`，要求 `positions` 为对象、`found` 为布尔值、`instances` 为数组，并要求每个实例的 `parameters` 为对象、每个参数值只能是 JSON 字符串或 `null`。它还会检查实例 `name/full_name` 一致性和 `full_name` 唯一性。`parameters` 中没有某个键表示 collector 确认该实例没有该参数；键存在且值为 `null` 表示参数存在但 effective 值无法可靠解析。`ports` 可以包含模块的全部 formal ports，checker 再按模块规则选择 clk/rst。

schema v3 的每个实例必须包含 `clock_trace` 键；不需要或无法创建 trace 时可为 `null`，匹配到的 RS 实例若为 `null` 会得到 `CRG_TRACE_UNAVAILABLE` warning。非空 `clock_trace` 严格要求 `clock_port`、`max_depth`、固定 `excluded_inputs=["clk","rst_n"]`、`status`、`modules` 和 `diagnostics`。`status` 只能是 `complete`、`depth_limited`、`unresolved`；每个 module node 的 `depth` 必须在 `1..max_depth`，`path` 末项必须等于该 node 的完整 `instance`。

`clk_sources` 继续作为兼容摘要保存上游模块，但 v3 的判定依据是 `clock_trace`。旧 v2 没有 `clock_trace`：某项同时带非空 `module` 且 `instance` 精确命中目标完整 hierarchy 时可兼容通过，其他情况仅能产生 `CRG_TRACE_UNAVAILABLE` warning，不能证明完整遍历未命中。`warnings` 是必需数组，其中其他 NPI traversal 问题会转换为全局 `NPI_UNRESOLVED` 硬错误；`notices` 是向后兼容的可选数组，partial load 会成为非致命 `NPI_LOAD_PARTIAL` warning。

升级前生成的 offline inventory 可能只采集了当时全局配置指定的两个端口。如果现在为模块配置了不同的 `clk_port/rst_port`，旧文件中没有对应 port key 会产生 `CLK_PORT_MISSING` 或 `RST_PORT_MISSING`；不能把它解释为 RTL 缺口。请用当前 collector 对原 elaborated KDB 重新在线采集后，再把新 inventory 用于离线检查。

## 8. 构建 NPI collector

从仓库根目录执行，避免改变后续命令的工作目录：

```bash
make -C npi VERDI_HOME=/path/to/verdi NPI_PLATFORM=LINUX64
```

默认产物：

```text
npi/build/rs_npi_collector
```

Makefile 使用 C++11，并同时依赖 NPI Language Model 与 NPI L1：

```text
NPI_INC       默认 $VERDI_HOME/share/NPI/inc
NPI_LIB       默认 $VERDI_HOME/share/NPI/lib/$NPI_PLATFORM（找不到时尝试小写平台名）
NPI_L1_INC    默认 $VERDI_HOME/share/NPI/L1/C/inc
NPI_L1_LIB    默认 NPI_LIB（找不到 libnpiL1.so 时尝试小写平台目录）
```

构建会检查 `npi.h`、`npi_L1.h`、`libNPI.so` 和 `libnpiL1.so`，并为两个库目录写入 rpath。

安装布局不符合上述结构时，可直接覆盖 Makefile 的目录和编译器：

```bash
: "${VERDI_HOME:?set VERDI_HOME in the current shell}"
: "${NPI_INC_DIR:?set NPI_INC_DIR in the current shell}"
: "${NPI_LIB_DIR:?set NPI_LIB_DIR in the current shell}"
: "${NPI_L1_INC_DIR:?set NPI_L1_INC_DIR in the current shell}"
: "${NPI_L1_LIB_DIR:?set NPI_L1_LIB_DIR in the current shell}"
make -C npi \
  VERDI_HOME="$VERDI_HOME" \
  NPI_INC="$NPI_INC_DIR" \
  NPI_LIB="$NPI_LIB_DIR" \
  NPI_L1_INC="$NPI_L1_INC_DIR" \
  NPI_L1_LIB="$NPI_L1_LIB_DIR" \
  CXX="${CXX:-g++}"
```

GNU Make 无法可靠表示带空白的目标路径，因此仓库路径、`NPI_INC_DIR`、`NPI_LIB_DIR`、`NPI_L1_INC_DIR` 和 `NPI_L1_LIB_DIR` 不得包含空白；Makefile 会在解析阶段给出明确错误。该约束不影响 `launch_verdi_gui.sh`，后者支持带空格的 KDB 路径。

运行前设置：

```bash
export VERDI_HOME=/path/to/verdi
export NPI_PLATFORM=LINUX64
```

Python runner 会尝试把标准 NPI 平台目录加入 collector 子进程的 `LD_LIBRARY_PATH`。若安装布局非标准，传入的 `--npi-lib-dir` 必须是**直接包含 `libNPI.so` 的目录**，例如：

```bash
--npi-lib-dir /tools/verdi/share/NPI/lib/LINUX64
```

`libnpiL1.so` 若位于不同目录，由构建时的 `NPI_L1_LIB` rpath 定位；运行前可用 `ldd npi/build/rs_npi_collector | grep -E 'libNPI|libnpiL1'` 确认两者都不是 `not found`。

清理构建产物：

```bash
make -C npi clean
```

通常不需要直接运行 collector。直接运行时，它要求一个每行一个 `position` 的文本文件，以及可选的 TAB 分隔 `module<TAB>clock_port<TAB>reset_port` trace rule 文件；设计输入仍只接受 `--elab-db`：

```bash
printf 'rs_pipe\tclk\trst\nrs_custom\tclock_i\treset_ni\n' > trace_rules.tsv
npi/build/rs_npi_collector \
  --positions positions.txt \
  --output inventory.json \
  --trace-rules trace_rules.tsv \
  --trace-max-depth 16 \
  --clk-port clk \
  --rst-port rst_n \
  --elab-db /absolute/path/to/kdb.elab++
```

`--clk-port/--rst-port` 仍是 collector 命令行的必填兼容参数；未提供规则文件时取这两项作全局起点；新规则文件每行指定模块、时钟口、复位口，手工提供的旧两列规则会使用 `--rst-port` 作为复位起点。`--trace-max-depth` 省略时默认 `16`，范围 `1..256`。Python runner 总会根据本次规格与模块数据库生成 trace rule 文件并显式传递配置深度。collector 会枚举每个实例的全部 formal ports，并用 NPI L1 `npi_mod_inst_get_port` 按实例全路径补采 Language Model 未返回的端口；最终由 Python checker 按逐模块 `clk_port/rst_port` 选择 clk/rst 端口并分别判定。

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
2. 解析 position 和 CRG_source 简写；把唯一完整 `position` 写入临时 positions 文件，并按规格中的 `RS_module` 与最终模块规则生成 `module<TAB>clk_port<TAB>rst_port` trace rule 文件；CRG_source 路径本身不会成为 positions；
3. 确认 collector 和 KDB 目录存在；
4. 启动 C++ collector；
5. collector 构造且只构造 `{程序名, "-elab", KDB路径}` 交给 `npi_init/npi_load_design`；若 load 返回 0，则枚举 top，存在可查询 top 时记录 partial notice 并继续，否则退出 11；
6. collector 遍历层次、逐实例全部 formal ports 和 effective parameters，并从各匹配 module 的规则 clk/rst formal 执行有界上游 Netlist 追踪，生成 inventory v4 的 `clock_trace`、`reset_trace` 与输出端证据；
7. Python 加载 inventory，执行组、拍数、模块、逐模块 clk/rst、RTL `RS_CRG_EN` 和 CRG 完整 hierarchy 精确匹配；未命中、深度截断或 trace 不可用只增加 warning；
8. 可选保存 inventory 和 JSON/CSV 报告。

在线专用参数：

| 参数 | 说明 |
|---|---|
| `--collector PATH` | C++ collector 可执行文件；与 `--inventory` 互斥 |
| `--elab-db DIR` | 必需，现存 Verdi elaborated KDB 目录 |
| `--keep-inventory PATH` | 保存本次实时采集结果，供审计或可信离线复用 |
| `--match-details` / `--match-report PATH` | 在终端打印／保存所有规格行的中文匹配明细；在线和离线模式都可用 |
| `--npi-timeout SECONDS` | 可选，collector 超时秒数，必须大于等于 1 |
| `--npi-lib-dir DIR` | 可选，直接包含 `libNPI.so` 的目录 |
| `--crg-trace-max-depth N` | 可选，单次覆盖配置的 CRG 模块跳数上限；`1..256` |

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

控制台首先输出整体结果，再逐行区分物理实例数、有效拍数和 Excel 期望：

```text
RESULT: PASS | rows=2 errors=0 warnings=0
[PASS] row 2 OUT_IF | tile_core -> top.u_tile / AAAA_BBB physical=6 effective=5 expected=5 CRG_source=crg_core -> top.u_tile.u_crg CRG_trace=PASS RS_CFG_EN=假门控
[PASS] row 3 CTRL_IF | tile_core -> top.u_tile / CTRL_RS_D0 physical=1 effective=1 expected=1 CRG_source=crg_aux -> top.u_tile.u_aux_crg CRG_trace=PASS RS_CFG_EN=假门控
```

### 11.2 JSON 报告

`--json-report` 写 UTF-8 JSON，包含：

- `schema_version`：报告和当前 collector 的 inventory 均为 `4`；
- `check_status`：`PASS`、`FAIL`、`PARTIAL`（有行无法定位例化）或 `SKIPPED`（无行需检查）。兼容布尔值 `summary.passed` 表示没有硬错误，不能据此判断是否做过完整检查；
- `summary.rows` 只统计参与检查的行。整行跳过的原行号及原因在顶层 `skipped_rows`；`summary.skipped_rows`、`summary.partial_rows` 有相应记录时给出数量。通过、失败、部分检查三者之和等于参与检查的行数；
- `global_findings`：例如硬错误 `NPI_UNRESOLVED`、`AMBIGUOUS_GROUP_MATCH`，或非致命 warning `NPI_LOAD_PARTIAL`；
- `rows[].instance_match`：`mode` 标识实际采用的字段。多项时 `mode=RS_inst`、`rule=per_item_union`，`query` 保留整格，`items` 按填写顺序保存每项的查询、规则、状态、完整路径 `matched_paths`、新增实例数 `added_count` 和适用的模糊排名；`matched_count` 是整行去重数。单项没有匹配组而进入模糊回退时，`rule=nearest_name_after_no_group_match`，`query` 是提取出的名字；普通完整名/组的 `rule=exact_name_or_prefix_with_configured_suffix`。模糊选择（`RS_inst` 自身、`RS_route` 或 `RS_route2`）的 `selected.full_name` 都是唯一选中路径，未选中时 `selected=null`；`candidate_count`、`best_distance`、`tied_best_count` 和 `top_candidates` 可用于复核。route 回退时 `primary_attempts` 记录此前每项未命中的依据；旧报告可以没有新增证据；
- `rows[].spec`：规范化后的规格字段（含 `RS_inst`、两个参考名、选择策略 `selector_policy` 及三项相同行数 `route_group_size`）和源行号；`position`、`CRG_source` 是各自解析后的完整 RTL 路径，命中映射时 `position_alias`、`crg_source_alias` 保存对应 Excel 原始简写，未命中时为空；兼容标签字段 `RS_CFG_EN` 可以是空字符串；
- `rows[].module_rule`：本行最终采用的规则；始终包含最终生效的 `clk_port/rst_port`；无显式覆盖时记录隐式默认的 `has_rs_cfg_en=true`、空 `step_parameters`、`clk_port="clk"`、`rst_port="rst_n"`；
- `rows[].step_check`：`expected`、`physical_instances`、`effective_step` 和逐实例 `contributions`；贡献为 `0`、`1` 或未知的 `null`；
- `rows[].crg_source_check`：目标完整 hierarchy、行级 `not_run|pass|warning`，以及每个实例的 `matched|not_found|depth_limited|unavailable` evaluation；命中时 `matched` 保存模块、depth 和 witness path；
- `rows[].matched_instances`：实例路径、模块、文件/行号、`parameters`、全部 formal port 连线、兼容字段 `clk_sources`、v3 `clock_trace` 及对应 `step_evaluation`；
- `rows[].findings`：code、message、expected、actual 等诊断信息。

输出目录不存在时会自动创建。

`rows[].spec.CRG_source` 保存解析后的完整目标路径，`rows[].spec.crg_source_alias` 在命中映射时保存 Excel 原值。CRG warning 会增加 `summary.warnings`，但不会把 `rows[].passed` 或 `summary.passed` 改为 `false`。

GUI 为历史问题复现保留 report schema v2/v3 的只读兼容显示；这些旧报告没有 `crg_source_check`，结果表 `CRG Trace` 显示 `N/A`。当前 CLI 新生成的报告始终是 v4。

### 11.3 CSV 报告

`--csv-report` 写 UTF-8 BOM CSV，可直接用 Excel 打开。列包括：

```text
status,row,position,position_alias,CRG_source,crg_source_alias,crg_trace_status,crg_trace_max_depth,crg_trace_evidence,source_signal_checks,source_signal_status,RS_module,RS_inst,RS_CFG_EN,physical_instances,effective_step,step_contributions,instance,code,message,expected,actual,RS_route,RS_route2,instance_match,match_details
```

整行跳过写一条 `SKIPPED`；其余行若有 finding 则每个 finding 写一条记录。无 finding 且无法定位例化的行写 `PARTIAL`；已执行的检查全部通过则写 `PASS`。
`position` 与 `CRG_source` 始终是解析后的 RTL 全路径；`position_alias` 与 `crg_source_alias` 是各自命中映射时的 Excel 简写。`crg_trace_status` 为行级 `pass|warning|not_run`，`crg_trace_max_depth` 保存配置上限，`crg_trace_evidence` 是逐实例 evaluation 的 JSON 文本。`step_contributions` 记录每个实例的 parameter 状态和贡献。逐实例完整证据保存在 JSON report v4 中。

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
| `11` | `npi_load_design` 返回 0，且 NPI Language Model 中没有任何可查询 top |
| `12` | `npi_end` 失败 |
| `13` | inventory 输出失败 |
| `14` | 未预期内部异常 |

通过 Python runner 使用 collector 时，collector 的非零退出会被转换为 Python CLI 的基础设施错误，通常返回 `2`。

## 12. 常见错误与处理

| 错误或 finding | 常见原因 | 处理建议 |
|---|---|---|
| `header validation failed` | 已显式开启严格诊断，但列号、表头行或标准表头大小写不匹配 | 若规格表应使用标准表头，修正列映射或表头；若业务表头本来就自定义，关闭该可选诊断并以列号映射为准 |
| `missing/unknown column mappings` | 九个内部属性映射不完整或属性键拼写错误 | `columns` 需包含九个必需键，可另外配置 `RS_route` 和 `RS_route2` |
| `column mappings must be unique` | 两个内部属性映射到同一列 | 调整为互不重复的 1-based 列号 |
| `ROWS_SKIPPED` / `EXCEL_FIELD_BLANK` | 前者是四项关键字段触发的整行跳过（信息）；后者是本行其他已映射字段留空（警告） | 按工作表、行号和字段补值；明确不检查的字段可填 `NA` |
| `step must be a non-negative integer` | `step` 不是非负整数，或用文本写成科学计数法 | 改为非负整数，如 `0` 或 `2` |
| `duplicate group` | 在同层使用等价的 `RS_inst` 项集合且没有备用参考名可区分行 | 核对重复规格；已有 route 的行在 RTL 选择后检查是否争用同一物理实例。三项组合重复本身不报错，按 `RS_route2` 区分 |
| `sheet ... not found` | sheet 名/序号不对 | 用实际工作表名或 1-based 序号覆盖 `--sheet` |
| `legacy .xls is not supported` | 输入是旧二进制 Excel | 另存为 `.xlsx` 或 CSV |
| `formula has no cached result` | 文件有公式，但没有保存可用的计算结果 | 按工作表、地址和字段找到单元格，用 Excel 或 LibreOffice 重算并保存，无需删除公式 |
| `formula returned Excel error` / `Excel error value` | 映射格包含 `#REF!`、`#N/A` 等真正的 Excel 错误 | 修复公式或引用，然后重算并保存；若要豁免门控，应返回文本 `"NA"` 而非错误值 |
| collector executable not found | 未构建或路径不对 | 执行 `make -C npi ...` 并检查 `npi/build/rs_npi_collector` |
| `npi_L1.h` / `libnpiL1.so` / `libNPI.so` 找不到 | Verdi NPI/L1 安装布局、`VERDI_HOME`、`NPI_PLATFORM` 或目录变量错误 | 设置 `NPI_INC_DIR`、`NPI_LIB_DIR`、`NPI_L1_INC_DIR`、`NPI_L1_LIB_DIR`；用 `ldd` 同时确认 `libNPI.so` 与 `libnpiL1.so` |
| elaborated database not found/must be a directory | `--elab-db` 路径不存在，或传入了普通文件 | 传入现存的 `elabcom -elab` KDB 目录 |
| `unexpected argument` / `unrecognized arguments` | 仍在使用旧 `-- -f/-sv/-lib` 透传 | 删除透传，先在外部流程生成 KDB，再只传 `--elab-db` |
| `npi_init failed` | NPI 环境、license 或版本问题 | 检查 license、Verdi 安装和平台库 |
| `NPI_LOAD_PARTIAL` | `npi_load_design` 报告 elaboration error，但至少一个 top 仍可查询 | 工具继续检查并显示非致命 warning；核对 report 中 position、实例、全部 formal ports 和 parameter 证据，相关缺失仍会使检查失败 |
| `error[NPI_LOAD]` / collector 退出 11 | load 返回 0，且没有任何 top 可查询 | 确认 collector 的 `ldd`、`VERDI_HOME` 与生成 KDB 的工具版本一致；查看 collector stdout、stderr 和 `rs_npi_collectorLog/compiler.log`，必要时重新 elaboration；`work.lib++` 会更早被拒绝 |
| collector timeout | KDB 很大或 NPI 卡住 | 调高 `--npi-timeout`，同时检查 license/存储/设计状态 |
| `POSITION_NOT_FOUND` | 映射后的路径不属于当前 elaborated top；或 Excel 简写未命中数据库，被当作完整路径直通 | 查看 report 的 `position`/`position_alias`，核对映射键、映射值和同一 KDB 中的完整 hierarchy |
| `GROUP_NOT_FOUND` | 非空 `RS_inst` 没有命中完整名或合法前缀组，且该层次下没有可供模糊匹配的同模块实例 | 核对 `position` 和大小写敏感的 `RS_module` |
| `RS_INST_AMBIGUOUS` | `RS_inst` 某项的多个同模块实例并列最近，且尚未通过有效参考名解决 | 查看候选排名；多项仅部分命中必须修正未命中项，不会用 route 补选 |
| `RS_ROUTE_NO_CANDIDATES` | 解析后的层次里没有本行 `RS_module` 的实例 | 核对 Position 映射与模块定义名（大小写敏感） |
| `RS_ROUTE_AMBIGUOUS` | 多个实例的本地名与 `RS_route` 的最小编辑距离相同 | 在「匹配明细」查看并列候选及数量，细化参考名、填写 `RS_inst` 或缩小 `position` |
| `RS_ROUTE2_REQUIRED` / `RS_ROUTE2_AMBIGUOUS` / `RS_ROUTE2_NO_CANDIDATES` | 同模块、同层次、同 `RS_route` 多行需区分，但第二列缺失、最近候选并列或没有候选 | 映射并填写该行的 `RS_route2`，或直接修正 `RS_inst`；工具不会退回共享的 `RS_route` |
| `INSTANCE_SUFFIX_INVALID` | 有前缀相同实例，但非空 suffix 不符合规则 | 修正命名或 regex；空 suffix 本身合法，该 finding 默认是 warning |
| 显式模块规则未生效 | 规则键与 Excel/RTL `RS_module` 大小写不完全一致 | 核对名称；没有精确匹配时工具会正常采用默认规则，而不是把近似名称当作覆盖项 |
| `STEP_PARAMETER_MISSING` | 规则要求的 parameter 未出现在实例证据中 | 核对模块规则拼写、实际 RTL module 和 fresh elaborated KDB |
| `STEP_PARAMETER_VALUE_UNRESOLVED` | step parameter 为 `null`、X/Z/`?` 或非法值 | 核对实例 override/KDB；未知值按 fail-closed 处理 |
| `STEP_CALCULATION_UNRESOLVED` | 至少一个匹配实例的拍贡献未知 | 先解决实例模块不匹配、parameter 缺失或值无法解析，不能只修改 Excel `step` |
| `STEP_MISMATCH` | 已知逐实例贡献之和与 Excel `step` 不同 | 查看 report v4 `step_check.contributions`，核对模块规则、实例 override 和规格拍数 |
| `SUFFIX_TAG_MISMATCH` | 同组实例的 tag 不同 | 核对命名；默认是 warning，连续 index 模式下还会导致硬错误 |
| `STAGE_INDEX_MISMATCH` | index 不连续、不从 `index_base` 开始或 tag 不唯一 | 修正实例命名或关闭不需要的连续检查 |
| `RS_MODULE_MISMATCH` | 实例 definition 与 `RS_module` 不同 | 核对模块替换、wrapper 和规格模块名 |
| `CLK_PORT_MISSING` / `RST_PORT_MISSING` | 当前模块规则指定的对应 formal port 不存在，或复用了只采旧全局端口的历史 offline inventory | 核对 report 的 `module_rule.clk_port/rst_port` 与 RTL；旧 inventory 用当前 collector 重采。两个端口独立判定：clk 存在且已连接、rst 不存在时只允许 `RST_PORT_MISSING`，不得再有 `CLK_PORT_MISSING`/`CLK_UNCONNECTED`；当前没有“跳过 rst”开关 |
| `CLK_UNCONNECTED` / `RST_UNCONNECTED` | formal port 没有 high connection | 修正例化连线 |
| `CLK_CONNECTION_MISMATCH` / `RST_CONNECTION_MISMATCH` | Excel 预期信号与实际连接不一致 | 使用相对目标 CRG 父层的简单名或完整源端线名，并核对该输出的实际连线 |
| `CLK_SOURCE_SIGNAL_UNAVAILABLE` / `RST_SOURCE_SIGNAL_UNAVAILABLE` | 未能取得已到达目标的输出连线或对应起点追踪 | warning；检查 KDB 和采集器，不能用 RS 端的线名补上 |
| `CRG_SOURCE_NOT_FOUND` | v3 trace 完整结束，但没有任何上游模块完整 instance hierarchy 精确匹配目标 | warning；核对 `crg-source-db resolve --json`、目标是否写到模块实例层次，以及 `clock_trace.modules/path` |
| `CRG_TRACE_DEPTH_LIMIT` | 目标可能在 `rtl.crg_trace_max_depth` 之外，或离线 v3 trace 比本次配置更深但已被本次上限截断 | warning；在确认设计规模后适度增大 `1..256` 范围内的上限并重新在线采集 |
| `CRG_TRACE_UNAVAILABLE` | 缺少可靠 trace、trace formal 与模块规则不一致、NPI traversal 未解析，或旧 v2 `clk_sources` 未精确命中 | warning；优先用当前 collector 对当前 KDB 生成 inventory v4，核对 `clock_trace.diagnostics` |
| `RS_CFG_EN_PARAMETER_MISSING` | 本项已填写且非 `NA` 的行的兼容规则键 `has_rs_cfg_en=true`，但实例没有 RTL `RS_CRG_EN` | 修正规则或 RTL/KDB；不能把缺失当作值 0 |
| `RS_CFG_EN_PARAMETER_UNEXPECTED` | 本项已填写且非 `NA` 的行为 `has_rs_cfg_en=false`，但实例实际存在 RTL `RS_CRG_EN` | 修正规则数据库或核对是否匹配错误模块/KDB |
| `RS_CFG_EN_LABEL_MISMATCH` | 本项已填写且非 `NA` 的行为 `has_rs_cfg_en=true`，但 Excel/internal `RS_CFG_EN` 不是精确文本 `假门控` | 使用文本 `假门控`，不要使用布尔值、数字或别名；`has_rs_cfg_en=false` 时任意字面内容都不会产生本 finding |
| `RS_CFG_EN_VALUE_MISMATCH` | 本项已填写且非 `NA` 的行的实例有 `RS_CRG_EN`，但 effective 字符串不表示数值 `0` | 核对实例 parameter override 和 elaborated KDB；不能仅修改 Excel 标签规避非零或非数值状态 |
| `RS_CFG_EN_VALUE_UNRESOLVED` | 本项已填写且非 `NA` 的行中 collector 找到 `RS_CRG_EN` 但无法可靠解析 effective 值 | 查看 inventory 中该实例的 `parameters.RS_CRG_EN=null`，检查 NPI/KDB 和参数表达式；该状态 fail-closed |
| `AMBIGUOUS_GROUP_MATCH` | 同一实例同时匹配多个重叠 `RS_inst`；完整名也可能与较短前缀重叠 | 重新设计互不重叠的组名；当前没有 exact-only 模式 |
| `NPI_UNRESOLVED` | collector 产生层次/端口遍历 warning | 视为硬错误；检查 KDB、层次和 NPI Language/L1 端口信息，不要忽略 |
| `NPI_LOAD_PARTIAL` | KDB 有 elaboration error，但 top 可查询 | severity 为 warning；仅表示允许继续，不能覆盖后续任何硬 finding |
| `no usable X11 display` | 当前 DISPLAY 不可用，或无法发现/认证其他会话 | 从本地/VNC/XRDP 图形终端运行，或使用带客户端 X server 的 `ssh -Y`；再执行 `--probe-only` |
| `multiple usable X11 displays` | 自动探测到多个有效 DISPLAY | 设置 `GUI_DISPLAY`，必要时同时设置 `GUI_USER` 或 `GUI_XAUTHORITY` |
| `no active gnome-session-binary session` | 使用了仓库旧版或外部旧启动脚本 | 更新仓库；工具 GUI 用 `scripts/launch_rscheck_gui.sh --probe-only`，Verdi 用 `scripts/launch_verdi_gui.sh --probe-only`；当前实现不要求 GNOME |

## 13. 当前边界

- `Intf_type` 仅作为业务标签，不验证 interface 的 input/output 数据链。
- 现有规格不足以证明每一拍的数据端口按顺序串接，也不检查首拍输入和末拍输出。
- `step` 是模块规则计算出的有效拍数，不等同于物理实例数；后缀连续性只按具有合法非空 remainder 的 indexed 物理实例检查，空后缀实例不参与编号检查。
- position 与 CRG_source 映射都是区分大小写的精确替换，不做模糊、前缀或多级递归解析；未命中值按完整路径直通。
- collector 只收集 `position` 的直接 module children；会穿过 `npiGenScope`，但不会进入已经遇到的普通子模块，也不会穿过 interface/program 等其他层次边界。
- SystemVerilog instance array 名如 `u[0]` 不符合默认 `AAAA_BBB_C0` 后缀风格；检查单个元素时可直接把 `u[0]` 作为完整本地 `RS_inst`，按数组前缀分组时才需要定制 `suffix_regex` 和前缀策略。
- clk/rst 的复杂表达式不做字符串猜测，统一按 `UNSUPPORTED_CONNECTION` 处理。
- CRG 追踪只遍历 NPI Netlist 可见的上游连接和模块边界；中间模块 input 精确排除 `clk`、`rst_n`，不按语义猜测其他时钟/复位别名。目标必须是完整 instance hierarchy，模块 definition 名或局部实例名不能替代。
- CRG 遍历受 `rtl.crg_trace_max_depth`（`1..256`）、每 trace 100,000 对象预算和环路 visited 状态限制。达到边界或证据不可用只报告 warning，不应被解释为已经证明目标不存在。
- RTL `RS_CRG_EN` 和 `step_parameters` 只按逐实例 effective 值判定；schema v1 或缺少 `parameters` 的 inventory 不具备所需证据，不能用于当前检查。
- `allow_leaf_signal_match=true` 会放宽层次比较，可能让不同 scope 的同名信号误匹配。
- inventory `warnings` 中的 NPI traversal 问题会升级为 `NPI_UNRESOLVED` error；partial-load stderr warning 写入 inventory `notices`，在 report/GUI 中保持非致命 `NPI_LOAD_PARTIAL`。
- 在线模式只消费现有 elaborated KDB，不接收 RTL/filelist，也不负责 RTL 编译或 elaboration。
- 离线 inventory 是可信快照例外，不包含来源证明；它不能替代新鲜 KDB 的在线采集。
- NPI 真实构建和在线运行依赖特定 Synopsys 版本、平台动态库和 license；离线 Python 测试不能覆盖这些环境因素。

## 14. 推荐操作顺序

1. 固定 RTL commit、宏、库、include 和 top，生成新的 elaborated KDB。
2. 配置内部属性的 1-based 列映射；实际表头可任意命名；维护 position 与 CRG_source 两套简写到 RTL 全路径的映射库，并确认 CRG 右侧是目标模块的完整 instance hierarchy。
3. 为端口名不是默认 `clk/rst_n` 或需要改变其他默认行为的 `RS_module` 添加显式规则，填写其 `clk_port/rst_port`；按设计层次选择 `rtl.crg_trace_max_depth`，未配置时使用 `16`。
4. 按最终解析规则填写 Excel/internal `RS_CFG_EN` 标签和预期有效 `step`；默认规则要求标签为 `假门控`、实际 RTL `RS_CRG_EN=0`，显式 `has_rs_cfg_en=false` 时该 Excel 字段任意字面内容均不参与标签判定；只有需要逐行跳过全部门控检查时才填写精确大写 `NA`。每个物理实例默认贡献 1，有效拍可为 0。
5. 执行 `validate`，确认 sheet、列号映射、position/CRG_source 解析结果和规范化内容；只有主动采用标准表头时才开启严格表头诊断。
6. 使用 `--collector + --elab-db` 做在线检查；设计输入只能是 Verdi elaborated KDB。
7. 同时输出 report schema v4 JSON/CSV，并用 `--keep-inventory` 保存 schema v4 快照；检查 `crg_source_check`、matched depth/path 和 warning。
8. 只有在来源和新鲜度都可证明时，才使用该 inventory 做离线复查。旧 v2 未精确 legacy 命中时只能得到 unavailable warning，应优先重采 v4。
9. RTL、KDB、两套路径映射、模块规则或 trace 深度变化后重新在线采集或检查。
10. 需要人工核对时，用 `launch_verdi_gui.sh --elab-db "$ELAB_DB"` 打开同一个 KDB。
