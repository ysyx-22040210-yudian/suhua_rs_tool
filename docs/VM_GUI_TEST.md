# Verdi GUI 跨设备运行与端到端测试

本文档说明如何在不同 Linux 设备和桌面环境中打开 Verdi GUI，并复现本仓库的完整正向链路。

`rscheck` 是命令行工具，GUI 始终是 Verdi。在线 NPI 的唯一设计输入是 `--elab-db <Verdi elaborated KDB目录>`；RTL、filelist、`work.lib++` 和任意 Verdi 参数透传都不是合法输入。

## 1. 仓库中的可移植组件

- `scripts/lib/gui_session.sh`：共享 X11/Xwayland 会话发现和 `xdpyinfo` 探测；
- `scripts/launch_verdi_gui.sh`：只用 `verdi -elab <KDB>` 打开已有 elaborated KDB；
- `scripts/test_vm_verdi_gui.sh`：运行 64 项测试、构建 collector、生成示例 KDB、打开 Verdi 并执行在线正例；
- `tests/test_gui_session_probe.py`：覆盖多桌面、SSH 转发、Wayland/Xwayland、用户过滤和多 DISPLAY；
- `tests/test_verdi_gui_launcher.py`：覆盖严格的 launcher 参数和 KDB 输入门禁；
- `examples/rtl/rs_example.sv`、`examples/specs.csv`：端到端测试输入。

KDB、编译库、日志、collector 和报告是本机生成物，不提交 Git：

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

## 2. GUI 前提条件

Verdi 是 X11 应用。下面三种显示方式均受支持：

1. Linux 本地图形桌面的终端；
2. VNC 或 XRDP 桌面内的终端；
3. 客户端已运行 X server 的 `ssh -Y` 会话。

GNOME 不是必需条件。KDE、Xfce、MATE、Cinnamon、LXQt 及其他可提供有效 X11 DISPLAY 的桌面均可使用。Wayland 桌面必须启用 Xwayland，只有 `WAYLAND_DISPLAY` 而没有可访问的 Xwayland `DISPLAY` 不能运行该版本 Verdi。

GUI 探测需要 `xdpyinfo`，完整端到端测试还需要 `xwininfo`。按系统选择一条安装命令。

Debian/Ubuntu：

```bash
sudo apt-get update
sudo apt-get install -y x11-utils
```

RHEL/CentOS：

```bash
sudo yum install -y xorg-x11-utils
```

另外需要 Bash 4.2+、Python 3.8+、支持 C++11 的编译器、GNU Make、`procps` 提供的 `pgrep`、Verdi/NPI 和有效的组织 license。端到端测试还会使用 `ldd`、`mktemp`、`nohup`、`timeout`、`sort`、`comm`、`tee` 和 `grep`。

完整 collector/端到端构建要求仓库路径、`NPI_INC_DIR` 和 `NPI_LIB_DIR` 不含空白；GNU Make 会拆分目标名，Makefile 会对此提前报错。只使用 `launch_verdi_gui.sh` 打开已有 KDB 时没有该限制，KDB 路径可以包含空格。

应优先以图形会话所属用户运行，并确保该用户能读取仓库和 KDB。把仓库放在该用户的 home 或共享工程目录通常最稳定。root 读取其他用户 `/proc/<PID>/environ` 并复用其 Xauthority 只作为旧部署兼容回退，可能被 SELinux、`hidepid`、文件权限或显示服务器策略阻止。

不要使用 `xhost +` 绕过 X11 认证。

## 3. 获取仓库并运行 GUI 探测

在新设备上可直接克隆：

```bash
git clone https://github.com/ysyx-22040210-yudian/suhua_rs_tool.git "$HOME/suhua_rs_tool"
cd "$HOME/suhua_rs_tool"
```

