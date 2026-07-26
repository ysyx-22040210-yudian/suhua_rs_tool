# RTL 打拍例化检查工具

该工具提供命令行（CLI）和自带的 Tkinter 桌面 GUI，把 Excel 中的打拍规格与 NPI 展开后的 RTL 层次做对比，当前检查：

- Excel `position` 简写是否能通过可选映射库解析到实际 RTL 全路径，以及解析后的路径是否存在；
- 同一 `RS_inst` 组（可填写前缀或完整本地例化名）的物理实例是否按模块规则计算出与 `step` 相等的有效拍数；
- 每个实例的模块定义名是否等于 `RS_module`；
- 每个实例按其 `RS_module` 规则分别选择并独立检查 clk/rst formal port 是否存在、已连接且符合 Excel；一个端口缺失不会抹掉另一个端口的有效证据；
- `CRG_source` 继续从 Excel 解析并写入报告，但当前暂不参与 PASS/FAIL；
- 每行解析出的默认或显式模块规则，以及逐实例 effective parameter 是否满足该规则的 `RS_CRG_EN` 和有效拍贡献条件；Excel/internal 字段仍使用兼容键 `RS_CFG_EN` 保存“假门控”标签；
- 重叠的 `RS_inst` 导致同一实例匹配多个 Excel 组时，明确报错。

`Intf_type` 作为业务标签进入报告，不参与 RTL 判定。仅凭当前九个字段无法可靠检查各拍之间的数据串接，详见“当前边界”。

## 文档

- [详细使用文档](docs/USAGE.md)
- [完整测试指南](docs/TESTING.md)
- [clk 存在、rst 缺失 finding 隔离与 VM GUI 压测验证记录（2026-07-26）](docs/TEST_RESULTS_CLK_PRESENT_RST_MISSING_2026-07-26.md)
- [NPI partial-load 兼容与 GUI 压测验证记录（2026-07-25）](docs/TEST_RESULTS_NPI_PARTIAL_LOAD_2026-07-25.md)
- [任意 Excel 表头与列号映射 GUI/NPI 验证记录（2026-07-25）](docs/TEST_RESULTS_COLUMN_MAPPING_2026-07-25.md)
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

Python 端要求 3.8 或更高版本，CLI 没有第三方运行时依赖；桌面 GUI 使用 Python 标准库 Tkinter，最小化 Linux 安装需另装对应 Python 版本的 Tk 包。支持 `.xlsx`、`.xlsm`、`.csv`、`.tsv`；旧二进制 `.xls` 需先另存为 `.xlsx`。宏不会执行，映射列中的公式和 Excel 错误值会被拒绝，以免读取过期缓存值或无效内容。

## 列映射

复制并修改 [config/rscheck.example.json](config/rscheck.example.json)。`Intf_type`、`RS_module` 等九个名称是工具内部属性键，不要求 Excel 对应列使用同名表头；实际表头可以是任意业务名称。每个键的 1-based `columns` 值唯一决定该属性从哪一列读取。Excel 允许包含任意其他列；九个映射列可以任意排列、无需连续，但列号必须为正数且互不重复：

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

也可以在命令行临时覆盖。下面示例把内部属性 `Intf_type`、`RS_module` 分别映射到第 10、11 列；这两个列号不会与配置中其余七个默认映射冲突：

```bash
python -m rscheck validate \
  --excel specs.xlsx \
  --config config/rscheck.example.json \
  --column Intf_type=10 \
  --column RS_module=11
```

默认 `validate_headers=false`，解析只按列号映射，不比较实际表头文字。只有用户显式设置 `validate_headers=true`、传入 `--header-check` 或在 GUI 勾选“严格校验表头（可选）”时，工具才额外要求映射列的表头精确等于内部属性名。工作表、表头行和数据起始行可在 JSON 中配置，也可通过 `--sheet`、`--header-row`、`--data-start-row` 覆盖。

