# RTL 打拍例化检查工具

该工具提供命令行（CLI）和自带的 Tkinter 桌面 GUI，把 Excel 中的打拍规格与 NPI 展开后的 RTL 层次做对比，当前检查：

- Excel `position` 简写是否能通过可选映射库解析到实际 RTL 全路径，以及解析后的路径是否存在；
- 同一 `RS_inst` 组（可填写前缀或完整本地例化名）的物理实例是否按模块规则计算出与 `step` 相等的有效拍数；
- 每个实例的模块定义名是否等于 `RS_module`；
- 每个实例的 clk/rst formal port 是否存在、已连接且符合 Excel；
- clk 是否可追到唯一上游模块，且模块定义名等于 `CRG_source`；
- 每行解析出的默认或显式模块规则，以及逐实例 effective parameter 是否满足该规则的 `RS_CFG_EN` 和有效拍贡献条件；
- 重叠的 `RS_inst` 导致同一实例匹配多个 Excel 组时，明确报错。

`Intf_type` 作为业务标签进入报告，不参与 RTL 判定。仅凭当前九个字段无法可靠检查各拍之间的数据串接，详见“当前边界”。

## 文档

- [详细使用文档](docs/USAGE.md)
- [完整测试指南](docs/TESTING.md)
- [完整 RS_inst 本地例化名与 GUI 压测验证记录（2026-07-25）](docs/TEST_RESULTS_FULL_INSTANCE_2026-07-25.md)
- [Position 映射库与 GUI 压测验证记录（2026-07-25）](docs/TEST_RESULTS_POSITION_MAPPING_2026-07-25.md)
- [动态 step 版本验证记录（2026-07-25）](docs/TEST_RESULTS_DYNAMIC_STEP_2026-07-25.md)
- [2026-07-24 GUI 发布验证记录](docs/TEST_RESULTS_2026-07-24.md)
- [rscheck 自带 GUI 与 Verdi GUI 的 VM 复现指南](docs/VM_GUI_TEST.md)
- [Excel 输入模板](examples/RS_Check_Excel_Template.xlsx)

## 工程结构

```text
rscheck/                 Python CLI/GUI、XLSX 解析、检查与报告
npi/                     C++ NPI 采集器及 Makefile
config/rscheck.example.json
examples/                可运行的 CSV 与 SystemVerilog 示例
docs/                    详细使用与测试文档
tests/                   无 NPI license 也能运行的离线测试
scripts/                 rscheck/Verdi GUI 启动器和端到端测试脚本
```

Python 端要求 3.8 或更高版本，CLI 没有第三方运行时依赖；桌面 GUI 使用 Python 标准库 Tkinter，最小化 Linux 安装需另装对应 Python 版本的 Tk 包。支持 `.xlsx`、`.xlsm`、`.csv`、`.tsv`；旧二进制 `.xls` 需先另存为 `.xlsx`。宏不会执行，映射列中的公式会被拒绝，以免读取过期缓存值。

## 列映射

复制并修改 [config/rscheck.example.json](config/rscheck.example.json)。Excel 允许包含任意其他列；工具只读取映射的九列。列号从 1 开始，九列可以任意排列、无需连续，但必须为正数且互不重复：

```json
"columns": {
  "Intf_type": 1,
  "RS_module": 2,
  "RS_inst": 3,
  "position": 4,
  "step": 5,
  "clk": 6,
  "rst": 7,
  "CRG_source": 8,
  "RS_CFG_EN": 9
}
```

也可以在命令行临时覆盖。下面示例假定新表把两个表头移到了第 10、11 列；这两个列号不会与配置中其余七个默认映射冲突：

```bash
python -m rscheck validate \
  --excel specs.xlsx \
  --config config/rscheck.example.json \
  --column Intf_type=10 \
  --column RS_module=11
```

默认会校验表头，防止列号填错。工作表、表头行和数据起始行可在 JSON 中配置，也可通过 `--sheet`、`--header-row`、`--data-start-row` 覆盖。

## Position 映射库

Excel 的 `position` 可以填写便于维护的简写，不必重复很长的 RTL hierarchy。可选的 `position_mappings` 数据库保存“Excel 简写 -> RTL 全路径”的对应关系：

```json
"position_mappings": {
  "tile_core": "top.u_tile",
  "lsu_pipe": "top.u_core.u_lsu.u_pipe"
}
```

