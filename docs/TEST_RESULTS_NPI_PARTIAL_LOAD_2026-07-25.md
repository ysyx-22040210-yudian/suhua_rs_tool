# NPI partial-load 兼容与 GUI 压测验证记录（2026-07-25）

> 复现边界：本文保留所列提交中的历史测试命令。复跑时必须 checkout 该提交并使用其中的旧 fresh driver；当前默认 kdebug 的 driver 需要额外固定 kverif commit 和 ELF SHA-256。

本记录对应 `rtl-rs-check 0.7.1`，完整被测提交为
`54b57ddf102db719b3018b679a8672d7c3c8e021`。该提交已经推送到 GitHub，并由 VM
fresh-checkout 驱动重新 clone 后验证，没有复用开发目录中的 collector、KDB 或 Python 文件。

本轮修复针对以下真实场景：`verdi -elab <TB.elab++>` 可以打开设计，NPI 也能查询 top 和目标
层次，但 `npi_load_design()` 因 elaboration 日志中的非致命错误返回 `0`。旧 collector 把返回值
`0` 一律当作硬失败并退出 `11`，因此在仍有完整目标证据时误拒绝检查。

## 1. 当前判定合同

| `npi_load_design`/top 状态 | collector 行为 | 工具结果 |
|---|---|---|
| 返回 `1` | 正常采集 | 按逐项 RTL 证据判定 |
| 返回 `0`，至少一个 top 可查询 | 继续采集，stderr 输出 `warning[NPI_LOAD_PARTIAL]`，inventory 写一条 `notices` | report/GUI 显示非致命 warning；目标证据完整时允许 PASS |
| 返回 `0`，没有可查询 top | 输出 `error[NPI_LOAD]`，退出 `11` | 硬失败 |

partial load 只允许继续，不会放宽检查。position、实例、module、clk/rst、parameter、动态 step 和
CRG source 仍逐项 fail-closed。inventory schema 仍为 v2：`warnings` 中的 traversal/driver 问题
继续转换为 `NPI_UNRESOLVED` error；向后兼容的可选 `notices` 当前用于
`NPI_LOAD_PARTIAL` warning。旧 schema v2 文件省略 `notices` 时仍可读取。

## 2. Windows 发布门禁

环境：Windows，Python 3.11，工作区 `D:\suhua_rs_tool`。

实际执行：

```powershell
Set-Location D:\suhua_rs_tool
python -B -m unittest discover -s tests -v
python -m compileall -q rscheck scripts tests
python -B -m build
git diff --check
```

结果：`Ran 196 tests`，`OK (skipped=44)`；152 项执行通过，44 项仅因 Windows 不适用
Linux Bash/X11/POSIX 合同而按预期跳过。`compileall`、wheel/sdist 构建和补丁检查均通过。

发布包 SHA-256：

```text
f840c50f1ab4fcc3094decafd326064c02bc7f770caeeacbe56f3061540e339f  rtl_rs_check-0.7.1-py3-none-any.whl
ae430c3816c499d06e6c4b18a8dc9547742168a942d7c27d892865c121890bf7  rtl_rs_check-0.7.1.tar.gz
```

## 3. CentOS/Verdi VM fresh-checkout

环境：CentOS 7.9、Python 3.8.13、G++ 11.2.1、Verdi/NPI O-2018.09-SP2。SSH shell
没有预设 `DISPLAY`；GUI resolver 从实际桌面进程选中 `DISPLAY=:0`，不要求 GNOME 或
`gnome-session-binary`。license 值没有进入命令行、仓库或本记录。

在 VM 的可信 bootstrap checkout 中执行：

```bash
cd /root/rscheck_fresh.iRv3TMgE/repo_attempt1/repository
GUI_ONLINE_ITERATIONS=3 \
GUI_STRESS_ITERATIONS=100 \
GUI_LOAD_ROWS=10000 \
GUI_VISIBLE_SECONDS=10 \
bash scripts/test_vm_fresh_checkout.sh \
  --commit 54b57ddf102db719b3018b679a8672d7c3c8e021
```

fresh 驱动重新从 GitHub clone，核对 detached HEAD 的完整 40 位 SHA，然后只运行新 clone
中的脚本。本轮实际保留路径：

```text
RUN_ROOT=/root/rscheck_fresh.qXm3PURi
ARTIFACT_ROOT=/root/rscheck_fresh.qXm3PURi/artifacts
TEST_ROOT=/root/rscheck_fresh.qXm3PURi/artifacts/verdi_gui_test.j8BCmdwu
```

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh clone | 第 1 次 clone 成功；HEAD 精确等于 `54b57ddf102db719b3018b679a8672d7c3c8e021` | PASS |
| Linux 全量测试 | `Ran 196 tests in 4.196s`，`OK`，无 skip | PASS |
| collector 构建 | `-Wall -Wextra -pedantic` 构建通过；`ldd` 指向当前 Verdi `libNPI.so` | PASS |
| partial KDB | 故意保留 1 个 elaboration error，同时 `top` 和 `top.u_tile` 可查询 | PASS |
| partial collector | `npi_load_design=0`，collector 返回 `0` 并输出 `warning[NPI_LOAD_PARTIAL]` | PASS |
| partial inventory | schema v2；`warnings=[]`；恰好 1 条 `notices`；目标实例、端口、parameter 和 CRG 证据完整 | PASS |
| partial CLI | 2 行 PASS、0 error、1 warning；首组 physical/effective/expected=`6/5/5` | PASS |
| partial 工具 GUI | 可见 Tk 窗口；2 行 PASS；GLOBAL 行显示 PASS 和 `0E/1W` | PASS |
| clean KDB | `vericom`、`elabcom` 均 0 error/0 warning | PASS |
| Verdi GUI | 新窗口标题严格匹配已加载 elaborated top `top` | PASS |
| clean 在线 GUI 正例 | 真实 collector/KDB 连续 3 轮，2 行 PASS、0 error/0 warning | PASS |
| clean 在线 GUI 反例 | 1 行 FAIL、2 errors、0 warning，smoke 正确识别预期失败 | PASS |
| 默认模块规则 | 未登记模块 physical/effective=`2/2`，贡献 `[1,1]` | PASS |
| 离线 GUI 稳定性 | 同一可见 Tk 窗口连续 100 轮，2 行 PASS | PASS |
| GUI 大表负载 | 同一可见 Tk 窗口渲染 10,000 行，全部 PASS | PASS |
| 清理 | fresh 驱动退出码 `0`，本轮 Verdi 进程按默认策略关闭 | PASS |

