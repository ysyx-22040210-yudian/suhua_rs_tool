# GUI 完整配置导入导出与 VM 压测验证记录（2026-07-26）

> 复现边界：本文保留所列提交中的历史测试命令。复跑时必须 checkout 该提交并使用其中的旧 fresh driver；当前默认 kdebug 的 driver 需要额外固定 kverif commit 和 ELF SHA-256。

本记录对应 `rtl-rs-check 0.8.0`，完整被测提交为
`d416493648aaffd446ca46bad2f994e5a131d06d`。该提交已经推送到 GitHub，并由 VM
fresh-checkout 驱动重新 clone 后验证，没有复用开发目录中的 Python 文件、collector 或 KDB。

本轮功能为工具自带 GUI 增加完整配置的“导入”“加载”“导出”。导出的 JSON 固定包含且仅包含
`excel`、`columns`、`rtl`、`position_mappings`、`module_rules` 五个根对象，因此两个数据库的
全部内存内容、当前 Excel 设置、九列映射和完整 RTL 配置可一起迁移到其他设备。Excel/CSV、
collector、Elab KDB、NPI 库、inventory、report 和超时等设备相关运行路径不进入配置。

## 1. 配置 I/O 合同

- “导出”生成副本，不切换 active path，不清除两个数据库的 dirty 状态，也不修改原配置。
- 已“应用”但未单独保存的模块规则和 Position 映射同样进入导出；搜索过滤不影响完整数据库。
- 导出目标为当前配置的规范化路径、符号链接或硬链接时拒绝写入。
- 路径输入框已变化但尚未“加载”时，导出、数据库保存和运行均拒绝继续。
- “导入”严格要求五个根对象全部存在；“加载”继续兼容历史配置的可选根对象。
- 导入/加载都先校验候选，再确认放弃 Excel 表单和两个 dirty 数据库，确认后重新读取并校验，
  成功时整体切换配置并清除 dirty；取消、拒绝或任一次失败都保持原状态。
- 配置中的纯数字字符串工作表名未编辑时保持字符串，不会误转为工作表序号。
- 固定 GUI smoke 证据为
  `config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules`。

## 2. Windows 发布门禁

环境：Windows，Python 3.11，工作区 `D:\suhua_rs_tool`。

```powershell
Set-Location D:\suhua_rs_tool
python -B -m unittest discover -s tests -v
python -m compileall -q rscheck scripts tests
python -B -m build
git diff --check
python scripts/test_rscheck_gui_smoke.py `
  --project-root D:\suhua_rs_tool `
  --iterations 1 `
  --visible-tab config `
  --visible-seconds 3
```

结果：`Ran 220 tests`，`OK (skipped=44)`；176 项执行通过，44 项仅因 Windows 不适用
Linux Bash/X11/POSIX 合同而按预期跳过。`compileall`、wheel/sdist 构建和补丁检查均通过。
可见 Tk 窗口在 `980x680` 下成功映射，配置页的“导入/加载/导出”按钮无重叠或越界；离线
smoke 为 2 行 PASS、0 error、0 warning，并输出固定配置往返 marker。

以下发布包由被测功能提交 `d416493648aaffd446ca46bad2f994e5a131d06d` 构建，SHA-256 为：

```text
0f252a8c1826b0519421fc5897e8535e6e7bf295910c08586ce38b563d5907a7  rtl_rs_check-0.8.0-py3-none-any.whl
c44e6a3d19c72b5ba4e3af7bb481b8a034c03542ca15fa910b2a340eb32e1a79  rtl_rs_check-0.8.0.tar.gz
```

## 3. CentOS/Verdi VM fresh-checkout

环境：CentOS 7.9、Python 3.8.13、G++ 11.2.1、Verdi/NPI O-2018.09-SP2。GUI resolver
从实际桌面进程选中 `DISPLAY=:0`，不依赖 GNOME 或 `gnome-session-binary`。license 值没有进入
命令行、仓库或本记录。

在 VM 的可信 bootstrap checkout 中执行：

```bash
cd /root/rscheck_fresh.iRv3TMgE/repo_attempt1/repository
GUI_ONLINE_ITERATIONS=3 \
GUI_STRESS_ITERATIONS=100 \
GUI_LOAD_ROWS=10000 \
GUI_VISIBLE_SECONDS=10 \
bash scripts/test_vm_fresh_checkout.sh \
  --commit d416493648aaffd446ca46bad2f994e5a131d06d
```

第 1 次 clone 因连接提前 EOF 失败，第 2 次因 `github.com:443` 连接超时失败；两份失败现场均按
设计保留。第 3 次 clone 成功，fresh 驱动核对 detached HEAD 的完整 40 位 SHA 后，只运行新
clone 中的脚本。本轮保留路径：

