# rscheck 自带 GUI 与 Verdi GUI 的跨设备测试

本文档说明如何在不同 Linux 设备和桌面环境中启动、检查并压测两个独立 GUI：

| 界面 | 用途 | 启动器 | 设计输入 |
|---|---|---|---|
| **rscheck 自带 GUI** | 设置九列和 CRG Trace 深度、维护三个数据库、运行检查并查看 trace 证据/日志 | `scripts/launch_rscheck_gui.sh` | inventory v3（兼容 v2），或 collector + elaborated KDB；输出 report v4 |
| **Verdi GUI** | 人工浏览 hierarchy、实例和连线 | `scripts/launch_verdi_gui.sh` | `verdi -elab <elaborated KDB>` |

`rscheck` 同时提供 CLI 和自带 Tkinter GUI；GUI 不是 Verdi 的包装窗口。两个 GUI 可以查看同一份检查对象，但职责不同。

源码 checkout 的在线 GUI 新会话默认选择无 NPI Python adapter
`scripts/rs_kdebug_collector.py`；wheel 安装选择同 Python 环境的
`rs-kdebug-collector` entry point。原版
kdebug 不能直接替换；必须先按 [kdebug 后端指南](KDEBUG_BACKEND.md) 构建配套
kverif `codex/rscheck-elab-inventory` 分支，并在启动 GUI 的 shell 导出绝对
`KDEBUG_BIN`。旧 C++ collector 仅作 baseline。

> **在线输入硬约束：**rscheck 自带 GUI 的在线模式只接受 collector 和 `elabcom` 生成的 elaborated KDB。界面不提供 RTL、filelist、top、`work.lib++`、编译参数或任意 Verdi passthrough 输入。Verdi launcher 同样只执行 `verdi -elab <KDB>`。

## 1. 仓库中的 GUI 组件

- `rscheck/gui.py`：工具自带 Tkinter 窗口、结果表、证据和日志。
- `rscheck/gui_backend.py`：严格构造 CLI 命令、读取报告和管理可取消的进程组。
- `rscheck/kdebug_collector.py`、`scripts/rs_kdebug_collector.py`：无 NPI 的 kdebug JSON adapter 与可执行入口。
- `scripts/launch_rscheck_gui.sh`：发现 X11/Xwayland 会话并启动工具自带 GUI。
- `scripts/test_rscheck_gui_smoke.py`：可见六根完整配置往返、CRG Source 三层命中、在线 depth-limit、离线正反例和批量压力。
- `scripts/lib/gui_session.sh`：两个 launcher 共用的 DISPLAY/Xauthority 发现与 `xdpyinfo` 探测。
- `scripts/lib/run_with_env_file.sh`：隔离加载可信站点环境并移除 xtrace/启动钩子。
- `scripts/launch_verdi_gui.sh`：只用 `verdi -elab <KDB>` 打开 Verdi GUI。
- `scripts/test_vm_fresh_checkout.sh`：在 `${VM_RUN_BASE:-$HOME}` 创建独立 fresh checkout、锁定提交并保存完整 VM 测试现场。
- `scripts/test_vm_verdi_gui.sh`：按显式 backend 运行测试、生成示例 KDB、打开 Verdi 并执行在线/压力回归；默认退出时关闭本次 Verdi。
- `tests/test_gui_backend.py`、`tests/test_gui_lifecycle.py`：GUI 命令、报告、取消和生命周期回归。
- `tests/test_rscheck_gui_launcher.py`、`tests/test_gui_session_probe.py`、`tests/test_verdi_gui_launcher.py`：Linux 启动器和跨桌面会话回归。

KDB、编译库、日志、collector、临时 inventory、JSON/CSV 报告和截图均为本机测试产物，不提交 Git：

```text
output/
npi/build/
work.lib++/
kdb.elab++/
vericomLog/
elabcomLog/
rs_npi_collectorLog/
*.log
```

## 2. 图形环境和依赖

以下显示方式均支持：

1. Linux 本地图形桌面的终端；
2. VNC 或 XRDP 桌面内的终端；
3. 客户端已经运行 X server 的 `ssh -Y` 会话。

GNOME 不是运行条件。KDE、Xfce、MATE、Cinnamon、LXQt 及其他提供有效 X11 DISPLAY 的桌面均可使用；Wayland 会话必须启用 Xwayland。只有 `WAYLAND_DISPLAY` 而没有可通过 `xdpyinfo` 访问的 `DISPLAY` 时，Tkinter 和该版本 Verdi 都不能显示窗口。

工具自带 GUI 还要求启动它的 Python 能 `import tkinter`。按系统选择一组安装命令。

Debian/Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y python3-tk x11-utils
```

RHEL/CentOS 系统 Python：

```bash
sudo yum install -y python3-tkinter xorg-x11-utils
```

RHEL/CentOS SCL Python 3.8：

```bash
sudo yum install -y rh-python38-python-tkinter xorg-x11-utils
source /opt/rh/rh-python38/enable
python3 -c 'import tkinter; print("Tk", tkinter.TkVersion)'
```

若发行版使用带版本号的包名，必须安装与实际 `python3` 可执行文件匹配的 Tkinter 包。`xdpyinfo` 用于验证显示访问；GUI smoke 输出自身的 X11 client ID，`xwininfo -tree -stats` 用于确认 rscheck 和 Verdi 窗口已经映射，不依赖 EWMH `_NET_CLIENT_LIST`。

Verdi/kdebug 在线测试还需要 Bash 4.2+、Python 3.8+、合法 Verdi、完整的配套 kdebug build 和组织批准的 license；仅从源码构建 kdebug 或运行旧 NPI baseline 时才需要 C++ 编译器/GNU Make。kdebug 测试模式不要求 NPI C/C++ header、`libNPI.so` 或 `libnpiL1.so` 作为脚本前置条件，只要求本机 Verdi 提供运行时 `npi_L1.tcl`。不要把 VM 地址、SSH 密码、license 地址、token 或会话生成的 Xauthority 路径写入仓库。

应优先以图形会话所属用户运行，并确保该用户能读取仓库和 KDB。root 从其他用户的 `/proc/<PID>/environ` 恢复会话只用于旧部署兼容，可能被 SELinux、`hidepid`、文件权限或显示服务器策略阻止。不要使用 `xhost +` 绕过认证。

## 3. 获取仓库并探测 GUI

在新设备克隆两个配套分支、构建 kdebug 并安装 rscheck。第 10 节的正式复现会另外创建 rscheck fresh checkout：

```bash
: "${VERDI_HOME:?export VERDI_HOME to the approved Verdi installation}"
export WORK_ROOT="${WORK_ROOT:-$HOME/rscheck_kdebug_repro}"
mkdir -p "$WORK_ROOT"
git clone --branch codex/rscheck-elab-inventory --single-branch \
  https://github.com/ysyx-22040210-yudian/kverif.git "$WORK_ROOT/kverif"
git clone --branch codex/kdebug-npi-backend --single-branch \
  https://github.com/ysyx-22040210-yudian/suhua_rs_tool.git "$WORK_ROOT/suhua_rs_tool"

