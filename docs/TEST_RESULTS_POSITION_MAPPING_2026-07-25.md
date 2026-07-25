# Position 映射库与 GUI 压测验证记录（2026-07-25）

本记录对应 `rtl-rs-check 0.5.0`，最终被测功能提交为
`845247089872146c0044c6cbd9553906d3126b99`。验证范围包括 Excel `position`
简写解析、JSON/GUI/CLI 映射库、完整 RTL 路径传递、report alias 证据、配置保存冲突保护、
长路径 GUI 显示、Python 3.8 兼容性，以及 fresh Verdi KDB 的真实 NPI/GUI 压测。

NPI 设计输入合同保持不变：collector 只接收 `elabcom` 生成的 elaborated KDB，测试没有向
NPI 传递 RTL、filelist、top、`work.lib++` 或任意 Verdi 参数透传。

## 1. 本机发布门禁

环境：Windows，工作区 `D:\suhua_rs_tool`，Python 3.11.5。

实际执行：

```powershell
Set-Location D:\suhua_rs_tool
python -B -m unittest discover
python -m build
python -m compileall -q rscheck tests scripts\test_rscheck_gui_smoke.py
git diff --check
```

最终结果：153 项测试通过，22 项仅因 Windows 不适用的 Linux Bash/X11/POSIX 合同而跳过；
`rtl_rs_check-0.5.0` wheel 和 sdist 构建成功。最终 wheel SHA-256 为
`00acdc46f8c23542d81f81c26ecc79269dc6b66ed64be336d5497b418133ac88`，sdist
SHA-256 为 `76c1211b167792288e8ab9fb333fb3f84e3c452f1c90f004e6155371426f1246`。

本机可见 Tk GUI 另行完成：Excel 验证正例、离线正例、离线反例、未登记模块默认规则、
连续 100 轮稳定性和 10,000 行负载，均由 smoke 脚本确认窗口为 mapped。映射正例显示
`tile_core -> top.u_tile`，report 保留 `position_alias=tile_core`，inventory positions 只包含
`top.u_tile`。结果表的长 Position 列实际通过水平视口移动断言。

Excel 模板两张工作表均经 artifact-tool 值/公式检查和渲染复核；step 与 `假门控` 数据验证仍在，
公式错误扫描为 0。模板 SHA-256 为
`066abb5cf1a7c4f169da78e8943f0d5828227bd595aa7f1a2766fa923ae87af3`。

## 2. Linux VM 完整结果

环境：CentOS 7.9 x86_64、Python 3.8.13、Tk 8.5、G++ 11.2.1、
Verdi/NPI O-2018.09-SP2。GUI resolver 从非 GNOME 专用逻辑选中现有 X11 会话
`DISPLAY=:0`，Tk 根窗口创建/销毁探测通过。

VM 从 GitHub 对最终提交执行新的独立克隆并核对 `git rev-parse HEAD`。站点 Verdi 环境由该 VM
已有初始化文件加载，测试没有打印或写入 license 值。等价执行入口如下：

```bash
RUN_ROOT=/root/rscheck_position_8452470_$(date +%Y%m%d_%H%M%S)
mkdir -p "$RUN_ROOT"
git clone \
  https://github.com/ysyx-22040210-yudian/suhua_rs_tool.git \
  "$RUN_ROOT/repo"
cd "$RUN_ROOT/repo"
git checkout --detach \
  "845247089872146c0044c6cbd9553906d3126b99"
test "$(git rev-parse HEAD)" = \
  "845247089872146c0044c6cbd9553906d3126b99"

# 仅该已验证 VM 需要先加载其现有站点环境；其他设备使用本站等价初始化方式。
source /home/host/.bashrc >/dev/null 2>&1
OUTPUT_BASE="$RUN_ROOT/artifacts" \
KEEP_VERDI_GUI=0 \
bash scripts/test_vm_verdi_gui.sh
```

最终实际产物根目录：

```text
/root/rscheck_position_845247089872146c0044c6cbd9553906d3126b99_20260725_152336
```

其中端到端产物目录为 `artifacts/verdi_gui_test.TjY8Ch1j`，完整控制台日志为
`full_vm_test.log`。最终 fresh clone 工作树干净，测试退出后 `Novas` 进程数为 0。

