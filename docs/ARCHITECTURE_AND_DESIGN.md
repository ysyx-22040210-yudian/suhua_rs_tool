# RTL 打拍例化检查工具设计方案

## 1. 文档说明

本文描述 RTL 打拍例化检查工具的当前设计，包括业务流程、软件架构、数据模型、
kdebug/Verdi 采集链路、检查算法、GUI、错误模型、进程生命周期和测试策略。

本文对应的有效行为基线：

| 项目 | 版本 |
| --- | --- |
| `suhua_rs_tool` 功能签核提交 | `dac071109808ea361c17bed68606a9c17e1d3553` |
| `kverif` 功能签核提交 | `2b43b799c8f7f8586a9e6c2128335e74d971e633` |
| Inventory | schema v3 |
| JSON Report | schema v4 |
| Python | 3.8 或更高版本 |

当前仓库后续提交只增加文档，不改变上述已签核业务行为。

## 2. 背景与目标

芯片设计中，同一条 interface 可能通过多个 RS module 实例完成多级打拍。规格维护在
Excel 中，RTL 真实结构则存在于 Verdi elaborated KDB。人工检查需要逐行确认实例数量、
module 类型、有效 parameter、clock/reset 连线和上游 CRG 路径，容易遗漏且难以复现。

本工具的目标是：

1. 将 Excel 中的打拍规格归一化为稳定的数据模型。
2. 从已有 Verdi `*.elab++` 中采集可审计的 RTL 结构证据。
3. 按 module 规则计算每个实例对 `step` 的实际贡献。
4. 独立检查 module、clock、reset、`RS_CRG_EN` 和 CRG 路径。
5. 同时提供 CLI、Tkinter GUI、JSON、CSV 和控制台结果。
6. 支持离线 inventory 复查和 Linux 上的在线 KDB 采集。
7. 对 partial KDB、超时和取消进行有界、可清理的处理。

### 2.1 非目标

当前版本明确不做以下事情：

- `Intf_type` 只作为报告标签，不验证 interface 数据内容。
- 不检查各拍 data port 是否首尾串接。
- 不检查第一拍输入或最后一拍输出的数据语义。
- 不把 filelist、RTL 源码或 `work.lib++` 当作在线采集输入。
- 不对复杂 clock/reset 表达式进行猜测性解析。
- CRG trace 证明的是结构可达性，不等价于完整时序语义证明。
- CRG 未命中、深度受限或证据不可用只产生 warning，不使整行失败。
- 当前没有“module 天生无 reset，因此跳过 reset 检查”的规则。

## 3. 术语和兼容命名

| 名称 | 设计含义 |
| --- | --- |
| `RS_CFG_EN` | Excel、配置对象和 finding code 中保留的兼容字段名 |
| `RS_CRG_EN` | RTL 中实际需要采集和检查的 effective parameter |
| `has_rs_cfg_en` | module rule 的兼容配置键，实际表示 RTL 是否应存在 `RS_CRG_EN` |
| `position` | RS 实例组所在的 RTL hierarchy；Excel 可填简写 |
| `CRG_source` | 期望到达的上游 CRG module 完整实例路径；Excel 可填简写 |
| `RS_inst` | 本地实例名前缀，也可填写完整本地实例名 |
| `physical step` | 匹配到的物理实例数 |
| `effective step` | 按 step parameter 规则计算出的有效拍数 |
| partial KDB | load 报错但仍至少有一个 top 可以查询的 elaborated KDB |

reset 默认值存在一个兼容边界：

- 未注册 module 的隐式规则固定使用 `clk/rst_n`。
- 新建 module rule 的业务默认也是 `clk/rst_n`。
- `rtl.rst_port` 的历史默认仍是 `rst`，仅供旧配置和缺省显式规则迁移兼容。

## 4. 用户场景

### 4.1 GUI 在线检查

用户在工具自带 Tkinter GUI 中选择 Excel、配置和 `*.elab++`，编辑三类规则库，
运行检查并查看逐行结果、parameter 证据和 CRG witness path。

### 4.2 CLI 在线检查

