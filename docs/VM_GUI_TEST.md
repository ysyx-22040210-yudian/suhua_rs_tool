# rscheck 自带 GUI 与 Verdi GUI 的跨设备测试

本文档说明如何在不同 Linux 设备和桌面环境中启动、检查并压测两个独立 GUI：

| 界面 | 用途 | 启动器 | 设计输入 |
|---|---|---|---|
| **rscheck 自带 GUI** | 设置九列、维护两个数据库、导入/导出五根完整配置，运行检查并查看证据/日志 | `scripts/launch_rscheck_gui.sh` | schema v2 离线 inventory，或 collector + elaborated KDB；输出 report v3 |
| **Verdi GUI** | 人工浏览 hierarchy、实例和连线 | `scripts/launch_verdi_gui.sh` | `verdi -elab <elaborated KDB>` |

`rscheck` 同时提供 CLI 和自带 Tkinter GUI；GUI 不是 Verdi 的包装窗口。两个 GUI 可以查看同一份检查对象，但职责不同。

> **在线输入硬约束：**rscheck 自带 GUI 的在线模式只接受 collector 和 `elabcom` 生成的 elaborated KDB。界面不提供 RTL、filelist、top、`work.lib++`、编译参数或任意 Verdi passthrough 输入。Verdi launcher 同样只执行 `verdi -elab <KDB>`。

## 1. 仓库中的 GUI 组件

- `rscheck/gui.py`：工具自带 Tkinter 窗口、结果表、证据和日志。
- `rscheck/gui_backend.py`：严格构造 CLI 命令、读取报告和管理可取消的进程组。
- `scripts/launch_rscheck_gui.sh`：发现 X11/Xwayland 会话并启动工具自带 GUI。
- `scripts/test_rscheck_gui_smoke.py`：可见完整配置往返、离线正例、反例、批量压力和在线 KDB smoke。
- `scripts/lib/gui_session.sh`：两个 launcher 共用的 DISPLAY/Xauthority 发现与 `xdpyinfo` 探测。
- `scripts/lib/run_with_env_file.sh`：隔离加载可信站点环境并移除 xtrace/启动钩子。
- `scripts/launch_verdi_gui.sh`：只用 `verdi -elab <KDB>` 打开 Verdi GUI。
- `scripts/test_vm_fresh_checkout.sh`：在 `${VM_RUN_BASE:-$HOME}` 创建独立 fresh checkout、锁定提交并保存完整 VM 测试现场。
- `scripts/test_vm_verdi_gui.sh`：运行测试、构建 collector、生成示例 KDB、打开 Verdi 并执行在线正例；默认退出时关闭本次 Verdi。
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

Verdi/NPI 在线测试还需要 Bash 4.2+、Python 3.8+、支持 C++11 的编译器、GNU Make、Verdi/NPI 和组织批准的 license。不要把 VM 地址、SSH 密码、license 地址、token 或会话生成的 Xauthority 路径写入仓库。

应优先以图形会话所属用户运行，并确保该用户能读取仓库和 KDB。root 从其他用户的 `/proc/<PID>/environ` 恢复会话只用于旧部署兼容，可能被 SELinux、`hidepid`、文件权限或显示服务器策略阻止。不要使用 `xhost +` 绕过认证。

## 3. 获取仓库并探测 GUI

在新设备克隆 bootstrap checkout 并安装可编辑入口。该 checkout 用于取得启动器；第 10 节的正式复现会另外创建 VM 本机 fresh checkout：

```bash
git clone https://github.com/ysyx-22040210-yudian/suhua_rs_tool.git "$HOME/suhua_rs_tool"
cd "$HOME/suhua_rs_tool"

if [ -f /opt/rh/rh-python38/enable ]; then
  source /opt/rh/rh-python38/enable
fi
python3 -m pip install -e .
```

工具自带 GUI 探测：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_rscheck_gui.sh --probe-only
```

预期：

```text
GUI access OK: source=... user=... DISPLAY=...
rscheck GUI probe PASS
```

在本地/VNC/XRDP 桌面已经登录、但当前 shell 继承了过期显示变量时，可直接验证自动发现。以下命令不会写死 DISPLAY、用户或 Xauthority 路径：

```bash
cd "$HOME/suhua_rs_tool"
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

macOS 只支持 Excel 验证和离线 inventory 模式；真实 NPI collector、`libNPI.so`、`libnpiL1.so` 和 elaborated KDB 在线加载只支持 Linux。

## 5. GUI 字段和操作验收

“检查配置”页应包含：