从 0.6.0 或更早版本复制的配置可能已经显式写入 `"validate_headers": true`；该值会继续启用严格诊断。使用自定义业务表头时请改为 `false`、在 GUI 取消勾选，或在单次 CLI 运行中传 `--no-header-check`。

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

`module_rules` 保存可选的逐模块覆盖项，不要求为 Excel 中每一种 `RS_module` 建项。工具先按区分大小写的模块名查找显式规则；精确匹配时使用该规则，否则自动使用隐式默认规则：`has_rs_cfg_en=true`、`step_parameters=[]`、`clk_port="clk"`、`rst_port="rst_n"`。`has_rs_cfg_en` 是为兼容既有配置保留的规则键，它控制的实际 RTL parameter 是 `RS_CRG_EN`。因此默认会逐实例检查 `RS_CRG_EN=0` 和 Excel/internal `RS_CFG_EN` 列中的 `假门控`，并让每个匹配物理实例贡献 `1` 拍：

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

- `has_rs_cfg_en=true`：该模块每个匹配 RTL 实例都必须具有 effective `RS_CRG_EN`，值必须为数值 `0`，且 Excel 本行的兼容字段 `RS_CFG_EN` 必须精确填写 `假门控`。
- `has_rs_cfg_en=false`：Excel/internal `RS_CFG_EN` 的任意字面单元格内容都不参与 PASS/FAIL，但解析后的文本仍会写入报告；RTL 实例若实际仍存在 `RS_CRG_EN`，仍报兼容 finding code `RS_CFG_EN_PARAMETER_UNEXPECTED`。
- `step_parameters=[]`：每个匹配物理实例贡献 `1` 拍。
- `step_parameters` 非空：所有参数值均可确定时，全部非零贡献 `1`，至少一个为零贡献 `0`。多个参数采用“全部非零”语义。
- `clk_port`、`rst_port`：该 `RS_module` 实际使用的 formal port 名。GUI 新建或修改规则时留空分别规范化为 `clk`、`rst_n`。
- `RS_CRG_EN` 由专门逻辑处理，不能放入 `step_parameters`；工具不会回退匹配 RTL `RS_CFG_EN`。RTL 中名为 `RS_CFG_EN` 的 parameter 不再具有门控特殊语义，但可作为普通动态拍参数使用。
- 任一参数缺失、值为 `null`、包含 X/Z/`?` 或不是可解析数值时，贡献为未知并 fail-closed，即使另一个参数已知为零也不使用部分证据计算 `step`。
- 未列入 `step_parameters` 的其他 RTL parameter（例如 `WIDTH`）允许存在并保留在报告证据中，但不影响拍数。

显式规则优先于默认规则。例如上面的 `rs_pipe` 使用 `rs_mode` 计算有效拍数；没有同名显式项的模块则沿用默认“每实例 1 拍”。若本意是覆盖默认值，规则键必须与 Excel/RTL `RS_module` 大小写完全一致。

例如 `AAAA_BBB_C0` 至 `AAAA_BBB_C5` 有 6 个物理实例，`rs_mode` 依次为 `1,1,0,1,1,1`，则逐实例贡献为 `1,1,0,1,1,1`，有效拍数是 `5`，Excel `step` 必须填 `5`。`step` 允许为 `0`；但没有任何物理实例匹配时仍报 `GROUP_NOT_FOUND`，不能用 `step=0` 掩盖错误路径或 `RS_inst`。

