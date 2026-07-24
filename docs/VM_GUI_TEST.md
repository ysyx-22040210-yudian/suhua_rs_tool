# rscheck 自带 GUI 与 Verdi GUI 的跨设备测试

本文档说明如何在不同 Linux 设备和桌面环境中启动、检查并压测两个独立 GUI：

| 界面 | 用途 | 启动器 | 设计输入 |
|---|---|---|---|
| **rscheck 自带 GUI** | 设置九列映射和模块规则、运行检查、查看动态拍数证据/日志 | `scripts/launch_rscheck_gui.sh` | schema v2 离线 inventory，或 collector + elaborated KDB；输出 report v3 |
| **Verdi GUI** | 人工浏览 hierarchy、实例和连线 | `scripts/launch_verdi_gui.sh` | `verdi -elab <elaborated KDB>` |

`rscheck` 同时提供 CLI 和自带 Tkinter GUI；GUI 不是 Verdi 的包装窗口。两个 GUI 可以查看同一份检查对象，但职责不同。

> **在线输入硬约束：**rscheck 自带 GUI 的在线模式只接受 collector 和 `elabcom` 生成的 elaborated KDB。界面不提供 RTL、filelist、top、`work.lib++`、编译参数或任意 Verdi passthrough 输入。Verdi launcher 同样只执行 `verdi -elab <KDB>`。

## 1. 仓库中的 GUI 组件

- `rscheck/gui.py`：工具自带 Tkinter 窗口、结果表、证据和日志。
- `rscheck/gui_backend.py`：严格构造 CLI 命令、读取报告和管理可取消的进程组。
- `scripts/launch_rscheck_gui.sh`：发现 X11/Xwayland 会话并启动工具自带 GUI。
- `scripts/test_rscheck_gui_smoke.py`：可见离线正例、反例、批量压力和在线 KDB smoke。
- `scripts/lib/gui_session.sh`：两个 launcher 共用的 DISPLAY/Xauthority 发现与 `xdpyinfo` 探测。
- `scripts/launch_verdi_gui.sh`：只用 `verdi -elab <KDB>` 打开 Verdi GUI。
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

在新设备克隆并安装可编辑入口：

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

macOS 只支持 Excel 验证和离线 inventory 模式；真实 NPI collector、`libNPI.so` 和 elaborated KDB 在线加载只支持 Linux。

## 5. GUI 字段和操作验收

“检查配置”页应包含：

- `Excel / CSV`、`配置 JSON` 和配置“加载”按钮；
- `工作表`、`表头行`、`数据起始行`、`校验映射表头`；
- 九个独立的 1-based 列映射：`Intf_type`、`RS_module`、`RS_inst`、`position`、`step`、`clk`、`rst`、`CRG_source`、`RS_CFG_EN`；
- 数据源单选项 `在线 NPI（elaborated KDB）` 和 `离线 Inventory`；
- 在线字段 `Collector`、`Elab KDB`、`NPI 库目录`、`保存 Inventory`、`超时（秒）`；
- 离线字段 `Inventory JSON`；
- `JSON` 和可选 `CSV` 报告路径；
- `验证 Excel`、`运行 RTL 检查`、`取消`、`打开报告目录`。

“模块规则库”页应支持搜索、新建、修改、删除和保存。`rs_pipe` 应显示显式覆盖 `has_rs_cfg_en=true`、`step_parameters=rs_mode`。规则未保存时不能运行；保存应写回当前配置 JSON并重新加载。显式规则名与 Excel `RS_module` 大小写敏感、精确匹配并优先于默认值；没有专属项时正常使用 `has_rs_cfg_en=true`、`step_parameters=[]`，即要求假门控且每个匹配实例贡献 1。

`has_rs_cfg_en=true` 时每个 RTL 实例都必须存在该 effective parameter、值为 0，且 Excel 精确填写 `假门控`；`false` 时 Excel 必须留空且 RTL 不得实际存在该 parameter。`step_parameters` 为空时每个物理实例贡献 1；非空且所有值均可解析时，全部非零贡献 1、至少一个为 0 贡献 0。任一缺失/`null`/X/Z/非法值都会让贡献未知并 fail-closed。`step` 可为 0，但没有物理匹配实例仍是 `GROUP_NOT_FOUND`。

“检查结果”页应分别显示“匹配实例”和“实际/期望拍”。示例首组必须显示 6 个物理实例、`5/5` 拍；证据面板应包含 `module_rule`、`step_check`、逐实例贡献 `[1,1,0,1,1,1]`、全部 effective `parameters`、port、clk 来源和源文件/行号。“运行日志”页应包含实际 CLI 命令、stdout、stderr 和退出码。