- `Excel / CSV`、`配置 JSON` 和配置“加载”按钮；
- `工作表`、`表头行`、`数据起始行`、默认未勾选的 `严格校验表头（可选）`；
- “内部属性 -> Excel 列号”区域中的九个独立 1-based 映射：`Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`、`RS_CFG_EN`；这些名称是工具内部属性键，实际 Excel 表头可以任意命名；兼容字段 `RS_CFG_EN` 的列映射始终必需，内容仍解析并显示，实际 RTL parameter 名为 `RS_CRG_EN`；`CRG_source` 当前仍必填和报告，但不参与 PASS/FAIL；
- 数据源单选项 `在线 NPI（elaborated KDB）` 和 `离线 Inventory`；
- 在线字段 `Collector`、`Elab KDB`、`NPI 库目录`、`保存 Inventory`、`超时（秒）`；
- 离线字段 `Inventory JSON`；
- `JSON` 和可选 `CSV` 报告路径；
- `验证 Excel`、`运行 RTL 检查`、`取消`、`打开报告目录`。

加载示例配置后必须确认严格表头诊断保持未勾选。当前 `examples/specs.csv` 使用“接口分类”“模块类型”等业务表头，与九个内部属性名均不同；GUI 的“验证 Excel”和后续检查仍应按配置中的 1-based 列号正常通过。手工勾选严格诊断后再次验证该文件，应按预期报告 `header validation failed`，证明精确表头比较只是 opt-in 诊断。

“模块规则库”页应支持搜索、新建、修改、删除和保存。`rs_pipe` 应显示显式覆盖 `has_rs_cfg_en=true`、`step_parameters=rs_mode`、`clk_port=clk`、`rst_port=rst`，并把兼容规则键说明为“有 `RS_CRG_EN` parameter”。新建或修改规则时把 clk/rst 端口输入留空，应分别规范化为 `clk`、`rst_n`。规则未保存时不能运行；保存应写回当前配置 JSON并重新加载。显式规则名与 Excel `RS_module` 大小写敏感、精确匹配并优先于默认值；没有专属项时正常使用 `has_rs_cfg_en=true`、`step_parameters=[]`、`clk_port=clk`、`rst_port=rst_n`，即要求 RTL `RS_CRG_EN=0`、Excel/internal `RS_CFG_EN=假门控`，且每个匹配实例贡献 1。旧 JSON 的显式规则缺少端口键时先继承 `rtl.clk_port/rst_port`，GUI 保存或导出后必须显式写入继承值。

“Position 映射库”页应支持搜索、新建、修改、删除和保存。示例配置必须显示 `tile_core -> top.u_tile`；映射未保存时不能运行，保存应原子写回当前配置 JSON 并重新加载。Excel 简写命中后检查、分组、clk/rst 相对解析和 NPI positions 均使用全路径；未命中值按完整路径直通。

当前 `0.9.1` 的“配置 JSON”行应同时显示“导入”“加载”“导出”。完整配置文件只有五个根对象：`excel`、`columns`、`rtl`、`position_mappings`、`module_rules`。手工验收导出时，先修改当前 GUI 的 sheet/行号/严格表头和九列列号，再在两个数据库中各“应用”一项但不单独保存，并保持搜索过滤生效；导出的副本必须包含当前 Excel/列号、已加载配置的完整 `rtl` 和两个未受过滤的完整内存数据库，包括每条模块规则的 `clk_port/rst_port` 及这两项未保存修改。导出后当前配置路径和两个 dirty 状态必须保持不变，原配置文件不得改变；选择当前配置自身作为导出目标必须被拒绝。只改路径输入框但尚未加载另一配置时也必须拒绝导出，避免混用旧 `rtl`。

导入验收必须确认候选 JSON 在任何丢弃提示之前先完成五根完整校验，缺少任一根对象都应拒绝；候选有效后，先确认与已加载配置不同的 Excel/列号表单，再逐一确认两个 dirty 数据库。全部接受后还应重新读取候选，复核仍有效才整体替换配置、切换 active path 并清除 dirty。兼容性“加载”允许历史可选根字段，但也必须经过相同的确认和二次读取。取消文件选择、首次/复核无效候选或拒绝任一确认时，当前 GUI 字段、active path、两个内存数据库和 dirty 状态都不得发生部分变化。配置若以字符串保存纯数字工作表名（例如 `"123"`），该字段未编辑时导出和运行必须继续使用字符串名称；不能误转成 1-based 数字序号。Excel/CSV、collector、Elab KDB、NPI 库、inventory、report 和超时等运行输入/输出路径不属于配置，导入后按目标设备重新选择。

