# 本机用户 Prompts 与工具需求整理

本文整理的是本次工具开发过程中，用户在本机对话中依次输入的 prompts。
它不是重新编写的实现方案，也不引用助手的回复或实现代码。

整理方式：

- 按需求出现的先后顺序归类。
- 同一句话连续重复时合并，并标明“多次重复强调”。
- 后续 prompt 修改了早期规则时，在“最终有效规则”中说明覆盖关系。
- VM 地址、登录密码和其他设备信息已脱敏。
- 公开 GitHub 项目名称和本机 NPI 手册路径予以保留。

## 一、原始需求提出

用户首先提出要开发一个 Excel 与 RTL 对照检查工具。

原始需求要点：

> Excel 有很多列，工具只需要处理其中的 `Intf_type`、`RS_module`、
> `RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`。
> 用户可以自行定义这些字段分别位于第几列。

> 该工具用于检查 RTL 中打拍模块的例化是否正确。

各字段最初定义：

- `Intf_type`：需要打拍的输入/输出 interface。
- `RS_module`：打拍模块的 module 名。
- `RS_inst`：打拍实例名称前缀。例如实例为 `AAAA_BBB_C0`、
  `AAAA_BBB_C1` 时，`RS_inst` 可以填 `AAAA_BBB`；相同前缀的实例是一组。
- `position`：打拍模块所在的例化路径。
- `step`：当前 position 下当前实例组的总打拍数；组中每个有效实例贡献一拍。
- `clk`：该组实例的 clock formal port 实际连线。
- `rst`：该组实例的 reset formal port 实际连线。
- `CRG_source`：该组 clock 连线的来源模块。

> 同一个 position 下可以存在多个实例组，每个组占 Excel 一行。

> RTL 通过 Verdi NPI 进行分析，NPI 手册位于：
> `D:\VMshare\CPU_CORE\ysyx\npc\VC_APPS_NPI.pdf`。

## 二、Excel 格式和列映射

用户随后补充了 Excel 相关要求：

> 给出你认为合理的 Excel 格式。

> Excel 可以包含其他业务列，不要求只包含工具使用的字段。

> 用户可以指定每个内部字段对应 Excel 的第几列。

> Excel 表头不需要严格等于工具内部字段名。例如用户选择第 3 列作为
> `Intf_type`，第 3 列的实际表头不需要叫 `Intf_type`。

最终含义是：`Intf_type` 等名称是工具内部对列属性的称呼，不是对 Excel 可见
表头的强制要求。

## 三、VM 测试、GUI 和文档

用户要求在一台 Linux VM 上进行开发和测试。原 prompt 中包含了具体 SSH 地址和
密码，本文已替换为：

```text
ssh root@<VM_HOST>
password: <REDACTED>
```

围绕 VM 和交付，用户依次提出：

> 在 VM 上测试开发的工具。

> 给出完整的 VM 测试命令。

> 命令必须能直接复制使用，并且启动 GUI 进行测试。

> 给出详细的工具测试文档和使用文档。

> 通过 GitHub Desktop 上传 GitHub。

> 将所有修改和测试内容上传仓库，保证其他设备可以复现。

> 在其他设备上运行 GUI 时出现：
> `ERROR: no active gnome-session-binary session for user`。

> 工具不能依赖固定的 GNOME session；要求在其他具备 Tk 和 Verdi 的设备上也能
> 正常运行 GUI。

> 工具必须自带 GUI，用户可以在 GUI 中输入配置、执行检查并查看结果。

> 在 VM 上启动 GUI 做压力测试，并上传 GitHub。（该要求被连续重复强调。）

> 不需要做 license/连接服务器校验；运行设备本身就是可以运行 Tk 和 Verdi 的设备。

因此 GUI 的目标不是 Verdi 启动页，而是工具自己提供的配置、运行和结果查看界面；
同时完整测试中还需要启动真实 Verdi GUI 验证 elaborated KDB。

## 四、NPI 输入必须是 elaborated KDB

用户明确限制 NPI 输入形式：

> NPI 输入必须是 Verdi 的 elaborated library/KDB，不能是 filelist。

后续所有测试和命令都应以现有 `*.elab++` 为输入，不能把 RTL/filelist 直接传给
collector。

## 五、RS parameter 和有效打拍数

