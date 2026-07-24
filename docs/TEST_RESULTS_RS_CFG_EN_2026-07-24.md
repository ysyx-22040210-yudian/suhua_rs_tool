# RS_CFG_EN 九字段版本验证记录（2026-07-24）

本记录对应新增 `RS_CFG_EN` Excel 字段、逐实例 effective parameter 检查、inventory/report schema v2 和九字段 GUI。设计输入严格使用 Verdi elaborated KDB；测试未向 collector 传入 RTL、filelist 或 top 参数。

## 1. 测试对象

- Windows 工作区：`D:\suhua_rs_tool`
- Linux VM 副本：`/root/suhua_rs_tool_rs_cfg_en_20260724`
- Python：Windows 当前解释器；VM SCL Python 3.8.13
- Verdi/NPI：VM 已安装的 Verdi O-2018.09-SP2
- 示例设计：`examples/rtl/rs_example.sv`
- Excel 模板：`examples/RS_Check_Excel_Template.xlsx`

示例 `rs_pipe` 的 `RS_CFG_EN` 声明默认值为 `1'b1`，三个实例均 override 为 `1'b0`；另有无关参数 `WIDTH=1'b1`。因此在线测试读取到 `RS_CFG_EN="0"` 和 `WIDTH="1"`，既证明 collector 使用 elaboration 后的逐实例 effective 值，也证明它枚举全部 value parameters，而不是只查询一个固定参数名。

## 2. 自动测试

| 环境 | 命令 | 结果 |
|---|---|---|
| Windows | `python -m unittest discover -v` | `Ran 102 tests`，`OK (skipped=22)`；80 项通过 |
| Linux VM | Python 3.8 `-m unittest discover -v` | `Ran 102 tests in 3.941s`，`OK`；102 项通过，0 skip |

覆盖内容包括九字段任意列映射、`RS_CFG_EN` 条件空值、参数不存在/0/非0/X/Z/`null`、同组逐实例差异、旧 schema v1 拒绝、schema v2 参数证据、GUI 报告解析、进程组取消和跨桌面 X11 会话发现。

## 3. Excel 模板

- `RS_Check` 输入表扩展为 `A1:I3`，第九个精确表头为 `RS_CFG_EN`。
- `字段说明` 表扩展为 `A5:G14`，明确“参数存在时填 `假门控`，不存在时留空”。
- `RS_CFG_EN` 示例单元格使用列表校验；原 `step` 正整数校验保留。
- 两张工作表均完成渲染检查；没有文字溢出、表头截断或公式错误。

## 4. 可见 GUI 与压力测试

| 用例 | 实测结果 |
|---|---|
| 离线正例循环 | 可见 Tk GUI 连续 100 次 PASS；2/2 行通过，0 error，0 warning |
| 10,000 行负载 | 10,000/10,000 行通过；0 error，0 warning；纯负载墙钟 2.12 秒 |
| 10,000 行峰值内存 | 175,612 KiB（约 171 MiB） |
| 最小窗口 | `xwininfo`：980×680、24-bit TrueColor、`Map State: IsViewable` |

已分别检查最小窗口的配置页和结果页。九个列映射、在线/离线来源、报告路径、操作按钮、`RS_CFG_EN` 结果列、finding 表和证据面板均可见且无控件重叠。截图仅作本地视觉验收，未提交仓库。

## 5. 真实 Verdi elaborated KDB

端到端脚本完成以下流程：

1. 用实际 Verdi NPI 头文件和 `libNPI.so` 构建 collector；无编译或链接错误。
2. `vericom` 编译示例 SystemVerilog；0 error、0 warning。
3. `elabcom -top top -elab <kdb.elab++>` 生成 fresh elaborated KDB；0 error、0 warning。
4. Verdi 以 `-elab <kdb.elab++>` 启动可见窗口，标题确认加载 top `top`。
5. collector 仅接收 elaborated KDB，并生成 schema v2 inventory。
6. CLI 在线正例 2/2 行 PASS，0 error，0 warning。
7. inventory 和 JSON report 中每个示例 `rs_pipe` 实例均包含 `parameters.RS_CFG_EN="0"` 和 `parameters.WIDTH="1"`。

工具自身的 Tk GUI 随后针对同一 collector/KDB 执行：

- 在线正例 3 次：2/2 行 PASS，0 error，0 warning，`contract=elab-only`；墙钟 6.45 秒，最大 RSS 51,948 KiB。
- 在线反例 3 次：1 行 FAIL，11 个 error，0 warning，包含 `RS_CFG_EN_LABEL_MISMATCH` 及原有规格差异；墙钟 6.69 秒，最大 RSS 51,952 KiB。

离线 GUI 正例连续 100 次全部通过；整轮墙钟 24.55 秒（含最后窗口保留 5 秒），最大 RSS 27,268 KiB。端到端脚本启动的 Verdi PID 在退出时已清理，执行前后的既有 Verdi PID 集合完全一致；测试副本内没有遗留 `rs_npi_collectorLog`。

## 6. 结论

九字段版本满足以下验收条件：

- Excel 中其他列可保留，九个字段列号可由用户独立配置且必须互不重复。
- `RS_CFG_EN` 参数存在时，只有 effective 值为 0 且 Excel 精确填写 `假门控` 才通过。
- 参数不存在时，只有 Excel 留空才通过；旧 inventory 或缺少参数证据不会被误判为无参数。
- Windows、Linux、压力、最小窗口、真实 NPI/KDB 和在线 GUI 正反例均已完成验证。

测试产物中的 elaborated KDB、collector 二进制、license 环境、运行日志和截图均未提交 GitHub；仓库只保留源代码、模板、可复现脚本、fixtures 和本记录。端到端脚本默认关闭它本次启动的 Verdi，避免遗留进程和 license 占用。
