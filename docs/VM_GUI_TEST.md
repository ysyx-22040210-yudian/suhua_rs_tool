# Verdi GUI 端到端复现指南

本文档说明如何在带有图形桌面、Verdi/NPI 和有效 license 的 Linux 设备上，一次性复现本仓库的完整正向链路：Python 自动测试、NPI collector 构建、示例 RTL 编译、elaborated KDB 生成、Verdi GUI 启动，以及同一 KDB 的在线检查。

GUI 是 Verdi，不是 `rscheck`。`rscheck` 保持为命令行工具，并且在线设计输入严格只有 `--elab-db <elaborated KDB目录>`。

## 1. 仓库中可复现的内容

以下内容随 Git 保存，可在其他设备获得：

- `scripts/test_vm_verdi_gui.sh`：一键 GUI 端到端测试；
- `examples/rtl/rs_example.sv`：可独立 elaboration 的最小 RTL；
- `examples/specs.csv` 和 Excel 模板：正向规格；
- `tests/`：47 项 Python 自动测试、正反例规格和离线 inventory；
- `config/rscheck.example.json`：八列映射和 RTL 规则；
- `docs/TESTING.md`：正例、反例和输入门禁的详细测试命令。

以下生成物不会提交 Git，因为它们依赖本机 Verdi 版本、可能体积很大，并且可由脚本重新生成：

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

## 2. 前提条件

- Linux 图形桌面已经登录，脚本运行用户能读取该桌面会话的 `/proc/<PID>/environ` 和 Xauthority；
- Bash 4.2 或更高版本；
- Python 3.8 或更高版本；
- 支持 C++11 的 G++；
- Verdi/NPI、`vericom`、`elabcom` 和有效 Synopsys license；
- `xdpyinfo`、`xwininfo`、`pgrep`、`make`、`ldd`、`mktemp` 和 `nohup`。

当仓库位于 `/root`、图形桌面属于另一个用户时，应以 `root` 运行脚本并复用图形用户的会话。不要改成以图形用户启动 Verdi，否则该用户可能无法访问 `/root` 下的 KDB。

## 3. 在另一台设备上复现

先使用已配置的 GitHub 凭据克隆仓库。不要把 token、SSH 密码或 license 地址写入仓库：

```bash
git clone https://github.com/ysyx-22040210-yudian/suhua_rs_tool.git
cd suhua_rs_tool
```

设置设备相关环境并运行脚本：

```bash
export VERDI_HOME=/path/to/verdi
export NPI_PLATFORM=LINUX64
export LM_LICENSE_FILE=<port>@<license-host>
export SNPSLMD_LICENSE_FILE="$LM_LICENSE_FILE"
export GUI_USER=<logged-in-desktop-user>

# 没有 Software Collections enable 脚本时显式置空，使用 PATH 中的工具。
export PYTHON_ENABLE=
export GCC_ENABLE=

bash scripts/test_vm_verdi_gui.sh
```

本仓库已验证 VM 的 Python/GCC enable 脚本和 Verdi 安装路径与脚本默认值一致，因此只需在 shell 中设置 license，再执行：

```bash
cd /root/suhua_rs_tool
export LM_LICENSE_FILE=<port>@<license-host>
export SNPSLMD_LICENSE_FILE="$LM_LICENSE_FILE"
bash scripts/test_vm_verdi_gui.sh
```

可覆盖变量：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `PROJECT_ROOT` | 脚本所在仓库根目录 | 仓库路径 |
| `VERDI_HOME` | `/home/synopsys/verdi/Verdi_O-2018.09-SP2` | Verdi 安装根目录 |
| `NPI_PLATFORM` | `LINUX64` | NPI 平台库目录 |
| `GUI_USER` | `host` | 已登录图形桌面的用户 |
| `GUI_SESSION_PATTERN` | `gnome-session-binary` | 图形会话进程匹配式 |
| `GUI_START_TIMEOUT` | `60` | 等待 Verdi X11 窗口的秒数 |
| `NPI_TIMEOUT` | `180` | collector 超时秒数 |
| `OUTPUT_BASE` | `<仓库>/output` | 本次测试产物根目录 |
| `PYTHON_ENABLE` | `/opt/rh/rh-python38/enable` | Python 工具链 enable 脚本，空值表示不 source |
| `GCC_ENABLE` | `/opt/rh/devtoolset-11/enable` | GCC 工具链 enable 脚本，空值表示不 source |

`DISPLAY`、`XAUTHORITY` 和 `DBUS_SESSION_BUS_ADDRESS` 不应硬编码。脚本会从 `GUI_USER` 当前 GNOME 会话动态读取，并先用 `xdpyinfo` 验证访问权限。

## 4. 测试执行顺序

```text
Python tests
    -> build rs_npi_collector
    -> vericom creates work.lib++
    -> elabcom creates kdb.elab++
    -> verdi -elab kdb.elab++ opens GUI
    -> rscheck --elab-db kdb.elab++
    -> assert JSON summary
```

`work.lib++` 只供同目录的 `elabcom` 使用，不能传给检查工具。脚本中的在线检查没有 filelist、`-f`、`-sv`、`-lib` 或 `--` 参数透传。

## 5. 成功判据

终端应包含：

```text
Ran 47 tests in ...
OK
RESULT: PASS | rows=2 errors=0 warnings=0
[PASS] row 2 OUT_IF | top.u_tile / AAAA_BBB (2/2 instances)
[PASS] row 3 CTRL_IF | top.u_tile / CTRL_RS (1/1 instances)
positive summary OK: ...
PASS: Verdi GUI launch and online NPI check completed.
```

Verdi 的层次窗口中展开 `top -> u_tile`，应看到：

```text
top
`-- u_tile
    |-- AAAA_BBB_C0
    |-- AAAA_BBB_C1
    `-- CTRL_RS_D0
```

脚本结束后 Verdi 保持打开，便于人工查看 hierarchy、实例定义、clk/rst 连线和 CRG 模块。终端会打印本次 `ELAB_DB`、JSON 报告和 GUI 日志的绝对路径。

## 6. 已验证结果

2026-07-23 在以下环境完成了实际 GUI 端到端测试：

```text
CentOS 7.9
Python 3.8.13
GCC/G++ 11.2.1
Verdi/NPI O-2018.09-SP2
NPI_PLATFORM=LINUX64
GNOME/X11 display=:0
```

实际结果：47 项 Python 测试全部通过；`vericom` 和 `elabcom` 均为 0 error、0 warning；检测到 Verdi 主窗口 `<Verdi:nTraceMain:1> top top`；同一 `kdb.elab++` 的在线 NPI 正例为 2 行 PASS、0 error、0 warning。

## 7. 常见故障

- `no active gnome-session-binary session`：先登录设备的图形桌面，或修改 `GUI_USER`/`GUI_SESSION_PATTERN`。
- `cannot read GUI environment`：以图形用户本人或具备读取权限的管理员用户运行。
- `xdpyinfo` 失败：检查动态读取到的 `DISPLAY` 和 `XAUTHORITY`，不要复制上一次登录产生的 `/run/gdm/auth-*` 路径。
- `no new Verdi X11 window loaded elaborated top`：查看脚本打印的窗口快照和 `verdi_gui.log`，检查 license、显示权限、KDB 和 Verdi 兼容库。
- `npi_load_design failed`：确认输入是脚本刚生成的 `kdb.elab++`，而不是 `work.lib++`。
- GUI 已打开但 SSH 断开：脚本使用 `nohup` 启动 Verdi；仍应从设备图形桌面人工确认窗口。