从本地图形终端或 VNC/XRDP 桌面终端运行：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/launch_verdi_gui.sh --probe-only
```

预期输出：

```text
GUI access OK: source=... user=... DISPLAY=...
GUI probe PASS
```

通过 SSH 转发时，先在客户端启动 X server，再从客户端 shell 执行：

```bash
: "${REMOTE_USER:?set REMOTE_USER in the client shell}"
: "${LINUX_HOST:?set LINUX_HOST in the client shell}"
ssh -Y "${REMOTE_USER}@${LINUX_HOST}"
```

进入远端 shell 后执行，并在使用 GUI 期间保持该 SSH 连接开启：

```bash
cd "$HOME/suhua_rs_tool"
xdpyinfo >/dev/null
bash scripts/launch_verdi_gui.sh --probe-only
```

`--probe-only` 不查找 Verdi、不要求 license，也不读取 KDB。它只验证当前或自动发现的 DISPLAY 能否通过 `xdpyinfo`。

## 4. GUI 会话发现规则

探测顺序如下：

1. 使用当前 shell 中已通过 `xdpyinfo` 的 `DISPLAY`，包括图形终端和 `ssh -Y`；
2. 若设置了 `GUI_DISPLAY`，使用 `GUI_XAUTHORITY` 显式验证该 DISPLAY；
3. 若设置了 `GUI_SESSION_PID`，从该进程环境提取显示和会话变量；
4. 否则扫描 GNOME、KDE、Xfce、MATE、Cinnamon、LXQt、Xwayland 及其他可读进程环境；
5. 对每个候选 DISPLAY 实际执行 `xdpyinfo`，只有成功的候选才可使用。

因此 GNOME 或 `gnome-session-binary` 从来不是成功条件。自动发现会排除常见登录 greeter；发现多个可用 DISPLAY 时会停止并要求设置 `GUI_DISPLAY`，不会任意选择旧会话。

支持的 GUI 覆盖项：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `GUI_USER` | 空 | 自动扫描时限定进程用户；配合显式 DISPLAY 时记录目标图形用户 |
| `GUI_DISPLAY` | 空 | 显式指定 DISPLAY，或从多个候选中选择，如 `:0` |
| `GUI_XAUTHORITY` | 当前 `XAUTHORITY` 或空 | 为显式 DISPLAY 指定 Xauthority 文件 |
| `GUI_SESSION_PID` | 空 | 从指定图形进程读取 DISPLAY 和会话环境 |
| `GUI_PROBE_TIMEOUT` | `5` | 单个 `xdpyinfo` 探测的超时秒数 |

当当前 shell 已经能运行 `xdpyinfo` 时，不应额外设置这些变量。显式选择示例：

```bash
: "${GUI_DISPLAY:?set GUI_DISPLAY to the intended X11 display}"
export GUI_DISPLAY
bash scripts/launch_verdi_gui.sh --probe-only
```

若该 DISPLAY 需要单独的认证文件，先在当前 shell 设置 `GUI_XAUTHORITY`，再执行同一命令。

在 root 兼容部署中，若 SELinux 或 `hidepid` 禁止读取图形用户的 `/proc`，管理员可显式提供三项配置。只要 `xdpyinfo` 验证成功，启动器不会再依赖进程扫描：

```bash
: "${GUI_USER:?set GUI_USER to the graphical account}"
: "${GUI_DISPLAY:?set GUI_DISPLAY, for example :0}"
: "${GUI_XAUTHORITY:?set GUI_XAUTHORITY to that session's auth file}"
export GUI_USER GUI_DISPLAY GUI_XAUTHORITY
bash scripts/launch_verdi_gui.sh --probe-only
```

## 5. 打开已有 elaborated KDB

Verdi 可执行文件按以下顺序定位：

1. `VERDI_BIN` 指定的可执行文件；
2. `VERDI_HOME/bin/verdi`；
3. `NOVAS_INST_DIR/bin/verdi`；
4. `PATH` 中的 `verdi`；
5. 已验证旧 VM 的兼容路径，仅作为最后回退。

前台启动会让当前 shell 附着于 Verdi，关闭 Verdi 后命令才返回：

```bash
cd "$HOME/suhua_rs_tool"
: "${ELAB_DB:?export ELAB_DB to an elaborated KDB directory}"
bash scripts/launch_verdi_gui.sh --elab-db "$ELAB_DB"
```

后台启动会立即打印 PID 和日志路径：

```bash
cd "$HOME/suhua_rs_tool"
: "${ELAB_DB:?export ELAB_DB to an elaborated KDB directory}"
bash scripts/launch_verdi_gui.sh \
  --elab-db "$ELAB_DB" \
  --background