仓库示例 RTL 还包含 `rs_custom` / `CUSTOM_RS`，其 formal ports 为 `clock_i`、`reset_ni`，用于真实 NPI 回归逐模块端口名和全部 formal port 采集。另有 `rs_clk_only` / `CLK_ONLY_RS`，其 formal ports 精确为 `clk/d/q`，`clk` 连接 `top.u_tile.clk_rs`，且模块完全没有 rst formal port；它用于证明该行应 FAIL 且 finding 只能是 `RST_PORT_MISSING`，不得误报 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`。

## 分组规则

一行 Excel 定义一组，组键为 `(解析后的完整 position, RS_inst)`。`position` 可以是 `position_mappings` 中的简写，也可以直接是组成员父 scope 的完整 NPI 路径。`RS_inst` 单元格本身必须非空，可以填写组前缀，也可以填写一个完整的 NPI 本地例化名；完整名不是 `top.u_tile.CTRL_RS_D0` 这样的层次全路径。

实例的本地名字减去 `RS_inst` 后，空 remainder 直接合法，用于完整名输入；非空 remainder 才必须完整匹配 `rtl.suffix_regex`。例如，`RS_inst=AAAA_BBB` 时 `AAAA_BBB_C0`、`AAAA_BBB_C12` 匹配，`AAAA_BBB0`、`AAAA_BBB_C` 不匹配；`RS_inst=CTRL_RS_D0` 时，本地实例 `CTRL_RS_D0` 以空后缀匹配。

空后缀实例仍照常检查 `RS_module`、parameters、`step` 贡献和 clk/rst；`CRG_source` 仍进入报告但不参与判定。空后缀实例不参与 suffix tag/index 或连续编号检查。非空后缀成员中，不同 suffix stem 仍属于同一组，只产生 warning；数字是否从 0 连续默认不影响 PASS，需要时把 `require_contiguous_indices` 设为 `true`。

匹配仍保留前缀语义：若同一 scope 同时存在 `PFX` 和 `PFX_C0`，填写 `RS_inst=PFX` 会同时匹配空后缀的 `PFX` 和带后缀的 `PFX_C0`。当前没有 exact-only 模式；需要只检查 `PFX` 时，应避免同 scope 中存在也符合该前缀与 suffix 规则的其他实例，或拆分命名。

Excel 中的简单 `clk`/`rst` 名称相对解析后的完整 `position` 解析，例如 `position=tile_core`、映射为 `top.u_tile`、`clk=clk_rs` 时对应 `top.u_tile.clk_rs`。formal port 名由匹配到的模块规则 `clk_port`、`rst_port` 指定；未知模块使用 `clk`、`rst_n`。旧 JSON 中显式模块规则若缺少这两个键，会先继承历史全局 `rtl.clk_port/rst_port`，下一次由 GUI 保存或导出时再显式写入规则，避免升级时改变既有配置含义。

`RS_CFG_EN` 内部属性的列号映射始终必需，但实际 Excel 表头可以任意命名；这个兼容字段保存解析后的用户输入并进入报告，RTL 中实际匹配的 parameter 名为 `RS_CRG_EN`。`has_rs_cfg_en=true` 时数据必须精确为 `假门控`；`false` 时任意字面单元格内容都不参与判定。映射字段中的公式和 Excel 错误值仍受通用解析限制。实例后缀连续性只按具有合法非空数字后缀的物理实例检查，不按空后缀实例或有效拍数检查。

## 工具自带桌面 GUI

这不是 Verdi GUI。它是 `rscheck` 自带的配置、执行和报告查看界面，和 CLI 使用同一套解析、检查及报告逻辑。Windows 和 macOS 可直接启动，用于 Excel 验证和离线 inventory 检查；真实 NPI collector、`libNPI.so`、`libnpiL1.so` 和 elaborated KDB 在线采集只支持 Linux：

当前 `0.9.1` GUI 支持完整配置 JSON 的导入和导出，便于把列映射及两个数据库一起迁移到其他设备。

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

“检查配置”页可选择 Excel/CSV 和配置 JSON，设置工作表、表头行、数据起始行，以及九个内部属性对应的互不重复 1-based 列号。“严格校验表头（可选）”默认未勾选，仅用于用户主动采用标准表头时的附加诊断。“Position 映射库”页可搜索、新建、修改、删除简写与 RTL 全路径并原子保存回当前配置 JSON；“模块规则库”页以相同方式维护兼容规则键 `has_rs_cfg_en`、逗号分隔的 `step_parameters` 和逐模块 `clk_port/rst_port`。端口输入留空时分别使用 `clk`、`rst_n`。任一数据库存在未保存修改时不能运行检查。

“配置 JSON”行的“导入”和“导出”处理的是完整配置，文件根对象必须恰好是 `excel`、`columns`、`rtl`、`position_mappings`、`module_rules`。导出以当前界面的 Excel 选项和九列列号、已加载配置的完整 `rtl`、两个内存数据库生成独立副本；已点击“应用新建/修改”但尚未单独保存的数据库修改也会进入副本，搜索过滤不会删减导出内容。导出不切换当前配置路径、不清除未保存状态，并拒绝把目标选为当前配置自身；若路径输入框已改为另一个尚未加载的文件，也会先拒绝导出，避免把旧 `rtl` 误认为新文件内容。

“导入”严格要求候选文件包含上述五个根对象；手动“加载”用于重读路径输入框，并继续兼容历史配置可省略的根字段。两者都会先读取并校验候选；若当前 Excel/列号表单不同于已加载配置，先确认是否丢弃，再依次确认模块规则库和 Position 映射库的未保存修改。所有确认完成后还会重新读取候选文件，只有复核仍有效才更新界面；导入还会把候选文件切为当前配置路径，成功后两个数据库的未保存状态都会清除。取消文件选择、候选无效或任一确认被拒绝时，当前配置字段、路径和内存数据库都保持不变。配置中原本以字符串保存的纯数字工作表名在 GUI 未编辑时仍按名字导出和运行，不会误转成序号。配置只保存可移植的检查语义；Excel/CSV、collector、Elab KDB、NPI 库、inventory 和报告等本次运行输入/输出路径不属于配置。

RTL 数据源可选：

- **在线 NPI**：填写 collector、Verdi elaborated KDB、可选 NPI 库目录、超时和 inventory 保存路径；
- **离线 Inventory**：选择已有 inventory JSON，用于回归和问题复现。

在线 GUI 与 CLI 的输入边界完全一致：只允许 collector 加 `elabcom` 生成的 elaborated KDB；不提供 RTL、filelist、top 或任意 Verdi 参数透传入口。JSON 报告必填，CSV 可选。“验证 Excel”只验证规格；“运行 RTL 检查”执行完整检查；“取消”会终止后台 CLI 及其 collector 子进程。

“检查结果”页显示 PASS/FAIL、行数、通过/失败数、error/warning 数、解析后的完整 `position`、Excel position 简写、Excel/internal `RS_CFG_EN` 标签、物理匹配实例数和“实际/期望拍”。选择结果行可查看模块规则、逐实例 parameter 状态与 `0/1/?` 贡献，以及 matched instances 的全部 effective `parameters`，其中门控参数证据键为 `RS_CRG_EN`；选择具体 finding 可查看 expected/actual。“运行日志”页保留实际命令、stdout、stderr 和退出码。

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

CSV 报告使用 UTF-8 BOM，可直接用 Excel 打开。NPI inventory 保持 schema v2；当前 JSON report 是 schema v3。每行 `spec.position` 是解析后的完整路径，命中映射时 `spec.position_alias` 保存 Excel 简写，否则为空。report v3 还包含每行最终采用的 `module_rule`（含 `clk_port/rst_port`）、`step_check.physical_instances`、`step_check.effective_step`、逐实例 `contributions`，以及实例、全部 formal ports 和 effective `parameters` 证据。`spec.CRG_source` 仍原样保留，但不参与当前 PASS/FAIL；新 collector 停用 clock source trace，因此在线新采 inventory 的 `clk_sources` 固定为 `[]`。CSV 同步包含 `position_alias`、`physical_instances`、`effective_step` 与 `step_contributions`。inventory 的 `warnings` 表示其余 NPI traversal 证据不完整，会转换为硬错误；可选 `notices` 当前用于记录可继续检查的 partial KDB，并在报告中显示非致命 `NPI_LOAD_PARTIAL` warning。

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
: "${NPI_L1_INC_DIR:?set NPI_L1_INC_DIR in the current shell}"
: "${NPI_L1_LIB_DIR:?set NPI_L1_LIB_DIR in the current shell}"
make -C npi \
  VERDI_HOME="$VERDI_HOME" \
  NPI_INC="$NPI_INC_DIR" \
  NPI_LIB="$NPI_LIB_DIR" \
  NPI_L1_INC="$NPI_L1_INC_DIR" \
  NPI_L1_LIB="$NPI_L1_LIB_DIR"
```