export KVERIF_HOME="$WORK_ROOT/kverif"
export RSCHECK_ROOT="$WORK_ROOT/suhua_rs_tool"
export PYTHON="$(command -v python3)"
export PATH="$VERDI_HOME/bin:$KVERIF_HOME/tools:$PATH"
make -C "$KVERIF_HOME/kdebug" clean
make -C "$KVERIF_HOME/kdebug" -j2 all
export KDEBUG_BIN="$KVERIF_HOME/kdebug/kdebug"

cd "$RSCHECK_ROOT"

if [ -f /opt/rh/rh-python38/enable ]; then
  source /opt/rh/rh-python38/enable
fi
python3 -m pip install -e .
test -x "$KDEBUG_BIN"
test -x scripts/rs_kdebug_collector.py
```

工具自带 GUI 探测：

```bash
cd "$RSCHECK_ROOT"
bash scripts/launch_rscheck_gui.sh --probe-only
```

预期：

```text
GUI access OK: source=... user=... DISPLAY=...
rscheck GUI probe PASS
```

在本地/VNC/XRDP 桌面已经登录、但当前 shell 继承了过期显示变量时，可直接验证自动发现。以下命令不会写死 DISPLAY、用户或 Xauthority 路径：

```bash
cd "$RSCHECK_ROOT"
export KDEBUG_BIN
unset DISPLAY XAUTHORITY DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR
bash scripts/launch_rscheck_gui.sh --probe-only
bash scripts/launch_rscheck_gui.sh
```

已验证环境会自动发现 `DISPLAY=:0`。这组清空命令不用于 `ssh -Y`：SSH X11 转发必须保留 SSH 分配的 `DISPLAY` 和认证环境。

Verdi GUI 探测使用同一个会话解析器，但独立命令为：

```bash
bash scripts/launch_verdi_gui.sh --probe-only
```

`--probe-only` 不导入 Tkinter、不查找 Verdi、不要求 license，也不读取 KDB，只验证当前或自动发现的 DISPLAY 可通过 `xdpyinfo`。

通过 SSH 转发时，先在客户端启动 X server，再从客户端 shell 登录：

```bash
: "${REMOTE_USER:?set REMOTE_USER in the client shell}"
: "${LINUX_HOST:?set LINUX_HOST in the client shell}"
ssh -Y "${REMOTE_USER}@${LINUX_HOST}"
```

进入远端后保持 SSH 连接，并执行：

```bash
cd "$HOME/suhua_rs_tool"
xdpyinfo >/dev/null
bash scripts/launch_rscheck_gui.sh --probe-only
```

## 4. 启动工具自带 GUI

Linux 前台启动命令：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_rscheck_gui.sh --probe-only
bash scripts/launch_rscheck_gui.sh
```

该启动器只支持无参数启动和 `--probe-only`。可用 `PYTHON_BIN` 指定解释器，用 `PYTHON_ENABLE` 指定工具链 enable 脚本；发现 `/opt/rh/rh-python38/enable` 时会默认使用 SCL Python 3.8。

安装项目后，若当前 shell 已经有有效 DISPLAY，也可直接运行：

```bash
rtl-rs-check-gui
```

Windows PowerShell 和 macOS 终端直接运行：

```bash
python -m rscheck gui
```

macOS 只支持 Excel 验证和离线 inventory 模式；kdebug/Verdi elaborated KDB 在线加载只支持 Linux。

## 5. GUI 字段和操作验收

“检查配置”页应包含：

- `Excel / CSV`、`配置 JSON` 和配置“加载”按钮；
- `工作表`、`表头行`、`数据起始行`、默认未勾选的 `严格校验表头（可选）`；
- RTL 数据来源区域的 `CRG Trace 最大层数`，默认 `16`，只接受 `1..256`；
- “内部属性 -> Excel 列号”区域中的九个独立 1-based 映射：`Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`、`RS_CFG_EN`；实际 Excel 表头可以任意命名；`CRG_source` 必填，可通过简称库解析为 trace 要精确匹配的完整 instance hierarchy；
- 数据源单选项 `在线 RTL Collector（elaborated KDB）` 和 `离线 Inventory`；
- 在线字段 `RTL / kdebug Collector`、`Elab KDB`、`运行库目录（可选）`、`保存 Inventory`、`超时（秒）`；源码 checkout 新会话必须默认为 `scripts/rs_kdebug_collector.py`，wheel 安装必须解析同环境的 `rs-kdebug-collector`；
- 离线字段 `Inventory JSON`；
- `JSON` 和可选 `CSV` 报告路径；
- `验证 Excel`、`运行 RTL 检查`、`取消`、`打开报告目录`。

加载示例配置后必须确认严格表头诊断保持未勾选。当前 `examples/specs.csv` 使用“接口分类”“模块类型”等业务表头，与九个内部属性名均不同；GUI 的“验证 Excel”和后续检查仍应按配置中的 1-based 列号正常通过。手工勾选严格诊断后再次验证该文件，应按预期报告 `header validation failed`，证明精确表头比较只是 opt-in 诊断。

“模块规则库”页应支持搜索、新建、修改、删除和保存。`rs_pipe` 应显示显式覆盖 `has_rs_cfg_en=true`、`step_parameters=rs_mode`、`clk_port=clk`、`rst_port=rst`，并把兼容规则键说明为“有 `RS_CRG_EN` parameter”。新建或修改规则时把 clk/rst 端口输入留空，应分别规范化为 `clk`、`rst_n`。规则未保存时不能运行；保存应写回当前配置 JSON并重新加载。显式规则名与 Excel `RS_module` 大小写敏感、精确匹配并优先于默认值；没有专属项时正常使用 `has_rs_cfg_en=true`、`step_parameters=[]`、`clk_port=clk`、`rst_port=rst_n`，即要求 RTL `RS_CRG_EN=0`、Excel/internal `RS_CFG_EN=假门控`，且每个匹配实例贡献 1。旧 JSON 的显式规则缺少端口键时先继承 `rtl.clk_port/rst_port`，GUI 保存或导出后必须显式写入继承值。

“Position 映射库”页应支持搜索、新建、修改、删除和保存。示例配置必须显示 `tile_core -> top.u_tile`；映射未保存时不能运行，保存应原子写回当前配置 JSON 并重新加载。Excel 简写命中后检查、分组、clk/rst 相对解析和 NPI positions 均使用全路径；未命中值按完整路径直通。

“CRG Source映射库”页应提供相同的搜索、CRUD、原子保存、dirty 阻断和外部修改冲突处理。示例配置应显示 `crg_core -> top.u_tile.u_crg`、`crg_aux -> top.u_tile.u_aux_crg`；命中后 GUI/JSON/CSV 同时保留简称与完整路径，并以右侧完整 hierarchy 作为 CRG trace 目标。简称和完整 CRG 路径都不得加入 NPI positions。

在线验收必须确认 trace 从每种 `RS_module` 最终规则的 clk formal 开始。追到中间模块 output 后，继续展开该模块全部 input formal，但精确排除名字为 `clk`、`rst_n` 的 input；对其余 input 分支递归重复。RS 模块本身不计深度，直驱模块为 depth 1，depth N 节点仍参与目标匹配但不再展开；net、assign、concat、slice 和 primitive 不消耗模块深度。目标是解析后的 CRG_source 完整 instance hierarchy，任一分支精确命中即通过。未命中、达到深度边界或 trace 不可用分别只产生 `CRG_SOURCE_NOT_FOUND`、`CRG_TRACE_DEPTH_LIMIT`、`CRG_TRACE_UNAVAILABLE` warning。