解析时按区分大小写的简写精确查找。命中时，内部 `position`、分组、clk/rst 相对路径和 NPI positions 请求全部使用右侧全路径，report v3 的 `spec.position_alias` 保留 Excel 原值；未命中时保持兼容，把 Excel 值直接当作完整 RTL 路径，`position_alias` 为空。因此旧表仍可直接填写 `top.u_tile`。显式映射只改变路径解析，不改变 Excel 列位置、模块规则或 `RS_inst` 匹配语义。

可以在 GUI 的“Position 映射库”页维护，也可以在无图形环境使用 CLI 原子修改配置：

```bash
python -m rscheck position-db set \
  --config config/rscheck.example.json \
  --alias core0_lsu \
  --rtl-path tb_top.dut.u_core0.u_lsu

python -m rscheck position-db list \
  --config config/rscheck.example.json

python -m rscheck position-db resolve \
  --config config/rscheck.example.json \
  --position core0_lsu

python -m rscheck position-db delete \
  --config config/rscheck.example.json \
  --alias core0_lsu
```

`set` 会新增或覆盖同名简写，`delete` 删除简写，`list --json` 和 `resolve --json` 可供脚本消费。数据库修改通过临时文件原子替换写回 `--config` 指定文件，但同一配置文件不支持多个 GUI/CLI 写入者并发合并；维护数据库时应保持单写者，外部修改后先在 GUI 重新加载再继续编辑。需要保留原配置时应先使用项目副本。

## 模块规则库和动态拍数

`module_rules` 保存可选的逐模块覆盖项，不要求为 Excel 中每一种 `RS_module` 建项。工具先按区分大小写的模块名查找显式规则；精确匹配时使用该规则，否则自动使用隐式默认规则：`has_rs_cfg_en=true`、`step_parameters=[]`。因此默认仍会逐实例检查 `RS_CFG_EN=0` 和 Excel `假门控`，并让每个匹配物理实例贡献 `1` 拍：

```json
"module_rules": {
  "rs_pipe": {
    "has_rs_cfg_en": true,
    "step_parameters": ["rs_mode"]
  },
  "rs_plain": {
    "has_rs_cfg_en": false,
    "step_parameters": []
  }
}
```

- `has_rs_cfg_en=true`：该模块每个匹配 RTL 实例都必须具有 effective `RS_CFG_EN`，值必须为数值 `0`，且 Excel 本行 `RS_CFG_EN` 必须精确填写 `假门控`。
- `has_rs_cfg_en=false`：Excel 本行必须留空；RTL 实例若实际仍存在 `RS_CFG_EN`，报 `RS_CFG_EN_PARAMETER_UNEXPECTED`。
- `step_parameters=[]`：每个匹配物理实例贡献 `1` 拍。
- `step_parameters` 非空：所有参数值均可确定时，全部非零贡献 `1`，至少一个为零贡献 `0`。多个参数采用“全部非零”语义。
- 任一参数缺失、值为 `null`、包含 X/Z/`?` 或不是可解析数值时，贡献为未知并 fail-closed，即使另一个参数已知为零也不使用部分证据计算 `step`。
- 未列入 `step_parameters` 的其他 RTL parameter（例如 `WIDTH`）允许存在并保留在报告证据中，但不影响拍数。

显式规则优先于默认规则。例如上面的 `rs_pipe` 使用 `rs_mode` 计算有效拍数；没有同名显式项的模块则沿用默认“每实例 1 拍”。若本意是覆盖默认值，规则键必须与 Excel/RTL `RS_module` 大小写完全一致。

例如 `AAAA_BBB_C0` 至 `AAAA_BBB_C5` 有 6 个物理实例，`rs_mode` 依次为 `1,1,0,1,1,1`，则逐实例贡献为 `1,1,0,1,1,1`，有效拍数是 `5`，Excel `step` 必须填 `5`。`step` 允许为 `0`；但没有任何物理实例匹配时仍报 `GROUP_NOT_FOUND`，不能用 `step=0` 掩盖错误路径或 `RS_inst`。

## 分组规则

一行 Excel 定义一组，组键为 `(解析后的完整 position, RS_inst)`。`position` 可以是 `position_mappings` 中的简写，也可以直接是组成员父 scope 的完整 NPI 路径。`RS_inst` 单元格本身必须非空，可以填写组前缀，也可以填写一个完整的 NPI 本地例化名；完整名不是 `top.u_tile.CTRL_RS_D0` 这样的层次全路径。

实例的本地名字减去 `RS_inst` 后，空 remainder 直接合法，用于完整名输入；非空 remainder 才必须完整匹配 `rtl.suffix_regex`。例如，`RS_inst=AAAA_BBB` 时 `AAAA_BBB_C0`、`AAAA_BBB_C12` 匹配，`AAAA_BBB0`、`AAAA_BBB_C` 不匹配；`RS_inst=CTRL_RS_D0` 时，本地实例 `CTRL_RS_D0` 以空后缀匹配。