自动化脚本显式指定 kdebug adapter 和 `*.elab++`。工具在线生成 Inventory v3，
随后执行检查并输出控制台、JSON v4 和可选 CSV。

### 4.3 离线复查

用户加载已有 Inventory v2/v3，跳过 Verdi 采集层，复用同一 checker。离线 inventory
属于用户信任的快照，不证明它与当前 RTL/KDB 保持同步；生产签核应在线重新采集 v3。

### 4.4 仅验证规格

`validate` 只加载配置并解析 Excel，输出完成列映射和路径映射后的 `SpecRow`，
不需要 inventory 或 Verdi。

## 5. 总体架构框图

![RTL 打拍例化检查工具总体架构框图](images/architecture/rs-tool-overall-architecture.png)

### 5.1 关键架构决策

| 决策 | 原因 |
| --- | --- |
| GUI 通过 CLI 子进程执行 | GUI 与自动化复用相同参数、检查器和退出码，避免两套业务逻辑 |
| 采集与检查以 Inventory 隔离 | Verdi/NPI 证据采集和业务规则可以独立测试、版本化和复查 |
| kdebug 使用外部 JSON action | 主工具与 kdebug ELF 均不直接链接 NPI，NPI 只运行在 Verdi Tcl 内部 |
| 一次 action 采集全部 position | 避免按 Excel 行或 position 反复加载大型 KDB |
| parameter 缺失时 fail closed | 无法证明有效拍数时不使用部分证据推测结果 |
| CRG 未命中为 warning | CRG 追踪可能被复杂表达式或深度限制截断，不遮蔽其他硬错误 |
| 配置使用单个 JSON 文件 | 方便 GUI/CLI 导入导出和跨设备复现，不依赖数据库服务 |

## 6. 模块职责

| 模块 | 主要职责 |
| --- | --- |
| `rscheck/model.py` | 领域模型、schema 常量、Finding、用户可见异常 |
| `rscheck/config.py` | 六根配置校验、归一化、序列化和原子保存 |
| `rscheck/excel_reader.py` | XLSX/XML、CSV/TSV 解析和 `SpecRow` 构造 |
| `rscheck/inventory.py` | Inventory v2/v3 反序列化和严格结构校验 |
| `rscheck/npi_runner.py` | collector 文件协议、临时目录、超时和 inventory 加载 |
| `rscheck/kdebug_collector.py` | collector 文件协议到 `kdebug.v1` JSON action 的适配 |
| `rscheck/checker.py` | 无文件 I/O 的核心对比算法 |
| `rscheck/reporting.py` | 控制台、JSON v4 和 CSV 输出 |
| `rscheck/cli.py` | 命令行参数、应用编排、数据库 CRUD 和退出码 |
| `rscheck/gui_backend.py` | GUI 命令构造、报告防腐校验和跨平台进程控制 |
| `rscheck/gui.py` | Tkinter 页面、配置草稿、导入导出、执行和证据展示 |
| `kdebug` C++ frontend | action 注册、公共 envelope/资源和基础必填项校验、engine 转发 |
| `kdebug_engine.py` | 临时文件协议、Verdi 启动、partial 判定和响应封装 |
| `kdebug_npi.tcl` | Verdi 内 Tcl action 分发和统一响应 |
| `rscheck_inventory.tcl` | NPI hierarchy、port、parameter 和 clock trace 采集 |

旧 `npi/rs_npi_collector.cpp` 只作为 A/B baseline 保留，不是当前默认在线后端。

## 7. 配置和输入模型

### 7.1 完整配置

配置文件是一个严格校验的 JSON 文档，不是独立数据库服务。规范化完整配置有且只有
六个根：

| 根节点 | 内容 |
| --- | --- |
| `excel` | sheet、表头行、数据起始行和可选严格表头检查 |
| `columns` | 九个内部字段到唯一 1-based 列号的映射 |
| `rtl` | suffix 规则、legacy 端口、索引和 CRG 深度等通用配置 |
| `position_mappings` | position 简写到完整 hierarchy 的精确映射 |
| `crg_source_mappings` | CRG_source 简写到完整 hierarchy 的精确映射 |
| `module_rules` | module 的门控、step parameter 和 formal port 规则 |