用户在原八个字段基础上增加了 Excel 列：

```text
RS_CFG_EN
```

最初规则：

> 部分 `RS_module` 有 `RS_CFG_EN` parameter。需要检查这些 module 的 parameter。
> 如果 parameter 为 0，并且 Excel 该行 `RS_CFG_EN` 填写“假门控”，则该项 PASS，
> 否则该行 FAIL。

之后用户将 RTL parameter 的最终名称改为：

```text
RS_CRG_EN
```

也就是说，Excel/internal 列名仍可保留 `RS_CFG_EN`，但实际 RTL parameter 匹配的是
`RS_CRG_EN`。

用户进一步要求建立 `RS_module` 规则数据库：

> 实际使用时 `RS_module` 可能有很多类型。有的有 `RS_CRG_EN`，有的没有；
> 有的还有其他决定打拍数的 parameter。

> 用户可以事先把不同 `RS_module` 录入工具数据库，并为每个 module 指定：
> 是否有 `RS_CRG_EN`、是否有其他决定打拍贡献的 parameter。

动态打拍示例：

> 如果 parameter 名为 `rs_mode`，当 `rs_mode=0` 时，该实例不参与打拍，
> 对 `step` 的贡献为 0；当 `rs_mode!=0` 时，该实例贡献 1。

> 某 module 实际例化 6 个实例，其中 5 个 `rs_mode!=0`、1 个 `rs_mode=0`，
> 则实际有效打拍数为 5，Excel 的 `step` 必须为 5 才算 PASS。

数据库匹配要求：

> 工具运行时根据 Excel 的 `RS_module` 匹配 module 数据库，再决定实际 RTL 拍数的
> 计算方式。规则无法正确应用时要给出错误，不能静默猜测。

未注册 module 的默认规则：

> 未在数据库中匹配到的 `RS_module`，默认每个实例对 `step` 的贡献为 1，
> 默认认为存在 `RS_CRG_EN` parameter。

后续 don't-care 规则：

> 如果一个 `RS_module` 被明确设置为没有 `RS_CRG_EN` parameter，则 Excel 该项
> 无论填写什么都不检查。

> 如果 Excel 某行 `RS_CFG_EN` 填写精确的 `NA`，则该行不做 `RS_CRG_EN` 检查。

## 六、position 和 CRG_source 简写数据库

用户指出 Excel 中的路径不一定是真实 RTL 全路径。

position 要求：

> Excel 中的 `position` 可能只是简写。工具需要提供一个数据库，让用户录入
> position 简写与 RTL 完整 hierarchy 的对应关系。

> 解析 Excel 后，根据简写找到实际 RTL 全路径，再进行检查。

CRG_source 要求：

> `CRG_source` 与 position 一样，也可能是简写。工具需要提供 CRG_source 简写与
> RTL 完整 hierarchy 的映射数据库。

> 解析 Excel 后，根据 CRG_source 简写找到实际来源模块全路径。

这些数据库需要同时支持 GUI 操作和配置导入导出。

## 七、RS_inst 允许完整实例名

用户多次重复强调以下规则：

> 允许 `RS_inst` 后缀为空。

> 允许用户在 `RS_inst` 中填写完整的本地例化名。

这条要求连续重复多次，表示它属于必须防回归的核心行为。

最终需要同时支持：

- 填写组前缀，匹配带后缀的一组实例。
- 填写完整实例名，只匹配该实例。
- 实例名相对前缀的后缀可以为空。
- 前缀重叠或同一实例同时匹配多个 Excel 组时，需要报告歧义，不能重复计数。

## 八、partial elab++ 加载问题

用户报告了其他设备上的实际失败：

> RTL 检查时报 `NPI collector exited with code 11`，`npi_load_design` 无法加载
> 某个 `TB.elab++`。

> 但在当前终端执行 `$VERDI_HOME/bin/verdi -elab <TB.elab++>` 可以正常打开。

> `rs_npi_collectorLog/compile.log` 中虽然有 error，Verdi GUI 也会显示这个 error，
> 但不影响查看和解析项目内容。

用户提供了可正常加载同类 KDB 的公开参考项目：

```text
ysyx-22040210-yudian/verdi_npi_port_trace
```

用户随后询问并要求：

> 当前工具是否已经能像该项目一样加载这种 `elab++`？

