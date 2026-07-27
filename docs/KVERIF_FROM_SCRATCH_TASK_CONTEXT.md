# kverif elaborated KDB inventory 工具从零开发上下文

这份内容可以直接交给其他 AI 模型或工程师。任务从原始 `kverif` 基线开始，
不查看任何已经完成的同类实现，也不依赖本机其他项目的源码或文档。

## 先说明我们要做什么

我们要给原始 `kverif` 的 `kdebug` 增加一种能力：用户传入一个 Verdi 已经
展开好的 `*.elab++` 数据库和若干 RTL hierarchy，`kdebug` 启动 Verdi，
通过 Verdi 内部的 Tcl NPI 读取设计，最后返回一份 JSON inventory。

这不是重新写一个 Verilog/SystemVerilog parser，也不是拿 filelist 重新编译 RTL。
工具只分析已经存在的 Verdi elaborated KDB。

原始代码：

```text
repository: https://github.com/ysyx-22040210-yudian/kverif
base commit: f0296a9e45db638448258d7cd5bc8499deded9aa
```

NPI 手册：

```text
D:\VMshare\CPU_CORE\ysyx\npc\VC_APPS_NPI.pdf
```

如果执行环境不能读取这个本机路径，需要把 PDF 作为附件提供给接手者。

## 用户最终怎么调用

调用方式：

```bash
kdebug --json -
```

stdin 输入一个 JSON request，stdout 返回一个 JSON response。

请求示例：

```json
{
  "api_version": "kdebug.v1",
  "action": "rscheck.inventory",
  "target": {
    "elab_db": "/project/TB.elab++"
  },
  "args": {
    "positions": [
      "top.u_tile",
      "top.u_tile_peer"
    ],
    "trace_rules": {
      "rs_pipe": "clk",
      "rs_custom": "clock_i"
    },
    "trace_max_depth": 16,
    "clk_port": "clk",
    "rst_port": "rst_n"
  },
  "limits": {
    "timeout_ms": 60000
  },
  "output": {
    "format": "json"
  }
}
```

字段含义：

- `elab_db`：已有的 Verdi `*.elab++` 目录。
- `positions`：用户希望采集的完整 RTL hierarchy。
- `trace_rules`：指定不同 module 应从哪个 clock formal port 开始追踪。
- `trace_max_depth`：最多向上追踪多少层，不能无限递归。
- `clk_port`、`rst_port`：module 没有单独配置时采用的默认端口名。
- `timeout_ms`：本次 action 的最长执行时间。

## 必须遵守的边界

1. 输入只允许已有的 `*.elab++` 目录。
2. 不允许输入 filelist、RTL 源文件、`-f` 或 `work.lib++`。
3. 不重新 elaboration，不修改或覆盖用户的 KDB。
4. 不做 license server 连通性检查，直接运行 Verdi并使用真实返回诊断。
5. `kdebug` ELF 不得链接 `libNPI` 或 `libnpiL1`。
6. Python 不得使用 `ctypes`、`cffi` 或扩展模块直接调用 NPI。
7. 所有 NPI 调用只能发生在 Verdi 内部执行的 Tcl 中。
8. 递归深度、进程等待、pipe drain 都必须有明确上限。
9. 不得提交 Verdi/NPI 文件、ELF、KDB、生产 RTL、license 或设备凭据。

期望的调用链是：

```text
用户或其他工具
  -> kdebug C++ JSON frontend
  -> kdebug Python engine
  -> verdi -batch -nologo -play <Tcl> -elab <KDB>
  -> Verdi 内部 Tcl NPI
  -> inventory JSON
```

## 成功和失败怎么返回

成功响应至少包含：

```json
{
  "api_version": "kdebug.v1",
  "action": "rscheck.inventory",
  "ok": true,
  "summary": {},
  "data": {
    "inventory": {},
    "summary": {},
    "verdi": {}
  },
  "error": null
}
```