运行期间输入控件和两个启动按钮应禁用，“取消”应启用。取消后状态应显示 `CANCELLED`，CLI 和 collector 进程组都应退出；关闭正在运行的窗口时应先出现取消确认。

Windows/Linux 布局仍以最小尺寸 `980x680` 验收四个页签无重叠或截断。2026-07-24 截图不含“模块规则库”和动态拍数列，不能作为当前版本证据；必须重新验收，截图仍只用于本地检查、不提交仓库。

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

先直接复制运行规则库页 smoke；它只保存临时配置，不改仓库文件：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --iterations 1 \
  --visible-tab rules \
  --visible-seconds 10
```

窗口保留期间检查 980×680 下规则表、搜索框和编辑控件无重叠，终端末行应为 `GUI_SMOKE_PASS`。

GUI “验证 Excel”路径连续 20 轮：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --validate-only \
  --iterations 20 \
  --visible-seconds 5
```

预期为 2 行 VALID、0 error、0 warning；输出包含 `GUI_SMOKE_WINDOW: window=mapped window_id=0x...`，末行包含 `mode=validate case=positive iterations=20 window=mapped window_id=0x...`。

可见离线正例连续 100 轮：

```bash
cd "$PROJECT_ROOT"
"$PYTHON_BIN" scripts/test_rscheck_gui_smoke.py \
  --project-root "$PROJECT_ROOT" \
  --iterations 100 \
  --visible-seconds 10
```

预期：`GUI_SMOKE_PASS`、`mode=offline case=positive iterations=100 window=mapped window_id=0x...`、结果页 2 行 PASS、0 error、0 warning。首组应显示 physical=6、effective/expected=5/5，逐实例贡献为 `[1,1,0,1,1,1]`。离线 inventory 必须为 schema v2，生成的 report 必须为 schema v3。

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
GUI_SMOKE_PASS: rows=行数 10000 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=1 window=mapped window_id=0x...
```

2026-07-24 九列版本的历史测量为 2.12 秒、最大 RSS 175,612 KiB。当前动态 step 版本新增规则保存和 report v3 渲染，必须重新测量；旧值既不是当前结果，也不是不同设备的硬门槛。脚本生成临时 CSV/inventory，走真实 GUI 后台 CLI、报告读取和 10,000 行结果表渲染，退出时自动清理。

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
  CXX="$CXX"
test -x "$COLLECTOR"
test -d "$ELAB_DB"
test -f "$NPI_LIB_DIR/libNPI.so"

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
GUI_SMOKE_PASS: rows=行数 2 errors=错误 0 warnings=警告 0 mode=online case=positive iterations=3 window=mapped window_id=0x... contract=elab-only
```

3 轮中的每一轮都必须通过真实 collector 加载该 KDB；最终 GUI 为 2 行 PASS、0 error、0 warning。首组必须有 6 个物理实例，`rs_mode` 为五个 1、一个 0，有效/期望拍为 `5/5`，贡献为 `[1,1,0,1,1,1]`。inventory 必须是 schema v2，report 必须是 schema v3 并包含 `module_rule`/`step_check`。运行日志中的实际命令必须包含 `--collector`、`--elab-db`，不得出现 inventory、RTL、filelist、`-top` 或 `--` passthrough。

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

