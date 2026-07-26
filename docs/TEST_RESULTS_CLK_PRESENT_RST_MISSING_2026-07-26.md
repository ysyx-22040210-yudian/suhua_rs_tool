# clk 存在、rst 缺失 finding 隔离与 VM GUI 压测验证记录（2026-07-26）

本记录对应 `rtl-rs-check 0.9.0`，被测功能提交为
`b3d701c2b95a4941fae398b4c2490c7f630127c3`。该提交先推送到 GitHub，随后由 VM
fresh-checkout 驱动直接重新 clone、核对 40 位 SHA，并在 detached HEAD 上执行默认规模的
Verdi/NPI 与可见 GUI 端到端压测。

本轮专门复现旧问题：RTL 模块的 `clk` formal port 确实存在并已连接，但模块没有任何 rst
formal port。当前产品仍要求模块规则指定的 rst 端口存在，因此该行应为 `FAIL`；正确行为是只产生
`RST_PORT_MISSING`，不能误报 `CLK_PORT_MISSING` 或 `CLK_UNCONNECTED`。

本记录已脱敏，不包含 VM 地址、密码、license 值、Xauthority 路径、KDB、collector 二进制或完整运行日志。

## 1. 精确复现合同

测试 RTL 在 `top.u_tile` 下例化完整本地实例名 `CLK_ONLY_RS`，模块定义名为
`rs_clk_only`。其 formal ports 精确为 `clk`、`d`、`q`，没有 `rst` 或 `rst_n`；`clk`
连接到 `top.u_tile.clk_rs`。模块参数证据为 `RS_CRG_EN=0`。

测试规格和规则为：

```text
position alias: tile_core
resolved position: top.u_tile
RS_module: rs_clk_only
RS_inst: CLK_ONLY_RS
step: 1
module rule ports: clk/rst_n
```

clean KDB 和故意带 elaboration error、但 top 仍可查询的 partial KDB 都必须采到
`CLK_ONLY_RS={clk,d,q}`。checker 必须独立查找规则指定的两个 formal port，不能因 rst 缺失而
丢弃已经采到的 clk 证据。`CRG_source` 当前仍只进入规格和报告，不参与 PASS/FAIL。

## 2. Windows 发布门禁

环境：Windows、Python 3.11，工作区 `D:\\suhua_rs_tool`。提交功能代码前执行：

```powershell
python -B -m unittest discover -s tests -v
python -m compileall -q rscheck scripts tests
python -m build
git diff --check
```

结果：`Ran 236 tests in 2.926s`、`OK (skipped=45)`，即 191 项执行通过，45 项仅因
Windows 不适用 Linux Bash/X11/POSIX 合同而按预期跳过；`compileall`、wheel/sdist 构建和
补丁检查全部通过。

## 3. GitHub fresh-checkout 主签核

Windows 发布端只读核验：

```text
origin/main = b3d701c2b95a4941fae398b4c2490c7f630127c3
```

功能提交的本机构建包 SHA-256：

```text
4dbe7436ef2ff0229b3ea0cfdb8bbd72ebdd0115cf8f732d3d97cc9371290a97  rtl_rs_check-0.9.0-py3-none-any.whl
124b2f589143f5252bdcdfc73249e9a91cd18d0a36a9f2a4c7a02bdce4839158  rtl_rs_check-0.9.0.tar.gz
```

VM 环境为 CentOS 7.9、Python 3.8.13、G++ 11.2.1、Verdi/NPI O-2018.09-SP2。
GUI resolver 从实际桌面进程取得可用的 `DISPLAY=:0`；不依赖 GNOME 或
`gnome-session-binary`，也没有把图形认证路径或 license 值写入日志和本记录。

在可信 bootstrap checkout 中执行：

```bash
bash scripts/test_vm_fresh_checkout.sh \
  --commit b3d701c2b95a4941fae398b4c2490c7f630127c3
```

GitHub clone 第 1 次成功，fresh 驱动打印的 `Verified commit` 和 detached HEAD 均精确等于
请求 SHA，fresh 工作树无修改。主签核现场为：