`has_rs_cfg_en=true` 时每个 RTL 实例都必须存在 effective `RS_CRG_EN`、值为 0，且 Excel/internal `RS_CFG_EN` 精确填写 `假门控`；`false` 时 Excel 字段的任意字面内容都不参与 PASS/FAIL，但仍解析、显示和写入报告，RTL 则不得实际存在 `RS_CRG_EN`，否则仍报 `RS_CFG_EN_PARAMETER_UNEXPECTED`。映射字段中的公式和 Excel 错误值仍受通用解析限制。工具不会回退匹配 RTL `RS_CFG_EN`。`step_parameters` 为空时每个物理实例贡献 1；非空且所有值均可解析时，全部非零贡献 1、至少一个为 0 贡献 0。`RS_CRG_EN` 不能加入 `step_parameters`；RTL 中另一个确实存在的 `RS_CFG_EN` 可作为普通动态拍 parameter。任一缺失/`null`/X/Z/非法值都会让贡献未知并 fail-closed。`step` 可为 0，但没有物理匹配实例仍是 `GROUP_NOT_FOUND`。每条规则还必须有非空 `clk_port/rst_port`；两个端口分别取证和判定。当前没有“无 rst/跳过 rst”模式：模块存在且连接了所指 clk、但缺少所指 rst formal port 时，该行应 FAIL 且只报 `RST_PORT_MISSING`，不得误报 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`。

“检查结果”页应分别显示“匹配实例”和“实际/期望拍”，Position 应显示 `tile_core -> top.u_tile`。示例首组必须显示 6 个物理实例、`5/5` 拍；证据面板应包含 `spec.position=top.u_tile`、`spec.position_alias=tile_core`、含 `clk_port/rst_port` 的 `module_rule`、`step_check`、逐实例贡献 `[1,1,0,1,1,1]`、全部 effective `parameters`、全部 formal ports、`clk_sources` 和源文件/行号。`spec.CRG_source` 仍可见，但不产生 finding；新在线 inventory 的 `clk_sources` 应为 `[]`。“运行日志”页应包含实际 CLI 命令、stdout、stderr 和退出码。

运行期间输入控件和两个启动按钮应禁用，“取消”应启用。取消后状态应显示 `CANCELLED`，CLI 和 collector 进程组都应退出；关闭正在运行的窗口时应先出现取消确认。

Windows/Linux 布局仍以最小尺寸 `980x680` 验收五个页签“检查配置”“模块规则库”“Position 映射库”“检查结果”“运行日志”以及“导入/加载/导出”三个配置按钮无重叠或截断。历史截图不含 `0.8.0` 完整配置按钮和往返证据，不能作为当前版本证据；必须重新验收，截图仍只用于本地检查、不提交仓库。

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

先直接复制运行 Position 映射库页 smoke；它只保存临时配置，不改仓库文件，并同时完成映射库和模块规则库 CRUD、五根完整配置导出和原子导入：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --iterations 1 \
  --visible-tab positions \
  --visible-seconds 10
```

窗口保留期间检查 980×680 下映射表、搜索框和编辑控件无重叠，确认 `tile_core -> top.u_tile`，终端末行应为 `GUI_SMOKE_PASS`，并包含 `header-map=column-index strict-header=false`、`module-rule-ports=preserved` 和 `config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules`。后两个 marker 证明副本保留五根、当前 Excel/列号、已加载 `rtl`、每条模块规则的 `clk_port/rst_port` 及包含未单独保存修改的两个完整数据库，且导入后成功切换路径并清除 dirty。

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

预期：`GUI_SMOKE_PASS`、`mode=offline case=positive iterations=100 window=mapped window_id=0x...`、`header-map=column-index strict-header=false`、`module-rule-ports=preserved`、固定 config-io marker、`position-map=tile_core->top.u_tile npi-positions=full-path-only`、结果页 2 行 PASS、0 error、0 warning。每轮 report 必须保留 alias、`CRG_source` 和最终 `clk_port/rst_port`，inventory positions 只能包含 `top.u_tile`；首组应显示 physical=6、effective/expected=5/5，逐实例贡献为 `[1,1,0,1,1,1]`，所有匹配实例的门控参数证据必须为 `parameters.RS_CRG_EN="0"`。旧 fixture 的 `clk_sources` 即使非空也不参与判定。离线 inventory 必须为 schema v2，生成的 report 必须为 schema v3。

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
GUI_SMOKE_PASS: rows=行数 10000 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=1 window=mapped window_id=0x... header-map=column-index strict-header=false config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules
```

2026-07-24 九列版本的 2.12 秒、最大 RSS 175,612 KiB，以及 0.7.1 fresh run 的 10,000 行结果都只是完整配置导入/导出之前的历史性能数据；旧时间和 RSS 既不是当前 `0.9.1` 结果，也不是不同设备的硬门槛。脚本生成临时 CSV/inventory，走真实 GUI 配置往返、后台 CLI、报告读取和 10,000 行结果表渲染，退出时自动清理。

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

以下命令不包含 VM 地址、密码或真实 license。先替换三个路径占位符，并通过组织环境设置 `LM_LICENSE_FILE`。`ELAB_DB` 必须是当前 RTL 由 `elabcom` 新生成的 elaborated KDB；不得传 filelist、RTL、top 或 `work.lib++`：

```bash
export PROJECT_ROOT="/path/to/suhua_rs_tool"
export VERDI_HOME="/path/to/verdi"
export ELAB_DB="/absolute/path/to/kdb.elab++"

