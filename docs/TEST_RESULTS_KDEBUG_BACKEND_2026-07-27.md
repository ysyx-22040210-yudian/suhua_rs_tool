# kdebug elaborated KDB 后端与 VM GUI 压测签核记录（2026-07-27）

本记录签核的是 `rscheck -> kdebug JSON action -> Verdi Tcl NPI -> inventory v3` 链路，
不是旧 C++ NPI collector baseline。两个仓库均先提交并推送独立分支，再固定提交和
kdebug ELF 哈希执行 VM 测试。

## 固定版本

| 项目 | 分支 | 提交/哈希 |
| --- | --- | --- |
| `suhua_rs_tool` | `codex/kdebug-npi-backend` | `dac071109808ea361c17bed68606a9c17e1d3553` |
| `kverif` | `codex/rscheck-elab-inventory` | `2b43b799c8f7f8586a9e6c2128335e74d971e633` |
| kdebug ELF SHA-256 | - | `28c5068662f82201b91f988ca57461da278ea1e06ee92a22955625b06d95da25` |
| kdebug engine SHA-256 | - | `8c7bb7c8d90163a8260072877f2212bf978eb4e511bf1376c43b79b97849f799` |
| Python engine SHA-256 | - | `08c97762ccca8c4fe18f2d70fb3b5952ed96e93d1317cdba58f9bae0e80a90bb` |
| NPI Tcl SHA-256 | - | `27a441c5d9710ad25f44daa0e20b1f7acb31a9f791589c044d0d983dc5e898e1` |
| inventory Tcl SHA-256 | - | `91bc3288bf20d09d1366c648dcef3a30ec0bbe20949c7abb8fb7bc5dadfdb5e6` |
| Python adapter SHA-256 | - | `1f1b06377201dba2c19fe701b4d0870ed81b45e746fdea263d4af0b53eb13ce0` |

最终 clean checkout 的 `kverif` 和 `suhua_rs_tool` 均为 detached HEAD、工作树 clean。
`ldd kdebug/kdebug` 只包含系统 C/C++、pthread 和 loader，不包含 `libNPI` 或
`libnpiL1`；Python adapter 也不使用 `ctypes`/`cffi`。NPI 调用只发生在 Verdi 内部
加载的 Tcl 层。

## 构建与基础回归

本机 `suhua_rs_tool`：

```text
Ran 321 tests
OK (skipped=50)
wheel/sdist build: PASS
isolated --user wheel install: current Python scripts 中发现 rs-kdebug-collector
```

VM clean `kverif` checkout：

```text
Python engine/test infra: 35 passed
Tcl inventory unit: PASS
schema validation: 182 files
example validation: 175 files
C++ unit binaries: PASS
runtime contract: 29 passed
make -C kdebug test-fast: PASS
```

VM 直接从 GitHub clone `kverif` 时曾遇到连接 EOF/180 秒超时。为保留 clean-build
证据，测试机改为传入刚刚推送提交生成的 Git bundle，重新 clone 到独立目录并再次核对
完整 SHA；`suhua_rs_tool` 的最终签核运行则从 GitHub 第一次 clone 成功。其他设备应直接
使用下面的 GitHub clone 命令。

## 真实 KDB focused 压测

最终 focused 现场：

```text
RUN_ROOT=/root/kverif_probe.OikEqE/kdebug_pressure.Xe0fG5Kh
exit code: 0
```

覆盖及结果：

- raw clean/partial action：两个 position、26 个实例，schema v3，PASS；
- Python adapter clean/partial：PASS，partial notice 精确为一个 `NPI_LOAD_PARTIAL`；
- clean elaborated KDB 重载：20/20 PASS；
- frontend cancellation：先从 `/proc` 确认真正的 Verdi NPI action 已运行，再 TERM frontend；
- adapter 1 秒强制 timeout：不生成成功 inventory；
- cancellation/timeout 后无 kdebug、engine、Verdi、临时目录、crash marker 或活动 session 泄漏。

## Fresh 可见 GUI 全量签核

最终现场：

```text
RUN_ROOT=/root/rscheck_fresh.MUYFmMJF
FULL_LOG=/root/rscheck_fresh.MUYFmMJF/full_vm_test.log
ARTIFACT_ROOT=/root/rscheck_fresh.MUYFmMJF/artifacts
GUI_ARTIFACTS=/root/rscheck_fresh.MUYFmMJF/artifacts/verdi_gui_test.VUo5XCgj
exit code: 0
```

