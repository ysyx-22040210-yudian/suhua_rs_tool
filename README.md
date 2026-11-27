# RTL 打拍例化检查工具

该工具提供命令行（CLI）和自带的 Tkinter 桌面 GUI，把 Excel 中的打拍规格与 NPI 展开后的 RTL 层次做对比，当前检查：

当前代码版本为 `0.19.0`；当前 collector 输出 inventory v4，CLI 输出 report v4。

**Python 3.8 可直接运行 CLI 和自带 GUI。** 在仓库根目录、可显示窗口的 Linux 终端执行 `python3.8 -m rscheck gui`。无需安装第三方 Python 包；GUI 需要该解释器自己的 Tkinter。见[Python 3.8 启动与自检](docs/USAGE.md#用-python-38-直接运行)。

**Excel 空值与 NA：** `Intf_type`、`RS_module`、`position`、`step` 任一留空或填大写 `NA`，整行跳过，不计入通过或失败。其余已映射字段留空时只跳过该项，每行每个空字段报一次警告；填 `NA` 同样跳过该项，但不报警。`step=0` 仍检查。见[填写示例及字段之间的依赖](docs/USAGE.md#空值与-na)。

**合并拍数（可选）：** 在 GUI「检查配置 → 列映射」给 `step_sum` 填列号，即可用「验证 Excel」核对每个纵向合并格是否等于范围内**参与检查行**的 `step` 之和。区内整行跳过的不计数；错误定位到工作表和合并范围。默认不启用；原有逐行 RTL 拍数检查不变。见[可运行的 Excel 样表](examples/specs_merged_steps.xlsx)与[填写方法](docs/USAGE.md#合并拍数-step_sum)。

- Excel `position` 简写是否能通过可选映射库解析到实际 RTL 全路径，以及解析后的路径是否存在；
- 同一 `RS_inst` 组（可填写前缀或完整本地例化名）的物理实例是否按模块规则计算出与 `step` 相等的有效拍数；
- `RS_inst` 优先：每项先查完整名或合法前缀组，再用该项自身找唯一最近的本地名。多项只命中一部分时整行报错，不用 route 补选；所有项都未命中时才启用下述参考名。`RS_inst` 为空、为 `NA` 或只有分隔符时，两列 route 都不参与，不猜实例和拍数；
- 每个实例的模块定义名是否等于 `RS_module`；
- 从每个 RS 的模块规则所指定的时钟口和复位口独立反追，比较目标 `CRG_source` **输出端实际连接的信号名**。若 CRG 的 `.clk_out(clk_core)` 经门控接到 RS 的 `clk_rs`，Excel 应填 `clk_core`；复位同理。缺少起点端口或源端线名不同为错误；追踪不到为警告。
- Excel `CRG_source` 简写是否能通过可选映射库解析到实际 RTL 全路径，以及每个 RS 实例能否从模块规则指定的 clk formal port 沿有界上游模块图命中该完整路径；命中时 CRG 子检查为 PASS，未命中、达到深度上限或证据不可用时只报告 warning；
- 每行解析出的默认或显式模块规则，以及逐实例 effective parameter 是否满足该规则的 `RS_CRG_EN` 和有效拍贡献条件；Excel/internal 字段仍使用兼容键 `RS_CFG_EN` 保存“假门控”标签；
- 重叠的 `RS_inst` 导致同一实例匹配多个 Excel 组时，明确报错。

**合并组的 RS 互联图：** 运行 RTL 检查后，选中同一 `step_sum` 合并区内任一参与行，打开「检查证据 → RS 互联图」。方框是 RS 例化，线直接接到另一个 RS 的端口：例如 `tx.data_o → rx.data_i` 的线上标 `shared_data`；通过同一 interface 实例连接时，线上标接口实例名，两端显示各自的 modport，不给整束接口指定方向。`Intf_type` 可以是端口名、interface 类型或带 modport 的类型。「连接明细」可核对成对的端口、实际线名和 Excel 命中行；不把行序当作证据。详见[互联图说明](docs/USAGE.md#合并组的-rs-互联图)。

**RS 只有一个总 interface 时：** 不必有 `mst/slv`，也不必拆分单独的数据输入、输出端口。共用同一实际接口画一条无向整束线；若两端各接自己的接口，而上层把成员通过 `assign` 直连，则只画有证据的成员（绿色赋值箭头），不把两个接口整体当成同一个。要看新增的成员证据，请重新编译 collector 并用原有 `*.elab++` 重新运行「RTL 检查」。

例如，Excel 写 `RS_inst=CTRL_RS_DO`（末尾是字母 O），elab 中是 `top.u_tile.CTRL_RS_D0`（末尾是数字 0）。若没有合法的同名或前缀组，且它在同模块候选中唯一最近，工具用这个实例检查有效拍数和端口，报告保留原名、选中路径和编辑距离 1。若填写的是 `AAAA_BBB`，已能命中六个合法组成员，则仍检查整组。这些行为不要求映射 `RS_route`。

**`RS_inst` 支持用空格、Tab、实际换行、分号、逗号、顿号和冒号分项。** 中英文标点可以混用，连续分隔符不产生空项。例如 `A  B，C、D::E` 会拆成 `A`、`B`、`C`、`D`、`E` 五项。每项先查完整名或合法前缀组，未命中再独立模糊匹配；多项命中同一物理实例只计一次。两列 route 都是参考名，格内的冒号、空格与汉字均按整串比较，不拆分。GUI 点「验证 Excel」可核对拆分结果，运行检查后选中该行，在「匹配明细」查看逐项依据。见[多分隔符样表](examples/specs_multi_inst.xlsx)、[换行样表](examples/specs_multiline_inst.xlsx)和[填写规则](docs/USAGE.md#在一个-rs_inst-单元格填写多个名字)。

**何时看 `RS_route2`？** 只有 `RS_inst` 有有效项、但每一项都未能选中实例时，才看 `RS_route`。若本工作表中 `RS_module`、解析后的 `position` 全路径、`RS_route` 三项组合唯一，用 `RS_route`；若三项都与其他参与匹配的行相同，只用本行的 `RS_route2` 选一个实例。相同 route 出现在不同模块或层次不算重复。最近候选并列时不任选；需要 `RS_route2` 却未填写也会报错。详见[中文使用说明](docs/USAGE.md#每一项的匹配顺序)和[两行中英文参考名示例](examples/specs_route2.csv)。

## 文档

- [详细使用文档](docs/USAGE.md)
- [完整测试指南](docs/TESTING.md)
- [有界递归 CRG Source 追踪与 VM GUI 压测验证记录（2026-07-27）](docs/TEST_RESULTS_CRG_TRACE_2026-07-27.md)
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
examples/                可运行的 Excel、CSV 与 SystemVerilog 示例
docs/                    详细使用与测试文档
tests/                   无 NPI license 也能运行的离线测试
scripts/                 rscheck/Verdi GUI 启动器和端到端测试脚本
```

Python 端要求 3.8 或更高版本，CLI 没有第三方运行时依赖；桌面 GUI 使用 Python 标准库 Tkinter，最小化 Linux 安装需另装对应 Python 版本的 Tk 包。支持 `.xlsx`、`.xlsm`、`.csv`、`.tsv`；旧二进制 `.xls` 需先另存为 `.xlsx`。

**映射列可以用 Excel 公式，不用粘贴为值。** 工具读取 `.xlsx` / `.xlsm` 中已保存的计算结果。例如，`step` 可以用 `SUM` 求和，`position` 可以用公式拼出简称，`RS_inst` 算出空字符串时不启用两列 route，拍数显示为未检查。改过源数据请先在 Excel 或 LibreOffice 中重新计算并保存；检查机不需要安装 Office，工具不执行宏、也不刷新外部引用。若本次实际需要的公式没有保存结果或结果为 `#REF!` 等错误，日志会指出工作表、单元格和映射字段，不会默认为 0。可直接打开[带公式的示例工作簿](examples/specs_formulas.xlsx)，参见[公式单元格的用法](docs/USAGE.md#公式单元格)。

## 列映射

复制并修改 [config/rscheck.example.json](config/rscheck.example.json)。`Intf_type`、`RS_module` 等名称是工具内部属性键，不要求 Excel 对应列使用同名表头；实际表头可以是任意业务名称。每个键的 1-based `columns` 值唯一决定该属性从哪一列读取。Excel 允许包含任意其他列；映射列可以任意排列、无需连续，但列号必须为正数且互不重复：

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
  "RS_CFG_EN": 9,
  "RS_route": 10,
  "RS_route2": 11
}
```

也可以在命令行临时覆盖。下面把内部属性 `Intf_type`、`RS_module` 分别映射到第 13、12 列，第 10、11 列仍是两个可选参考名：

```bash
python -m rscheck validate \
  --excel specs.xlsx \
  --config config/rscheck.example.json \
  --column Intf_type=13 \
  --column RS_module=12
```

默认 `validate_headers=false`，解析只按列号映射，不比较实际表头文字。只有用户显式设置 `validate_headers=true`、传入 `--header-check` 或在 GUI 勾选“严格校验表头（可选）”时，工具才额外要求映射列的表头精确等于内部属性名。工作表、表头行和数据起始行可在 JSON 中配置，也可通过 `--sheet`、`--header-row`、`--data-start-row` 覆盖。

从 0.6.0 或更早版本复制的配置可能已经显式写入 `"validate_headers": true`；该值会继续启用严格诊断。使用自定义业务表头时请改为 `false`、在 GUI 取消勾选，或在单次 CLI 运行中传 `--no-header-check`。

## Position 与 CRG Source 映射库

Excel 的 `position` 可以填写便于维护的简写，不必重复很长的 RTL hierarchy。可选的 `position_mappings` 数据库保存“Excel 简写 -> RTL 全路径”的对应关系：

```json
"position_mappings": {
  "tile_core": "top.u_tile",
  "lsu_pipe": "top.u_core.u_lsu.u_pipe"
}
```

解析时按区分大小写的简写精确查找。命中后用右侧全路径作搜索上界，报告保留原简写；未命中则把 Excel 值当作完整 RTL 路径。`position` 不再要求是 RS 实例的直接父层。源端 clk/rst 信号的解析基准仍是目标 CRG，不是 position。

**嵌套 position 从深到浅匹配，与 Excel 行序无关。** 例如子行在 `top.tile` 中选到了 `RS_A`，父行 `top` 就不能再选它；`top.tile` 内没被子行选中的 `RS_B` 仍可被父行选中。同一边界的多行不按先后抢占，选中同一物理实例仍报冲突。在结果的“匹配明细”可查排除数、实例全路径和所属子行。[详细规则](docs/USAGE.md#32-position-简写与完整路径)

“Position 映射库”和“CRG Source映射库”都支持**选择 DUT 层次…**。先在“检查配置”指定在线 NPI collector 和 Verdi elaborated KDB（如 `top.elab++`），打开选择器就能从顶层逐级展开，不必输入正则。也可按完整路径筛选：`u_lsu` 查包含该片段的路径，`(?i)\.u_crg$` 查以 `.u_crg` 结尾的分支。命中项保留在原来的父层之下。“使用所选路径”只回填当前编辑框；确认后再点“应用修改”和“保存映射库”。

首次加载成功后，两个选择器共用同一份 DUT 层次树。关窗再开直接复用内存；重启工具也能从当前用户的本地缓存恢复，无需重读 Verdi。换到新的 KDB、库被替换或点击「清除层次缓存并重载」才重新采集；「清除筛选」不会清缓存。详见[层次缓存与重载](docs/USAGE.md#dut-层次缓存)。

查询默认最多展示 500 条、正则最长 256 个字符；结果过多时应收窄正则。collector 的 hierarchy 枚举最多深入 256 层、访问 1,000,000 个内部 scope；非候选容器 scope 只穿透、不展示，从而保留其后代模块。达到上限或 KDB 部分加载时会明确标记结果不完整。查询只接受 `elabcom -elab` 生成的 elaborated KDB 目录（目录名可自定义），不会接收 filelist、RTL 源码或 `work.lib++`。无图形环境也可以查询，并可继续用 CLI 原子修改配置：

```bash
python -m rscheck hierarchy-search \
  --collector /absolute/path/to/rs_npi_collector \
  --elab-db /absolute/path/to/top.elab++ \
  --regex '^top\.u_core\..*u_lsu$' \
  --limit 500 \
  --npi-timeout 180 \
  --json
```

映射库 CRUD 命令：

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

Excel 的 `CRG_source` 同样可以使用简写。可选的 `crg_source_mappings` 数据库保存“Excel CRG_source 简写 -> RTL 全路径”；查找区分大小写：

```json
"crg_source_mappings": {
  "crg_aux": "top.u_tile.u_aux_crg",
  "crg_core": "top.u_tile.u_crg"
}
```

命中时，内部 `CRG_source` 使用右侧完整路径，report v4/CSV 的 `crg_source_alias` 保留 Excel 原值；未命中时把 Excel 值直接视为完整路径，`crg_source_alias` 为空。checker 将该完整路径作为有界时钟追踪的目标，并与追踪到的上游模块完整 instance hierarchy 做区分大小写的精确匹配。CRG 路径仍不会加入 NPI collector 的 positions 请求；runner 会另行把每种 `RS_module` 最终生效的 `clk_port/rst_port` 作为 trace rule 传给 collector。

时钟、复位分别追 driver，只在命中用户登记的 DIV / GATE / OCC 模块输出时，才跳到该条规则的指定输入。未登记的模块或输出不会展开；直接接到目标 CRG 无需规则。GUI：“检查配置 → RTL 数据来源 → DIV / GATE / OCC 端口规则…”。一个输出可登记多个输入；同一模块的时钟、复位输出可以各走各的输入。命中 Excel 指定的完整 CRG 实例即停止该分支，使用该输出的实际连线比对。规则随配置导入/导出。见[使用指南与配置示例](docs/USAGE.md#34-crg-来源简写与完整路径)。升级请重新编译 collector；可运行 `bash scripts/test_vm_driver_map.sh` 验证。

最大模块跳数由 `rtl.crg_trace_max_depth` 配置，默认 `16`，只接受 `1..256`。这是有限遍历，不会无上限递归；collector 还使用已访问对象的最小深度终止环路，并为每次 trace 设置 100,000 个 Netlist 对象的固定访问上限。未命中产生 `CRG_SOURCE_NOT_FOUND`，达到深度上限产生 `CRG_TRACE_DEPTH_LIMIT`，无法取得可靠 trace 证据产生 `CRG_TRACE_UNAVAILABLE`；三者都是 warning，不改变该行其他检查的 PASS/FAIL，也不会把本应为 `0` 的 CLI 退出码改成 `1`。

可以在 GUI 的“CRG Source映射库”页维护，也可以使用对称的 CLI：

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

`crg-source-db` 的 `set/delete` 与 `position-db` 一样原子写回配置并遵守单写者约束；`resolve` 未命中时返回 direct 语义，即完整路径保持输入值、alias 为空。

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
- `has_rs_cfg_en=false`：非空且非 `NA` 的 Excel/internal `RS_CFG_EN` 任意字面单元格内容都不参与 PASS/FAIL，但解析后的文本仍会写入报告；RTL 实例若实际仍存在 `RS_CRG_EN`，仍报兼容 finding code `RS_CFG_EN_PARAMETER_UNEXPECTED`。
- 逐行特殊值 `NA`：Excel/internal `RS_CFG_EN` 去除首尾空白后若精确等于大写 `NA`，无论模块规则的 `has_rs_cfg_en` 为何，都跳过该行全部 `RS_CFG_EN` 标签和 RTL `RS_CRG_EN` 参数存在性/值检查。这里只豁免门控检查；`position`、实例匹配、`RS_module`、动态 `step`、clk 和 rst 仍照常检查。`na`、`N/A` 等其他写法不等价于 `NA`。
- `step_parameters=[]`：每个匹配物理实例贡献 `1` 拍。
- `step_parameters` 非空：所有参数值均可确定时，全部非零贡献 `1`，至少一个为零贡献 `0`。多个参数采用“全部非零”语义。
- `clk_port`、`rst_port`：该 `RS_module` 实际使用的 formal port 名。GUI 新建或修改规则时留空分别规范化为 `clk`、`rst_n`。
- `RS_CRG_EN` 由专门逻辑处理，不能放入 `step_parameters`；工具不会回退匹配 RTL `RS_CFG_EN`。RTL 中名为 `RS_CFG_EN` 的 parameter 不再具有门控特殊语义，但可作为普通动态拍参数使用。
- 任一参数缺失、值为 `null`、包含 X/Z/`?` 或不是可解析数值时，贡献为未知并 fail-closed，即使另一个参数已知为零也不使用部分证据计算 `step`。
- 未列入 `step_parameters` 的其他 RTL parameter（例如 `WIDTH`）允许存在并保留在报告证据中，但不影响拍数。

显式规则优先于默认规则。例如上面的 `rs_pipe` 使用 `rs_mode` 计算有效拍数；没有同名显式项的模块则沿用默认“每实例 1 拍”。若本意是覆盖默认值，规则键必须与 Excel/RTL `RS_module` 大小写完全一致。

例如 `AAAA_BBB_C0` 至 `AAAA_BBB_C5` 有 6 个物理实例，`rs_mode` 依次为 `1,1,0,1,1,1`，则逐实例贡献为 `1,1,0,1,1,1`，有效拍数是 `5`，Excel `step` 必须填 `5`。`step` 允许为 `0`；但没有选中实例（无候选、最近并列或层次不存在）时仍不通过，不能用 `step=0` 掩盖选择问题。

仓库示例 RTL 还包含 `rs_custom` / `CUSTOM_RS`，其 formal ports 为 `clock_i`、`reset_ni`，用于真实 NPI 回归逐模块端口名和全部 formal port 采集。另有 `rs_clk_only` / `CLK_ONLY_RS`，其 formal ports 精确为 `clk/d/q`，`clk` 连接 `top.u_tile.clk_rs`，且模块完全没有 rst formal port；它用于证明该行应 FAIL 且 finding 只能是 `RST_PORT_MISSING`，不得误报 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`。

## 分组规则

填写 `RS_inst` 时，一行定义一组；解析后的完整 `position` 和该格提取的名字集合用于识别重复规格，分隔符、顺序和同格重复项不改变组的身份。`position` 可以是 `position_mappings` 中的简写，也可以直接是该组搜索上界的完整 NPI 路径。`RS_inst` 可以填写组前缀，也可以填写一个完整的 NPI 本地例化名；完整名不是 `top.u_tile.CTRL_RS_D0` 这样的层次全路径。

实例的本地名字减去 `RS_inst` 后，空 remainder 直接合法，用于完整名输入；非空 remainder 才必须完整匹配 `rtl.suffix_regex`。例如，`RS_inst=AAAA_BBB` 时 `AAAA_BBB_C0`、`AAAA_BBB_C12` 匹配，`AAAA_BBB0`、`AAAA_BBB_C` 不匹配；`RS_inst=CTRL_RS_D0` 时，本地实例 `CTRL_RS_D0` 以空后缀匹配。

空后缀实例仍照常检查 `RS_module`、parameters、`step` 贡献和 clk/rst，并从该实例的规则 clk formal 执行 CRG 有界追踪。空后缀实例不参与 suffix tag/index 或连续编号检查。非空后缀成员中，不同 suffix stem 仍属于同一组，只产生 warning；数字是否从 0 连续默认不影响 PASS，需要时把 `require_contiguous_indices` 设为 `true`。

匹配仍保留前缀语义：若同一 scope 同时存在 `PFX` 和 `PFX_C0`，填写 `RS_inst=PFX` 会同时匹配空后缀的 `PFX` 和带后缀的 `PFX_C0`。当前没有 exact-only 模式；需要只检查 `PFX` 时，应避免同 scope 中存在也符合该前缀与 suffix 规则的其他实例，或拆分命名。

Excel 中 `clk`/`rst` 可填完整源端连线名，或相对目标 CRG **父层**的信号名；`position` 只定位 RS，不是源端信号的解析基准。formal port 名由匹配到的模块规则 `clk_port`、`rst_port` 指定；未知模块使用 `clk`、`rst_n`。旧 JSON 中显式模块规则若缺少这两个键，会先继承历史全局 `rtl.clk_port/rst_port`，下一次由 GUI 保存或导出时再显式写入规则，避免升级时改变既有配置含义。

`RS_CFG_EN` 内部属性的列号映射始终必需，但实际 Excel 表头可以任意命名；这个兼容字段保存去除首尾空白后的用户输入并进入报告，RTL 中实际匹配的 parameter 名为 `RS_CRG_EN`。精确大写 `NA` 是逐行门控检查豁免值：它跳过该行全部 `RS_CFG_EN`/`RS_CRG_EN` 检查，但不跳过 `RS_module`、实例、`step`、clk 或 rst 检查；`na`、`N/A` 不具有该语义。该格留空则只报一个空字段警告并同样跳过；其余非空值在 `has_rs_cfg_en=true` 时 要求数据精确为 `假门控`，`false` 时该标签不参与判定，但 RTL 若意外存在 `RS_CRG_EN` 仍会报错。公式返回的文本 `"NA"` 与手填 `NA` 等效；Excel 错误值 `#N/A` 则不是豁免值。实例后缀连续性只按具有合法非空数字后缀的物理实例检查，不按空后缀实例或有效拍数检查。

## 工具自带桌面 GUI

这不是 Verdi GUI。它是 `rscheck` 自带的配置、执行和报告查看界面，和 CLI 使用同一套解析、检查及报告逻辑。Windows 和 macOS 可直接启动，用于 Excel 验证和离线 inventory 检查；真实 NPI collector、`libNPI.so`、`libnpiL1.so` 和 elaborated KDB 在线采集只支持 Linux：

窗口名为 **RS Studio**，使用原生 Python Tk/ttk，不需要浏览器、联网字体或额外主题包。左侧「工作区」放检查配置、结果和日志，「数据库」放模块规则、Position 和 CRG 映射。在任一页都可以从底栏验证 Excel、运行或取消检查、打开报告目录。GUI 也支持完整配置 JSON 的导入和导出，便于把列映射、`rtl.crg_trace_max_depth` 及三个数据库一起迁移到其他设备。

默认界面为暖纸色：圆角卡片区分配置和诊断区域，陶土色主按钮执行操作。输入框、数字框和按钮仍是原生控件，支持 Tab 移动焦点、键盘输入和数字框上下键；PASS、FAIL、WARNING 保留独立的文字及状态色，不以品牌色代替检查结果。

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

“检查配置”页可选择 Excel/CSV 和配置 JSON，设置工作表、表头行、数据起始行、`CRG Trace 最大层数`（`1..256`），以及各内部属性对应的互不重复 1-based 列号。“严格校验表头（可选）”默认未勾选，仅用于用户主动采用标准表头时的附加诊断。两套映射库都能从 DUT 树选路径，并将简写与所选全路径原子保存回配置 JSON。“模块规则库”页维护兼容规则键 `has_rs_cfg_en`、逗号分隔的 `step_parameters` 和逐模块 `clk_port/rst_port`。端口输入留空时分别使用 `clk`、`rst_n`。任一数据库存在未保存修改时不能运行检查。

“配置 JSON”行的“导入”和“导出”处理的是完整配置，文件根对象必须恰好是 `excel`、`columns`、`rtl`、`position_mappings`、`crg_source_mappings`、`module_rules`。导出以当前界面的 Excel 选项和列号（含可选 `RS_route`、`RS_route2`、`step_sum`）、已加载配置的完整 `rtl`、三个内存数据库生成独立副本；已点击“应用新建/修改”但尚未单独保存的数据库修改也会进入副本，搜索过滤不会删减导出内容。导出不切换当前配置路径、不清除未保存状态，并拒绝把目标选为当前配置自身；若路径输入框已改为另一个尚未加载的文件，也会先拒绝导出，避免把旧 `rtl` 误认为新文件内容。

“导入”严格要求候选文件包含上述六个根对象；手动“加载”用于重读路径输入框，并继续兼容历史配置省略 `position_mappings` 或 `crg_source_mappings` 的情况，其中缺少 `crg_source_mappings` 时按空库处理。两者都会先读取并校验候选；若当前 Excel/列号表单不同于已加载配置，先确认是否丢弃，再依次确认模块规则库、Position 映射库和 CRG Source 映射库的未保存修改。所有确认完成后还会重新读取候选文件，只有复核仍有效才更新界面；导入还会把候选文件切为当前配置路径，成功后三个数据库的未保存状态都会清除。取消文件选择、候选无效或任一确认被拒绝时，当前配置字段、路径和内存数据库都保持不变。配置中原本以字符串保存的纯数字工作表名在 GUI 未编辑时仍按名字导出和运行，不会误转成序号。配置只保存可移植的检查语义；Excel/CSV、collector、Elab KDB、NPI 库、inventory 和报告等本次运行输入/输出路径不属于配置。

RTL 数据源可选：

- **在线 NPI**：填写 collector 和 Verdi elaborated KDB；如需指定 NPI 库目录、超时或 inventory 保存路径，展开「高级采集设置」。
- **离线 Inventory**：选择已有 inventory JSON。想先熟悉界面，可从仓库根目录启动，保留自带规格和配置，点击「离线 Inventory」→「使用示例 Inventory」→「运行 RTL 检查」。这个示例不需要 Verdi 或许可证。

在线 GUI 与 CLI 的输入边界完全一致：只允许 collector 加 `elabcom` 生成的 elaborated KDB；不提供 RTL、filelist、top 或任意 Verdi 参数透传入口。JSON 报告必填，CSV 可选。“验证 Excel”只验证规格；“运行 RTL 检查”执行完整检查；“取消”会终止后台 CLI 及其 collector 子进程。

在「检查结果」选一条 Excel 规格，打开右下 **「匹配明细」**，即可逐项核对 `RS_inst`、实际启用的 `RS_route` 或 `RS_route2`、回退原因、匹配依据、选中的 RTL 全路径、可用的源文件行号和计拍贡献。模糊匹配显示候选排名、距离和未选原因；精确名和前缀组列出全部选中成员；重叠实例标明不重复计拍。点「复制匹配明细」可复制当前行；要分享全部行，CSV 的 `match_details` 列和 CLI `--match-report output/match-details.txt` 提供同一份说明。见[人工复核指南](docs/USAGE.md#人工复核实例匹配)。

「诊断摘要」显示检查偏差，「原始 JSON」保留逐实例参数和时钟追踪证据。「仅看问题」只过滤结果行，不改变整份报告的统计；关闭它也能复核 PASS 行。「运行日志」保留实际命令、stdout、stderr 和退出码。

## 先验证 Excel

```bash
python -m rscheck validate \
  --excel examples/specs.csv \
  --config config/rscheck.example.json \
  --sheet 1
```

## 离线检查

已有可信 schema v4 NPI inventory 时，无需 Verdi 环境即可检查并生成 JSON/CSV 报告；加载器也继续接受旧 schema v2：

```bash
python -m rscheck check \
  --excel examples/specs.csv \
  --config config/rscheck.example.json \
  --sheet 1 \
  --inventory examples/inventory.json \
  --json-report output/rs_report.json \
  --csv-report output/rs_report.csv
```

仓库的 `examples/inventory.json` 是从示例 RTL 采集的 schema v4 样例，每个实例都显式包含 `clock_trace` 和 `reset_trace`；`tests/fixtures/inventory.json` 则刻意保留为 schema v2，只用于 legacy loader/checker 兼容回归。

CSV 报告使用 UTF-8 BOM，可直接用 Excel 打开。当前 collector 生成 inventory v4，当前 JSON report 为 v4；inventory loader 兼容 v2/v3/v4，GUI report loader 兼容 v2/v3/v4。每行 `spec.position` 和 `spec.CRG_source` 都保存解析后的完整路径；映射命中时，`spec.position_alias`、`spec.crg_source_alias` 分别保存 Excel 简写。report v4 在既有模块规则、动态 step、端口和 parameter 证据之外新增 `crg_source_check`；每个 matched instance 还保存 inventory 的 `clock_trace`。CSV 新增 `crg_trace_status`、`crg_trace_max_depth` 和 `crg_trace_evidence`。

inventory v4 的 `clock_trace` / `reset_trace` 分别记录时钟与复位，`endpoints` 包含被追到的输出口、实际外侧连线及路径；`clock_trace.modules` 记录上游模块完整 instance hierarchy、module definition、模块深度和 witness `path`，`status` 为 `complete`、`depth_limited` 或 `unresolved`。兼容字段 `clk_sources` 仍保留，并可包含发现的上游模块。旧 v2 没有完整有界 trace 合同：只有某个 `clk_sources` 项同时具有非空 `module`，且其 `instance` 与目标完整路径精确相等时可按 legacy 证据通过，否则产生 `CRG_TRACE_UNAVAILABLE` warning。旧 report v2/v3 可在 GUI 中只读查看，其 `CRG Trace` 显示 `N/A`。

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

构建产物默认位于 `npi/build/rs_npi_collector`，同时链接 `libNPI.so` 和 `libnpiL1.so`。采集器使用 NPI Language Model 枚举每个实例的全部 formal ports，并用 `npi_mod_inst_get_port` 作为 NPI L1 fallback；这能覆盖 partial KDB 中 `instance -> npiPort` 关系为空、但按完整实例路径仍可查询端口的情况。跨越中间模块只查命中输出上明确登记的输入，不遍历模块全部输入。Python runner 通过 `--trace-rules` 指定各 RS 起点，`--driver-plan` 传入端口映射和每组目标，`--trace-max-depth` 指定上限；时钟和复位按实例、起点、角色及目标隔离。`npi_load_design` 只接收 `-elab <path>`，不会接收源码、filelist 或任意 Verdi 参数透传。若 load 返回 0，collector 会按 NPI 手册示例继续探测 top：至少一个 top 可查询时继续并报告 `NPI_LOAD_PARTIAL`；没有任何 top 可查询时才退出 11。后续 position、实例、formal port 和 parameter 证据仍 fail-closed。

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

命令行可用 `--crg-trace-max-depth N` 临时覆盖配置，`N` 必须为 `1..256`；不传时使用 `rtl.crg_trace_max_depth`，配置未写该键时默认 `16`。

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

- 可以根据 `Intf_type` 查看合并组的实际共享网络，但规格未声明预期的级联顺序、首尾端点或协议行为，不据此裁定拓扑对错。
- 不把 RS 端的表达式当作源端名字。NPI 不能证实上游目标输出或其实际连线时提示证据不足。
- 模块规则必须同时指定非空的 clk 与 rst formal port 名；端口名可自定义；Excel 行的 `rst` 填 `NA` 可静默跳过复位检查，留空则跳过并提示 warning。两个期望值都已填写时，checker 对两个端口独立取证和判定：实际模块存在且已连接规则所指 clk、但没有规则所指 rst 时，该行 FAIL 且只报告 `RST_PORT_MISSING`，不得同时误报 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`。
- CRG 来源只做有界上游模块遍历，不解析时序逻辑语义；中间模块只走登记的输出→输入映射，没有按端口名的隐式排除或展开。目标必须与上游模块完整 instance hierarchy 精确匹配。未命中、深度截断和 trace 不可用仅为 warning，不替代 clk/rst 连线的硬检查。
- `RS_CRG_EN` 和动态拍数都使用 elaboration 后的逐实例 effective 参数值，不用模块声明默认值替代实例 override；模块规则要求的参数缺失或无法解析时 fail-closed。
- inventory `warnings` 中的 NPI traversal 问题仍按 `NPI_UNRESOLVED` 硬错误处理；partial load 本身写入可选 `notices` 并显示为非致命 warning，只有 position、实例、端口和 parameter 证据仍完整时检查才可能 PASS。
- `position` 是向下搜索的边界。采集器进入普通模块、generate 等子层次；嵌套边界单独存储，只把子行未认领的实例交给上层继续匹配。
- SystemVerilog instance array 的名字形如 `u[0]`，与 `AAAA_BBB_C0` 这类后缀命名不是同一种分组格式；单个元素可按完整本地名填写，按数组前缀分组则需要定制 suffix 规则。

## 测试

```bash
python -m unittest discover -v
```

自动测试覆盖 XLSX/CSV 解析、自定义列映射、`RS_inst` 的逐项选择以及 `RS_route` / `RS_route2` 回退、position/CRG Source 两套简称映射、`step=0`、实例分组、逐模块 clk/rst 端口、有 clk/无 rst finding 隔离、动态拍数、RTL `RS_CRG_EN`、Excel `NA`/don't-care，以及从规则 clk formal 出发的DIV/GATE/OCC 指定端口反追、时钟与复位独立证据、深度边界、环路终止、完整 hierarchy trace cache 隔离、partial KDB Language/L1 input 补追、v2 legacy 兼容、inventory v3/report v4、GUI CRG Trace 展示与 CSV trace 证据。真实 NPI/L1 编译、partial/clean KDB、多层追踪和可见 GUI 仍必须在有对应 Synopsys 安装和 license 的 Linux 环境中执行。

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

工具自带 GUI 的完整配置往返、可见离线正例/反例、100 轮稳定性、10,000 行负载、取消启动竞态和在线 KDB smoke 命令见 [完整测试指南](docs/TESTING.md) 和 [VM GUI 复现指南](docs/VM_GUI_TEST.md)。CRG Source 映射专项默认 20 轮，固定证据包含 `crg-source-check=pass trace-depth=3 findings=none`；在线深度上限专项也默认 20 轮，由 `GUI_CRG_TRACE_DEPTH_ITERATIONS` 控制，日志为 `online_gui_crg_trace_depth_limit.log`，固定证据包含 `crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6`。GUI smoke 统一输出 `schemas=report-v4/inventory-v4` 和六根配置往返 marker；VM 端到端脚本对新增深度专项在内的十二份 GUI 日志逐一硬断言这些标记。

GUI 探测优先使用当前 shell 已可访问的 `DISPLAY`，否则扫描常见桌面/Xwayland 进程和可读的进程环境；不要求固定桌面用户名、GNOME 或 `gnome-session-binary`。`scripts/test_vm_verdi_gui.sh --gui-probe-only` 也可执行同一探测。fresh 驱动最终调用的完整脚本会运行全部 Python 测试、构建 collector、生成新的 `kdb.elab++` 并启动 `verdi -elab`；只有新窗口标题匹配 `VERDI_READY_REGEX`、明确显示已展开的 `top` 才进入 NPI/GUI 检查，其他启动页或无关 Verdi 窗口不能作为就绪证据。测试默认在退出时关闭本次启动的 Verdi，避免遗留进程和 license 占用；人工检查时可显式设置 `KEEP_VERDI_GUI=1`。

## 已验证环境

旧版有界递归策略的历史验证环境如下（不代表当前端口映射策略）：

```text
CentOS 7.9
Python 3.8.13
GCC/G++ 11.2.1
Verdi/NPI O-2018.09-SP2
NPI_PLATFORM=LINUX64
```

[有界递归 CRG Source 追踪与 VM GUI 压测验证记录（2026-07-27）](docs/TEST_RESULTS_CRG_TRACE_2026-07-27.md) 固定到 GitHub 提交 `b84be55638fd0af9fc9c3874bbc35786fd497a61`：CentOS/Python 3.8 的 299 项测试全部通过且无 skip；collector 编译无 warning；partial/clean elaborated KDB 均验证 `top.u_tile` / `top.u_tile_peer` 两个同名时钟锥按完整 hierarchy 隔离，并验证 Language/L1 端口集合补追；inventory v3/report v4、mapped Verdi/Tk GUI、depth 3 分支命中、depth 2 专项 20 轮、普通在线 3 轮、其他四个 20 轮专项、离线 100 轮、10,000 行负载和十二日志门禁全部通过。

下列固定 SHA 记录是引入有界递归 CRG 追踪前的历史验证基线：

[CRG_source 映射库与 VM GUI 压测验证记录（2026-07-26）](docs/TEST_RESULTS_CRG_SOURCE_MAPPING_2026-07-26.md) 固定到 GitHub 提交 `366c54114bc23f2878e0715357f7ab40f2ef7ea5`，记录的是当时只保留 CRG alias+full、尚未启用来源追踪的 `0.10.0` 行为和十一日志门禁，不能作为当前 inventory v3/report v4 追踪功能的验证证据。

下列记录属于更早功能版本，仅用于历史对照：

[RS_CFG_EN=NA 逐行跳过与 VM GUI 压测验证记录（2026-07-26）](docs/TEST_RESULTS_RS_CFG_NA_2026-07-26.md) 固定到上一版 GitHub 提交 `7570ed09879abc85c7b5e40de57fde735898e47f`：CentOS/Python 3.8 的 248 项测试全部通过且无 skip；当时的 `NA`、don't-care、无 rst、在线和负载测试及十日志门禁全部通过。

[RS_CFG_EN don't-care 与 VM GUI 压测验证记录（2026-07-26）](docs/TEST_RESULTS_RS_CFG_DONTCARE_2026-07-26.md) 固定到 GitHub 功能与门禁提交 `37ccef3bbd15a1191e85664a00e165a296699d12`：CentOS/Python 3.8 的 240 项测试全部通过且无 skip；GitHub fresh clone、partial/clean elaborated KDB、mapped Verdi/Tk GUI、don't-care 专项 20 轮、无 rst 专项 20 轮、普通在线 3 轮、离线 100 轮、10,000 行负载和九日志门禁全部通过。专项确认 Excel/internal `RS_CFG_EN=任意非标准文本` 进入报告，`has_rs_cfg_en=false`、RTL `RS_CRG_EN` 不存在且 findings 为空。

[clk 存在、rst 缺失 finding 隔离与 VM GUI 压测验证记录（2026-07-26）](docs/TEST_RESULTS_CLK_PRESENT_RST_MISSING_2026-07-26.md) 固定到 GitHub 功能提交 `b3d701c2b95a4941fae398b4c2490c7f630127c3`：CentOS/Python 3.8 的 236 项测试全部通过且无 skip；GitHub fresh clone、partial/clean elaborated KDB、mapped Verdi/Tk GUI、专项在线 20 轮、普通在线 3 轮、离线 100 轮、10,000 行负载和当时的八日志门禁全部通过。`CLK_ONLY_RS` 的 formal ports 精确为 `clk/d/q`，`clk` 已连接且 rst 缺失时 finding 只包含 `RST_PORT_MISSING`。

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