: "${LM_LICENSE_FILE:?set LM_LICENSE_FILE through the approved site environment}"
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-$LM_LICENSE_FILE}"
export NPI_PLATFORM="${NPI_PLATFORM:-LINUX64}"
export PYTHON_BIN="${PYTHON_BIN:-python3}"
export CXX="${CXX:-g++}"
export NPI_INC_DIR="${NPI_INC_DIR:-$VERDI_HOME/share/NPI/inc}"
export NPI_LIB_DIR="${NPI_LIB_DIR:-$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM}"
export NPI_L1_INC_DIR="${NPI_L1_INC_DIR:-$VERDI_HOME/share/NPI/L1/C/inc}"
if [ -z "${NPI_L1_LIB_DIR:-}" ]; then
  if [ -f "$NPI_LIB_DIR/libnpiL1.so" ]; then
    NPI_L1_LIB_DIR="$NPI_LIB_DIR"
  else
    NPI_PLATFORM_LOWER="$(printf '%s' "$NPI_PLATFORM" | tr '[:upper:]' '[:lower:]')"
    NPI_L1_LIB_DIR="$VERDI_HOME/share/NPI/lib/$NPI_PLATFORM_LOWER"
  fi
fi
export NPI_L1_LIB_DIR
export COLLECTOR="$PROJECT_ROOT/npi/build/rs_npi_collector"

cd "$PROJECT_ROOT"
if [ -f /opt/rh/rh-python38/enable ]; then
  source /opt/rh/rh-python38/enable
fi

make -C npi \
  VERDI_HOME="$VERDI_HOME" \
  NPI_PLATFORM="$NPI_PLATFORM" \
  NPI_INC="$NPI_INC_DIR" \
  NPI_LIB="$NPI_LIB_DIR" \
  NPI_L1_INC="$NPI_L1_INC_DIR" \
  NPI_L1_LIB="$NPI_L1_LIB_DIR" \
  CXX="$CXX"
test -x "$COLLECTOR"
test -d "$ELAB_DB"
test -f "$NPI_LIB_DIR/libNPI.so"
test -f "$NPI_L1_INC_DIR/npi_L1.h"
test -f "$NPI_L1_LIB_DIR/libnpiL1.so"
ldd "$COLLECTOR" | grep 'libNPI\.so'
ldd "$COLLECTOR" | grep 'libnpiL1\.so'

bash scripts/launch_rscheck_gui.sh --probe-only
source scripts/lib/gui_session.sh
gui_session_resolve

"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
  --timeout 180 \
  --iterations 3 \
  --visible-seconds 15