当前 `0.11.0` 的“配置 JSON”行应同时显示“导入”“加载”“导出”。完整配置文件仍有六个根对象；导出副本的 `rtl.crg_trace_max_depth` 必须等于 GUI 当前值，重新导入后 Spinbox 值和检查命令的 `--crg-trace-max-depth` 必须一致。三个数据库、dirty、副本和原子导入语义保持不变。

手工验收仍要在三个数据库中各应用未保存修改并保持搜索过滤，然后导出副本；副本必须包含完整未过滤数据库和当前 Excel/列号/trace 深度，导出不得改变 active path、dirty 状态或原配置。完整导入必须先校验六根，再确认待丢弃修改，最后二次读取并整体替换；取消、拒绝或无效候选不得产生部分状态变化。

导入验收必须确认候选 JSON 在任何丢弃提示之前先完成六根完整校验，缺少任一根对象都应拒绝；候选有效后，先确认与已加载配置不同的 Excel/列号表单，再逐一确认三个 dirty 数据库。全部接受后还应重新读取候选，复核仍有效才整体替换配置、切换 active path 并清除 dirty。兼容性“加载”允许历史配置省略 `crg_source_mappings` 并按空库处理，但也必须经过相同的确认和二次读取。取消文件选择、首次/复核无效候选或拒绝任一确认时，当前 GUI 字段、active path、三个内存数据库和 dirty 状态都不得发生部分变化。配置若以字符串保存纯数字工作表名（例如 `"123"`），该字段未编辑时导出和运行必须继续使用字符串名称；不能误转成 1-based 数字序号。Excel/CSV、collector、Elab KDB、NPI 库、inventory、report 和超时等运行输入/输出路径不属于配置，导入后按目标设备重新选择。

Excel/internal `RS_CFG_EN` 去除首尾空白后若精确等于大写 `NA`，该行全部 `RS_CFG_EN` 标签及 RTL `RS_CRG_EN` 参数存在性/值检查都会跳过；`na`、`N/A` 和 `Na` 均不等价。这个逐行特殊值只豁免门控检查，`RS_module`、实例分组、动态 `step`、clk 和 rst 仍正常执行。非 `NA` 时，`has_rs_cfg_en=true` 要求每个 RTL 实例存在 effective `RS_CRG_EN`、值为 0，且 Excel 标签精确填写 `假门控`；`false` 时 Excel 字段的任意字面内容都不参与标签判定，但仍解析、显示和写入报告，RTL 则不得实际存在 `RS_CRG_EN`，否则仍报 `RS_CFG_EN_PARAMETER_UNEXPECTED`。映射字段中的公式和 Excel 错误值仍受通用解析限制。工具不会回退匹配 RTL `RS_CFG_EN`。`step_parameters` 为空时每个物理实例贡献 1；非空且所有值均可解析时，全部非零贡献 1、至少一个为 0 贡献 0。`RS_CRG_EN` 不能加入 `step_parameters`；RTL 中另一个确实存在的 `RS_CFG_EN` 可作为普通动态拍 parameter。任一缺失/`null`/X/Z/非法值都会让贡献未知并 fail-closed。`step` 可为 0，但没有物理匹配实例仍是 `GROUP_NOT_FOUND`。每条规则还必须有非空 `clk_port/rst_port`；两个端口分别取证和判定。当前没有“无 rst/跳过 rst”模式：模块存在且连接了所指 clk、但缺少所指 rst formal port 时，该行应 FAIL 且只报 `RST_PORT_MISSING`，不得误报 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`。

“检查结果”页应新增 `CRG Trace` 列，并继续显示“匹配实例”和“实际/期望拍”。report v4 正常命中显示 `PASS`，warning 显示 `WARNING`，旧 report v2/v3 显示 `N/A`。只有 CRG warning 的通过行状态也显示 `WARNING`，但仍计入 passed rows，CLI 退出码仍为 `0`。证据面板必须包含 `crg_source_check` 和逐实例 `clock_trace`，可看到完整 hierarchy、depth、witness path、规则 clock formal、最大深度和固定排除项 `["clk","rst_n"]`。

运行期间输入控件和两个启动按钮应禁用，“取消”应启用。取消后状态应显示 `CANCELLED`，CLI 和 collector 进程组都应退出；关闭正在运行的窗口时应先出现取消确认。

Windows/Linux 布局仍以最小尺寸 `980x680` 验收六个页签“检查配置”“模块规则库”“Position 映射库”“CRG Source映射库”“检查结果”“运行日志”以及“导入/加载/导出”三个配置按钮无重叠或截断。历史截图不含当前 CRG Source 页签和六根往返证据，不能作为当前版本证据；必须重新验收，截图仍只用于本地检查、不提交仓库。

## 6. VM 可见验证、离线 smoke、负载与取消测试

下列命令不需要 Verdi 或 license。应在图形终端/VNC/XRDP/`ssh -Y` shell 中执行：

```bash
PROJECT_ROOT="/path/to/suhua_rs_tool"
cd "$PROJECT_ROOT"

if [ -f /opt/rh/rh-python38/enable ]; then
  source /opt/rh/rh-python38/enable
fi
export PYTHON_BIN="${PYTHON_BIN:-python3}"

bash scripts/launch_rscheck_gui.sh --probe-only
source scripts/lib/gui_session.sh
gui_session_resolve
xdpyinfo >/dev/null
```

先直接复制运行 Position 映射库页 smoke；它只保存临时配置，不改仓库文件，并同时完成两套映射库和模块规则库 CRUD、六根完整配置导出和原子导入：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --iterations 1 \
  --visible-tab positions \
  --visible-seconds 10
```

窗口保留期间检查 980×680 下映射表、搜索框和编辑控件无重叠，确认 `tile_core -> top.u_tile`，终端末行应为 `GUI_SMOKE_PASS`，并包含 `header-map=column-index strict-header=false`、`module-rule-ports=preserved`、`crg-source-db=crud-complete` 和 `config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules`。这些 marker 证明副本保留六根、当前 Excel/列号、已加载 `rtl`、每条模块规则端口及包含未单独保存修改的三个完整数据库，且导入后成功切换路径并清除 dirty。

GUI “验证 Excel”路径连续 20 轮：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --validate-only \
  --iterations 20 \
  --visible-seconds 5
```

预期为 2 行 VALID、0 error、0 warning；输出包含 `GUI_SMOKE_WINDOW: window=mapped window_id=0x...`，末行包含 `mode=validate case=positive iterations=20 window=mapped window_id=0x...`、`header-map=column-index strict-header=false`、`position-map=tile_core->top.u_tile` 和固定 config-io marker。

可见离线正例连续 100 轮：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --iterations 100 \
  --visible-seconds 10
```

预期：`GUI_SMOKE_PASS`、`mode=offline case=positive iterations=100`、六根 config-io marker 和 `schemas=report-v4/inventory-v3`；结果页 2 行 PASS、0 error、0 warning。每轮 inventory v3 必须含 `clock_trace`，report v4 必须含 `crg_source_check`，CSV 必须含 `crg_trace_status/max_depth/evidence`。旧 v2 fixture 另有兼容测试：只有带非空 `module` 且 `instance` 精确命中完整目标的 legacy `clk_sources` 项可通过，否则是 `CRG_TRACE_UNAVAILABLE` warning。