```text
RUN_ROOT=/root/rscheck_fresh.l5fj2U88
FULL_LOG=/root/rscheck_fresh.l5fj2U88/full_vm_test.log
ARTIFACT_ROOT=/root/rscheck_fresh.l5fj2U88/artifacts
TEST_ROOT=/root/rscheck_fresh.l5fj2U88/artifacts/verdi_gui_test.8n4waYKN
```

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh clone | 第 1 次 clone 成功；`Verified commit`、detached HEAD 均为目标 40 位 SHA | PASS |
| Linux 全量测试 | `Ran 236 tests in 4.444s`、`OK`，无 skip | PASS |
| collector 构建 | NPI Language Model 与 L1 collector 编译、链接成功 | PASS |
| partial KDB | load 报告预期 elaboration error，但 top 可查询；L0/L1 合并后 formal ports 完整 | PASS |
| partial CLI/GUI | 2 行 PASS、0 error、1 个 `NPI_LOAD_PARTIAL` warning；`CLK_ONLY_RS={clk,d,q}` | PASS |
| clean KDB | `vericom`、`elabcom` 完成，生成新的 elaborated `kdb.elab++` | PASS |
| Verdi GUI | 2 秒内发现标题严格匹配 elaborated top `top` 的 mapped 窗口 | PASS |
| clean inventory | `CLK_ONLY_RS` ports 精确为 `clk/d/q`；`clk.connection=top.u_tile.clk_rs` | PASS |
| 专项 CLI | 1 行预期 FAIL、1 error；finding 精确为 `RST_PORT_MISSING` | PASS |
| 专项在线 GUI | 每轮重新由 collector 加载同一 clean KDB，连续 20 轮均为预期 FAIL、1 error、0 warning | PASS |
| 普通在线 GUI | 真实 collector/KDB 连续 3 轮；2 行 PASS、0 error、0 warning | PASS |
| 自定义端口在线 GUI | `clock_i/reset_ni` 规则，1 行 PASS、0 error、0 warning | PASS |
| 普通在线反例 | 1 行预期 FAIL、2 errors、0 warning | PASS |
| 默认模块规则 | 未登记模块使用 `clk/rst_n`；physical/effective=`2/2` | PASS |
| 离线 GUI 稳定性 | 同一 mapped Tk 窗口连续 100 轮；2 行全部 PASS | PASS |
| GUI 大表负载 | 同一 mapped Tk 窗口单轮渲染 10,000 行；全部 PASS | PASS |
| 八日志门禁 | 八份 GUI 日志逐一命中五根配置 round-trip、逐模块端口和 report/inventory schema marker | PASS |
| 完整驱动 | 最终退出码 `0`；本轮 Verdi 按默认策略关闭 | PASS |

## 4. clk/rst 隔离证据

clean inventory 中的核心事实为：

```text
full_name=top.u_tile.CLK_ONLY_RS
module=rs_clk_only
ports={clk,d,q}
clk.connection=top.u_tile.clk_rs
rst/rst_n formal port=absent
parameters.RS_CRG_EN=0
```

专项 CLI 报告只包含一个 finding：

```text
code=RST_PORT_MISSING
instance=top.u_tile.CLK_ONLY_RS
expected=rst_n
actual=null
```

完整日志的硬断言结果为：

```text
clk-present/rst-missing CLI evidence OK: ports=clk,d,q
finding isolation OK: RST_PORT_MISSING only; CLK_PORT_MISSING absent
```

专项 GUI 的最终 marker 为：

```text
GUI_SMOKE_PASS: state=FAIL rows=1 errors=1 warnings=0 mode=online
case=clk-present-rst-missing iterations=20 window=mapped
rule-ports=clk/rst_n clk-port-evidence=present rst-port-evidence=missing
finding-codes=RST_PORT_MISSING
```

这里 `state=FAIL` 是被测 RTL 行的预期结果；smoke 进程和完整驱动返回 `0`，表示它精确识别了预期
failure。测试和 GUI 门禁同时拒绝 `CLK_PORT_MISSING`、`CLK_UNCONNECTED` 或任何额外 finding。

八份 GUI 日志的覆盖范围为：

```text
partial_load_gui.log: case=partial-load iterations=1 rows=2 errors=0 warnings=1 window=mapped
online_gui_positive.log: case=positive iterations=3 rows=2 errors=0 warnings=0 window=mapped
online_gui_custom_port.log: case=custom-port iterations=1 rows=1 errors=0 warnings=0 rule-ports=clock_i/reset_ni window=mapped
online_gui_clk_present_rst_missing.log: case=clk-present-rst-missing iterations=20 rows=1 errors=1 warnings=0 finding-codes=RST_PORT_MISSING window=mapped
online_gui_negative.log: case=negative iterations=1 rows=1 errors=2 warnings=0 window=mapped
offline_gui_default_rule.log: case=default-rule iterations=1 rows=1 errors=0 warnings=0 rule-ports=clk/rst_n window=mapped
offline_gui_100_rounds.log: case=positive iterations=100 rows=2 errors=0 warnings=0 window=mapped
offline_gui_10000_rows.log: case=positive iterations=1 rows=10000 errors=0 warnings=0 window=mapped
```

每份日志还包含：

```text
config-io=roundtrip-complete roots=excel,columns,rtl,position_mappings,module_rules
module-rule-ports=preserved
schemas=report-v3/inventory-v2
```

## 5. 主签核审计哈希