```

成功判据：脚本返回 `0` 并打印：

```text
GUI_SMOKE_PASS: rows=行数 2 errors=错误 0 warnings=警告 0 mode=online case=positive iterations=3 window=mapped window_id=0x... header-map=column-index strict-header=false contract=elab-only position-map=tile_core->top.u_tile npi-positions=full-path-only config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules module-rule-ports=preserved
```

3 轮中的每一轮都必须通过真实 collector 加载该 KDB；最终 GUI 为 2 行 PASS、0 error、0 warning，日志必须包含 `header-map=column-index strict-header=false`、`module-rule-ports=preserved` 和固定 config-io marker。示例 Excel 使用 `tile_core`，report 必须记录 `position_alias=tile_core` 和 `position=top.u_tile`，本次 inventory 的 positions key 只能是 `top.u_tile`。首组必须有 6 个物理实例，`rs_mode` 为五个 1、一个 0，有效/期望拍为 `5/5`，贡献为 `[1,1,0,1,1,1]`；每个 RS 实例的 inventory/report 参数证据必须包含 `RS_CRG_EN="0"`，不能用 RTL `RS_CFG_EN` 代替。collector 必须保留 `rs_pipe` 的全部 `clk/rst/d/q` formal ports，并把所有新采 `clk_sources` 写为 `[]`；这同时覆盖 partial KDB 的 NPI L1 fallback。inventory 必须是 schema v2，report 必须是 schema v3，`module_rule` 必须含 `clk_port/rst_port`。`CRG_source` 仍进入 `spec` 但不参与 PASS/FAIL。运行日志中的实际命令必须包含 `--collector`、`--elab-db`，不得出现 inventory、RTL、filelist、`-top` 或 `--` passthrough。

示例 RTL 还含 `rs_custom.CUSTOM_RS`，formal ports 为 `clock_i/reset_ni/d/q`。一键 VM 脚本会从真实 inventory 生成专用规格和 `clk_port=clock_i`、`rst_port=reset_ni` 规则，并故意填写错误 `CRG_source`；专用行仍必须 PASS，日志固定包含：

```text
custom module clk/rst formal-port rule evidence OK: clock_i/reset_ni
CRG_source evidence retained without PASS/FAIL validation
```

同一个 clean KDB 还包含 `rs_clk_only.CLK_ONLY_RS`：formal ports 精确为 `clk/d/q`，`clk` 连接 `top.u_tile.clk_rs`，没有任何 rst formal port。直接复制下面命令，可用工具自带的可见 GUI 连续执行 20 轮端口隔离回归：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --collector "$COLLECTOR" \
  --elab-db "$ELAB_DB" \
  --npi-lib-dir "$NPI_LIB_DIR" \
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

Verdi 端到端脚本会运行全量测试、链接 NPI Language/L1 的 collector，先生成故意带 elaboration error 但 top 可查询的 partial KDB，要求 NPI L1 fallback 补齐全部 formal ports，CLI 和工具 GUI 都以 2 行 PASS、0 error、1 个 `NPI_LOAD_PARTIAL` warning 完成；随后用 clean 示例 RTL 生成 fresh `kdb.elab++`、执行 `verdi -elab`，并等待新窗口标题匹配 `VERDI_READY_REGEX`、明确显示已展开的 top `top`，之后才用同一 clean KDB 做在线 NPI 检查。clean KDB 还会执行有 clk/无 rst 的 CLI 精确 finding 检查和默认 20 轮可见 GUI 压测。每次 GUI smoke 都执行含逐模块端口名的五根配置往返；任意新 Verdi 窗口或固定等待时间都不能代替该标题证据。

正式复现推荐从 bootstrap checkout 调用 fresh 驱动。下面命令会在 `${VM_RUN_BASE:-$HOME}/rscheck_fresh.*` 创建唯一运行根目录，最多执行三次同时带 TERM timeout 和 KILL 上限的 GitHub clone，每次使用独立且永久保留的 `repo_attemptN` 目录；成功后锁定克隆时的 `origin/main`，将全部控制台输出写入 `full_vm_test.log`，并强制把正式测试产物写入 `artifacts`：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_verdi_gui.sh --probe-only
bash scripts/test_vm_fresh_checkout.sh
```

精确复现指定提交：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/test_vm_fresh_checkout.sh --commit FULL_SHA
```

如果当前图形 shell 尚未加载站点 Verdi/NPI 环境，可让驱动在正式测试前静默加载一个可信的绝对路径文件。该文件只在隔离子进程中执行；它不能改变 fresh 驱动核对过的仓库/产物路径，stdout/stderr 和 xtrace 不写入日志，返回非零时立即终止。里面设置的 Verdi/NPI/GUI 变量会传给正式脚本：

```bash
cd "$HOME/suhua_rs_tool"
VERDI_ENV_FILE=/path/to/site_env.sh \
bash scripts/test_vm_fresh_checkout.sh --commit FULL_SHA
```

无论 PASS 或 FAIL，退出信息都会打印 `RUN_ROOT`、`FULL_LOG`、`ARTIFACT_ROOT` 和 clone 尝试位置。驱动不会删除任何运行目录或失败 clone。`CLONE_TIMEOUT` 可覆盖单次 clone 的默认 180 秒，`VM_RUN_BASE` 可用绝对路径覆盖运行父目录；正式脚本的其他环境变量不变。

root 通过非交互 SSH 启动、当前环境没有 license 时，正式脚本会从 GUI 会话解析出的桌面用户登录环境中自动导入 `LM_LICENSE_FILE`/`SNPSLMD_LICENSE_FILE`。该流程不写死用户名/home、不执行解析到的文本、不导入 PATH，也不输出 license 值；已有 license 值优先。站点使用其他机制时可设置 `VERDI_AUTO_LICENSE_IMPORT=0`，让 Verdi 自行诊断。

只有当前 checkout 已经可信并位于 VM 本机文件系统时，才可跳过 fresh clone 直接执行：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/test_vm_verdi_gui.sh
```