```text
RUN_ROOT=/root/rscheck_fresh.A1DaFkGr
ARTIFACT_ROOT=/root/rscheck_fresh.A1DaFkGr/artifacts
TEST_ROOT=/root/rscheck_fresh.A1DaFkGr/artifacts/verdi_gui_test.Z71XjTyE
```

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh clone | 第 3 次成功；HEAD 精确等于被测 SHA | PASS |
| Linux 全量测试 | `Ran 220 tests in 4.082s`，`OK`，无 skip | PASS |
| collector 构建 | `-Wall -Wextra -pedantic` 通过；链接当前 Verdi `libNPI.so` | PASS |
| partial KDB | 1 个预期 elaboration error；`top`/目标层次仍可查询 | PASS |
| partial CLI/GUI | 2 行 PASS、0 error、1 个 `NPI_LOAD_PARTIAL` warning | PASS |
| clean KDB | `vericom`、`elabcom` 均 0 error/0 warning | PASS |
| Verdi GUI | 4 秒内出现严格匹配 elaborated top `top` 的新窗口 | PASS |
| clean 在线 GUI 正例 | 真实 collector/KDB 连续 3 轮；2 行 PASS、0 error/0 warning | PASS |
| clean 在线 GUI 反例 | 1 行预期 FAIL、2 errors、0 warning | PASS |
| 默认模块规则 | 未登记模块 physical/effective=`2/2`，贡献 `[1,1]` | PASS |
| 离线 GUI 稳定性 | 同一可见 Tk 窗口连续 100 轮；2 行 PASS | PASS |
| GUI 大表负载 | 同一可见 Tk 窗口渲染 10,000 行；全部 PASS | PASS |
| 配置往返门禁 | 六份 GUI 日志逐一包含固定 marker | PASS |
| 清理 | fresh 驱动退出码 `0`，本轮 Verdi 按默认策略关闭 | PASS |

六份日志的关键输出分别为：

```text
partial_load_gui.log: case=partial-load iterations=1 rows=2 errors=0 warnings=1 config-io=roundtrip-complete
online_gui_positive.log: case=positive iterations=3 rows=2 errors=0 warnings=0 config-io=roundtrip-complete
online_gui_negative.log: case=negative iterations=1 rows=1 errors=2 warnings=0 config-io=roundtrip-complete
offline_gui_default_rule.log: case=default-rule iterations=1 rows=1 errors=0 warnings=0 config-io=roundtrip-complete
offline_gui_100_rounds.log: case=positive iterations=100 rows=2 errors=0 warnings=0 config-io=roundtrip-complete
offline_gui_10000_rows.log: case=positive iterations=1 rows=10000 errors=0 warnings=0 config-io=roundtrip-complete
```

## 4. VM 审计文件

以下文件保留在本轮 `RUN_ROOT`，SHA-256 为：

```text
14b5a0ff94511fbdc431fe6ed84719c986cff1d199bcb91fe4eb12aae4455bed  full_vm_test.log
d1831fbf32bacdb9cf4115f54e5658b320686432d98887d1c911bb8a7b37d66b  python_tests.log
384718f2cabd07d61c339b4f3f25aa88e0aa268c92a6197faf72f1efe141c345  partial_load_gui.log
d7006200f9ed5010775d13561e5cc5eb506482d7c13fe228e68a32918f9bf742  online_gui_positive.log
48a79f296d311628d92d28256f11a49300207aae34260128021ee70598a5182c  online_gui_negative.log
8f4f94acad2472d197b2f3d67cd275a7412c0f64601a0f8c8ae5c892c48613b6  offline_gui_default_rule.log
4fbb9f6b1c72543bbb59b64025937d9f3a255e61245d4014f5568a9a5796920e  offline_gui_100_rounds.log
36f50c9e98e5f65c3945910828dfbfe62fa64deb621df1b7998499ce7e7ed603  offline_gui_10000_rows.log
8eb13225443078d2467b7e6f4fe5cf31eece5e953c500faa0ce1a270d3f2d10b  positive_report.json
962d184956d7dcf427526d8d6663b53eebb64f16bb7a3033f11f33d56216fb42  partial_load_report.json
```

仓库只提交生成、执行和断言这些结果的源码、脚本、测试与本记录，不提交生成的 KDB、collector
二进制、完整运行日志、license 或 X11 认证数据。

## 5. 结论

`rtl-rs-check 0.8.0` 的完整配置可以在 GUI 中作为单个 JSON 副本导出，并在另一设备严格导入，
其中两个数据库不会因未单独保存或界面搜索过滤而丢项。所有取消、无效输入、写盘失败、路径
别名和 dirty 状态分支保持原子性。该功能在 Windows 本机 Tk 和 CentOS 7/Python 3.8/Verdi
真实桌面中均通过，且没有改变 elaborated KDB-only 的 NPI 输入边界、partial-load fail-closed
策略、任意 Excel 表头列位置、Position 映射、完整实例名、parameter 或动态 step 判定。