GUI 完整导入/导出使用 `load_complete_config`，要求六个根全部存在，并覆盖三类规则库；
导出内容不保存 `KDEBUG_BIN`、license 或设备路径。普通 `load_config` 为旧配置保留兼容：
`columns` 仍必须完整，其他缺省区段按默认值或空库补齐，规范化保存后会写出全部六个根。

配置在内存中完成归一化校验后，写入同目录临时文件并用 `os.replace` 替换。GUI 保存单个
规则库时会检测该区段是否被其他进程修改；当前仍是单写者模型，不提供多写者自动合并。

### 7.2 Excel 九个内部字段

| 内部字段 | 行内要求 | 用途 |
| --- | --- | --- |
| `Intf_type` | 必填 | 报告标签，不参与 RTL 判定 |
| `RS_module` | 必填 | module rule 和 RTL definition 匹配键 |
| `RS_inst` | 必填 | 本地实例名或实例组前缀 |
| `position` | 必填 | RS 实例组所在 scope，可经映射解析 |
| `step` | 必填非负整数 | 期望有效拍数 |
| `clk` | 必填 | 期望 clock 实际连线 |
| `rst` | 必填 | 期望 reset 实际连线 |
| `CRG_source` | 必填 | 期望上游 CRG 完整实例路径，可经映射解析 |
| `RS_CFG_EN` | 单元格可空 | “假门控”标签或精确 `NA` 跳过标志 |

九个字段都必须配置唯一列号，但 Excel 可包含任意额外列，列可以乱序、不连续，表头也
可以使用任意业务名称。`validate_headers=false` 时不要求表头等于内部字段名。

支持 `.xlsx`、`.xlsm`、`.csv` 和 `.tsv`；不支持旧二进制 `.xls`。全空行被跳过，
部分填写的行会报告所有缺失字段。

### 7.3 映射语义

position 和 CRG_source 都采用区分大小写的精确一层映射：

```text
Excel 简写 --精确查表--> 完整 RTL hierarchy
未命中       ---------> 按用户已经填写完整路径处理
```

不做模糊匹配、前缀匹配或 alias 递归展开。`SpecRow` 同时保存 alias 和解析后的完整值，
用于 GUI、JSON 和 CSV 展示。

## 8. 核心数据模型

![RTL 打拍例化检查工具核心数据模型图](images/architecture/rs-tool-core-data-model.png)

### 8.1 证据与结论分离

- `SpecRow` 保存用户期望和解析后的完整路径。
- `Inventory` 只保存 RTL 证据，不写入 `step` 或门控业务结论。
- `InstanceStepEvaluation` 保存每个 parameter 的 present/raw/state/contribution。
- `CrgTraceEvaluation` 保存目标、trace 状态、匹配节点和诊断。
- `Finding` 统一保存 severity、code、expected、actual 和实例定位。
- `RowResult`、`CheckReport` 才表达 PASS/FAIL。

## 9. 端到端主流程图

![RTL 打拍例化检查工具端到端主流程图](images/architecture/rs-tool-end-to-end-workflow.png)

## 10. 在线采集设计

### 10.1 Collector 文件协议

`npi_runner.collect_inventory` 将所有规格行聚合为：

- 去重后的完整 position 列表。
- 按 module rule 生成的 `module -> clock formal` trace rules。
- 全局 trace depth 和 legacy clock/reset fallback。

这些内容写入唯一临时目录，随后只启动一次 collector。collector 返回 Inventory JSON，
runner 重新加载并严格验证后才允许保存用户指定的 inventory 副本。

### 10.2 kdebug JSON action

adapter 把文件协议转换为单个公共请求：

```json
{
  "api_version": "kdebug.v1",
  "action": "rscheck.inventory",
  "target": {"elab_db": "/abs/path/TB.elab++"},
  "args": {
    "positions": ["top.u_tile"],
    "trace_rules": {"rs_pipe": "clk"},
    "trace_max_depth": 16,
    "clk_port": "clk",
    "rst_port": "rst_n"
  },
  "limits": {"timeout_ms": 58000},
  "output": {"format": "json"}
}
```