最终需求是：不能只根据 `npi_load_design` 返回值或 compile.log 中存在 error 就立即
失败。如果 top hierarchy 仍然可查询，应继续收集可用证据，并明确报告 partial-load；
只有设计完全不可查询时才终止。

## 九、配置导入导出

用户要求：

> 工具 GUI 支持导入和导出配置。

> 配置必须包含所有数据库内容。

“所有数据库”至少包括：

- Excel 内部字段到列号的映射。
- `RS_module` 规则和 parameter 规则。
- 每个 module 的 clock/reset formal port 名。
- position 简写到完整 hierarchy 的映射。
- CRG_source 简写到完整 hierarchy 的映射。
- CRG 递归深度等运行配置。

导入失败不能破坏当前 GUI 状态；导出后重新导入应完整恢复配置。

## 十、clk/rst formal port 问题

用户遇到过以下检查结果：

```text
module rule requires RS_CFG_EN but RTL parameter is missing
formal port 'clk' is missing
formal port 'rst' is missing
no upstream module source was resolved for clk
```

用户指出：

> 当前 `RS_module` 明明有 clk input，只是没有 rst，为什么仍显示 clk missing？

随后给出最终端口规则：

> 录入 `RS_module` 数据库时，支持配置该 module 的 clk 和 rst formal port 名称。

> 用户未填写时，默认接口名为 `clk` 和 `rst_n`。

> 需要在 VM 上复现“clk 存在但误报 missing”的问题并做压力测试。

有效行为应是：clk 存在、rst 缺失时只报告 rst 缺失，不能因为其中一个 formal 不存在
而丢掉另一个 formal 的证据。

## 十一、CRG_source 递归追踪

用户最初曾要求暂时不判断 CRG_source 正确性，之后又用更具体的 prompt 覆盖了该规则：

> RS 实例的 clk 到 CRG_source 中间可能经过 OCC、mux、门控等很多 module。

> 从 RS 实例 clk formal 开始，追到上游 module A 的 output 后，继续追 module A 的
> 所有 input，但排除 `clk` 和 `rst_n`。

> 再从这些 input 追到 module B 的 output，然后继续追 module B 除时钟和复位之外的
> input，循环执行。

> 如果在配置深度内找到 CRG_source 的完整 hierarchy，则该子检查 PASS；
> 如果没有找到，则报告 warning。

> 递归层数由用户配置，不能无限递归。

因此最终 CRG 行为是“有界、多分支、可终止的上游 module 图追踪”，而不是只查看
clock 的直接 driver。

## 十二、用 kverif/kdebug 替代直接 NPI collector

用户提出新的后端方向：

> 分析公开项目 `ysyx-22040210-yudian/kverif` 中编译后的 NPI 可执行文件 `kdebug`
> 是否可以替代当前工具的 NPI 部分。

> 如果可行，对 `kdebug` 进行二次开发，作为工具的 NPI 实现，使主工具不需要直接
> 调用 NPI 函数。

> 完成后在 VM 上做压力测试，并在 GitHub 新建分支提交。

之后用户多次输入“继续”，要求任务不中途停在分析或半成品状态。

这部分需求最终形成的边界是：

- 主工具只通过稳定的 JSON action 调用 kdebug。
- kdebug frontend 和主工具不直接链接 NPI。
- NPI 实际运行在 Verdi 内部 Tcl 环境。
- kdebug 输入仍然只能是 `*.elab++`。
- 需要验证 clean KDB、partial KDB、timeout、取消和残留进程清理。
- 修改分别提交到独立 GitHub 分支。

## 十三、给其他模型的上下文要求

工具完成后，用户又要求整理一份可以交给其他模型的任务上下文。

最初问题：

> 如果要在其他模型上做一个和当前 kverif 分支相同的工具，需要输入什么上下文？

随后增加限制：

> 不可以参考现成主工具项目，就当从 0 开始做。

> 任务上下文要口语化一些，让人也能一下看明白。

最后对文档目标进行了纠正：

> 要整理的是用户在本机输入的 prompts，不是助手重新生成一份开发 prompt。

本文就是对这一最终要求的响应。

## 十四、最终有效规则

下面是将全部 prompts 合并后，当前应以其为准的最终规则。

### Excel