空后缀实例仍照常检查 `RS_module`、parameters、`step` 贡献、clk/rst 和 `CRG_source`，但不参与 suffix tag/index 或连续编号检查。非空后缀成员中，不同 suffix stem 仍属于同一组，只产生 warning；数字是否从 0 连续默认不影响 PASS，需要时把 `require_contiguous_indices` 设为 `true`。

匹配仍保留前缀语义：若同一 scope 同时存在 `PFX` 和 `PFX_C0`，填写 `RS_inst=PFX` 会同时匹配空后缀的 `PFX` 和带后缀的 `PFX_C0`。当前没有 exact-only 模式；需要只检查 `PFX` 时，应避免同 scope 中存在也符合该前缀与 suffix 规则的其他实例，或拆分命名。

Excel 中的简单 `clk`/`rst` 名称相对解析后的完整 `position` 解析，例如 `position=tile_core`、映射为 `top.u_tile`、`clk=clk_rs` 时对应 `top.u_tile.clk_rs`。formal port 名默认是 `clk`、`rst`，可通过 `rtl.clk_port`、`rtl.rst_port` 修改。

`RS_CFG_EN` 的列映射和精确表头始终必需，数据单元格则由匹配到的模块规则决定。实例后缀连续性只按具有合法非空数字后缀的物理实例检查，不按空后缀实例或有效拍数检查。

## 工具自带桌面 GUI

这不是 Verdi GUI。它是 `rscheck` 自带的配置、执行和报告查看界面，和 CLI 使用同一套解析、检查及报告逻辑。Windows 和 macOS 可直接启动，用于 Excel 验证和离线 inventory 检查；真实 NPI collector、`libNPI.so` 和 elaborated KDB 在线采集只支持 Linux：

```bash
python -m rscheck gui
```

安装项目后也可使用独立入口：

```bash
rtl-rs-check-gui
```

Linux 必须安装与实际 Python 解释器匹配的 Tkinter 和 X11 工具。Debian/Ubuntu 使用 `python3-tk x11-utils`；RHEL/CentOS 使用匹配版本的 `python3-tkinter xorg-x11-utils`；SCL Python 3.8 使用 `rh-python38-python-tkinter xorg-x11-utils`。先探测显示会话，再启动：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_rscheck_gui.sh --probe-only
bash scripts/launch_rscheck_gui.sh
```

“检查配置”页可选择 Excel/CSV 和配置 JSON，设置工作表、表头行、数据起始行、表头校验，以及九个互不重复的 1-based 列号。“Position 映射库”页可搜索、新建、修改、删除简写与 RTL 全路径并原子保存回当前配置 JSON；“模块规则库”页以相同方式维护 `has_rs_cfg_en` 与逗号分隔的 `step_parameters`。任一数据库存在未保存修改时不能运行检查。RTL 数据源可选：

- **在线 NPI**：填写 collector、Verdi elaborated KDB、可选 NPI 库目录、超时和 inventory 保存路径；
- **离线 Inventory**：选择已有 inventory JSON，用于回归和问题复现。

在线 GUI 与 CLI 的输入边界完全一致：只允许 collector 加 `elabcom` 生成的 elaborated KDB；不提供 RTL、filelist、top 或任意 Verdi 参数透传入口。JSON 报告必填，CSV 可选。“验证 Excel”只验证规格；“运行 RTL 检查”执行完整检查；“取消”会终止后台 CLI 及其 collector 子进程。

“检查结果”页显示 PASS/FAIL、行数、通过/失败数、error/warning 数、解析后的完整 `position`、Excel position 简写、Excel `RS_CFG_EN` 标签、物理匹配实例数和“实际/期望拍”。选择结果行可查看模块规则、逐实例 parameter 状态与 `0/1/?` 贡献，以及 matched instances 的全部 effective `parameters`；选择具体 finding 可查看 expected/actual。“运行日志”页保留实际命令、stdout、stderr 和退出码。

## 先验证 Excel

```bash
python -m rscheck validate \
  --excel examples/specs.csv \
  --config config/rscheck.example.json \
  --sheet 1
```

## 离线检查

已有可信 schema v2 NPI inventory 时，无需 Verdi 环境即可检查并生成 JSON/CSV 报告：

```bash
python -m rscheck check \
  --excel examples/specs.csv \
  --config config/rscheck.example.json \
  --sheet 1 \
  --inventory tests/fixtures/inventory.json \
  --json-report output/rs_report.json \
  --csv-report output/rs_report.csv