也可只测试端到端脚本的会话发现，不启动 Verdi：

```bash
bash scripts/test_vm_verdi_gui.sh --gui-probe-only
```

端到端覆盖变量包括 `VERDI_BIN`、`VERDI_HOME`、`NOVAS_INST_DIR`、`VERDI_WINDOW_REGEX`、`VERDI_READY_REGEX`、`PYTHON_BIN`、`CXX`、`NPI_PLATFORM`、`NPI_INC_DIR`、`NPI_LIB_DIR`、`NPI_L1_INC_DIR`、`NPI_L1_LIB_DIR`、`PYTHON_ENABLE`、`GCC_ENABLE`、`GUI_START_TIMEOUT`、`NPI_TIMEOUT`、`KEEP_VERDI_GUI`、`GUI_ONLINE_ITERATIONS`、`GUI_CLK_WITHOUT_RST_ITERATIONS`、`GUI_RS_CFG_DONTCARE_ITERATIONS`、`GUI_STRESS_ITERATIONS`、`GUI_LOAD_ROWS`、`GUI_VISIBLE_SECONDS`、`VERDI_ENV_FILE` 和 `VERDI_AUTO_LICENSE_IMPORT`。fresh 驱动另支持 `VM_RUN_BASE` 和 `CLONE_TIMEOUT`；直接运行正式脚本时还可设置 `OUTPUT_BASE`，fresh 驱动会固定覆盖为本轮 `artifacts`。默认 `VERDI_READY_REGEX` 匹配 nTrace 主窗口标题中的 `top`；若 Verdi 版本标题格式不同，可显式覆盖，但表达式仍必须标识已展开目标 top。普通在线正例默认 3 轮，有 clk/无 rst 的专项在线 GUI 默认 20 轮；`has_rs_cfg_en=false` / Excel 任意文本离线 GUI 专项默认 20 轮；离线稳定性默认 100 轮，负载默认 10,000 行。各轮次变量必须是十进制正整数。`NPI_LIB_DIR` 必须直接包含 `libNPI.so`，`NPI_L1_INC_DIR` 必须含 `npi_L1.h`，`NPI_L1_LIB_DIR` 必须直接包含 `libnpiL1.so`；默认退出时关闭本次启动的 Verdi，设置 `KEEP_VERDI_GUI=1` 才在成功后保留窗口。

成功输出应包含：

```text
Ran ... tests in ...
OK
partial NPI load evidence OK: load reported errors but requested RTL remained queryable
partial NPI formal-port L0/L1 inventory evidence OK; clock-source tracing disabled
RESULT: PASS | rows=2 errors=0 warnings=1
[WARNING] NPI_LOAD_PARTIAL: ...
Verdi GUI loaded elaborated top 'top' after ...
RESULT: PASS | rows=2 errors=0 warnings=0
[PASS] row 2 OUT_IF | tile_core -> top.u_tile / AAAA_BBB physical=6 effective=5 expected=5 RS_CFG_EN=假门控
[PASS] row 3 CTRL_IF | tile_core -> top.u_tile / CTRL_RS_D0 physical=1 effective=1 expected=1 RS_CFG_EN=假门控
custom module clk/rst formal-port rule evidence OK: clock_i/reset_ni
clk-present/rst-missing CLI evidence OK: ports=clk,d,q
finding isolation OK: RST_PORT_MISSING only; CLK_PORT_MISSING absent
CRG_source evidence retained without PASS/FAIL validation
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=online case=custom-port ... rule-ports=clock_i/reset_ni ...
GUI_SMOKE_PASS: state=FAIL rows=行数 1 errors=错误 1 warnings=警告 0 mode=online case=clk-present-rst-missing iterations=20 ... clk-port-evidence=present rst-port-evidence=missing finding-codes=RST_PORT_MISSING ...
GUI_SMOKE_PASS: state=PASS rows=行数 1 errors=错误 0 warnings=警告 0 mode=offline case=rs-cfg-dontcare iterations=20 ... has-rs-cfg-en=false label=dont-care rs-crg-en=absent findings=none parsed-rs-cfg-en=任意非标准文本 ...
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules
module-rule-ports=preserved
PASS: partial KDB compatibility, arbitrary Excel headers, position mapping, fresh KDB online GUI checks, and offline GUI stress suite completed.
```