fresh driver 第一次 clone 成功，精确 checkout `dac071109808ea361c17bed68606a9c17e1d3553`，
并核对 clean kverif commit 和 ELF SHA-256。测试结果：

- VM Python：321/321 PASS；
- partial KDB：保留可查询 top，正式输出一个 `NPI_LOAD_PARTIAL` notice；
- clean KDB：Verdi 窗口标题明确包含 elaborated `top`，Tk 窗口为 mapped；
- online positive：3 轮 PASS；
- CRG depth-limit：20 轮 PASS，六个预期 `CRG_TRACE_DEPTH_LIMIT` warning；
- custom clk/rst formal：PASS，错误 CRG source 只产生 warning；
- clk 存在/rst 缺失：20 轮仅产生 `RST_PORT_MISSING`，没有 `CLK_PORT_MISSING`；
- `RS_CRG_EN` don't-care、Excel `NA`、CRG_source 映射：各 20 轮 PASS；
- GUI 稳定性：100 轮 PASS；
- GUI 负载：10,000 行 PASS；
- 完整配置导入导出：六个根节点及三个数据库往返 PASS；
- 收尾：无 crash marker、活动 registry session 或 backend 进程泄漏。

一次非签核运行曾在外层额外设置 `GUI_USER`/`GUI_DISPLAY`，污染 launcher 单测故意使用的
假 display `:77`，因此 5 项模拟测试按预期拒绝 X11。移除这两个不必要的外层覆盖后，fresh
脚本自行从桌面进程发现可用的 `<DESKTOP_USER>/:0`，上述完整运行退出 0。正式复现命令不应预设
`GUI_USER`/`GUI_DISPLAY`，除非设备确实存在多会话歧义。

## 其他设备复现命令

下面命令只接受 elaborated `*.elab++` 链路，不使用 filelist/RTL 作为 collector 输入。
设备需已经能运行 Tk 和 Verdi，并在当前 shell 中提供站点所需的 Verdi 环境。

```bash
set -euo pipefail

export KVERIF_ROOT="$HOME/kverif"
export RSCHECK_ROOT="$HOME/suhua_rs_tool"
export KVERIF_COMMIT=2b43b799c8f7f8586a9e6c2128335e74d971e633
export RSCHECK_COMMIT=dac071109808ea361c17bed68606a9c17e1d3553
export KDEBUG_SHA256=28c5068662f82201b91f988ca57461da278ea1e06ee92a22955625b06d95da25
export PYTHON_BIN="${PYTHON_BIN:-python3}"

git clone --branch codex/rscheck-elab-inventory \
  https://github.com/ysyx-22040210-yudian/kverif.git "$KVERIF_ROOT"
(
  cd "$KVERIF_ROOT"
  git checkout --detach "$KVERIF_COMMIT"
  make -C kdebug -j2 all
  PYTHON="$PYTHON_BIN" make -C kdebug test-fast
  test "$(sha256sum kdebug/kdebug | awk '{print $1}')" = "$KDEBUG_SHA256"
)

git clone --branch codex/kdebug-npi-backend \
  https://github.com/ysyx-22040210-yudian/suhua_rs_tool.git "$RSCHECK_ROOT"
(
  cd "$RSCHECK_ROOT"
  git checkout --detach "$RSCHECK_COMMIT"
  RSCHECK_COLLECTOR_BACKEND=kdebug \
  KDEBUG_BIN="$KVERIF_ROOT/kdebug/kdebug" \
  KVERIF_EXPECTED_COMMIT="$KVERIF_COMMIT" \
  KDEBUG_EXPECTED_SHA256="$KDEBUG_SHA256" \
  PYTHON_BIN="$PYTHON_BIN" \
  bash scripts/test_vm_fresh_checkout.sh --commit "$RSCHECK_COMMIT"
)
```

脚本会打印并永久保留本次唯一 `RUN_ROOT`、`FULL_LOG` 和 `ARTIFACT_ROOT`。日志不记录
license 值；不要把生产 KDB、RTL、license 或主机凭据提交到仓库。