partial 路径的关键输出：

```text
warning[NPI_LOAD_PARTIAL]: npi_load_design reported elaboration errors, but 1 top instance(s) remain queryable (first: top); continuing with fail-closed RTL evidence checks
RESULT: PASS | rows=2 errors=0 warnings=1
[WARNING] NPI_LOAD_PARTIAL: ...
GUI_SMOKE_PASS: state=PASS rows=行数 2 errors=错误 0 warnings=警告 1 mode=online case=partial-load iterations=1 ... notice=NPI_LOAD_PARTIAL schemas=report-v3/inventory-v2
```

clean 和压力路径的关键输出：

```text
GUI_SMOKE_PASS: state=PASS rows=行数 2 errors=错误 0 warnings=警告 0 mode=online case=positive iterations=3 ... contract=elab-only
GUI_SMOKE_PASS: state=FAIL rows=行数 1 errors=错误 2 warnings=警告 0 mode=online case=negative iterations=1 ... contract=elab-only
GUI_SMOKE_PASS: state=PASS rows=行数 2 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=100 ...
GUI_SMOKE_PASS: state=PASS rows=行数 10000 errors=错误 0 warnings=警告 0 mode=offline case=positive iterations=1 ...
```

## 4. VM 审计文件

以下文件保留在本轮 `RUN_ROOT`，SHA-256 为：

```text
e4311bcfc77d7c5d692bf30cbc9df85645dfb2b81dd17ffc3b2d9843d6fc78ea  full_vm_test.log
b8891f8f5e3dd3c0129507406218adf8e341ea885af44be51ae5f446bd824588  python_tests.log
835cec3585e86927a1b841bd01f8e3458190975e4c473f525b90704bcb463748  collector_ldd.txt
c6ae6956bf99eb94988c018c792bfdf67b5b689e98b516b349c17e8c020e61a8  partial_load.stdout
ae8890f54e04322efd78357c73f08b29ff79340702591626049eb0954e5d283c  partial_load.stderr
b842425329c8d755e5ebd756594fa6cb0ae3178772e1d3d25460ce24d8653c0f  partial_load_inventory.json
a60988124d78885c3bea66cdeeeef1831389ab8b50e6f385ab6452939b38158d  partial_load_report.json
62011b67b52019f2a0eab7a2e56976c80ab4a16ebe051791784b3aae5bd61a54  partial_load_check.log
8df841ab93202459fd9a813f1f1514367c7bee3565d8903fa9d5d3e83cd4e6b0  partial_load_gui.log
12ed1aa5680f022c0a44c2d1b0405ee136b0ab772ec9e84099f4db5e45c5c49d  partial_load_elab/elabcomLog/compiler.log
c3d9139af4e870aa1bd442feed4d4aab514607cd0f3f36d4b67eef9e6a3d6f95  partial_load_elab/rs_npi_collectorLog/compiler.log
0aeef164e6b5ff451ee4b6b43af0228b6a3d64120c1b2a1f2ed098d09fa4b5fd  positive_report.json
1d9fedfe63124b631fc7b16fe3df45445302d69c863b099f324b1f93e5581c27  positive_report.csv
2a11ddab64edbd256c1b3aa02a23007ff2997c47e67c499de2c8a634746e0949  positive_inventory.json
47542332f8023664f6e5bee26f6cff7f914c2154fd9c10de8f1411fbe031e091  verdi_gui.log
aaa1cd234ff2f7b35ba9d926e509e1bce04dddd4190a4d478abc56db16d89596  online_gui_positive.log
206162f0cca15025226824a9d3f6b62dca1a62136e95a20c0bd28f2812270406  online_gui_negative.log
8a9e925af1a8799e73f610143c8c9dc850755877b29919e4a0c00b0343adca06  offline_gui_default_rule.log
6dae80f2b20ddeceb4aa38dbd0c8aa886f195ebe658ca7ccdf80d93befee2688  offline_gui_100_rounds.log
67b23e6ccf7dabcbb46ab833f4783a6cab815b36cbe07c56264064cdc2647952  offline_gui_10000_rows.log
```

仓库只提交生成、执行和断言这些结果的源码、脚本、测试与本记录，不提交生成的 KDB、collector
二进制、完整运行日志、license 或 X11 认证数据。

## 5. 结论

`rtl-rs-check 0.7.1` 已证明：NPI 返回 `0` 不能单独等同于设计完全不可查询。collector 会先用
NPI Language Model 枚举 top，再决定继续还是退出 `11`。存在可查询 top 时，工具能在保留
`NPI_LOAD_PARTIAL` 可见诊断的同时，对目标 RTL 证据继续做严格检查；不存在 top 或任一目标证据
缺失时仍然硬失败。该变化没有削弱 elaborated KDB-only 输入边界、任意 Excel 表头列号映射、
position 映射、完整实例名、模块 parameter 规则、动态 step 或跨桌面 GUI 运行能力。