失败响应至少包含：

```json
{
  "api_version": "kdebug.v1",
  "action": "rscheck.inventory",
  "ok": false,
  "summary": {},
  "data": null,
  "error": {
    "code": "ERROR_CODE",
    "message": "actionable diagnostic"
  }
}
```

需要同时提供严格的 request/response JSON Schema。Schema 必须保证：

- `ok=true` 时 `data` 是成功对象。
- `ok=false` 时 `data=null`，并且 `error.code/message` 非空。
- 合法的错误响应也必须通过 response schema。
- 路径、字段类型或参数错误必须返回结构化错误，不能崩溃。

## inventory 里需要有什么

inventory 顶层格式：

```json
{
  "schema_version": 3,
  "positions": {},
  "warnings": [],
  "notices": []
}
```

每个 position 至少包含：

```json
{
  "found": true,
  "instances": []
}
```

每个 module instance 至少包含：

```json
{
  "name": "AAAA_BBB_C0",
  "full_name": "top.u_tile.AAAA_BBB_C0",
  "module": "rs_pipe",
  "file": "/project/rs_pipe.sv",
  "line": 123,
  "ports": {
    "clk": {
      "connection": "top.u_tile.clk_rs",
      "type": "npiInput"
    }
  },
  "parameters": {
    "RS_CRG_EN": "0",
    "rs_mode": "1"
  },
  "clk_sources": [],
  "clock_trace": null
}
```

也就是说，工具要提供：

- 实例本地名称和完整 hierarchy。
- module definition 名称。
- 源文件和行号。
- formal port 及其真实连接。
- 实例最终生效的 effective parameter。
- 直接时钟来源。
- 有界递归得到的上游时钟模块和 witness path。

## KDB 有错误时怎么办

实际项目中可能出现这种情况：`npi_load_design` 返回失败，`compile.log` 也有
error，但相同的 `elab++` 仍能用 `verdi -elab` 打开，并且 top hierarchy 可以查询。

处理规则：

1. `npi_load_design` 成功且 top 可查询：正常采集。
2. load 报错但至少一个 top 仍可查询：继续采集。
3. 继续采集时在 `inventory.notices` 中增加且只增加一个 `NPI_LOAD_PARTIAL`。
4. partial KDB 中缺失的证据输出 warning 或 null，不能猜测。
5. 如果一个 top 都查不到，才返回 `NPI_LOAD` 错误。

不能简单地因为日志里出现 `error` 就终止。

## hierarchy 和 generate scope

对每个 `positions` 项：

1. 精确解析完整 hierarchy。
2. position 不存在时输出 `found=false`。
3. 遍历 position 下的模块实例。
4. generate scope 本身不作为 module instance 输出。
5. generate scope 里面的实例需要正常输出。
6. 支持嵌套 generate scope。
7. 不同 position 中的同名 local instance 不能共用错误缓存。
8. 所有 cache key 应包含完整 hierarchy，而不是只有 local name。

## formal port 采集

端口采集要同时利用能获得的语言层和 netlist 层证据：

1. 获取 module definition 的 formal ports。
2. 获取每个实例 formal port 的真实连接。
3. clk 存在但 rst 不存在时，仍然必须保留 clk 证据。
4. 一个端口失败不能导致整个 port map 丢失。
5. formal port 查找优先明确指定 `npiNlInstPort`。
6. net 连接查找优先明确指定 `npiNlNet`。
7. module instance 查找优先明确指定 `npiNlInst`。
8. 明确类型查不到后，才能用 `npiNlUndefined` 兜底。
9. 避免 instance、net、inst-port 同名时选错对象。
10. partial KDB 中允许用语言层 formal 信息补足 netlist 证据。
11. 所有 handle 和 iterator 都要按照 NPI 手册释放。

需要重点查阅手册中的：