| 项目 | 实际结果 | 状态 |
|---|---|---|
| Linux 全量测试 | `Ran 153 tests in 4.014s`，`OK`，无 skip | PASS |
| NPI collector | 实际 `npi.h`/`libNPI.so`，G++ 11 构建及 `ldd` 解析通过 | PASS |
| fresh KDB | `vericom` 0 error/0 warning；`elabcom -top top -elab` 0 error/0 warning | PASS |
| Verdi GUI | 2 秒后严格匹配 `<Verdi:nTraceMain:1> top`，窗口 `1143x745` | PASS |
| 在线 CLI 正例 | 2 行 PASS，0 error/0 warning | PASS |
| Position 证据 | `tile_core -> top.u_tile`；NPI inventory 仅含完整路径 | PASS |
| 动态 step | 首组 physical=6，effective/expected=`5/5`，贡献 `[1,1,0,1,1,1]` | PASS |
| 在线 GUI 正例 | 同一 fresh KDB、真实 collector 连续 3 轮，2 行 PASS | PASS |
| 在线 GUI 反例 | 1 行 FAIL、2 errors、0 warning，脚本按预期判定 smoke PASS | PASS |
| 默认模块规则 | 未登记模块 `has_rs_cfg_en=true`、空 step parameters、贡献 `[1,1]` | PASS |
| 离线 GUI 稳定性 | 可见 Tk 窗口连续 100 轮，2 行 PASS | PASS |
| GUI 负载 | 可见 Tk 窗口完成 10,000 行，10,000 行 PASS | PASS |
| 日志和清理 | Verdi/collector 失败标记扫描通过；本轮 Verdi 按精确 KDB 路径关闭 | PASS |

在线阶段共执行 5 次真实 collector 加载：独立 CLI 正例 1 次、GUI 正例 3 次、GUI 反例 1 次。
每次设计输入均为本轮 fresh `kdb.elab++`。inventory 为 schema v2，report 为 schema v3；
JSON/CSV/GUI 均保留解析后的完整路径和 Excel alias 证据。

审计哈希：

```text
full_vm_test.log       36a2dfbb6b3f1ca5fd0b70dd05b1c376d6b9a3c8d687527447047757f091e5bd
positive_report.json   9ac9da6a2097a64577327c4ffd7312cda8cfc2027609e83b3ad3089dc6556c91
positive_inventory.json 8be93ba0583c9536a31b6a5ce634338fe55648f6120099d666f361d3ee0ab916
```

## 3. 执行中发现并闭环的问题

- 首次 VM 门禁暴露新增测试使用了 Python 3.10 的括号式多 context manager；已改为 Python 3.8
  兼容语法并在 VM 以 153 项全量测试锁定。
- VM 到 GitHub 的 HTTPS 曾出现一次临时 RPC 中断；fresh clone 重试后成功，最终被测内容仍直接
  来自 GitHub，并核对了完整提交号。
- 非交互 root shell 未继承站点 license 环境；加载 VM 已有初始化文件后 Verdi 正常启动，整个过程
  未输出 license 地址。
- 在线正例已通过后，旧测试脚本因证据字段顺序做了错误的相邻字符串断言；提交 `8452470` 将
  `contract`、position 映射和 schema 改为独立断言，随后从 GitHub fresh clone 完整复跑通过。

## 4. 结论

`rtl-rs-check 0.5.0` 已在 Windows 与 CentOS/Verdi VM 上完成 position 简写数据库、解析后全路径、
报告证据、GUI CRUD/冲突保护/长路径显示，以及真实 NPI online 和两档 GUI 压测验证。该结论覆盖
仓库示例和记录提交；生产 RTL 仍应使用正式流程生成的 elaborated KDB 执行同一套检查。

后续设备复现不改写上述历史执行事实。应从当前 bootstrap checkout 使用仓库自带驱动，并把需要验证的完整提交号传给它：

```bash
bash scripts/test_vm_fresh_checkout.sh --commit FULL_SHA
```

驱动会重新从 GitHub 克隆到 VM 本机 `/root/rscheck_fresh.*`，核对完整 HEAD，并永久保留每次 clone、`full_vm_test.log` 和 `artifacts` 供审计。