下列 SHA-256 均在 GitHub fresh-checkout 主现场重新计算：

```text
58a1094682a1db0b0f7892b8a47fd18d87cc99ea6201c4240c516e1f7234e060  full_vm_test.log
2903329a9308a1335878d8402e20f5c8416183436c12b721f2ba925f05c90a5f  python_tests.log
80f087f79f593f5bb55f8ec2b13766bafd17b8727b7e7e94b13b347b3cf94e19  partial_load_gui.log
cc7b37a04255126ebedfe5cd3735d1c7fd0303c1754bb8a8aa4e0b1f0b81322d  online_gui_positive.log
4dfad5d6a52061b6699e4e876e4aeeeb226da9cc5d5369865b207ec352255f55  online_gui_custom_port.log
00f0174f0f22d9c47e3db9e5c008f77b4be5cddcb6432cd6d5c1feeeac2f2abc  online_gui_clk_present_rst_missing.log
0b58c0d3131110e834c80095145d144780c2ed2dc254b7c7ec646a30716fe8c4  online_gui_negative.log
8447e264d4ff40a97b07135cdecdc422054d2db803062088d4ac4098b23e546a  offline_gui_default_rule.log
1fd443a7a1fda76404163331d3cffb07d2d0cd78c6a21a6b1888c868215bb05c  offline_gui_100_rounds.log
91dc89a157f3f22f5dcb8836226297de433cfb9a4424089ce91085f88bd13668  offline_gui_10000_rows.log
beb19d4cb4f701745fe4fc407b7c55333c0c7d383fd6aff6454d39ddf76cdfaf  positive_report.json
c50e3d748795ee74a9f7b5162368b63b474f0714c7dc68b376287601af4228ff  custom_port_report.json
ab076524428c0802cfc16654cec5d0b98e471c54f8d91b61cff157c0792d1de7  clk_present_rst_missing_report.json
82bd89633ce5f065903745671ad0408d2fcb541e536113530dc017fb3f5b0849  partial_load_report.json
8266e8967da1a6b095cdaf66e22203e0e22cfd3a71eac17d171aa4e733e294f0  positive_inventory.json
19f895ea88dbb832d11c0a86fbd0db607deac78b64002fc842096e931cbc22b1  partial_load_inventory.json
5267c887b83867353e803485073f9c6e3bc4809caeed1fc6d15d70c765fcd98d  rs_npi_collector
```

这些文件保留在 VM 的临时测试现场用于审计；仓库不提交 KDB、collector 二进制、日志、bundle、
密码、license 或 X11 认证数据。

## 6. 补充网络与 bundle 审计

主签核之前，VM 直连 GitHub 的一次 fresh driver 运行在
`/root/rscheck_fresh.wgkw3Ojb` 连续三次 clone 失败：前两次为 HTTPS EOF，第 3 次为
`github.com:443` 连接超时，驱动按预期返回 `1` 并保留三个失败现场。这是 VM 到 GitHub 的网络故障，
没有进入产品测试，不能作为功能失败或功能通过的依据。

当时 Windows 已通过 `git ls-remote` 确认 `origin/main` 等于目标 SHA。随后从已推送的完整 Git
历史生成 bundle，其 SHA-256 为：

```text
c3bd18fb8c223e271af6b61be77d59802cc638d8190200094944286f4dd1e120  suhua_rs_tool_b3d701c.bundle
```

VM 在 `/root/rscheck_bundle.opX4aqr2` 独立 clone 该 bundle，并 detached checkout 到精确目标
SHA；默认 236 项测试、partial/clean KDB、可见 GUI、专项 20 轮、普通 3 轮、离线 100 轮、
10,000 行和八日志门禁全部通过，完整压测退出 `0`。之后 VM 网络恢复，本文第 3 至 5 节记录的
GitHub 直接 fresh clone 又完整通过，因此最终签核以 `/root/rscheck_fresh.l5fj2U88` 为准，bundle
轮仅作为额外重复验证。

## 7. 结论

“RTL 有 `clk` input、但没有 rst formal port 时误报 `formal port 'clk' is missing`”的问题已在
真实 Verdi elaborated KDB 和 NPI collector 上精确复现并验证修复。clean 与 partial KDB 都能保留
`clk` formal port 及其连线，CLI 与连续 20 轮可见 GUI 都只报告 `RST_PORT_MISSING`，没有
`CLK_PORT_MISSING`、`CLK_UNCONNECTED` 或额外 finding。

这不等于取消 rst 检查：当前模块规则仍要求 rst formal port，确实没有 rst 的模块仍会按设计返回
FAIL。若以后需要“该模块不检查 rst”，应作为独立配置能力设计和验证，不能把本轮 finding 隔离修复解释为
自动跳过 rst。
