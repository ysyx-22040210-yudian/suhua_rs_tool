# 完整 RS_inst 本地例化名与 GUI 压测验证记录（2026-07-25）

本记录对应 `rtl-rs-check 0.6.0`，被测功能提交为
`e4fd87a532d90cad59d13522f7e1366038bf653f`。本轮验证的核心变化是：Excel
`RS_inst` 仍为非空必填项，但既可填写组前缀，也可直接填写完整的 NPI 本地例化名；实例名减去
`RS_inst` 后的 remainder 为空时合法。空后缀实例仍执行 module、parameter、有效 `step`、clk/rst
和 CRG 检查，只是不参与 suffix tag/index/连续编号检查。

NPI 输入合同未改变：collector 和 Verdi GUI 只接收 `elabcom` 生成的 elaborated KDB。本轮没有把
RTL、filelist、`work.lib++` 或任意 Verdi 参数透传作为 NPI 设计输入。

## 1. Windows 本机发布门禁

环境：Windows，工作区 `D:\suhua_rs_tool`，Python 3.11。

实际执行：

```powershell
Set-Location D:\suhua_rs_tool
python -B -m unittest discover -s tests -v
python -B -m compileall -q rscheck tests scripts\test_rscheck_gui_smoke.py
python -B -m build
git diff --check
```

结果：`Ran 185 tests`，`OK (skipped=44)`；其中 141 项执行通过，44 项仅因 Windows 不适用
Linux Bash/X11/POSIX 合同而按预期跳过。checker 新增用例覆盖完整实例名、无数字实例名、空后缀动态拍贡献、空后缀与 indexed 成员
混合、连续编号排除和重叠组歧义。源码编译、补丁检查、wheel 和 sdist 构建均通过。

构建产物 SHA-256：

```text
f9a67a739fdb36c77544478fd1d85fa3b107c6dfc07a60e621eb69d02a24506b  rtl_rs_check-0.6.0-py3-none-any.whl
4f6dc1ad01c972e9200cf759f9a43e3d8f1f0ac923469dc2d4eb5e1f7bd2670e  rtl_rs_check-0.6.0.tar.gz
```

Excel 模板通过 artifact-tool 重新导入、导出和两张工作表渲染复核，公式错误扫描为 0。模板仍有
两条数据验证：`E2:E3` 为 `0..2147483647` 整数范围，`I2:I3` 为 `假门控` 列表。模板 SHA-256：

```text
4734ba4daf0ebf592d33da9b6053736baf46db08c59d729687b6e257f12b3543  RS_Check_Excel_Template.xlsx
```

## 2. CentOS/Verdi VM fresh-checkout 结果

环境：CentOS 7、Python 3.8.13、G++ 11.2.1、Verdi/NPI O-2018.09-SP2。非交互 root shell
没有预设 `DISPLAY`，本轮 GUI resolver 从桌面会话进程自动选中现有用户的 `DISPLAY=:0`。通用
resolver 和 Linux 合同测试不要求 GNOME 或 `gnome-session-binary`；license 值没有进入命令行或日志。

从已存在的可信 bootstrap checkout 执行以下等价命令。fresh 驱动重新从 GitHub clone、checkout
固定提交并核对完整 HEAD，然后只运行新 clone 中的测试脚本：

```bash
cd /root/rscheck_fresh.sEzVRZtu/repo_attempt2/repository
GUI_ONLINE_ITERATIONS=3 \
GUI_STRESS_ITERATIONS=100 \
GUI_LOAD_ROWS=10000 \
GUI_VISIBLE_SECONDS=10 \
bash scripts/test_vm_fresh_checkout.sh \
  --commit e4fd87a532d90cad59d13522f7e1366038bf653f
```

本轮实际路径：

```text
RUN_ROOT=/root/rscheck_fresh.iRv3TMgE
ARTIFACT_ROOT=/root/rscheck_fresh.iRv3TMgE/artifacts
TEST_ROOT=/root/rscheck_fresh.iRv3TMgE/artifacts/verdi_gui_test.ug0XP9OV
```