`rscheck.inventory` 是 `design` 类稳定 action。C++ frontend 负责 action 注册、公共 envelope、
design resource 和基础必填项校验，再把完整公共 JSON 交给 Python engine；Python engine
继续校验 action 参数的完整类型、范围和组合约束。该 action 的唯一设计资源是绝对
`target.elab_db`，不接受 filelist 或 Verdi 参数透传。

### 10.3 Python engine 到 Verdi

Python engine 在独立临时目录中创建 UTF-8 request、positions、trace rules 和 response
文件，通过受控环境变量把路径交给 Tcl，然后执行：

```bash
verdi -batch -nologo -play <kdebug_npi.tcl> -elab <KDB>
```

Tcl 分发器加载 `rscheck_inventory.tcl`，在 Verdi 进程内部调用 NPI。Tcl 生成 embedded
Inventory v3；Python engine 再执行 top/queryable、partial 和响应封装。adapter 最后用
主工具自己的 Inventory loader 再验证一次，采用临时文件和 `os.replace` 原子写出。

### 10.4 正常调用时序

![GUI CLI kdebug Verdi NPI 在线采集正常调用时序图](images/architecture/rs-tool-online-collection-sequence.png)

## 11. NPI 采集算法

### 11.1 Position 和实例

- 对每个 position 做精确 hierarchy 查找。
- 找不到时输出 `found=false`，而不是让整个 action 失败。
- 只采集 position 的直接 module children。
- generate scope 对遍历透明，嵌套 generate 中的直接 module 会被输出。
- 不下钻普通 module 的内部实例，避免把不同 ownership 层级混在一个 position。
- 实例按稳定键排序，保证多次运行 JSON 可比较。

### 11.2 Formal port

端口同时使用语言层和 Netlist 层证据，单个端口失败不丢弃其他端口。Netlist 名称查找
遵循 typed-first：

1. formal port 优先 `npiNlInstPort`。
2. connection 优先 `npiNlNet`。
3. module instance 优先 `npiNlInst`。
4. 明确类型失败后才使用 `npiNlUndefined`。

这样可以避免 instance、net 和 inst-port 同名时错误选中 instance，并防止 CRG false PASS。
所有 iterator 和拥有所有权的 handle 都在对应作用域中释放。

### 11.3 Effective parameter

采集器枚举实例最终生效的 parameters，并保留可可靠取得的原始字符串。已枚举但无法可靠
取值的 parameter 保存为 `null`；根本没有枚举到的 parameter 则不产生该 key。checker
区分 missing 与 unresolved，再把常见十进制、Verilog based literal、`'0`/`'1` 归一为
`zero/nonzero/unresolved`，两类证据不足都按 fail-closed 处理。

### 11.4 Partial KDB

`npi_load_design` 返回非零不直接决定 action 失败。Python engine 使用 Tcl 返回的 top
摘要做硬门禁：

- 至少一个 top 可查询：保留 inventory，并增加唯一 `NPI_LOAD_PARTIAL` notice。
- 没有可查询 top：返回结构化 `NPI_LOAD` action error。

partial notice 只是 warning，不会遮蔽 position、port、parameter 或 trace 的实际硬错误。

## 12. CRG 有界递归追踪

采集器不知道 Excel 期望的 CRG_source；它为每个 RS 实例收集指定 clock formal 可达的
上游 module 图。checker 随后用解析后的 CRG_source 完整 hierarchy 精确匹配 trace 节点。

### 12.1 追踪流程图

![CRG 来源有界递归追踪流程图](images/architecture/rs-tool-crg-trace-workflow.png)

### 12.2 有界性和缓存

- 用户最大 module hop 默认 16，可配置范围 1..256。
- 单条 trace 另有 Netlist 对象预算，防止异常图无限膨胀。
- 使用对象 visited set 终止环路。
- trace state/cache key 包含完整 instance hierarchy，不按 local name 共享。
- `excluded_inputs` 当前固定为精确小写 `clk`、`rst_n`。
- witness path 长度必须和 depth 一致，最后节点必须等于 module instance。

## 13. 单行检查流程

![Excel 单行规格与 RTL 证据检查流程图](images/architecture/rs-tool-row-check-workflow.png)