`has_rs_cfg_en=false` / Excel 任意文本专项连续 20 轮：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --rs-cfg-dontcare \
  --iterations 20 \
  --visible-tab results \
  --visible-seconds 10
```

专项故意把 Excel/internal `RS_CFG_EN` 写成任意非标准文本，同时让实例 parameters 不含 `RS_CRG_EN`。预期为 `GUI_SMOKE_PASS`、1 行 PASS、0 error、0 warning，并包含 `mode=offline case=rs-cfg-dontcare iterations=20`、`has-rs-cfg-en=false label=dont-care rs-crg-en=absent findings=none` 和文本证据 `parsed-rs-cfg-en=任意非标准文本`。GUI 和 report 必须保留解析后的 Excel 文本，`module_rule.has_rs_cfg_en=false`，findings 为空；不能产生 `RS_CFG_EN_LABEL_MISMATCH`。一键脚本通过 `GUI_RS_CFG_DONTCARE_ITERATIONS` 控制轮数，默认 20，并把日志保存为 `offline_gui_rs_cfg_dontcare.log`。

精确大写 `NA` 的逐行门控豁免专项连续 20 轮：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --rs-cfg-na \
  --iterations 20 \
  --visible-tab results \
  --visible-seconds 10
```

专项把 Excel/internal `RS_CFG_EN` 写为 `NA`，并故意让实例保留 `RS_CRG_EN=1`。预期为 1 行 PASS、0 error、0 warning，输出包含 `mode=offline case=rs-cfg-na iterations=20` 和固定证据 `rs-cfg-en=NA check=skipped rtl-rs-crg-en=1 findings=none`。GUI/report 必须保留 `RS_CFG_EN=NA`，同时保留 module、step、clk/rst 均已执行的证据。解析器会去除首尾空白，所以 ` NA ` 等价；小写 `na` 和 `N/A` 不等价。一键脚本通过正整数 `GUI_RS_CFG_NA_ITERATIONS` 控制轮数，默认 20，并把日志保存为 `offline_gui_rs_cfg_na.log`。

CRG_source 简称映射专项连续 20 轮：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --crg-source-mapping \
  --iterations 20 \
  --visible-tab results \
  --visible-seconds 10
```

预期为 1 行 PASS、0 error、0 warning，并包含 `mode=offline case=crg-source-mapping iterations=20` 和 `crg-source-map=core_clock_source->top.u_soc.u_crg_core gui-json-csv=alias+full crg-source-check=pass trace-depth=3 findings=none`。GUI、report v4 和 CSV 必须同时保留 alias+full，matched witness depth 为 3。一键脚本通过正整数 `GUI_CRG_SOURCE_MAPPING_ITERATIONS` 控制轮数，默认 20，并把日志保存为 `offline_gui_crg_source_mapping.log`。

可见离线反例：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --negative \
  --iterations 1 \
  --visible-seconds 10
```

反例 GUI 应显示 FAIL 和非零 error；smoke 脚本把这个预期 FAIL 判为测试通过，因此自身返回 `0`，并打印 `mode=offline case=negative iterations=1`。

10,000 行单轮 GUI 负载：

```bash
cd "$PROJECT_ROOT"
/usr/bin/time -f 'GUI_LOAD wall=%e sec max_rss=%M KiB' \
  "$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --generated-rows 10000 \
  --iterations 1 \
  --timeout 180 \
  --visible-seconds 0
```

预期末行：

```text
GUI_SMOKE_PASS: rows=行数 10000 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=1 window=mapped window_id=0x... header-map=column-index strict-header=false config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules
```

旧版本的时间和 RSS 只是历史数据，不是当前 `0.11.0` 或其他设备的硬门槛。脚本生成临时 CSV/inventory，走真实 GUI、report v4/inventory v3、六根配置往返和 10,000 行结果表渲染。

取消发生在后台进程启动阶段的竞态连续 100 轮：

```bash
cd "$PROJECT_ROOT"
set -e
CANCEL_START_SECONDS="$(date +%s)"
for _ in $(seq 1 100); do
  "$PYTHON_BIN" -m unittest \
    tests.test_gui_backend.ProcessControllerTests.test_cancel_during_process_start_is_not_lost \
    >/dev/null 2>&1
done
CANCEL_ELAPSED="$(( $(date +%s) - CANCEL_START_SECONDS ))"
printf 'cancel-during-start: 100/100 PASS in %s seconds\n' "$CANCEL_ELAPSED"
```

2026-07-24 历史基线为 100 轮通过、约 15.3 秒；当前版本仍应重新执行。完整套件还覆盖运行中取消和完整进程组强制清理。