| 项目 | 实际结果 | 状态 |
|---|---|---|
| GitHub fresh clone | clone attempt 1 成功，完整 HEAD 等于被测提交，最终工作树干净 | PASS |
| Linux 全量测试 | `Ran 185 tests in 4.154s`，`OK`，无 skip | PASS |
| NPI collector | 使用实际 `npi.h`/`libNPI.so` 构建，`ldd` 指向当前 Verdi NPI 库 | PASS |
| fresh elaborated KDB | `vericom` 与 `elabcom` 均为 0 error/0 warning，设计输入为 `kdb.elab++` | PASS |
| Verdi GUI | 2 秒后严格匹配已加载 elaborated top `top` 的新窗口 | PASS |
| 在线 CLI 正例 | 2 行 PASS，0 error/0 warning | PASS |
| 完整本地例化名 | `RS_inst=CTRL_RS_D0` 在本轮 RTL 中匹配到唯一同名本地实例，physical/effective/expected=`1/1/1` | PASS |
| 在线 GUI 正例 | 工具自带 Tk GUI 使用真实 collector/KDB 连续 3 轮，2 行 PASS | PASS |
| 在线 GUI 预期反例 | 1 行 FAIL、2 errors、0 warning；GUI 正确显示失败，smoke 判定通过 | PASS |
| 默认模块规则 | 未登记模块按默认规则得到 physical/effective=`2/2`、贡献 `[1,1]` | PASS |
| 离线 GUI 稳定性 | 可见 Tk 窗口连续 100 轮，2 行 PASS | PASS |
| GUI 大表负载 | 可见 Tk 窗口完成 10,000 行，10,000 行 PASS | PASS |
| 清理 | fresh 驱动退出码 0；本轮 Verdi PID 已关闭 | PASS |

真实 report v3 对完整名行给出的证据为：

```text
spec.RS_inst         = CTRL_RS_D0
matched name         = CTRL_RS_D0
matched full_name    = top.u_tile.CTRL_RS_D0
module               = rs_pipe
RS_CFG_EN             = 0
rs_mode               = 1 (nonzero, contribution=1)
step                  = physical 1 / effective 1 / expected 1
clk                   = top.u_tile.clk_aux
rst                   = top.u_tile.rst_n
unique clk source     = top.u_tile.u_aux_crg / crg_aux
findings              = []
```

脚本对 `matched_instances` 的本地名字做精确断言，必须等于 `['CTRL_RS_D0']`，因此该 PASS 不是只由
汇总拍数推断。module、`RS_CFG_EN`、`rs_mode`、clk/rst 和 CRG 证据同时证明空后缀实例没有绕过
其他检查。

## 3. VM 审计文件

```text
9923647cf196a1660954a01f030b6f2cb7dff9716fa3e519abbb7e07065564b6  full_vm_test.log
1e17f6df1fedcd955e27f5b34c14676f5b501cf71b7e0bdbb66204798c05a310  positive_report.json
cb98275c5e35a24578185c8574e761d237650e0780c386131dca74ebff2c5bd5  positive_inventory.json
713c9e3a430e5460f2c6b971f57c0008db235de721b523d1bdc36ab2f0dd7b39  verdi_gui.log
```

日志和运行制品保留在 VM 的本轮 `RUN_ROOT`，不提交 license、X11 认证数据、生成的 KDB 或大体积
临时文件。仓库提交的是生成与断言这些结果的完整脚本、示例、测试和本记录。

## 4. 结论与边界

`rtl-rs-check 0.6.0` 已在 Windows 和 CentOS/Verdi VM 上证明完整本地 `RS_inst` 的空 remainder
可以匹配，并且真实 NPI 与工具自带 GUI 的完整检查链路和压力测试均通过。

匹配仍保持向后兼容的前缀语义，不是 exact-only 模式。同一 scope 同时存在 `PFX` 与
`PFX_C0` 时，填写 `RS_inst=PFX` 会匹配两者；如果多行 Excel 组命中同一实例，工具仍报告
`AMBIGUOUS_GROUP_MATCH`。生产 RTL 应继续使用正式流程生成的 elaborated KDB 运行同一套检查。