每个错误节点只追加 Finding，不抛弃已经取得的其他证据。所有 `RowResult` 生成后，
`check_specs` 再按 `position + full instance hierarchy` 汇总 ownership；同一物理实例被多个
Excel 组匹配时生成全局 `AMBIGUOUS_GROUP_MATCH` error。

### 13.1 RS_inst 分组

- 实例本地名必须以 Excel `RS_inst` 开头。
- remainder 为空时合法，表示用户填写了完整本地实例名。
- remainder 非空时必须完整匹配 `suffix_regex`。
- suffix 必须提供 ASCII 数字命名组 `index`，可选检查连续索引。
- 完整名输入不是强制 exact-only：若同时存在 `PFX` 和 `PFX_C0`，`RS_inst=PFX`
  可以同时匹配；跨行重复 ownership 会报告歧义。
- 即使 Excel `step=0`，完全没有物理实例仍报告 `GROUP_NOT_FOUND`。

### 13.2 动态 step

对 module 名正确的每个匹配实例：

| step parameter 状态 | contribution |
| --- | --- |
| 没有配置 step parameter | `1` |
| 所有配置 parameter 都是已知非零 | `1` |
| 全部已解析，且至少一个为零 | `0` |
| 任意一个缺失或 unresolved | `null`，优先级最高，整行 fail closed |

所有 contribution 可解析时求和，并与 Excel `step` 比较。

### 13.3 门控规则

| module rule / Excel / RTL | 结果 |
| --- | --- |
| Excel `RS_CFG_EN` 精确为 `NA` | 只跳过本行门控标签和 `RS_CRG_EN` 检查 |
| `has_rs_cfg_en=true` | Excel 必须精确为“假门控”，RTL 必须存在可解析且为 0 的 `RS_CRG_EN` |
| `has_rs_cfg_en=false` 且 RTL 无该参数 | Excel 任意内容不 care |
| `has_rs_cfg_en=false` 但 RTL 实际存在该参数 | 规则与 RTL 不一致，报告 unexpected error |

兼容 finding code 仍使用 `RS_CFG_EN_*`，message 和证据明确指向 RTL `RS_CRG_EN`。

### 13.4 Clock/reset

- formal 名来自 module rule，未注册 module 使用 `clk/rst_n`。
- Excel `clk/rst` 是期望实际连接，不是 formal 名。
- clock 和 reset 完全独立取证；clk formal 存在、已连接且匹配，而 rst formal 缺失时，
  只报告 `RST_PORT_MISSING`，不会误报 clock missing/unconnected。
- 空连接报告 `*_UNCONNECTED`。
- 复杂 Operation 对象报告 `UNSUPPORTED_CONNECTION`，不进行字符串猜测。
- 默认按规范化完整信号路径匹配；可选 legacy leaf match。

## 14. GUI 设计

工具自带 GUI 是 Tkinter 应用，不是 Verdi GUI。主要页面：

1. 检查配置。
2. 模块规则库。
3. Position 映射库。
4. CRG Source映射库。
5. 检查结果。
6. 运行日志。

### 14.1 状态管理

- GUI 为三类规则库维护内存草稿、加载基线和 dirty 状态。
- 配置路径改变后必须显式加载，避免使用旧内容写入新文件。
- 完整导入先解析并验证候选配置，确认丢弃 dirty 草稿后再整体替换。
- 导出前重新验证完整快照，并拒绝覆盖当前配置或输入/输出路径别名。
- 当前在线结果按 report v4 防腐校验；历史 report v2/v3 按对应兼容合同只读加载，GUI
  不直接信任任意 JSON。

### 14.2 后台执行和取消

GUI 不在 Tk 主线程运行检查。`gui_backend` 构造与 CLI 相同的命令，由
`ProcessController` 启动独立进程组并并发 drain stdout/stderr。GUI 线程只接收
`WorkerOutcome`，更新日志、摘要、逐行 finding 和证据视图。

Linux GUI 探测可使用当前 shell X11，也可从桌面/Xwayland/VNC/XRDP 进程发现 display，
不依赖 GNOME 或固定用户名。Windows/macOS 支持 validate 和离线检查；在线 Verdi/KDB
采集只支持 Linux。

