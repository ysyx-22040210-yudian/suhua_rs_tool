# kverif 下载、kdebug ELF 构建与真实 KDB 压测记录（2026-07-29）

## 1. 结论

本次从 `ysyx-22040210-yudian/kverif` 的固定分支下载源码，并固定到提交：

```text
2b43b799c8f7f8586a9e6c2128335e74d971e633
```

实际生成的是 Linux x86-64 ELF 可执行文件 `kdebug`。rscheck 的直接执行命令只有：

```text
$KDEBUG_BIN --json -
```

rscheck 不执行 C/C++ 源文件，也不直接执行或 source `kdebug_npi.tcl`。现有 kverif 的
ELF 启动后会定位相邻 `libexec/kdebug-engine`，其私有 engine 再使用 Python/Tcl 与 Verdi
交互。因此 `kdebug_npi.tcl` 必须随运行包存在，但它不是 `KDEBUG_BIN`，也不是 rscheck
入口。若要求整个运行时彻底没有 Tcl，仅重新编译现有工程不能实现，必须重写 kverif
的 Verdi 访问后端。

## 2. 固定构建产物

| 项目 | 结果 |
| --- | --- |
| kverif 仓库 | `https://github.com/ysyx-22040210-yudian/kverif.git` |
| kverif 分支 | `codex/rscheck-elab-inventory` |
| kverif 提交 | `2b43b799c8f7f8586a9e6c2128335e74d971e633` |
| kdebug 类型 | `ELF 64-bit LSB executable, x86-64` |
| kdebug SHA-256 | `28c5068662f82201b91f988ca57461da278ea1e06ee92a22955625b06d95da25` |
| 运行包 SHA-256 | `b3008608341a02fbc952f44fa9728bdc322533510e6369d97a3497f6072fa704` |
| VM Python | `3.8.13` |
| VM 构建编译器 | `g++ 4.8.5` |

运行包包含 `kdebug + libexec + LICENSE + BUILD_INFO.txt + SHA256SUMS`。四次独立构建生成
的压缩包 SHA-256 完全相同，证明归档顺序、时间、owner/group 和 gzip header 已规范化。

最终 VM 构建现场标识：

```text
kverif_kdebug_build.9ML8cmVK
```

## 3. 可复制构建命令

以下命令不包含设备地址、密码、license 值或生产 KDB 内容。在线烟测前应在当前 shell
中准备好设备自己的 Verdi 和 license 环境；KDB 必须是 `elabcom -elab` 生成的
`*.elab++` 目录，不得使用 filelist 或 `work.lib++`。

```bash
set -euo pipefail

export RSCHECK_ROOT=/absolute/path/to/suhua_rs_tool
export OUTPUT_BASE="$HOME/rscheck-kdebug-builds"
export PYTHON_BIN="${PYTHON_BIN:-python3}"
export ELAB_DB=/absolute/path/to/kdb.elab++
export SMOKE_POSITION=top.u_tile

cd "$RSCHECK_ROOT"
"$PYTHON_BIN" -c 'import pytest, jsonschema'
bash scripts/build_kdebug_from_kverif.sh \
  --output-base "$OUTPUT_BASE" \
  --python "$PYTHON_BIN" \
  --jobs 2 \
  --smoke-elab-db "$ELAB_DB" \
  --smoke-position "$SMOKE_POSITION"
```

脚本固定 canonical 仓库、分支和提交，不能通过环境变量替换分支或提交。仅在可信镜像
测试时可设置 `KVERIF_MIRROR_URL`；即使使用镜像，checkout 后的完整提交号仍必须等于
固定 SHA。脚本输出的 `RUNTIME_KDEBUG_BIN` 是目标设备应设置的绝对 `KDEBUG_BIN`。
外层构建清单只记录 `canonical` 或 `trusted_mirror`，不会落盘镜像 URL；清单中的产物
路径均相对本轮构建目录，便于搬运后复核。

## 4. 自动验证内容

构建脚本在成功前依次完成：

1. 在下载前检查编译工具，以及所选 Python 的 `pytest`、`jsonschema` 测试依赖。
2. 下载固定分支并核对完整提交号，拒绝脏 checkout。
3. 执行 `make -C kdebug clean` 和 `make -C kdebug all`，确认 ELF 魔数为 `7f454c46`。
4. 执行 kverif `test-fast`。
5. 检查 `ldd` 不含 `libNPI`、`libnpiL1` 或 `not found`。
6. 检查 ELF 注册 `rscheck.inventory`，并验证私有 engine 文件存在且非空。
7. 生成可搬运运行包，展开后执行内部 `SHA256SUMS` 和打包后 ELF action 复检。
8. 指定真实 KDB 时，用打包后的 ELF 执行 `rscheck.inventory`，要求成功返回 inventory v3、
   指定位置对象、`found=true` 和合法的实例数组；外层超时到期后最多再等待 5 秒即强制
   终止，并把 `--python` 选择传给私有 engine。

kverif `test-fast` 结果：

```text
Python engine/unit: 35 passed
Tcl inventory unit: PASS
schema validation: 182 files
example validation: 175 files
C++ unit binaries: PASS
runtime action contract: 29 passed
```

## 5. 真实 KDB 与压力结果

打包后 ELF 的单位置烟测结果：

```text
LIVE_SMOKE=PASS schema=v3 position=top.u_tile instances=13
position_count=1
instance_count=13
warning_count=0
```

最终 20 轮压力测试使用同一运行包，覆盖 raw action、Python adapter、clean/partial KDB、
前端取消、1 秒硬超时和结束泄漏检查：

```text
raw clean: PASS, positions=2, instances=26, schema=v3
raw partial: PASS, NPI_LOAD_PARTIAL notice=1
adapter clean+partial: PASS
clean reload: 20/20 PASS
cancellation + timeout cleanup: PASS
process/session/crash-marker leak: none
```

最终压力现场标识：

```text
kdebug_pressure.zmIUX4iv
```

压力脚本对 Tcl 路径的引用只用于 kdebug 私有运行树完整性、哈希和进程泄漏取证；实际
adapter 启动命令仍固定为 `[KDEBUG_BIN, "--json", "-"]`。

## 6. 主工具回归结果

| 测试 | 结果 |
| --- | --- |
| Windows 本机完整 Python 回归 | `342` 项通过，`52` 项按平台/显式集成条件跳过 |
| VM Linux 完整 Python 回归 | `335` 项通过，`1` 项显式构建集成测试按默认条件跳过 |
| VM 显式构建集成测试 | `9/9 PASS`，包含 clone/build/package/live KDB smoke |
| 新脚本 `bash -n` 和参数负例 | PASS |

完整 Python 回归默认不重复下载和编译 kverif。要显式启用耗时的构建集成测试，在 Linux
设置可信镜像和可选 KDB 后运行：

```bash
KDEBUG_BUILD_INTEGRATION_MIRROR=file:///absolute/path/to/trusted/kverif.git \
KDEBUG_BUILD_INTEGRATION_ELAB_DB=/absolute/path/to/kdb.elab++ \
KDEBUG_BUILD_INTEGRATION_POSITION=top.u_tile \
python3 -m unittest tests.test_build_kdebug_from_kverif -v
```

## 7. GitHub 下载说明

仓库不提交架构相关的预编译 ELF、生产 KDB、RTL、license 或现场临时日志。提交固定构建
脚本、自动测试、SHA-256 和本记录，其他设备在本机 Verdi/Tk 环境中重新构建，可避免
预编译二进制与目标 glibc/CPU/Verdi 组合不兼容。