```

CSV 报告使用 UTF-8 BOM，可直接用 Excel 打开。NPI inventory 保持 schema v2；当前 JSON report 是 schema v3。每行 `spec.position` 是解析后的完整路径，命中映射时 `spec.position_alias` 保存 Excel 简写，否则为空。report v3 还包含每行 `module_rule`、`step_check.physical_instances`、`step_check.effective_step`、逐实例 `contributions`，以及实例、端口、CRG 和全部 effective `parameters` 证据。CSV 同步包含 `position_alias`、`physical_instances`、`effective_step` 与 `step_contributions`。

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

Verdi GUI 与上面的 `rscheck` 自带 GUI 是两个独立窗口。`rscheck` GUI 用于输入和查看检查结果；需要人工浏览 RTL hierarchy、实例或连线时，再用仓库内的 Verdi 启动器打开同一个 elaborated KDB。启动器只执行 `verdi -elab <KDB>`，不接受 RTL、filelist、`work.lib++`、`-top` 或任意参数透传：

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

两个 Linux GUI 都可以来自本地图形终端、VNC/XRDP 桌面，或客户端已运行 X server 的 `ssh -Y` 会话。GNOME 不是必需条件；KDE、Xfce、MATE、Cinnamon、LXQt 等 X11 桌面均可使用。Wayland 桌面必须启用 Xwayland。Debian/Ubuntu 安装 `x11-utils`，RHEL/CentOS 安装 `xorg-x11-utils`，以提供 `xdpyinfo`、`xprop` 和 `xwininfo`。

应优先由图形会话所属用户运行，并确保该用户能读取仓库和 KDB。root 从其他用户进程恢复 GUI 环境只作为旧部署兼容回退，可能受 SELinux、`hidepid` 和 Xauthority 权限限制。

Verdi 路径可通过 `VERDI_BIN`、`VERDI_HOME`、`NOVAS_INST_DIR` 覆盖；GUI 选择可通过 `GUI_USER`、`GUI_DISPLAY`、`GUI_XAUTHORITY`、`GUI_SESSION_PID` 覆盖。完整测试脚本还支持 `PYTHON_BIN`、`CXX`、`NPI_INC_DIR`、`NPI_LIB_DIR`。详细矩阵和可复制命令见 [Verdi GUI 端到端复现指南](docs/VM_GUI_TEST.md)。

## 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 所有硬检查通过；可能有 warning |
| `1` | RTL 与规格不一致，或存在无法可靠解析的连接/驱动 |
| `2` | 配置、Excel、inventory、NPI 启动或设计加载失败 |

## 当前边界

- 当前版本仅把 `Intf_type` 作为报告标签。若要检查 interface 数据链，需要补充 input/output formal port 映射、首尾预期信号和 stage 顺序定义。
- clk/rst 的复合表达式（concat、运算、mux 等）不会做字符串猜测，而是报 `UNSUPPORTED_CONNECTION`。
- `CRG_source` 默认与第一个唯一上游模块的 `npiDefName` 精确比较；以 NPI module cell 表示的 clock gate/buffer 会被视作 source，primitive gate/buffer 会继续向上追踪。多驱动或顶层输入等无法确定来源的场景会 fail-closed。
- `RS_CFG_EN` 和动态拍数都使用 elaboration 后的逐实例 effective 参数值，不用模块声明默认值替代实例 override；模块规则要求的参数缺失或无法解析时 fail-closed。
- 采集器产生的任何 NPI traversal/driver warning 都按 `NPI_UNRESOLVED` 硬错误处理，避免层次截断后误报 PASS。
- 默认只收集 `position` 下的直接 module children；为了兼容 generate，采集器会穿过非 module 的 generate scope，但不会下钻进已经遇到的普通子模块。
- SystemVerilog instance array 的名字形如 `u[0]`，与 `AAAA_BBB_C0` 这类后缀命名不是同一种分组格式；单个元素可按完整本地名填写，按数组前缀分组则需要定制 suffix 规则。

## 测试

```bash
python -m unittest discover -v
```

自动测试覆盖 XLSX/CSV 解析、九列映射、`step=0`、实例分组、模块规则库、单/多 parameter 动态拍数、未知值 fail-closed、逐实例 `RS_CFG_EN`、schema v2 inventory、schema v3 report、报告导出、GUI 命令构造/生命周期、完整进程组取消和 Linux GUI 启动器。测试总数以当前 `unittest` 输出为准。真实 NPI 编译、effective 参数采集和设计加载必须在有对应 Synopsys 安装和 license 的 Linux 环境中执行。

在已登录图形桌面并安装 Verdi/NPI、当前 shell 已能正常启动 Verdi 的 Linux 设备上，推荐从当前 bootstrap checkout 启动 fresh-checkout 驱动。它会在 VM 本机当前用户的 `$HOME` 下重新克隆仓库，默认锁定克隆时的 `origin/main`，再运行完整 GUI 正向链路；`VM_RUN_BASE` 可用绝对路径改写运行目录的父目录：

```bash
bash scripts/launch_verdi_gui.sh --probe-only
bash scripts/test_vm_fresh_checkout.sh
```

需要精确复现某个版本时传完整提交号。root 通过 SSH 启动时，正式脚本会从已解析出的桌面用户登录环境中自动导入标准 Synopsys license 变量，不依赖固定用户名或硬编码 home。站点仍需要专用初始化文件时，用绝对路径指定可信文件；文件会在隔离进程中加载，输出和 xtrace 不会写入日志，失败会立即终止：

```bash
bash scripts/test_vm_fresh_checkout.sh --commit FULL_SHA
VERDI_ENV_FILE=/path/to/site_env.sh bash scripts/test_vm_fresh_checkout.sh --commit FULL_SHA
```

每次运行的唯一目录、`full_vm_test.log` 和 `artifacts` 路径会在退出时打印；三次 clone 尝试都受 timeout 和强制结束上限约束，所有测试现场均保留且不自动删除。已有 `LM_LICENSE_FILE`/`SNPSLMD_LICENSE_FILE` 优先于自动导入；`VERDI_AUTO_LICENSE_IMPORT=0` 可关闭自动导入。只有当前 checkout 已经可信且位于 VM 本机文件系统时，才直接运行 `bash scripts/test_vm_verdi_gui.sh`。

工具自带 GUI 的可见离线正例/反例、100 轮稳定性、10,000 行负载、取消启动竞态和在线 KDB smoke 命令见 [完整测试指南](docs/TESTING.md) 和 [VM GUI 复现指南](docs/VM_GUI_TEST.md)。

GUI 探测优先使用当前 shell 已可访问的 `DISPLAY`，否则扫描常见桌面/Xwayland 进程和可读的进程环境；不要求固定桌面用户名、GNOME 或 `gnome-session-binary`。`scripts/test_vm_verdi_gui.sh --gui-probe-only` 也可执行同一探测。fresh 驱动最终调用的完整脚本会运行全部 Python 测试、构建 collector、生成新的 `kdb.elab++` 并启动 `verdi -elab`；只有新窗口标题匹配 `VERDI_READY_REGEX`、明确显示已展开的 `top` 才进入 NPI/GUI 检查，其他启动页或无关 Verdi 窗口不能作为就绪证据。测试默认在退出时关闭本次启动的 Verdi，避免遗留进程和 license 占用；人工检查时可显式设置 `KEEP_VERDI_GUI=1`。

## 已验证环境

2026-07-25 已在以下环境重新完成完整本地 `RS_inst` 0.6.0 版本的真实构建、fresh KDB、在线正负例和 GUI 压测：

```text
CentOS 7.9
Python 3.8.13
GCC/G++ 11.2.1
Verdi/NPI O-2018.09-SP2
NPI_PLATFORM=LINUX64
```

[完整 RS_inst 本地例化名与 GUI 压测验证记录（2026-07-25）](docs/TEST_RESULTS_FULL_INSTANCE_2026-07-25.md) 记录了 Windows 185 项回归、Excel 模板复核，以及 VM 上 fresh KDB、真实 NPI 完整名证据、严格 top 窗口、在线正负例、可见 GUI、100 轮和 10,000 行压力的当前实测结果。

[Position 映射库与 GUI 压测验证记录（2026-07-25）](docs/TEST_RESULTS_POSITION_MAPPING_2026-07-25.md) 是完整本地实例名功能之前的 0.5.0 历史基线。

[动态 step 版本验证记录（2026-07-25）](docs/TEST_RESULTS_DYNAMIC_STEP_2026-07-25.md) 是引入 Position 映射库之前的 0.4.0 历史基线。

[RS_CFG_EN 九字段版本验证记录](docs/TEST_RESULTS_RS_CFG_EN_2026-07-24.md) 是引入动态 `step_parameters` 之前的历史基线；其中 102 项测试和压力数字不能代表当前 Position 映射版本。

[2026-07-24 GUI 发布验证记录](docs/TEST_RESULTS_2026-07-24.md) 保留的是引入 `RS_CFG_EN` 前的八字段历史基线，仅用于对照。