## 15. 错误、warning、notice 和退出码

| 类型 | 示例 | 是否使检查失败 |
| --- | --- | --- |
| 基础设施错误 | 配置、Excel、KDB、collector 或 schema 错误 | CLI 退出码 `2` |
| `error` Finding | RTL 与规格硬检查不一致 | 行 FAIL，CLI 退出码 `1` |
| `warning` Finding | CRG 未找到、深度受限、证据不可用、suffix tag warning | 不改变行 PASS |
| inventory notice | `NPI_LOAD_PARTIAL` | 转成可见 warning，不单独使检查失败 |
| 正常通过 | 无 error Finding | CLI 退出码 `0` |

Inventory 中普通 collector warning 默认按 fail-closed 处理；只有明确识别的 CRG 追踪类
warning 不升级为全局 error。

## 16. 进程生命周期和原子性

### 16.1 分层 timeout

在线链路存在多层 deadline：

1. GUI 默认配置 collector 外层 timeout；CLI 仅在显式传入 `--npi-timeout` 时启用该层。
2. adapter 对 kdebug frontend 的硬 timeout。
3. kdebug request 传给 engine 的更短内部 timeout。
4. engine 对 Verdi 的有界 timeout。

存在用户外层 timeout 时，各内层 deadline 依次提前，为响应封装和进程清理保留时间。
CLI 未指定外层 timeout 时，adapter 仍有 125 秒硬上限，engine 对 Verdi 默认使用 120 秒。

### 16.2 取消/超时序列

![GUI 在线检查取消与超时清理时序图](images/architecture/rs-tool-timeout-cancellation-sequence.png)

Windows GUI 使用 `taskkill /T /F` 清理子树；POSIX 使用稳定 PGID，leader 先退出后仍会
清扫组内后代。C++ engine child 设置 Linux `PR_SET_PDEATHSIG`，Verdi 使用独立 session。
所有 `communicate()`、`wait()` 和 pipe drain 均有上限。

### 16.3 原子输出

- 配置先完成归一化校验，再写同目录临时文件并原子 replace。
- adapter inventory 和保留的 inventory 副本执行 flush/fsync 与 loader/schema 校验后，
  再原子 replace；失败时保留原目标文件。
- action 临时目录和本次活动 session/registry 记录在退出路径清理。
- 用户显式指定或继承的 `KDEBUG_HOME` 不删除，其中日志可保留用于复现。

## 17. 安全与信任边界

- 在线唯一设计输入是已有 `*.elab++` 目录。
- 明确拒绝 `work.lib++`、filelist、RTL、`-f/-sv/-lib/-top` 和任意 Verdi 参数透传。
- 不执行独立 license server 连通性预检，实际 Verdi 负责报告环境问题。
- kdebug ELF 和 adapter 不链接、不加载 `libNPI/libnpiL1`；NPI 只在 Verdi Tcl 内部。
- 用户指定的离线 inventory 被视为可信快照，工具只验证结构，不验证来源和新鲜度。
- 配置不保存 license、`KDEBUG_BIN` 或机器专属会话凭据。
- 日志不打印 license 值，不应提交生产 KDB、RTL、Synopsys 文件或设备凭据。
- 所有 JSON/Tcl 交换使用 UTF-8 和 LF；CSV 使用 UTF-8 BOM 便于 Excel 打开。

## 18. 性能设计

主要性能策略：

- 所有 Excel 行先聚合 position 和 trace rule，一次 action 只加载一次 KDB。
- position 去重，实例和 trace 输出稳定排序。
- clock trace 使用 visited set、module depth 和对象预算三重边界。
- 完整 hierarchy 作为 cache 身份，防止不同 scope 的同名 cone 污染。
- checker 是内存纯计算，不重复访问 Verdi。
- GUI 后台 drain stdout/stderr，避免大输出填满 pipe 阻塞子进程。

性能目标不是常驻服务吞吐量，而是单次大型 KDB 检查的稳定性、可取消性和证据完整性。

## 19. 测试策略