```text
npi_load_design
npi_handle_by_name
npi_nl_handle_by_name
npi_handle / npi_iterate / npi_scan
npi_nl_handle / npi_nl_iterate / npi_nl_scan
npiNlInstPort / npiNlPseudoInstPort
npiNlNet / npiNlDeclNet
npiNlInst / npiNlActual
npiNlDriver / npiNlLoad
npiGenScope
handle 和 iterator release 规则
```

不要根据 API 名称猜用法，每个关键 NPI 调用都要对照手册。

## effective parameter 采集

collector 只负责提供真实 RTL 证据，不负责业务判断：

1. 收集实例最终生效的 effective parameter。
2. 支持整数、Verilog literal、宽值和字符串。
3. 无法解析时输出 null，不能擅自写成 0。
4. 单个 parameter 失败不能丢弃其他 parameter。
5. parameter 名称区分大小写。
6. 不把 `RS_CRG_EN`、`rs_mode` 的具体判断逻辑写进 collector。

## 上游时钟怎么追

起点是被检查实例的 clock formal port。module 在 `trace_rules` 中有配置时使用
配置端口，否则使用默认 `clk_port`。

追踪过程：

1. 查找当前 clock 对象的所有上游 driver。
2. 遇到模块实例 output 时，记录完整实例路径、module、depth 和 witness path。
3. 再遍历该上游模块的所有 input formal port。
4. 精确排除名称为 `clk` 和 `rst_n` 的 input。
5. 其他 input 全部继续向上追踪。
6. 支持 mux、OCC、门控、组合模块和多分支 driver。
7. 使用 visited set 防止环路。
8. 到达 `trace_max_depth` 后停止并标记 `depth_limited`。
9. 对象不可解析时标记 `unresolved`，并保留 diagnostics。
10. 输出顺序保持稳定，方便 JSON 比较和回归测试。

`clock_trace.status` 只能是：

```text
complete
depth_limited
unresolved
```

## 编码要求

1. Tcl request、response、positions 和 trace rules 文件统一使用 UTF-8。
2. 文本文件统一使用 LF。
3. Python 读取 JSON 时显式指定 UTF-8。
4. 非 ASCII hierarchy、文件路径和 escaped name 不能被转换成 `?`。
5. 增加一个在非 UTF-8 system encoding 下仍能 round-trip 中文的测试。

## timeout 和取消必须可靠

进程清理不是附加功能，而是正式验收内容：

1. C++ frontend 启动 Python engine 时，让 engine 建立独立 process group。
2. Linux child 设置 `PR_SET_PDEATHSIG`。
3. frontend 意外退出后，engine 必须收到终止信号。
4. Python engine 启动 Verdi 时使用独立 session/process group。
5. timeout 或 cancel 时先对整个 Verdi 进程组发送 `SIGTERM`。
6. 有界等待后仍未退出，再发送 `SIGKILL`。
7. `communicate()` 和 `wait()` 都不能无限等待。
8. Verdi wrapper 已退出时也要清理仍存活的后代。
9. 临时 action 目录必须在 finally 中删除。
10. 输出文件先完整校验，再原子替换。
11. 最后不能遗留 engine、Verdi、临时目录、crash marker 或活动 session。

## 开始编码前先读什么

先完整阅读原始 `kverif` 中这些模块：

```text
kdebug/src/main.cpp
kdebug/src/api/
kdebug/src/backend/engine_adapter.cpp
kdebug/src/core/process/process_runner.cpp
kdebug/src/runtime/work_dir.cpp
kdebug/tcl_engine/kdebug_engine.py
kdebug/tcl_engine/kdebug_npi.tcl
kdebug/specs/actions/
kdebug/schemas/v1/
kdebug/examples/
kdebug/tests/conftest.py
kdebug/tests/contract/
kdebug/tests/unit/
```

先说明现有 action 如何注册、校验、分发和调用 engine，再沿用现有项目风格加入
`rscheck.inventory`。不要另外做一套不兼容的 frontend。