Verdi 端到端脚本会运行全量测试、构建 collector、用当前示例 RTL 生成 fresh `kdb.elab++`、执行 `verdi -elab` 窗口检测，并用同一 KDB 做在线 NPI 检查。下面的块可在已进入仓库、当前 shell 已能正常启动 Verdi 的图形 shell 直接复制；脚本不要求特定 license 环境变量名：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_verdi_gui.sh --probe-only
bash scripts/test_vm_verdi_gui.sh
```

也可只测试端到端脚本的会话发现，不启动 Verdi：

```bash
bash scripts/test_vm_verdi_gui.sh --gui-probe-only
```

端到端覆盖变量包括 `VERDI_BIN`、`VERDI_HOME`、`NOVAS_INST_DIR`、`PYTHON_BIN`、`CXX`、`NPI_PLATFORM`、`NPI_INC_DIR`、`NPI_LIB_DIR`、`PYTHON_ENABLE`、`GCC_ENABLE`、`GUI_START_TIMEOUT`、`NPI_TIMEOUT`、`OUTPUT_BASE`、`KEEP_VERDI_GUI`、`GUI_ONLINE_ITERATIONS`、`GUI_STRESS_ITERATIONS`、`GUI_LOAD_ROWS` 和 `GUI_VISIBLE_SECONDS`。默认分别运行在线 3 轮、离线 100 轮和 10,000 行。`NPI_LIB_DIR` 必须直接包含 `libNPI.so`；默认退出时关闭本次启动的 Verdi，设置 `KEEP_VERDI_GUI=1` 才在成功后保留窗口。

成功输出应包含：

```text
Ran ... tests in ...
OK
Verdi GUI window detected ...
RESULT: PASS | rows=2 errors=0 warnings=0
[PASS] row 2 OUT_IF | top.u_tile / AAAA_BBB physical=6 effective=5 expected=5 RS_CFG_EN=假门控
[PASS] row 3 CTRL_IF | top.u_tile / CTRL_RS physical=1 effective=1 expected=1 RS_CFG_EN=假门控
PASS: fresh KDB online positive/negative GUI checks and offline GUI stress suite completed.
```

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
- `no new Verdi X11 window appeared`：检查 GUI 日志、license、DISPLAY 权限、KDB 和 Verdi/KDB 版本兼容性；必要时调高 `GUI_START_TIMEOUT`。
- `npi.h` 或 `libNPI.so` 找不到：设置 `NPI_INC_DIR` 和 `NPI_LIB_DIR`；后者必须直接包含 `libNPI.so`。
- `npi_load_design failed`：确认 KDB 来自 `elabcom -elab`、内容完整，并与当前 Verdi/NPI 版本兼容；`work.lib++` 及其符号链接别名会更早被 Python runner 拒绝。
- GUI 在线日志中出现 `-f`、RTL 或 `-top`：停止签核；当前实现不应构造这些参数，按输入边界回归处理。
- 显式模块规则未生效：核对规则键与 Excel/RTL 模块名的大小写；没有精确匹配时工具采用默认 `has_rs_cfg_en=true`、`step_parameters=[]`。
- `STEP_PARAMETER_MISSING` / `STEP_PARAMETER_VALUE_UNRESOLVED`：规则要求的拍数 parameter 缺失或未知；查看 report v3 逐实例证据。
- `STEP_CALCULATION_UNRESOLVED`：至少一个贡献未知，整行 fail-closed。
- `RS_CFG_EN_PARAMETER_MISSING`：规则声明有该 parameter，但实例证据缺失。
- `RS_CFG_EN_PARAMETER_UNEXPECTED`：规则声明无该 parameter，但 RTL 实际存在。
- `RS_CFG_EN_LABEL_MISMATCH`：Excel 未按 `has_rs_cfg_en` 填写精确 `假门控` 或空白。
- `RS_CFG_EN_VALUE_MISMATCH`：effective 字符串不表示数值 `0`；检查实例 override 和本次 KDB。
- `RS_CFG_EN_VALUE_UNRESOLVED`：schema v2 `parameters.RS_CFG_EN` 为 `null`；检查 NPI 参数遍历和 KDB，不能把它当作无参数。
- schema v1 inventory：旧格式没有逐实例参数证据，必须用当前 collector 重新生成 schema v2 文件。

## 12. 验证记录

当前动态 step 状态见 `TEST_RESULTS_DYNAMIC_STEP_2026-07-25.md`；其中未完成的 VM 项必须实际重跑后才能改为 PASS。`TEST_RESULTS_RS_CFG_EN_2026-07-24.md` 和以下 2026-07-24 内容均是动态规则库之前的历史基线，只用于对照。

- CentOS 89 项全量自动测试通过，无 skipped。
- CentOS 7.9、Python 3.8.13、G++ 11.2.1、Verdi/NPI O-2018.09-SP2 环境完成真实 collector/KDB 正向链路验证。
- 清空 `DISPLAY`、`XAUTHORITY`、`DBUS_SESSION_BUS_ADDRESS` 和 `XDG_RUNTIME_DIR` 后，GUI 会话发现仍自动选中 `DISPLAY=:0`，不依赖 `gnome-session-binary`。
- GUI “验证 Excel”20 轮均为 2 行 VALID、0 error、0 warning；可见离线正例 100 轮均为 2 行 PASS、0 error、0 warning。
- 10,000 行 GUI 负载最终测量为 1.98–2.08 秒，最大 RSS 150060–150148 KiB，0 error、0 warning。
- 取消启动竞态连续 100 轮通过，总耗时约 15.3 秒。
- Verdi launcher 能打开真实 elaborated KDB；同一 KDB 的在线 GUI smoke 连续 3 轮均为 2 行 PASS、0 error、0 warning。
- Windows 实测发现 89 项自动测试，其中 21 项 Linux Bash/X11 测试和 1 项 POSIX 进程组测试按预期 skipped，其余 67 项通过；macOS 预期只跳过 21 项 Linux-only 测试。
- Windows 工具自带 GUI 在最小窗口 `980x680` 完成截图布局验收，无文字/控件重叠；截图没有提交仓库。

具体 VM 每次压力和在线 smoke 的终端输出应随提交一起记录在测试说明或提交信息中，但不得包含主机、密码、license 或会话认证路径。