正式脚本不会只检查一次 marker。它对以下九份日志逐一执行 `grep -Fq` 硬断言，任一缺失都会使端到端测试失败：

```text
online_gui_positive.log
online_gui_custom_port.log
online_gui_clk_present_rst_missing.log
partial_load_gui.log
online_gui_negative.log
offline_gui_default_rule.log
offline_gui_rs_cfg_dontcare.log
offline_gui_100_rounds.log
offline_gui_10000_rows.log
```

九份日志都必须包含完全相同的 `config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules` 和 `module-rule-ports=preserved`。其中 `partial_load_gui.log` 明确门禁 partial KDB 路径；`online_gui_custom_port.log` 还必须包含 `mode=online case=custom-port` 和 `rule-ports=clock_i/reset_ni`；`online_gui_clk_present_rst_missing.log` 必须包含 `case=clk-present-rst-missing`、`clk-port-evidence=present`、`rst-port-evidence=missing` 和 `finding-codes=RST_PORT_MISSING`，并且不得出现 `CLK_PORT_MISSING`；`offline_gui_rs_cfg_dontcare.log` 必须包含 `case=rs-cfg-dontcare` 和 `has-rs-cfg-en=false label=dont-care rs-crg-en=absent findings=none`。这证明 clean/partial、普通正例、自定义端口、端口隔离反例、普通反例、默认规则、`has_rs_cfg_en=false` 的 Excel 任意文本 don't-care、稳定性和负载入口都实际经过同一套完整配置导入/导出合同，且逐模块端口名没有在往返中丢失。

正例第 3 行故意把完整本地例化名 `CTRL_RS_D0` 填入 `RS_inst`。该行通过证明空 remainder 合法，而且实例仍完成 module、parameters、step 和逐模块 clk/rst 检查；`CRG_source` 仅作为报告证据。这里不能改填 `top.u_tile.CTRL_RS_D0`。空后缀实例不参与 tag/index/连续编号检查。若同一 scope 另有符合 suffix 规则的 `CTRL_RS_D0_*数字`，较短的 `RS_inst=CTRL_RS_D0` 仍会按前缀语义一并匹配，当前没有 exact-only 模式。