## 自己建立最小测试工程

测试不能依赖生产 RTL。自行建立一个小型 RTL fixture，至少包含：

- `top.u_tile` 和 `top.u_tile_peer` 两个结构相同的 hierarchy。
- 两处使用相同 local instance name 的 clock cone。
- 普通 `clk/rst_n` 的 module。
- 有 clk 但没有 rst 的 module。
- 使用 `clock_i/reset_ni` 的 module。
- `RS_CRG_EN` 和 `rs_mode` parameter。
- 多个 RS 实例。
- OCC、clock mux、clock gate 和两个 CRG 分支。
- 三层以上的上游模块链。
- generate block 中的实例。
- 一个正常 clean `elab++`。
- 一个有 elaboration error 但 top 仍可查询的 partial `elab++`。

collector 的真实测试只能接收生成后的两个 `elab++`，不能把 fixture 的 RTL/filelist
直接传给 inventory action。

## 必须补齐的测试

C++ 测试：

- action 注册和资源分类。
- `target.elab_db` 解析。
- ProcessRunner parent-death。
- timeout 的 TERM/KILL。
- 工作目录和 session 隔离。

Python 测试：

- request 参数准备。
- Verdi 命令只使用 `-elab`。
- partial response。
- UTF-8 response。
- Verdi process-group timeout。
- engine 收到 SIGTERM 后清理 Verdi。
- pipe 不关闭时仍能有界退出。

Tcl mock 测试：

- position found/not found。
- parameter 成功和 unresolved。
- formal 缺失不影响其他端口。
- 同名 instance/net/inst-port 的 typed lookup。
- 分支 driver、环路和深度边界。
- 不同 hierarchy 的 cache 隔离。
- nested generate scope。
- UTF-8/LF response。

contract 测试：

- action catalog 包含 `rscheck.inventory`。
- schema action 返回 checked-in schema。
- request/response example 均通过 schema。
- 真实错误响应也通过 response schema。

## 最终怎么验收

基础构建和自动测试：

```bash
make -C kdebug -j2 all
PYTHON=python3 make -C kdebug test-fast
ldd kdebug/kdebug
```

`ldd` 结果不能包含：

```text
libNPI
libnpiL1
not found
```

真实 Verdi环境还必须完成：

1. raw clean KDB action。
2. raw partial KDB action。
3. 至少 20 次 clean KDB 重载。
4. 确认 Verdi NPI 子进程已运行后，终止 frontend。
5. 执行 1 秒 timeout。
6. 检查没有 kdebug、engine、Verdi 后代残留。
7. 检查没有临时目录、crash marker 或活动 session。
8. 对最终 ELF、engine 和 Tcl 文件生成 SHA-256。

## 最终交付物

完成后必须提供：

- 一个新 Git 分支，不直接修改 main。
- 完整源码修改。
- request/response JSON Schema。
- request/response examples。
- C++、Python、Tcl 和 contract tests。
- 最小 RTL fixture 和 KDB 生成脚本。
- focused pressure 脚本。
- 使用文档和测试结果文档。
- 固定 commit SHA 和最终 kdebug ELF SHA-256。
- 推送到 GitHub 的分支。

在真实 clean/partial KDB、取消、timeout 和残留检查全部通过之前，不要宣称完成。

## 下发时需要一起提供的材料

把这份任务上下文交给其他模型时，还需要同时提供：

1. 原始 `kverif` 基线仓库，固定到上述 base commit。
2. NPI 手册，或允许接手者访问本机 PDF 路径。
3. 一台已经能运行 Verdi、Tk 和 NPI 的 Linux 设备。
4. clean/partial `elab++`；也可以授权接手者用最小 fixture 自行生成。

不需要提供任何现成 collector、adapter 或已完成 feature 分支。这样可以确认接手者
确实是在原始 `kverif` 上独立完成设计。