标准 Verdi 布局下，NPI L1 头文件默认位于 `$VERDI_HOME/share/NPI/L1/C/inc`；`libnpiL1.so` 通常与 `libNPI.so` 同目录，也可能只存在于小写平台目录（例如 `.../lib/linux64`）。GNU Make 会按空白拆分目标名，因此仓库路径及上述四个 NPI 目录不得包含空白；Makefile 会对此提前报错。该限制只影响 collector/端到端构建，独立 GUI 启动器仍支持 KDB 路径包含空格。

构建产物默认位于 `npi/build/rs_npi_collector`，同时链接 `libNPI.so` 和 `libnpiL1.so`。采集器使用 NPI Language Model 枚举每个实例的全部 formal ports，并用 `npi_mod_inst_get_port` 作为 NPI L1 fallback；这能覆盖 partial KDB 中 `instance -> npiPort` 关系为空、但按完整实例路径仍可查询端口的情况。采集不再按全局 clk/rst 名过滤，也不再执行 Netlist clock source trace。`npi_load_design` 只接收 `-elab <path>`，不会接收源码、filelist 或任意 Verdi 参数透传。若 load 返回 0，collector 会按 NPI 手册示例继续探测 top：至少一个 top 可查询时继续并报告 `NPI_LOAD_PARTIAL`；没有任何 top 可查询时才退出 11。后续 position、实例、formal port 和 parameter 证据仍 fail-closed。

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