`work.lib++` 仅供同目录的 `elabcom` 准备 KDB；Verdi GUI 和 NPI 检查都使用 `kdb.elab++`。

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
- `npi.h`、`npi_L1.h`、`libNPI.so` 或 `libnpiL1.so` 找不到：设置 `NPI_INC_DIR`、`NPI_LIB_DIR`、`NPI_L1_INC_DIR`、`NPI_L1_LIB_DIR`；两个库目录必须分别直接包含对应 `.so`，并用 `ldd` 确认都不是 `not found`。
- `warning[NPI_LOAD_PARTIAL]`：load 报告 elaboration error，但至少一个 top 可查询；工具会继续并在 report/GUI 显示非致命 warning。逐项检查目标 position、实例、全部 formal ports 和 parameter；`CRG_source` 当前不参与判定。
- `error[NPI_LOAD]` / collector 退出 11：load 返回 0 且没有任何 top 可查询。确认 KDB 来自 `elabcom -elab`，执行 `ldd "$COLLECTOR" | grep -E 'libNPI|libnpiL1'` 核对运行库与 Verdi/KDB 版本，并查看 collector stdout、stderr 和 `rs_npi_collectorLog/compiler.log`；`work.lib++` 及其符号链接别名会更早被拒绝。
- GUI 在线日志中出现 `-f`、RTL 或 `-top`：停止签核；当前实现不应构造这些参数，按输入边界回归处理。
- 示例出现 `POSITION_NOT_FOUND`：确认 Excel 为 `tile_core`、当前配置含 `position_mappings.tile_core=top.u_tile`；report 中 alias 为空表示未命中并按路径直通，优先检查简写大小写和实际加载的配置文件。
- 显式模块规则未生效：核对规则键与 Excel/RTL 模块名的大小写；没有精确匹配时工具采用默认 `has_rs_cfg_en=true`、`step_parameters=[]`、`clk_port=clk`、`rst_port=rst_n`，实际要求 RTL `RS_CRG_EN` 并检查默认端口。
- `CLK_PORT_MISSING` / `RST_PORT_MISSING`：查看 report 中最终 `module_rule.clk_port/rst_port` 和 inventory 的全部 `ports`。旧 offline inventory 若只采了全局端口，必须用当前 collector 重采。partial KDB 中端口仍为空时检查 NPI L1 库与 fallback 日志。clk/rst 独立判定：模块存在且连接了规则指定的 clk、但没有 rst 时，行 FAIL 且只能报 `RST_PORT_MISSING`；同时出现 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED` 应按回归缺陷处理。当前不能关闭 rst 检查。
- `CRG_source` 不一致但仍 PASS：这是当前行为；字段仍解析/报告，新 inventory 的 `clk_sources=[]`，不做来源正确性判断。
- `STEP_PARAMETER_MISSING` / `STEP_PARAMETER_VALUE_UNRESOLVED`：规则要求的拍数 parameter 缺失或未知；查看 report v3 逐实例证据。
- `STEP_CALCULATION_UNRESOLVED`：至少一个贡献未知，整行 fail-closed。
- `RS_CFG_EN_PARAMETER_MISSING`：兼容规则键声明有门控 parameter，但实例证据缺少 `RS_CRG_EN`。
- `RS_CFG_EN_PARAMETER_UNEXPECTED`：规则声明无门控 parameter，但 RTL 实际存在 `RS_CRG_EN`。
- `RS_CFG_EN_LABEL_MISMATCH`：仅在 `has_rs_cfg_en=true` 且 Excel/internal `RS_CFG_EN` 不是精确文本 `假门控` 时产生；`false` 时任意字面内容都不产生该 finding。
- `RS_CFG_EN_VALUE_MISMATCH`：effective `RS_CRG_EN` 字符串不表示数值 `0`；检查实例 override 和本次 KDB。
- `RS_CFG_EN_VALUE_UNRESOLVED`：schema v2 `parameters.RS_CRG_EN` 为 `null`；检查 NPI 参数遍历和 KDB，不能把它当作无参数。
- schema v1 inventory：旧格式没有逐实例参数证据，必须用当前 collector 重新生成 schema v2 文件。

## 12. 验证记录

当前代码版本为 `0.9.1`，需要为本版本生成新的固定 SHA 验证记录。[clk 存在、rst 缺失 finding 隔离与 VM GUI 压测验证记录](TEST_RESULTS_CLK_PRESENT_RST_MISSING_2026-07-26.md) 固定到功能提交 `b3d701c2b95a4941fae398b4c2490c7f630127c3`：GitHub fresh clone 第一次成功，CentOS/Python 3.8 的 236 项全部通过且无 skip；partial/clean KDB 均采到 `CLK_ONLY_RS={clk,d,q}`，其 `clk` 已连接而 rst 缺失时，CLI 与专项在线 GUI 连续 20 轮只产生 `RST_PORT_MISSING`。普通在线 3 轮、离线 100 轮、10,000 行负载和该历史提交当时的 8 个 GUI 日志门禁也全部通过；当前门禁已扩展为上文九份日志，不能用该旧记录代替复测。本轮 Verdi 和 rscheck Tk 窗口均为 mapped；resolver 从实际桌面会话取得 `DISPLAY=:0`，不要求 GNOME 或 `gnome-session-binary`。

下述 [逐模块 clk/rst 端口与 CRG 暂停判定验证记录](TEST_RESULTS_MODULE_PORTS_2026-07-26.md) 是本轮 finding 隔离修复之前的 `0.9.0` 历史基线，固定到功能提交 `2e90d6636accee3d5450a1feac64dc2f36edc608`。[RTL RS_CRG_EN 匹配与 VM GUI 压测验证记录](TEST_RESULTS_RS_CRG_EN_2026-07-26.md) 是 `0.8.1` 历史基线，固定到 GitHub 提交 `a9a26869b99d69d3826ffb0071e967cfedbf5c92`。`TEST_RESULTS_CONFIG_IO_2026-07-26.md` 是 `0.8.0` 历史基线。

`TEST_RESULTS_NPI_PARTIAL_LOAD_2026-07-25.md`、`TEST_RESULTS_COLUMN_MAPPING_2026-07-25.md`、`TEST_RESULTS_FULL_INSTANCE_2026-07-25.md`、`TEST_RESULTS_POSITION_MAPPING_2026-07-25.md`、`TEST_RESULTS_DYNAMIC_STEP_2026-07-25.md`、`TEST_RESULTS_RS_CFG_EN_2026-07-24.md` 和 `TEST_RESULTS_2026-07-24.md` 是此前功能阶段的历史基线，只用于对照。

具体 VM 每次压力和在线 smoke 的终端输出应随提交一起记录在测试说明或提交信息中，但不得包含主机、密码、license 或会话认证路径。
