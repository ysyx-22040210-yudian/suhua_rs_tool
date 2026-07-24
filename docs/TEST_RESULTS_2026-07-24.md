# 2026-07-24 GUI 发布验证记录

本文记录 `rscheck` 自带 Tkinter GUI 的发布前实测结果。可复制命令见 [完整测试指南](TESTING.md) 和 [VM GUI 复现指南](VM_GUI_TEST.md)。本记录已脱敏，不包含主机地址、密码、license 值、Xauthority 路径或临时 KDB 绝对路径。

## 验证范围

- Excel/CSV 规格输入、配置加载和八个独立的 1-based 列映射；
- GUI 的 Excel 验证、离线 inventory、真实 NPI/elaborated KDB 三种执行路径；
- PASS/FAIL 摘要、逐行 finding、证据、日志和 JSON/CSV 报告；
- GNOME 非必需的 X11 会话发现，以及 Tk 8.5/8.6 窗口映射；
- 取消启动竞态、运行中取消和完整 POSIX 进程组清理；
- 在线命令只包含 collector、elaborated KDB 和运行配置，不接受 RTL/filelist/top/passthrough。

## Windows 结果

环境：Python 3.11.5、Tk 8.6。

| 项目 | 结果 |
|---|---|
| `python -m unittest discover -v` | `Ran 89 tests`，`OK (skipped=22)`；其余 67 项通过 |
| Windows 进程树取消 | 运行中取消用例通过；无残留 Python 子进程 |
| GUI Excel 验证 smoke | 3 轮通过，2 行 VALID、0 error、0 warning、`window=mapped` |
| GUI 离线正例 smoke | 3 轮通过，2 行 PASS、0 error、0 warning、`window=mapped` |
| GUI 离线反例 smoke | 1 行 FAIL、9 error、0 warning；脚本按预期返回 0 |
| GUI 10,000 行负载 | 10,000 行、0 error、0 warning；本机墙钟约 3.15 秒 |
| 最小窗口布局 | `980x680` 下八列映射、数据源、报告和操作区无重叠或截断 |
| Excel 模板 | 两个工作表可读；八个精确表头和两行示例正确；公式错误扫描为 0 |

22 项 skipped 包含 21 项 Linux Bash/X11 launcher 测试和 1 项 POSIX 进程组语义测试。macOS 预期只跳过前 21 项；POSIX 后代清理回归应实际执行。

## CentOS/Verdi 结果

环境：CentOS 7.9、Python 3.8.13、Tk 8.5、G++ 11.2.1、Verdi/NPI O-2018.09-SP2。

| 项目 | 结果 |
|---|---|
| 关键文件同步 | 本地与 VM 哈希一致 |
| Shell 语法 | 4 个相关 shell 脚本 `bash -n` 通过 |
| 清空显示变量后的全量测试 | `Ran 89 tests in 3.735s`，`OK`，0 skipped |
| GUI 会话探测 | 未要求 `gnome-session-binary`；自动发现可用 `DISPLAY=:0` |
| GUI Excel 验证 | 连续 20 轮通过；2 行 VALID、0 error、0 warning |
| GUI 离线正例 | 连续 100 轮通过；2 行 PASS、0 error、0 warning |
| GUI 离线反例 | 1 行 FAIL、9 error、0 warning |
| GUI 10,000 行负载 | 最终测量 1.98-2.08 秒、150060-150148 KiB；0 error、0 warning |
| 取消启动竞态 | 100/100 通过，总耗时约 15.3 秒 |
| 忽略 SIGTERM 后代清理 | 全量测试中的真实 POSIX 进程组回归通过 |
| 真实 elaborated KDB 在线 GUI | 连续 3 轮通过；2 行 PASS、0 error、0 warning；1.88 秒、52180 KiB |
| 在线命令契约 | 成功行包含 `contract=elab-only`；GUI 日志断言无 inventory/filelist/top/passthrough |
| rscheck GUI X11 证据 | smoke 输出自身 client XID；`xwininfo -tree -stats` 验证父标题、`IsViewable`、1180x780 |
| Verdi 同 KDB 证据 | 已有 Novas/Verdi 窗口 `IsViewable`、1143x745；进程参数为 `-elab <同一 KDB>` |
| 结束残留检查 | 无 collector、GUI smoke、`python3 -m rscheck`、xwininfo 或相关僵尸进程 |

在线首次尝试因 shell 中两个 license 变量为空而在 NPI 初始化阶段返回 code 10，GUI 正确显示基础设施 `ERROR`。通过受控登录环境恢复 license 变量后，真实 KDB 在线 3 轮全部通过。该过程没有向日志或仓库输出 license 值。

## 输入与报告结论

- 在线 GUI 的唯一设计输入是 Verdi elaborated KDB；`work.lib++` 和其符号链接别名均被拒绝。
- Excel 允许额外列，八个字段可位于任意互不重复的正数列号；GUI 可逐项输入列号。
- RTL 差异退出码 1 会正常加载报告并显示 FAIL，不会误当基础设施错误。
- JSON 报告 schema、行数、通过/失败数和 finding 计数会在显示前交叉校验。
- JSON、CSV 和保留 inventory 不得互相覆盖，也不得覆盖输入、collector，或写入 KDB/NPI 库目录。
- GUI 取消会终止 CLI 与 collector 所在完整进程组；三秒后仍存活的后代会被强制清理。

## 安全检查

- `git diff --check` 通过，仅有 Windows 行尾转换提示；
- 仓库敏感信息扫描未发现 VM 地址、密码、真实 license、私钥或 GitHub token；
- 测试结束后本地和 VM 均确认无本轮后台进程残留。