Verdi 路径可通过 `VERDI_BIN`、`VERDI_HOME`、`NOVAS_INST_DIR` 覆盖；GUI 选择可通过 `GUI_USER`、`GUI_DISPLAY`、`GUI_XAUTHORITY`、`GUI_SESSION_PID` 覆盖。完整测试脚本还支持 `PYTHON_BIN`、`CXX`、`NPI_INC_DIR`、`NPI_LIB_DIR`、`NPI_L1_INC_DIR`、`NPI_L1_LIB_DIR`。详细矩阵和可复制命令见 [Verdi GUI 端到端复现指南](docs/VM_GUI_TEST.md)。

## 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 所有硬检查通过；可能有 warning |
| `1` | RTL 与规格不一致，或存在无法可靠解析的连接/驱动 |
| `2` | 配置、Excel、inventory、NPI 启动或设计加载失败 |

## 当前边界

- 当前版本仅把 `Intf_type` 作为报告标签。若要检查 interface 数据链，需要补充 input/output formal port 映射、首尾预期信号和 stage 顺序定义。
- clk/rst 的复合表达式（concat、运算、mux 等）不会做字符串猜测，而是报 `UNSUPPORTED_CONNECTION`。
- 模块规则必须同时指定非空的 clk 与 rst formal port 名；端口名可自定义，但当前没有“无 rst/跳过 rst”模式。checker 对两个端口独立取证和判定：实际模块存在且已连接规则所指 clk、但没有规则所指 rst 时，该行 FAIL 且只报告 `RST_PORT_MISSING`，不得同时误报 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`。
- `CRG_source` 当前只作为必填 Excel 字段和报告证据保留，完全不参与 PASS/FAIL。新 collector 不追踪 clock source，在线新采 inventory 的 `clk_sources` 为 `[]`；旧 inventory 中已有的 `clk_sources` 也仅作为证据加载。
- `RS_CRG_EN` 和动态拍数都使用 elaboration 后的逐实例 effective 参数值，不用模块声明默认值替代实例 override；模块规则要求的参数缺失或无法解析时 fail-closed。
- inventory `warnings` 中的 NPI traversal 问题仍按 `NPI_UNRESOLVED` 硬错误处理；partial load 本身写入可选 `notices` 并显示为非致命 warning，只有 position、实例、端口和 parameter 证据仍完整时检查才可能 PASS。
- 默认只收集 `position` 下的直接 module children；为了兼容 generate，采集器会穿过非 module 的 generate scope，但不会下钻进已经遇到的普通子模块。
- SystemVerilog instance array 的名字形如 `u[0]`，与 `AAAA_BBB_C0` 这类后缀命名不是同一种分组格式；单个元素可按完整本地名填写，按数组前缀分组则需要定制 suffix 规则。

## 测试

```bash
python -m unittest discover -v
```

自动测试覆盖 XLSX/CSV 解析、九列映射、`step=0`、实例分组、逐模块 clk/rst 端口规则与旧配置继承、有 clk/无 rst 时仅产生 `RST_PORT_MISSING` 的隔离回归、单/多 parameter 动态拍数、未知值 fail-closed、逐实例 RTL `RS_CRG_EN`、`has_rs_cfg_en=true` 的 Excel/internal 标签要求及 `false` 时任意字面值 don't-care、CRG 判定停用、schema v2 inventory、schema v3 report、partial-load notice、NPI L1 端口 fallback 合同、报告导出、GUI 完整配置导入/导出的事务与副本语义、GUI 命令构造/生命周期、完整进程组取消和 Linux GUI 启动器。测试总数以当前 `unittest` 输出为准。真实 NPI/L1 编译、partial KDB、全部 formal port/effective parameter 采集和设计加载必须在有对应 Synopsys 安装和 license 的 Linux 环境中执行。

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

工具自带 GUI 的完整配置往返、可见离线正例/反例、100 轮稳定性、10,000 行负载、取消启动竞态和在线 KDB smoke 命令见 [完整测试指南](docs/TESTING.md) 和 [VM GUI 复现指南](docs/VM_GUI_TEST.md)。有 clk/无 rst 的专项在线 GUI 回归默认连续运行 20 轮，每轮都重新通过 collector 加载同一 elaborated KDB；`has_rs_cfg_en=false` 且 Excel/internal `RS_CFG_EN` 填任意非标准文本的离线 GUI 专项也默认运行 20 轮，可用 `GUI_RS_CFG_DONTCARE_ITERATIONS` 覆盖。GUI smoke 成功行必须包含 `config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules`；VM 端到端脚本会在九份 GUI 日志中逐一硬断言该标记，其中包括 don't-care 专项的 `offline_gui_rs_cfg_dontcare.log`、partial KDB 的 `partial_load_gui.log`、逐模块自定义端口的 `online_gui_custom_port.log`，以及端口隔离回归的 `online_gui_clk_present_rst_missing.log`。

GUI 探测优先使用当前 shell 已可访问的 `DISPLAY`，否则扫描常见桌面/Xwayland 进程和可读的进程环境；不要求固定桌面用户名、GNOME 或 `gnome-session-binary`。`scripts/test_vm_verdi_gui.sh --gui-probe-only` 也可执行同一探测。fresh 驱动最终调用的完整脚本会运行全部 Python 测试、构建 collector、生成新的 `kdb.elab++` 并启动 `verdi -elab`；只有新窗口标题匹配 `VERDI_READY_REGEX`、明确显示已展开的 `top` 才进入 NPI/GUI 检查，其他启动页或无关 Verdi 窗口不能作为就绪证据。测试默认在退出时关闭本次启动的 Verdi，避免遗留进程和 license 占用；人工检查时可显式设置 `KEEP_VERDI_GUI=1`。

## 已验证环境

当前代码版本为 `0.9.1`。本版本需在以下环境完成新的固定 SHA fresh-checkout 验收；下列既有记录对应各自固定的历史提交：

```text
CentOS 7.9
Python 3.8.13
GCC/G++ 11.2.1
Verdi/NPI O-2018.09-SP2
NPI_PLATFORM=LINUX64
```

[clk 存在、rst 缺失 finding 隔离与 VM GUI 压测验证记录（2026-07-26）](docs/TEST_RESULTS_CLK_PRESENT_RST_MISSING_2026-07-26.md) 固定到 GitHub 功能提交 `b3d701c2b95a4941fae398b4c2490c7f630127c3`：CentOS/Python 3.8 的 236 项测试全部通过且无 skip；GitHub fresh clone、partial/clean elaborated KDB、mapped Verdi/Tk GUI、专项在线 20 轮、普通在线 3 轮、离线 100 轮、10,000 行负载和八日志门禁全部通过。`CLK_ONLY_RS` 的 formal ports 精确为 `clk/d/q`，`clk` 已连接且 rst 缺失时 finding 只包含 `RST_PORT_MISSING`。

下列记录属于更早功能版本，仅用于历史对照：

[逐模块 clk/rst 端口与 CRG 暂停判定验证记录（2026-07-26）](docs/TEST_RESULTS_MODULE_PORTS_2026-07-26.md) 固定到 GitHub 功能提交 `2e90d6636accee3d5450a1feac64dc2f36edc608`：Windows 233 项回归中 189 项通过、44 项平台限定用例按预期跳过；CentOS/Python 3.8 的 233 项全部通过且无 skip；NPI L0/L1、partial/clean KDB、Verdi GUI、在线自定义 `clock_i/reset_ni`、错误 `CRG_source` 仍 PASS、在线正反例、默认规则、离线 100 轮和 10,000 行负载均通过。

[RTL RS_CRG_EN 匹配与 VM GUI 压测验证记录（2026-07-26）](docs/TEST_RESULTS_RS_CRG_EN_2026-07-26.md) 固定到 GitHub 提交 `a9a26869b99d69d3826ffb0071e967cfedbf5c92`：Windows 223 项回归中 179 项通过、44 项平台限定用例按预期跳过；CentOS/Python 3.8 的 223 项全部通过且无 skip；partial/clean KDB、Verdi GUI、逐实例 `RS_CRG_EN` 证据、在线正负例、默认规则、离线 100 轮和 10,000 行负载均通过。

[GUI 完整配置导入导出与 VM 压测验证记录（2026-07-26）](docs/TEST_RESULTS_CONFIG_IO_2026-07-26.md) 是本次 RTL parameter 改名之前的 `0.8.0` 历史基线。

[NPI partial-load 兼容与 GUI 压测验证记录（2026-07-25）](docs/TEST_RESULTS_NPI_PARTIAL_LOAD_2026-07-25.md) 是完整配置导入/导出之前的 `0.7.1` 历史基线。

[任意 Excel 表头与列号映射 GUI/NPI 验证记录（2026-07-25）](docs/TEST_RESULTS_COLUMN_MAPPING_2026-07-25.md) 是本次 partial-load 修复之前的 0.7.0 历史基线。

[完整 RS_inst 本地例化名与 GUI 压测验证记录（2026-07-25）](docs/TEST_RESULTS_FULL_INSTANCE_2026-07-25.md) 是任意业务表头支持之前的 0.6.0 历史基线。

[Position 映射库与 GUI 压测验证记录（2026-07-25）](docs/TEST_RESULTS_POSITION_MAPPING_2026-07-25.md) 是完整本地实例名功能之前的 0.5.0 历史基线。

[动态 step 版本验证记录（2026-07-25）](docs/TEST_RESULTS_DYNAMIC_STEP_2026-07-25.md) 是引入 Position 映射库之前的 0.4.0 历史基线。

[RS_CFG_EN 九字段版本验证记录](docs/TEST_RESULTS_RS_CFG_EN_2026-07-24.md) 是引入动态 `step_parameters` 之前的历史基线；其中 102 项测试和压力数字不能代表当前 Position 映射版本。

[2026-07-24 GUI 发布验证记录](docs/TEST_RESULTS_2026-07-24.md) 保留的是引入 `RS_CFG_EN` 前的八字段历史基线，仅用于对照。