| 层级 | 覆盖重点 |
| --- | --- |
| Python unit | Excel、配置、映射、分组、parameter、ports、CRG、报告、GUI 状态 |
| kdebug engine unit | request 准备、UTF-8、partial 封装、Verdi timeout/取消 |
| Tcl mock | NPI handle、typed lookup、parameter、分支 trace、环路和编码 |
| C++ unit | action 注册、资源、ProcessRunner、PDEATHSIG、session 路径 |
| JSON contract | action catalog、request/response schema 和 example |
| 真实 KDB focused | clean/partial、20 次重载、1 秒 timeout、live Verdi cancellation |
| Fresh GUI | GitHub clean checkout、可见 Verdi/Tk、专项循环和 10,000 行负载 |

当前有效签核结果：

- 主工具本机：321 tests，OK，50 项因平台条件 skip；wheel/sdist 和隔离 user install PASS。
- VM kdebug：Python 35 passed、Tcl PASS、182 个 schema 文件校验、175 个 example 文件校验、
  C++ unit PASS、29 个 runtime contract PASS。
- 真实 KDB：raw/adapter clean+partial PASS，两个 position、26 个实例，20/20 次 reload PASS。
- timeout/cancel：确认 live Verdi 后 TERM frontend，1 秒 timeout 后无进程、临时目录、
  crash marker 或活动 session 泄漏。
- Fresh GUI：VM 321/321；online positive 3 轮；CRG depth-limit、clk 有/rst 无、
  don't-care、`NA`、CRG mapping 各 20 轮；GUI 100 轮；10,000 行 PASS。
- Verdi 窗口明确加载 elaborated `top`，Tk 窗口为 mapped。

## 20. 部署与构建

### 20.1 主工具

```bash
python -m pip install .
rtl-rs-check --help
rtl-rs-check-gui
```

CLI 核心没有第三方运行时依赖；GUI 依赖 Python Tk。

### 20.2 kdebug 后端

在对应 `kverif` checkout 中：

```bash
make -C kdebug -j2 all
PYTHON=python3 make -C kdebug test-fast
export KDEBUG_BIN="$PWD/kdebug/kdebug"
```

`ldd "$KDEBUG_BIN"` 不得出现 `libNPI`、`libnpiL1` 或 `not found`。在线运行设备仍然
必须安装合法且与 KDB 兼容的 Verdi/NPI 环境；隔离 NPI API 不等于消除 Verdi 依赖。

## 21. 当前限制和演进方向

### 21.1 当前限制

- 最终真实 VM KDB 签核 fixture 未明确覆盖 generate block，当前 generate 行为主要由实现、
  mock/静态合同和 NPI 语义支撑。
- 复杂 clock/reset Operation 不尝试表达式求值。
- 普通 module hierarchy 不递归下钻，只透明穿过 generate scope。
- CRG 输入排除名固定为 `clk/rst_n`，尚不是按每种中间 module 配置。
- 配置文件是单写者模型，没有数据库事务或多进程合并。
- `crg_match` 是历史兼容配置；Inventory v3 实际按完整 CRG instance hierarchy 精确匹配。
- instance array 或非标准后缀需要用户定制 `suffix_regex`。

### 21.2 建议演进

1. 在真实 KDB fixture 中增加嵌套 generate scope 并纳入 fresh 签核。
2. 为“module 无 reset”增加显式可选规则，而不是依赖缺口错误。
3. 将中间 clock module 的排除 input 名扩展为可配置规则库。
4. 若需要检查数据链，新增独立 data-path inventory/schema，不复用 clock trace 推断。
5. 为超大设计增加采集阶段计时、对象数量和 cache 命中统计。
6. 保持 Inventory/Report schema 向后兼容，通过新版本增加字段而不是改变旧字段语义。

## 22. 设计资料索引

- [使用手册](USAGE.md)
- [测试指南](TESTING.md)
- [kdebug 后端说明](KDEBUG_BACKEND.md)
- [最终 kdebug VM 签核记录](TEST_RESULTS_KDEBUG_BACKEND_2026-07-27.md)
- [本机用户 Prompts 与需求整理](LOCAL_USER_PROMPTS_AND_REQUIREMENTS.md)
- [示例配置](../config/rscheck.example.json)
- [主工具 README](../README.md)