```

SSH X11 转发的后台 Verdi 仍依赖 SSH 隧道；关闭连接后不能继续转发窗口。本地/VNC/XRDP DISPLAY 上启动的后台 Verdi 不依赖 SSH 转发。

启动器只接受以上三种形式：

```text
bash scripts/launch_verdi_gui.sh --probe-only
bash scripts/launch_verdi_gui.sh --elab-db DIR
bash scripts/launch_verdi_gui.sh --elab-db DIR --background
```

`--probe-only` 不能与启动选项组合。启动器会拒绝 `work.lib++`、普通文件、不存在的目录、RTL/filelist、`-f`、`-sv`、`-lib`、`-top`、位置参数和 `--` passthrough。实际 Verdi 参数固定为：

```text
verdi -elab <ELAB_DB>
```

## 6. 完整端到端 GUI 测试

端到端脚本执行以下流程：

```text
64 Python tests
    -> build rs_npi_collector
    -> vericom creates work.lib++
    -> elabcom creates kdb.elab++
    -> verdi -elab kdb.elab++ opens a new GUI window
    -> rscheck --elab-db kdb.elab++
    -> assert JSON summary
```

先在当前图形 shell 或组织环境模块中设置 Verdi 和 license。下面的测试命令不包含密码、IP 或 license 地址：

```bash
cd "$HOME/suhua_rs_tool"
: "${LM_LICENSE_FILE:?set LM_LICENSE_FILE in the current shell}"
export LM_LICENSE_FILE
export SNPSLMD_LICENSE_FILE="${SNPSLMD_LICENSE_FILE:-$LM_LICENSE_FILE}"

bash scripts/launch_verdi_gui.sh --probe-only
bash scripts/test_vm_verdi_gui.sh
```

也可只检查端到端脚本的 GUI 会话解析，不加载 Verdi：

```bash
cd "$HOME/suhua_rs_tool"
bash scripts/test_vm_verdi_gui.sh --gui-probe-only
```

端到端脚本支持以下设备覆盖：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `VERDI_BIN` | 自动发现 | 直接指定 Verdi 可执行文件 |
| `VERDI_HOME` | `NOVAS_INST_DIR` 或自动发现 | Verdi 安装根目录 |
| `NOVAS_INST_DIR` | 空 | Verdi 安装根目录的兼容变量 |
| `PYTHON_BIN` | `python3` | Python 可执行文件名或路径 |
| `CXX` | `g++` | C++ 编译器名或路径 |
| `NPI_PLATFORM` | 自动，优先 `LINUX64` | NPI 平台库名 |
| `NPI_INC_DIR` | `$VERDI_HOME/share/NPI/inc` | 直接包含 `npi.h` 的目录 |
| `NPI_LIB_DIR` | 自动平台目录 | 直接包含 `libNPI.so` 的目录 |
| `PYTHON_ENABLE` | 已知脚本存在时自动 | 可选 Python 工具链 enable 脚本；显式空值表示不 source |
| `GCC_ENABLE` | 已知脚本存在时自动 | 可选 GCC 工具链 enable 脚本；显式空值表示不 source |
| `GUI_START_TIMEOUT` | `180` | 等待新 Verdi 窗口的秒数 |
| `NPI_TIMEOUT` | `180` | 在线 collector 超时秒数 |
| `OUTPUT_BASE` | `<仓库>/output` | 本次测试产物根目录 |

`GUI_USER`、`GUI_DISPLAY`、`GUI_XAUTHORITY` 和 `GUI_SESSION_PID` 同样适用于完整脚本。构建时，脚本把 `NPI_INC_DIR`/`NPI_LIB_DIR` 分别传给 Makefile 的 `NPI_INC`/`NPI_LIB`；在线检查时还显式传入 `--npi-lib-dir "$NPI_LIB_DIR"`，因此非标准 NPI 安装布局不依赖固定目录。

## 7. 成功判据

终端应包含：

```text
Ran 64 tests in ...
OK
Verdi GUI window detected ...
RESULT: PASS | rows=2 errors=0 warnings=0
[PASS] row 2 OUT_IF | top.u_tile / AAAA_BBB (2/2 instances)
[PASS] row 3 CTRL_IF | top.u_tile / CTRL_RS (1/1 instances)
positive summary OK: ...
PASS: Verdi GUI launch and online NPI check completed.
```

某些 Verdi 版本会显示强匹配标题 `<Verdi:nTraceMain...> top`；其他版本标题不同，只要在超时内出现新的 Verdi/Novas/Debussy X11 窗口并经过通用就绪延迟，也可判定 GUI 已启动。

层次窗口中展开 `top -> u_tile`，应看到：

```text
top
`-- u_tile
    |-- AAAA_BBB_C0
    |-- AAAA_BBB_C1
    `-- CTRL_RS_D0