smoke 本身会要求自己的 Tk 窗口处于 mapped/viewable 状态，并输出 Tk client 的 `window_id`。需要额外的桌面证据时，执行 `xwininfo -id <XID> -tree -stats`：client 必须是 `IsViewable`，同一树中必须出现标题为 `RTL RS Check GUI Smoke` 的 Tk wrapper，并显示有效 Width/Height。该方式不依赖 EWMH `_NET_CLIENT_LIST` 或旧 Tk 可能缺失的 `_NET_WM_PID`。完整复制块见 [测试指南](TESTING.md#41-linux-工具自带-gui-可见-smoke稳定性与负载测试)。

## 7. VM 在线 KDB GUI smoke

以下命令不包含 VM 地址、密码或 license 值。先设置三个受控路径；`ELAB_DB` 必须是当前 RTL 由 `elabcom` 新生成的 elaborated KDB，`KDEBUG_BIN` 必须是配套 kverif build 的绝对 ELF；不得传 filelist、RTL、top 或 `work.lib++`：

```bash
export PROJECT_ROOT="/path/to/suhua_rs_tool"
export VERDI_HOME="/path/to/verdi"
export ELAB_DB="/absolute/path/to/kdb.elab++"
export KDEBUG_BIN="/absolute/path/to/kverif/kdebug/kdebug"

export PYTHON_BIN="${PYTHON_BIN:-python3}"
export COLLECTOR="$PROJECT_ROOT/scripts/rs_kdebug_collector.py"

cd "$PROJECT_ROOT"
if [ -f /opt/rh/rh-python38/enable ]; then
  source /opt/rh/rh-python38/enable
fi

test -x "$COLLECTOR"
test -x "$KDEBUG_BIN"
test -d "$ELAB_DB"
if ldd "$KDEBUG_BIN" | grep -Eq 'libNPI|libnpiL1|not found'; then
  echo "kdebug frontend has a forbidden or unresolved dependency" >&2
  exit 1
fi

bash scripts/launch_rscheck_gui.sh --probe-only
source scripts/lib/gui_session.sh
gui_session_resolve

"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --timeout 180 \
  --iterations 3 \
  --visible-seconds 15
```

成功判据：脚本返回 `0` 并打印：

```text
GUI_SMOKE_PASS: rows=行数 2 errors=错误 0 warnings=警告 0 mode=online case=positive iterations=3 window=mapped window_id=0x... contract=elab-only ... schemas=report-v4/inventory-v3
```

3 轮中的每一轮都必须通过真实 collector 加载该 KDB。首组 6 个实例都必须从规则 `clk` formal 追到 `u_occ` depth 1、`u_clk_mux` depth 2 和 `u_crg`/`u_aux_crg` depth 3；目标 `u_crg` 任一分支命中即通过。`clk_occ` 的 input `clk/rst_n` 必须被精确排除。第二组 `u_aux_crg` 为 direct depth 1。inventory 必须是 v3，report 必须是 v4，运行命令必须包含 `--crg-trace-max-depth 16`。

直接复制以下命令做在线深度上限 20 轮 GUI 压测：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --timeout 180 \
  --crg-depth-limit \
  --iterations 20 \
  --visible-tab results \
  --visible-seconds 15
```

最大深度为 2 时，首组六个实例必须各产生一个 `CRG_TRACE_DEPTH_LIMIT` warning，第二组 depth 1 仍命中。GUI 总体仍为 PASS、2 passed rows、0 error、6 warning；首行显示 `WARNING` 和 `CRG Trace=WARNING`。末行必须包含：

```text
mode=online case=crg-depth-limit iterations=20
crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6
schemas=report-v4/inventory-v3
```

一键脚本用 `GUI_CRG_TRACE_DEPTH_ITERATIONS` 控制轮数，默认 20，日志为 `online_gui_crg_trace_depth_limit.log`。

示例 RTL 还含 `rs_custom.CUSTOM_RS`，formal ports 为 `clock_i/reset_ni/d/q`。一键 VM 脚本会用该规则重新在线采集，并故意填写错误 `CRG_source`；硬检查和行仍 PASS，但产生 1 个 `CRG_SOURCE_NOT_FOUND` warning：

```text
custom module clk/rst formal-port rule evidence OK: clock_i/reset_ni
crg-source-check=warning finding-code=CRG_SOURCE_NOT_FOUND
```

同一个 clean KDB 还包含 `rs_clk_only.CLK_ONLY_RS`：formal ports 精确为 `clk/d/q`，`clk` 连接 `top.u_tile.clk_rs`，没有任何 rst formal port。直接复制下面命令，可用工具自带的可见 GUI 连续执行 20 轮端口隔离回归：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --timeout 180 \
  --clk-without-rst \
  --iterations 20 \
  --visible-tab results \
  --visible-seconds 15
```

GUI 中被测行应显示 FAIL、1 error、0 warning，但 smoke 进程应返回 `0`，表示预期失败被精确识别。末行必须包含：

```text
case=clk-present-rst-missing iterations=20
rule-ports=clk/rst_n
clk-port-evidence=present rst-port-evidence=missing finding-codes=RST_PORT_MISSING
```

每一轮都会重新启动 collector 并加载该 `$ELAB_DB`。report finding 集合必须精确为 `{RST_PORT_MISSING}`，不得出现 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`。

## 8. 打开 Verdi GUI

本节启动 Verdi 本身，不是 rscheck 自带 GUI。Verdi 可执行文件按 `VERDI_BIN`、`VERDI_HOME/bin/verdi`、`NOVAS_INST_DIR/bin/verdi`、`PATH` 的顺序定位。

前台启动，关闭 Verdi 后 shell 才返回：

```bash
cd "$HOME/suhua_rs_tool"
: "${ELAB_DB:?export ELAB_DB to an elaborated KDB directory}"
bash scripts/launch_verdi_gui.sh --elab-db "$ELAB_DB"
```

后台启动会打印 PID 和日志路径：

```bash
cd "$HOME/suhua_rs_tool"
: "${ELAB_DB:?export ELAB_DB to an elaborated KDB directory}"
bash scripts/launch_verdi_gui.sh \
  --elab-db "$ELAB_DB" \
  --background
```

该 launcher 只支持 `--probe-only`、`--elab-db DIR` 和可选 `--background`。它拒绝 `work.lib++`、普通文件、不存在的目录、RTL/filelist、`-f`、`-sv`、`-lib`、`-top`、位置参数和 `--` passthrough；实际参数固定为：

```text
verdi -elab <ELAB_DB>
```

SSH X11 转发的前台或后台 Verdi 都依赖当前隧道，使用期间必须保持 SSH 连接。

## 9. GUI 会话发现和覆盖变量

两个 Linux launcher 的探测顺序相同：

1. 使用当前 shell 中已通过 `xdpyinfo` 的 DISPLAY，包括图形终端和 `ssh -Y`；
2. 若设置 `GUI_DISPLAY`，使用可选 `GUI_XAUTHORITY` 显式验证；
3. 若设置 `GUI_SESSION_PID`，从该进程环境提取显示和会话变量；
4. 否则扫描 GNOME、KDE、Xfce、MATE、Cinnamon、LXQt、Xwayland 等可读进程环境；
5. 对每个候选实际运行 `xdpyinfo`，只有成功的候选可用。

因此 `gnome-session-binary` 从来不是成功条件。发现多个可用 DISPLAY 时会停止并要求显式选择，不会任意连接旧会话。

| 变量 | 用途 |
|---|---|
| `GUI_USER` | 自动扫描时限定进程用户 |
| `GUI_DISPLAY` | 显式指定 DISPLAY，例如 `:0` |
| `GUI_XAUTHORITY` | 为显式 DISPLAY 指定当前会话认证文件 |
| `GUI_SESSION_PID` | 从指定图形进程读取显示环境 |
| `GUI_PROBE_TIMEOUT` | 单个 `xdpyinfo` 探测超时，默认 5 秒 |
| `PYTHON_BIN` | rscheck GUI 使用的 Python，默认 `python3` |
| `PYTHON_ENABLE` | 可选 Python 工具链 enable 脚本 |

当前 shell 能运行 `xdpyinfo` 时，不应额外设置 GUI 变量。显式选择示例：

```bash
: "${GUI_DISPLAY:?set GUI_DISPLAY to the intended X11 display}"
export GUI_DISPLAY
bash scripts/launch_rscheck_gui.sh --probe-only
```

不要把某次登录的 `/run/...` Xauthority 路径硬编码进脚本或 Git；它会随登录会话变化。

## 10. 全量测试和 Verdi 端到端链路

全量 Python 测试：

```bash
cd "$HOME/suhua_rs_tool"
python3 -m unittest discover -v
```

当前预期为；测试数量以当前分支实际发现结果为准：

```text
Ran ... tests in ...
OK
```

Verdi 端到端脚本在 `RSCHECK_COLLECTOR_BACKEND=kdebug` 时核对 adapter/frontend 无直接 NPI 依赖，并验证 partial/clean KDB 和可见 Verdi/Tk GUI。partial 与 clean inventory 都同时采集 `top.u_tile` 和 `top.u_tile_peer` 两个结构相同的 hierarchy，逐 scope 断言 13 个实例、33 个 trace node、4 个 unique module、foreign-prefix=0，以证明同名 local clock cone 的 cache 按完整 hierarchy 隔离；partial KDB 还验证漏失端口分支仍能补追。clean KDB 上还会验证三层分支 CRG trace、规则 `clock_i`、depth 2 截断、clk/rst 端口隔离、两个门控专项、CRG 映射、100 轮稳定性和 10,000 行负载。每次 GUI smoke 都执行六根配置和三个数据库往返并断言 report v4/inventory v3。`RSCHECK_COLLECTOR_BACKEND=npi` 只用于旧 C++ baseline。

正式复现推荐从 bootstrap checkout 调用 fresh 驱动。下面命令会在 `${VM_RUN_BASE:-$HOME}/rscheck_fresh.*` 创建唯一运行根目录，最多执行三次同时带 TERM timeout 和 KILL 上限的 GitHub clone，每次使用独立且永久保留的 `repo_attemptN` 目录；成功后锁定克隆时的 `origin/main`，将全部控制台输出写入 `full_vm_test.log`，并强制把正式测试产物写入 `artifacts`：

```bash
cd "$RSCHECK_ROOT"
bash scripts/launch_verdi_gui.sh --probe-only
RSCHECK_COLLECTOR_BACKEND=kdebug \
KDEBUG_BIN="$KDEBUG_BIN" \
KVERIF_EXPECTED_COMMIT=<KVERIF_SHA> \
KDEBUG_EXPECTED_SHA256=<KDEBUG_ELF_SHA256> \
bash scripts/test_vm_fresh_checkout.sh \
  --commit origin/codex/kdebug-npi-backend
```

精确复现指定提交：

```bash
cd "$RSCHECK_ROOT"
RSCHECK_COLLECTOR_BACKEND=kdebug KDEBUG_BIN="$KDEBUG_BIN" \
KVERIF_EXPECTED_COMMIT=<KVERIF_SHA> \
KDEBUG_EXPECTED_SHA256=<KDEBUG_ELF_SHA256> \
bash scripts/test_vm_fresh_checkout.sh --commit <RSCHECK_SHA>
```

如果当前图形 shell 尚未加载站点 Verdi/NPI 环境，可让驱动在正式测试前静默加载一个可信的绝对路径文件。该文件只在隔离子进程中执行；它不能改变 fresh 驱动核对过的仓库/产物路径，stdout/stderr 和 xtrace 不写入日志，返回非零时立即终止。里面设置的 Verdi/NPI/GUI 变量会传给正式脚本：

```bash
cd "$HOME/suhua_rs_tool"
VERDI_ENV_FILE=/path/to/site_env.sh \
RSCHECK_COLLECTOR_BACKEND=kdebug KDEBUG_BIN="$KDEBUG_BIN" \
KVERIF_EXPECTED_COMMIT=<KVERIF_SHA> \
KDEBUG_EXPECTED_SHA256=<KDEBUG_ELF_SHA256> \
bash scripts/test_vm_fresh_checkout.sh --commit <RSCHECK_SHA>
```

无论 PASS 或 FAIL，退出信息都会打印 `RUN_ROOT`、`FULL_LOG`、`ARTIFACT_ROOT` 和 clone 尝试位置。驱动不会删除任何运行目录或失败 clone。kdebug 模式还保存 `kdebug_build_manifest.txt`、隔离的 `kdebug_home` 日志及前后进程快照，并拒绝 crash marker 或活动 registry session；fresh kdebug 运行必须指定 `KVERIF_EXPECTED_COMMIT`、`KDEBUG_EXPECTED_SHA256`，任一指纹不匹配都会立即失败。`CLONE_TIMEOUT` 可覆盖单次 clone 的默认 180 秒，`VM_RUN_BASE` 可用绝对路径覆盖运行父目录；正式脚本的其他环境变量不变。

root 通过非交互 SSH 启动、当前环境没有 license 时，正式脚本会从 GUI 会话解析出的桌面用户登录环境中自动导入 `LM_LICENSE_FILE`/`SNPSLMD_LICENSE_FILE`。该流程不写死用户名/home、不执行解析到的文本、不导入 PATH，也不输出 license 值；已有 license 值优先。站点使用其他机制时可设置 `VERDI_AUTO_LICENSE_IMPORT=0`，让 Verdi 自行诊断。

只有当前 checkout 已经可信并位于 VM 本机文件系统时，才可跳过 fresh clone 直接执行：

```bash
cd "$HOME/suhua_rs_tool"
RSCHECK_COLLECTOR_BACKEND=kdebug \
KDEBUG_BIN="$KDEBUG_BIN" \
bash scripts/test_vm_verdi_gui.sh
```

也可只测试端到端脚本的会话发现，不启动 Verdi：

```bash
bash scripts/test_vm_verdi_gui.sh --gui-probe-only
```

端到端覆盖变量包括 `RSCHECK_COLLECTOR_BACKEND`、`KDEBUG_BIN`、`KVERIF_EXPECTED_COMMIT`、`KDEBUG_EXPECTED_SHA256`、`VERDI_BIN`、`VERDI_HOME`、`NOVAS_INST_DIR`、`VERDI_WINDOW_REGEX`、`VERDI_READY_REGEX`、`PYTHON_BIN`、`PYTHON_ENABLE`、`GUI_START_TIMEOUT`、`NPI_TIMEOUT`、`KEEP_VERDI_GUI`、`GUI_ONLINE_ITERATIONS`、`GUI_CRG_TRACE_DEPTH_ITERATIONS`、`GUI_CLK_WITHOUT_RST_ITERATIONS`、`GUI_RS_CFG_DONTCARE_ITERATIONS`、`GUI_RS_CFG_NA_ITERATIONS`、`GUI_CRG_SOURCE_MAPPING_ITERATIONS`、`GUI_STRESS_ITERATIONS`、`GUI_LOAD_ROWS`、`GUI_VISIBLE_SECONDS`、`VERDI_ENV_FILE` 和 `VERDI_AUTO_LICENSE_IMPORT`。kdebug 模式不要求 `make`、C++ compiler、NPI C/L1 headers 或直接链接库；旧 baseline 才使用 `CXX`、`NPI_PLATFORM`、`NPI_INC_DIR`、`NPI_LIB_DIR`、`NPI_L1_INC_DIR`、`NPI_L1_LIB_DIR`。普通在线正例默认 3 轮；CRG depth-limit、有 clk/无 rst、don't-care、精确 `NA` 和 CRG Source 映射专项默认各 20 轮；离线稳定性默认 100 轮，负载默认 10,000 行。各轮次变量必须是十进制正整数。

成功输出应包含：

```text
Ran ... tests in ...
OK
Collector backend: kdebug JSON action rscheck.inventory
KDEBUG_MANIFEST=.../kdebug_build_manifest.txt
partial NPI load evidence OK: load reported errors but requested RTL remained queryable
partial-load NPI clock trace evidence OK: RS->u_occ->u_clk_mux->{u_crg,u_aux_crg}, custom clock_i, witness-depth=3
partial-load trace cache scope isolation OK: top.u_tile and top.u_tile_peer contain only their own same-named clock cones
partial NPI formal-port L0/L1 inventory evidence OK
clean-load NPI clock trace evidence OK: RS->u_occ->u_clk_mux->{u_crg,u_aux_crg}, custom clock_i, witness-depth=3
clean-load trace cache scope isolation OK: top.u_tile and top.u_tile_peer contain only their own same-named clock cones
RESULT: PASS | rows=2 errors=0 warnings=1
[WARNING] NPI_LOAD_PARTIAL: ...
Verdi GUI loaded elaborated top 'top' after ...
RESULT: PASS | rows=2 errors=0 warnings=0
[PASS] row 2 OUT_IF | ... CRG_source=crg_core -> top.u_tile.u_crg CRG_trace=PASS ...
[PASS] row 3 CTRL_IF | ... CRG_source=crg_aux -> top.u_tile.u_aux_crg CRG_trace=PASS ...
custom module clk/rst formal-port rule evidence OK: clock_i/reset_ni
clk-present/rst-missing CLI evidence OK: ports=clk,d,q
finding isolation OK: RST_PORT_MISSING only; CLK_PORT_MISSING absent
GUI_SMOKE_PASS: ... mode=online case=positive ... schemas=report-v4/inventory-v3
GUI_SMOKE_PASS: state=PASS ... warnings=警告 1 mode=online case=custom-port ... crg-source-check=warning finding-code=CRG_SOURCE_NOT_FOUND ...
GUI_SMOKE_PASS: state=PASS ... warnings=警告 6 mode=online case=crg-depth-limit iterations=20 ... crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6 ...
GUI_SMOKE_PASS: state=FAIL rows=行数 1 errors=错误 1 warnings=警告 0 mode=online case=clk-present-rst-missing iterations=20 ... clk-port-evidence=present rst-port-evidence=missing finding-codes=RST_PORT_MISSING ...
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=offline case=rs-cfg-dontcare iterations=20 ... has-rs-cfg-en=false label=dont-care rs-crg-en=absent findings=none parsed-rs-cfg-en=任意非标准文本 ...
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=offline case=rs-cfg-na iterations=20 ... rs-cfg-en=NA check=skipped rtl-rs-crg-en=1 findings=none ...
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=offline case=crg-source-mapping iterations=20 ... crg-source-map=core_clock_source->top.u_soc.u_crg_core gui-json-csv=alias+full crg-source-check=pass trace-depth=3 findings=none ...
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,crg_source_mappings,module_rules
module-rule-ports=preserved
crg-source-db=crud-complete
PASS: partial KDB compatibility, arbitrary Excel headers, position mapping, fresh KDB online GUI checks, and offline GUI stress suite completed.
```

正式脚本不会只检查一次 marker。它对以下十二份日志逐一执行 `grep -Fq` 硬断言，任一缺失都会使端到端测试失败：

```text
online_gui_positive.log
online_gui_crg_trace_depth_limit.log
online_gui_custom_port.log
online_gui_clk_present_rst_missing.log
partial_load_gui.log
online_gui_negative.log
offline_gui_default_rule.log
offline_gui_rs_cfg_dontcare.log
offline_gui_rs_cfg_na.log
offline_gui_crg_source_mapping.log
offline_gui_100_rounds.log
offline_gui_10000_rows.log
```

十二份日志都必须包含完全相同的六根 config-io marker、`module-rule-ports=preserved`、`crg-source-db=crud-complete`、`dirty-copy=export-preserved/import-cleared` 和 `schemas=report-v4/inventory-v3`。映射日志必须包含 `crg-source-check=pass trace-depth=3 findings=none`；新增 `online_gui_crg_trace_depth_limit.log` 必须包含 `crg-trace-max-depth=2 finding-code=CRG_TRACE_DEPTH_LIMIT count=6`，并保持 2 passed rows、0 error。

正例第 3 行故意把完整本地例化名 `CTRL_RS_D0` 填入 `RS_inst`。该行通过证明空 remainder 合法，而且实例仍完成 module、parameters、step、clk/rst 和 direct depth 1 CRG trace。这里不能改填完整层次实例名。

`work.lib++` 仅供同目录的 `elabcom` 准备 KDB；Verdi GUI 和 kdebug/rscheck 检查都使用 `kdb.elab++`。

## 11. 常见故障

- `ERROR: no active gnome-session-binary session for user`：这是旧版或外部脚本的限制，不是当前工具的错误条件。执行 `git pull --ff-only` 更新仓库；本地/VNC/XRDP 桌面可清空四个过期显示变量后运行 `bash scripts/launch_rscheck_gui.sh --probe-only`，`ssh -Y` 会话则保留转发环境并先运行 `xdpyinfo`。当前会话解析不要求 GNOME。
- `tkinter is unavailable`：安装与 `PYTHON_BIN` 匹配的 Tk 包。SCL Python 3.8 使用 `rh-python38-python-tkinter`，并先 source `/opt/rh/rh-python38/enable`。
- `no usable X11 display`：从本地/VNC/XRDP 图形终端运行，或在客户端启动 X server 后使用 `ssh -Y`；先确认 `xdpyinfo` 成功。
- `multiple usable X11 displays`：根据错误列出的端点设置 `GUI_DISPLAY=:N`；必要时增加 `GUI_USER` 或正确的 `GUI_XAUTHORITY`。
- 只有 `WAYLAND_DISPLAY`：启用 Xwayland 并确认 `DISPLAY` 能通过 `xdpyinfo`。
- 读取不到其他用户 `/proc/<PID>/environ`：改由图形用户运行，并把仓库/KDB 放到该用户可访问的位置。
- `xdpyinfo` 认证失败：检查 `DISPLAY`/`XAUTHORITY` 是否属于当前登录，不要复制旧认证文件，也不要使用 `xhost +`。
- SSH 转发窗口在断开后关闭：保持 `ssh -Y` 隧道；`nohup` 或后台模式不能替代 X11 隧道。
- `xprop` 或 `xwininfo` 找不到：Debian/Ubuntu 安装 `x11-utils`；RHEL/CentOS 安装 `xorg-x11-utils`。
- `Verdi not found`：设置 `VERDI_BIN`、`VERDI_HOME` 或 `NOVAS_INST_DIR`，或把 `verdi` 加入 `PATH`。
- `no new Verdi X11 window title matched VERDI_READY_REGEX`：脚本看到了的窗口不代表 elaboration 已加载完成；检查 GUI 日志、license、DISPLAY 权限、KDB、Verdi/KDB 版本和实际窗口标题。必要时调高 `GUI_START_TIMEOUT`；仅当该 Verdi 版本确实使用不同标题格式时才覆盖 `VERDI_READY_REGEX`，且表达式仍须匹配目标 top，不能放宽为任意 Verdi 窗口。
- `kdebug executable not found`：在启动 GUI/测试的同一 shell 导出绝对 `KDEBUG_BIN`，并确认同一 build tree 的 `kdebug/libexec` 完整存在。
- `KDEBUG_RESPONSE`：确认两个仓库分别使用 `codex/rscheck-elab-inventory`、`codex/kdebug-npi-backend`；kdebug stdout 只能包含一个 JSON response，普通日志必须走 stderr。
- `warning[NPI_LOAD_PARTIAL]`：load 报告 elaboration error，但至少一个 top 可查询；工具会继续。CRG trace 若因此不完整会另报 `CRG_TRACE_UNAVAILABLE` warning，端口/parameter 硬错误仍正常失败。
- `KDEBUG_ACTION` / adapter 退出 11：action 失败或没有任何 top 可查询。确认 KDB 来自 `elabcom -elab`，核对 `KDEBUG_BIN`、Verdi/KDB 版本，并查看 kdebug stderr 与 `error.code/error.message`；`work.lib++` 及其符号链接别名会更早被拒绝。
- GUI 在线日志中出现 `-f`、RTL 或 `-top`：停止签核；当前实现不应构造这些参数，按输入边界回归处理。
- 示例出现 `POSITION_NOT_FOUND`：确认 Excel 为 `tile_core`、当前配置含 `position_mappings.tile_core=top.u_tile`；report 中 alias 为空表示未命中并按路径直通，优先检查简写大小写和实际加载的配置文件。
- 显式模块规则未生效：核对规则键与 Excel/RTL 模块名的大小写；没有精确匹配时工具采用默认 `has_rs_cfg_en=true`、`step_parameters=[]`、`clk_port=clk`、`rst_port=rst_n`，实际要求 RTL `RS_CRG_EN` 并检查默认端口。
- `CLK_PORT_MISSING` / `RST_PORT_MISSING`：查看 report 中最终 `module_rule.clk_port/rst_port` 和 inventory 的全部 `ports`。旧 offline inventory 若只采了全局端口，必须用当前 collector 重采。partial KDB 中端口仍为空时检查 NPI L1 库与 fallback 日志；当前 collector 会把 Netlist 已解析端口和 Language Model/L1 集合合并，补追非空但不完整的 Netlist input 集合，同时跳过已解析同名端口。clk/rst 独立判定：模块存在且连接了规则指定的 clk、但没有 rst 时，行 FAIL 且只能报 `RST_PORT_MISSING`；同时出现 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED` 应按回归缺陷处理。当前不能关闭 rst 检查。
- `CRG_SOURCE_NOT_FOUND`：完整 v3 trace 未精确命中目标完整 hierarchy；查看 report v4 `crg_source_check` 和 `clock_trace.modules/path`。
- `CRG_TRACE_DEPTH_LIMIT`：目标可能超过当前最大模块跳数；核对 GUI `CRG Trace 最大层数`，在 `1..256` 内调整并重采。
- `CRG_TRACE_UNAVAILABLE`：trace 缺失/未解析、规则 formal 不一致，或旧 v2 legacy 证据未精确命中。三个 CRG code 都只影响 warning 数，不改变通过行或 CLI 退出码。
- `STEP_PARAMETER_MISSING` / `STEP_PARAMETER_VALUE_UNRESOLVED`：规则要求的拍数 parameter 缺失或未知；查看 report v4 逐实例证据。
- `STEP_CALCULATION_UNRESOLVED`：至少一个贡献未知，整行 fail-closed。
- Excel/internal `RS_CFG_EN` 去空白后精确为大写 `NA` 时：下列全部 `RS_CFG_EN_*` finding 都应被跳过，即使 RTL `RS_CRG_EN` 缺失、未知或非零；`na`、`N/A` 不触发豁免。若同时存在 module、step、clk 或 rst finding，仍应正常显示和修复。
- `RS_CFG_EN_PARAMETER_MISSING`：非 `NA` 行的兼容规则键声明有门控 parameter，但实例证据缺少 `RS_CRG_EN`。
- `RS_CFG_EN_PARAMETER_UNEXPECTED`：非 `NA` 行的规则声明无门控 parameter，但 RTL 实际存在 `RS_CRG_EN`。
- `RS_CFG_EN_LABEL_MISMATCH`：仅在非 `NA`、`has_rs_cfg_en=true` 且 Excel/internal `RS_CFG_EN` 不是精确文本 `假门控` 时产生；`false` 时任意非 `NA` 字面内容都不产生该 finding。
- `RS_CFG_EN_VALUE_MISMATCH`：非 `NA` 行的 effective `RS_CRG_EN` 字符串不表示数值 `0`；检查实例 override 和本次 KDB。
- `RS_CFG_EN_VALUE_UNRESOLVED`：非 `NA` 行的 `parameters.RS_CRG_EN` 为 `null`；检查 NPI 参数遍历和 KDB，不能把它当作无参数。
- schema v1 inventory：旧格式没有逐实例参数证据，必须用当前 collector 重新生成 schema v3 文件。v2 可加载，但不具备完整 `clock_trace`。

## 12. 验证记录

当前代码版本为 `0.11.0`。[有界递归 CRG Source 追踪与 VM GUI 压测验证记录（2026-07-27）](TEST_RESULTS_CRG_TRACE_2026-07-27.md) 固定到 GitHub 提交 `b84be55638fd0af9fc9c3874bbc35786fd497a61`，只作为旧 C++ NPI collector 的历史 VM GUI baseline。该提交不含 kdebug backend，不能替代本分支的 kdebug/fresh 签核。GUI resolver 仍不要求 GNOME 或 `gnome-session-binary`。

现有 [CRG_source 映射库与 VM GUI 压测验证记录](TEST_RESULTS_CRG_SOURCE_MAPPING_2026-07-26.md) 固定到 `0.10.0` 提交 `366c54114bc23f2878e0715357f7ab40f2ef7ea5`，是尚未启用来源追踪时的十一日志历史基线，不能作为当前 inventory v3/report v4 和 depth-limit 专项证据。下列记录只作为历史对照。

[RS_CFG_EN don't-care 与 VM GUI 压测验证记录](TEST_RESULTS_RS_CFG_DONTCARE_2026-07-26.md) 是上一版 `0.9.1` 的固定基线，对应提交 `37ccef3bbd15a1191e85664a00e165a296699d12`：GitHub fresh clone 第一次成功，CentOS/Python 3.8 的 240 项全部通过且无 skip；partial/clean KDB、mapped Verdi/Tk GUI、`has_rs_cfg_en=false` / Excel 任意文本专项 20 轮、clk 存在/rst 缺失专项 20 轮、普通在线 3 轮、离线 100 轮、10,000 行负载和九份 GUI 日志门禁全部通过。更早的 [clk 存在、rst 缺失 finding 隔离记录](TEST_RESULTS_CLK_PRESENT_RST_MISSING_2026-07-26.md) 固定到历史功能提交 `b3d701c2b95a4941fae398b4c2490c7f630127c3`。

下述 [逐模块 clk/rst 端口与 CRG 暂停判定验证记录](TEST_RESULTS_MODULE_PORTS_2026-07-26.md) 是本轮 finding 隔离修复之前的 `0.9.0` 历史基线，固定到功能提交 `2e90d6636accee3d5450a1feac64dc2f36edc608`。[RTL RS_CRG_EN 匹配与 VM GUI 压测验证记录](TEST_RESULTS_RS_CRG_EN_2026-07-26.md) 是 `0.8.1` 历史基线，固定到 GitHub 提交 `a9a26869b99d69d3826ffb0071e967cfedbf5c92`。`TEST_RESULTS_CONFIG_IO_2026-07-26.md` 是 `0.8.0` 历史基线。

`TEST_RESULTS_NPI_PARTIAL_LOAD_2026-07-25.md`、`TEST_RESULTS_COLUMN_MAPPING_2026-07-25.md`、`TEST_RESULTS_FULL_INSTANCE_2026-07-25.md`、`TEST_RESULTS_POSITION_MAPPING_2026-07-25.md`、`TEST_RESULTS_DYNAMIC_STEP_2026-07-25.md`、`TEST_RESULTS_RS_CFG_EN_2026-07-24.md` 和 `TEST_RESULTS_2026-07-24.md` 是此前功能阶段的历史基线，只用于对照。

具体 VM 每次压力和在线 smoke 的终端输出应随提交一起记录在测试说明或提交信息中，但不得包含主机、密码、license 或会话认证路径。
