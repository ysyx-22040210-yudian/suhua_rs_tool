# kdebug 编译产物强制校验与 VM 压测记录（2026-07-28）

## 1. 测试对象

- rscheck 分支：`codex/kdebug-npi-backend`
- rscheck 提交：`a72895381c562592ffc4fa922ef165f93e6ae7ce`
- kverif 分支：`codex/rscheck-elab-inventory`
- kverif 提交：`2b43b799c8f7f8586a9e6c2128335e74d971e633`
- kdebug ELF SHA-256：`28c5068662f82201b91f988ca57461da278ea1e06ee92a22955625b06d95da25`
- VM Python：`3.8.13`

测试使用上述 kverif 提交的干净构建树。实际压测树中的 `kdebug`、
`kdebug-engine`、Python engine 和两个 Tcl engine 与干净构建树逐文件 SHA-256
一致。

## 2. 强制约束

rscheck 只允许把 `KDEBUG_BIN` 配置为 `make -C kdebug all` 生成的 Linux ELF
可执行文件。适配器和两套 VM 脚本都会在加载 elaborated KDB 前检查：

1. `KDEBUG_BIN` 是绝对路径；
2. 路径指向可执行的常规文件；
3. 前四字节为 ELF 魔数 `7f454c46`；
4. `.c/.cc/.cpp/.cxx` 等 C/C++ 源文件和 shell/Python/文本包装脚本均被拒绝。

通过检查后的固定调用形式为：

```text
<compiled-kdebug-ELF> --json -
```

## 3. 可复现测试命令

先在 VM 中设置当前设备的实际路径。`CLEAN_ELAB_DB` 和 `PARTIAL_ELAB_DB` 必须是
Verdi `elabcom -elab` 生成的 elaborated KDB 目录，不是 filelist 或 `work.lib++`：

```bash
set -euo pipefail

export RSCHECK_ROOT=/absolute/path/to/suhua_rs_tool
export KVERIF_ROOT=/absolute/path/to/kverif
export KDEBUG_BIN="$KVERIF_ROOT/kdebug/kdebug"
export CLEAN_ELAB_DB=/absolute/path/to/clean/kdb.elab++
export PARTIAL_ELAB_DB=/absolute/path/to/partial/partial.elab++
export PYTHON_BIN=python3.8

cd "$RSCHECK_ROOT"
test "$(git rev-parse HEAD)" = a72895381c562592ffc4fa922ef165f93e6ae7ce
(cd "$KVERIF_ROOT" && test "$(git rev-parse HEAD)" = 2b43b799c8f7f8586a9e6c2128335e74d971e633)

test -x "$KDEBUG_BIN"
test "$(LC_ALL=C od -An -tx1 -N4 "$KDEBUG_BIN" | tr -d '[:space:]')" = 7f454c46
test "$(sha256sum "$KDEBUG_BIN" | awk '{print $1}')" = \
  28c5068662f82201b91f988ca57461da278ea1e06ee92a22955625b06d95da25
file "$KDEBUG_BIN"
ldd "$KDEBUG_BIN" | grep -Eiq 'libNPI|libnpiL1|not found' && exit 1 || true

KVERIF_EXPECTED_COMMIT=2b43b799c8f7f8586a9e6c2128335e74d971e633 \
KDEBUG_EXPECTED_SHA256=28c5068662f82201b91f988ca57461da278ea1e06ee92a22955625b06d95da25 \
"$PYTHON_BIN" -m unittest discover -v

KDEBUG_BIN="$KDEBUG_BIN" \
CLEAN_ELAB_DB="$CLEAN_ELAB_DB" \
PARTIAL_ELAB_DB="$PARTIAL_ELAB_DB" \
PRESSURE_ITERATIONS=20 \
ACTION_TIMEOUT_SECONDS=60 \
PYTHON_BIN="$PYTHON_BIN" \
bash scripts/test_kdebug_backend_pressure.sh
```

如果当前 root shell 没有 license 环境，可按站点规范额外设置
`VERDI_LICENSE_USER=<GUI 用户名>`，由测试脚本只导入该用户已有的 license 环境变量；
不要把 license 值写入命令、脚本或仓库。

## 4. 测试结果

| 检查项 | 结果 |
| --- | --- |
| 本地 Windows 全量 Python 测试 | `325` 项通过，`50` 项按平台条件跳过 |
| VM Linux 全量 Python 测试 | `325/325 PASS` |
| 两套 VM shell 脚本 `bash -n` | PASS |
| 实际 kdebug 文件类型 | `ELF 64-bit LSB executable, x86-64` |
| 实际 kdebug ELF 魔数 | `7f454c46` |
| frontend 直接 NPI/缺失动态库 | 无 |
| raw clean/partial action | PASS，两个 position、26 个实例、schema v3 |
| Python adapter clean/partial | PASS，partial notice 为一个 `NPI_LOAD_PARTIAL` |
| clean elaborated KDB 重载 | `20/20 PASS` |
| frontend 取消与 adapter 硬超时 | PASS |
| 结束后的进程、session、crash marker 泄漏 | 无 |
| 把 `KDEBUG_BIN` 指向真实 `.cpp` 源文件 | `error[KDEBUG_EXEC]`，退出码 10 |
| C++ 源文件拒绝后生成 inventory | 否 |

最终 focused 压测退出码为 `0`。这证明在线检查实际执行的是 kverif 编译后的
`kdebug` ELF，而不是原始 C++ 文件或脚本。