```

脚本结束后 Verdi 保持打开，终端打印 `ELAB_DB`、JSON 报告和 GUI 日志路径。`work.lib++` 只供同目录的 `elabcom` 使用；GUI 和 NPI 检查都使用真正的 `kdb.elab++`。

## 8. 常见故障

- `ERROR: no active gnome-session-binary session for user`：这是旧版或外部启动脚本的报错。更新仓库并确认存在 `scripts/lib/gui_session.sh`，然后运行 `bash scripts/launch_verdi_gui.sh --probe-only`。当前实现不要求 GNOME。
- `no usable X11 display`：从本地/VNC/XRDP 图形终端运行，或在客户端启动 X server 后使用 `ssh -Y`；先确认 `xdpyinfo` 成功。
- `multiple usable X11 displays`：根据错误列出的端点设置 `GUI_DISPLAY=:N`；必要时增加 `GUI_USER` 或正确的 `GUI_XAUTHORITY`。
- 只有 `WAYLAND_DISPLAY`：启用 Xwayland 并确认 `DISPLAY` 能通过 `xdpyinfo`；Verdi 不能直接使用 Wayland socket。
- 读取不到其他用户 `/proc/<PID>/environ`：改由图形用户自己运行，并将仓库/KDB 移到可访问路径；root 跨用户发现不是首选部署方式。
- `xdpyinfo` 认证失败：检查 `DISPLAY`/`XAUTHORITY` 是否属于当前登录，不要复制旧 `/run/gdm/auth-*`，也不要使用 `xhost +`。
- SSH 转发窗口在断开后关闭：保持 `ssh -Y` 隧道；`nohup` 或 `--background` 不能替代 X11 隧道。
- `Verdi not found`：设置 `VERDI_BIN`、`VERDI_HOME` 或 `NOVAS_INST_DIR`，或把 `verdi` 加入 `PATH`。
- `xwininfo not found`：Debian/Ubuntu 安装 `x11-utils`；RHEL/CentOS 安装 `xorg-x11-utils`。
- `no new Verdi X11 window appeared`：检查脚本打印的 `verdi_gui.log`、license、DISPLAY 权限、KDB 和 Verdi/KDB 版本兼容性；必要时调高 `GUI_START_TIMEOUT`。
- `npi.h` 或 `libNPI.so` 找不到：设置 `NPI_INC_DIR` 和 `NPI_LIB_DIR`；后者必须直接包含 `libNPI.so`。
- `npi_load_design failed`：确认 `ELAB_DB` 是 `elabcom -elab` 生成的 elaborated KDB，不是 `work.lib++`，并确认 KDB 与当前 Verdi/NPI 版本兼容。

## 9. 2026-07-24 实测结果

实际 GUI 端到端环境为 CentOS 7.9、Python 3.8.13、G++ 11.2.1、Verdi/NPI O-2018.09-SP2。结果如下：

- 64 项全量测试全部通过；
- 17 项 GUI 会话和 launcher 定向测试全部通过；
- 未设置 `GUI_USER`、`GUI_DISPLAY`、`GUI_XAUTHORITY` 或 `GUI_SESSION_PID` 时，自动发现主机 `DISPLAY=:0`；
- Verdi 在 2 秒内出现新窗口；
- `launch_verdi_gui.sh --elab-db` 成功打开真实 elaborated KDB；
- 同一 KDB 的在线 NPI 检查为 2 行 PASS、0 error、0 warning。

该设备使用的桌面类型只是一个已验证样本，不是运行依赖。Windows 和 macOS 允许 17 项 Linux Bash/X11 测试 skipped，其余 47 项必须通过；Linux GUI 环境应执行全部 Bash 回归。