- 工具处理九个内部字段：`Intf_type`、`RS_module`、`RS_inst`、`position`、
  `step`、`clk`、`rst`、`CRG_source`、`RS_CFG_EN`。
- 用户按 1-based 列号指定字段位置。
- Excel 可以有任意其他列。
- 可见表头不要求等于内部字段名。
- `Intf_type` 是报告标签，不直接参与 RTL 结构判断。

### 实例和打拍数

- `RS_inst` 可以是组前缀，也可以是完整本地实例名。
- 后缀允许为空。
- 每个实例的 step 贡献由 module 规则和 effective parameter 决定。
- 未注册 module 默认每个实例贡献 1，默认认为有 `RS_CRG_EN`。
- module 明确没有 `RS_CRG_EN` 时，Excel `RS_CFG_EN` 内容不参与检查。
- Excel `RS_CFG_EN=NA` 时，跳过该行的 `RS_CRG_EN` 检查。
- RTL parameter 的最终名称是 `RS_CRG_EN`，不是早期 prompt 中的 `RS_CFG_EN`。

### module 数据库

- 用户可以录入任意 `RS_module` 类型。
- 可以配置是否存在 `RS_CRG_EN`。
- 可以配置决定 step 贡献的其他 parameter。
- 可以配置 clock/reset formal port 名。
- 默认 formal port 为 `clk`、`rst_n`。
- Excel module 无法应用对应规则时要给出明确错误。

### 路径数据库

- position 支持简写到完整 RTL hierarchy 的映射。
- CRG_source 支持简写到完整 RTL hierarchy 的映射。
- GUI 支持两类映射的增删改查。
- 配置导入导出包含全部数据库。

### RTL 后端

- 输入只接受 Verdi elaborated `*.elab++`。
- 不接受 filelist/RTL 作为 collector 输入。
- 可查询的 partial KDB 继续采集，并输出明确 notice。
- 主工具不直接调用或链接 NPI。
- 通过 kdebug JSON action 调用 Verdi 内部 Tcl NPI。
- timeout 和取消必须有界，并清理整个进程树。

### CRG 追踪

- 从 module 规则选择的 clock formal port 开始。
- 支持 OCC、mux、门控和任意中间 module。
- 命中上游 module output 后，继续追踪其除 `clk`、`rst_n` 外的 input。
- 支持分支、环路去重和 hierarchy 隔离。
- 用户配置最大层数。
- 找到 CRG_source 完整 hierarchy 为 PASS；未找到或达到深度上限为 warning。

### GUI、测试和交付

- 工具自带 Tkinter GUI，可配置、执行并查看结果。
- 不依赖固定 GNOME session 或固定用户名。
- 不增加独立的 license server 连通性校验。
- VM 命令应可直接复制使用并启动可见 GUI。
- 需要 clean/partial KDB、专项 20 轮、稳定性 100 轮和 10,000 行负载测试。
- 所有源码、脚本、测试和文档上传 GitHub，便于其他设备复现。

## 十五、prompt 覆盖关系

为避免阅读历史 prompts 时产生歧义，需要注意以下后续覆盖：

| 早期说法 | 后续最终说法 |
| --- | --- |
| RTL parameter 为 `RS_CFG_EN` | RTL parameter 改为 `RS_CRG_EN`；Excel/internal 列仍叫 `RS_CFG_EN` |
| 默认 reset 端口按早期示例写 `rst` | module 规则未填写时默认 `rst_n` |
| 暂时不检查 CRG_source | 后续要求有界递归追踪；找不到时报 warning |
| `RS_inst` 是实例前缀 | 后续允许完整实例名，并允许空后缀 |
| `npi_load_design` 非零即失败 | top 仍可查询时按 partial KDB 继续采集 |
| 主工具直接使用原 NPI collector | 后续改为通过二次开发的 kdebug JSON action 隔离 NPI |
| 给其他模型一份新生成开发 prompt | 最终要求改为整理用户在本机实际输入的 prompts |

## 十六、脱敏说明

原始对话曾包含 VM SSH 地址和登录密码。为了让本文可以安全上传 GitHub，相关内容只保留
为 `<VM_HOST>` 和 `<REDACTED>`。本文没有记录 license 值、生产 KDB、生产 RTL 或其他
设备凭据。
