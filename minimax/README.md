# MiniMax × OceanBase：SDD 上下文基础

当前交付是**真实可运行的基础工程，MiniMax 实际参与仍待 API 与可用额度**。本次没有调用 MiniMax，没有实现待模型修改的优先级筛选，也没有声称赛题完成或取得 20 分。

最新独立环境实跑证据：[summary.json](logs/foundation-20261102T074727445829Z/summary.json)、[命令与退出码](logs/foundation-20261102T074727445829Z/commands.json)、[SDK/依赖版本](logs/foundation-20261102T074727445829Z/versions.json)。20 项基础与回归通过，5 项待新增功能明确 RED；验证脚本退出 0，完整测试集按预期退出 1。

`sample/tasks.py` 是为本次任务新建的小型业务基线。它已有状态筛选、排序和复制返回值；`min_priority` 是预留的新功能，非 None 时明确抛出 `NotImplementedError`，不是发现的历史缺陷。目标测试将该情况报告为明确的功能断言失败。

## 环境与依赖

- Linux/WSL Ubuntu，Python ≥3.11（实测 Python 3.12.16）；依赖版本固定于 `requirements.txt`。
- 已运行、允许创建 `minimax_sdd` 数据库的 seekdb MySQL 协议服务。实测为 WSL `127.0.0.1:2896`，root 空密码；模块只创建/修改专属 `minimax_sdd.sdd_documents`，不会启动、停止或配置服务。
- 使用真实 PowerContext SDK，而非自行仿制的状态库。[官方项目](https://github.com/oceanbase/powercontext/tree/2026-competition-ecosystem)为原始来源；本次固定版本是[参赛 fork 的 2026-competition 分支提交](https://github.com/David-goldenman/powercontext/tree/1be152d62a4c6df59709f15b184c6b93085fb76f) `1be152d62a4c6df59709f15b184c6b93085fb76f`，不能称为官方仓库提交。
- 测试和基础上下文不需要任何模型 API，也不读取 `MINIMAX_API_KEY`。

从当前目录执行以下 Bash 命令。`bootstrap.sh` 默认把固定版本参赛 fork 克隆到本目录的 `_work/powercontext`，并建立本目录 `.venv`；不会修改其他环境。已有源码时可设置 `POWERCONTEXT_SOURCE=/absolute/path/to/checkout`。

```bash
PYTHON_BIN=python3.12 bash scripts/bootstrap.sh
export PYTHONPATH="$PWD/_work/powercontext/src"
export PYTHONDONTWRITEBYTECODE=1
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2896 SEEKDB_USER=root SEEKDB_DATABASE=minimax_sdd
# 若你的服务设置了密码，自行在环境中设置 SEEKDB_PASSWORD；不要写进日志或提交。
.venv/bin/python scripts/verify_foundation.py
```

本机已有源码的执行方式：

```bash
cd /mnt/d/ob-race/powercontext/minimax
export PYTHONPATH=/mnt/d/ob-race/powercontext-evidence/src
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python scripts/verify_foundation.py
```

当前 WSL 的旧代理不可用，直接下载包文件也曾超时；实际采用 Windows 下载官方 Linux wheel、WSL 本地安装的方式验证独立环境。可在 Windows PowerShell 从本模块目录执行（代理参数仅在确有可用本地代理时添加，不改全局设置）：

```powershell
New-Item -ItemType Directory -Force _work/tmp,_work/pip-cache,_work/wheels | Out-Null
$env:TEMP="$PWD/_work/tmp"; $env:TMP=$env:TEMP; $env:PIP_CACHE_DIR="$PWD/_work/pip-cache"
python -m pip download --index-url https://pypi.org/simple --proxy http://127.0.0.1:7890 `
  --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 `
  --platform manylinux_2_27_x86_64 --platform manylinux_2_28_x86_64 `
  --python-version 312 --implementation cp --abi cp312 --only-binary=:all: `
  -r requirements.txt --dest _work/wheels
```

随后在 WSL 独立 `.venv` 中执行 `.venv/bin/python -m pip install --no-index --find-links _work/wheels -r requirements.txt`，并运行同一验证脚本。Ubuntu 22.04 满足这些 manylinux wheel 的 glibc 要求。下载/安装原始结果保存在 `logs/windows-wheel-download-sdk-closure.txt` 和 `logs/independent-venv-install-sdk-closure.txt`。

基础验证脚本自身期望退出 0；它要求业务回归 5 项通过、上下文真实集成 7 项通过、适配器离线测试 8 项通过，同时目标新功能 5 项明确失败，共运行 25 项。完整 `unittest discover` 的实际退出码应为 1，因为目标尚未实现；此状态会如实写入日志，拒绝零用例或跳过测试。`logs/foundation-*/commands.json` 保存每条实际命令、退出码、UTC 时间和基线代码版本；同目录 `versions.json`、源码快照、原始测试输出和 `summary.json` 形成审阅证据。

## 调用入口与输入输出

```bash
# 输入 task id 和实际文件清单；导入四个完整源文件到真实 seekdb。
.venv/bin/python context_cli.py import --task priority-filter --manifest sample/manifest-v1.json
.venv/bin/python context_cli.py context --task priority-filter

# 规格变化后重新导入；查询结果应出现 v2 内容和 sample/spec-v2.md 来源。
.venv/bin/python context_cli.py import --task priority-filter --manifest sample/manifest-v2.json
.venv/bin/python context_cli.py context --task priority-filter

# 保存阶段状态，然后用另一进程读取并用于 next_action。
.venv/bin/python context_cli.py save-state --task priority-filter \
  --completed 'Baseline regression passed' \
  --todo 'Use MiniMax to implement min_priority after API/quota become available' \
  --verification 'Target feature remains RED'
.venv/bin/python context_cli.py context --task priority-filter
```

输出 JSON 包含 `sources[].kind/path/symbol/line_start/line_end/content`、实际查询 SQL/参数/返回行数、`state` 和由已保存 todo 得出的 `next_action`。代码和测试均来自 seekdb 查询结果。构造上下文时还检查文件确实存在且内容未过期；若磁盘文件改变，先重新导入。它不会在 DB 不可用时返回硬编码样例。

每次导入按 task 在事务中替换其全部文档，避免 v1 或已移除来源继续出现。选材策略是显式 task manifest 的精确依赖集，适合本例；不宣称具备任意仓库的语义检索能力。路径必须留在本模块内，数据库名固定允许 `minimax_sdd`。

PowerContext 状态默认位于 `runtime/task-state.sqlite3`，支持全局参数 `--store runtime/another.sqlite3`（放在子命令前）。真实使用 `SQLiteProfile` + `ScopeApplication`，以 `ScopeBindingKey(integration='minimax-sdd',kind='task',external_id=task)` 解析任务，并通过版本化 Scope summary 保存 completed/todo/verification。这里的结构化 JSON 是 Scope 中的字段内容，不是 JSON 文件替代 PowerContext。跨进程读写测试会检查 Scope id 一致、进程 id 不同、下一步动作采用保存的 todo。

## 验证样例与固定测试

必要来源为 `sample/spec-v2.md` 的 SDD-BASE-1..3 / SDD-NEW-1..4、`sample/tasks.py::list_tasks`、`tests/test_regression.py::RegressionTests`、`tests/test_target.py::TargetTests`。清单文件列明全部来源；上下文附上完整内容和真实行号。

- 初始任务数据：id 1/open/priority 2，id 2/open/4，id 3/done/4，id 4/open/1。
- 保持：默认按 priority 降序、id 升序得 `[2,3,1,4]`；open 筛选得 `[2,1,4]`；返回副本、不改输入。
- 待新增：`min_priority=3` 得 `[2,3]`；`status='open',min_priority=4` 得 `[2]`；边界 1 包含全部、5 得空结果。
- 待新增验证：只接受整数 1..5，排除 bool；0、6、负数、浮点与字符串均 ValueError，空任务也先验证参数。

```bash
.venv/bin/python -m unittest discover -s tests -p test_regression.py -v  # 退出0
.venv/bin/python -m unittest discover -s tests -p test_target.py -v      # 基线退出1，5项功能失败
.venv/bin/python -m unittest discover -s tests -p test_context.py -v     # 真实DB/PC，退出0
.venv/bin/python -m unittest discover -s tests -p test_adapter.py -v     # 离线解析边界，退出0
.venv/bin/python -m unittest discover -s tests -v                       # 基线退出1，仅目标5项失败
```

基础脚本使用唯一 task id 和全新 SQLite 文件，可以重复执行而不删除已有状态或其他项目数据。人工复现空状态时也使用新的 `--task` 和 `--store runtime/new-name.sqlite3`。后续模型修改后，可从本次运行的 `logs/foundation-*/baseline_tasks.py` 还原 `sample/tasks.py` 再重新导入，无需删除任何数据库。不要重置其他目录或服务。

## 未来 MiniMax 实际修改

当前默认调用只生成真实上下文及提示词，不读模型密钥、不联网、不写业务代码：

```bash
.venv/bin/python minimax_change.py --task priority-filter
```

用户取得 API 与现有可用额度后，自行把 `MINIMAX_API_KEY`、`MINIMAX_MODEL` 放入环境，确认额度后设置 `MINIMAX_QUOTA_CONFIRMED=1`，再执行：

```bash
.venv/bin/python minimax_change.py --task priority-filter --execute
```

`--execute` 会发起一次真实请求并保存提案后停止，实际产生 API 消耗；本次未执行。它不会自动运行生成代码。脚本按 [MiniMax 官方 OpenAI 兼容文档](https://platform.minimax.cn/docs/api-reference/text-openai-api) 使用 `/v1/chat/completions`，模型名由用户当前方案提供；默认中国官方端点，也支持环境指定国际官方端点 `https://api.minimax.io/v1/chat/completions`。传输协议依据 2026-11-02 官方文档编写，真实账号权限与请求仍待实测。

请求使用真实上下文及恢复的待办，模型必须返回仅修改 `sample/tasks.py` 的完整代码 JSON。脚本保留提示词、基线/提案源码、统一 diff、模型 id/响应 id/usage 和基线测试输出。请求开始、响应收到、解析拒绝与失败阶段都有即时状态记录，不能把网络尝试当作模型成功参与。它丢弃 `reasoning_content` 和 `<think>` 内容，不保存原始响应、密钥或 HTTP 认证头。失败无自动付费重试、开户或购买行为。

提案状态为 `PROPOSAL_AWAITING_REVIEW`。当前开发助手/父级实际读过 `after.py` 和 `change.diff`，确认修改仅限规定函数、无文件/网络/进程等额外行为后，才执行独立步骤（使用提案对应的同一 task/store）：

```bash
.venv/bin/python minimax_change.py --task priority-filter --apply-reviewed logs/minimax-real-实际运行目录
```

该步骤执行的是经过审阅的本地 Python，并不是 Python 沙箱。测试子进程仅获最小白名单环境，不继承任何模型 API 密钥；脚本逐份核对冻结的原始测试正文，在运行前后检查没有变更，要求回归和目标各实际运行 5 项且无 skip，然后保存测试输出和退出码。仅有进程退出 0 不能标记 `VERIFIED`。当前基础交付不会执行任何模型提案或恶意测试载荷。

目标和回归通过后才能核实代码修改有效；失败结果会保存并写回 PowerContext，供下一轮恢复使用。必须审阅真实模型记录与 diff 后再固定提交。此时尚未满足“MiniMax 实际参与”和“代码修改有效”两项必做验收，整个赛题仍未完成。

## 材料范围和来源

业务代码、规格、测试与上下文适配器为本次新写；PowerContext API 用法参考固定参赛 fork 的 `tests/builtin/test_scope_application.py` 和 `src/powercontext/builtin/scope/application.py`，原项目 Apache-2.0。数据库版本与 Python 包精确版本见实跑 `versions.json`/导入日志。没有读取正式评测 SciFact 标签。`TECHNICAL_NOTE.md` 为一页技术说明，`DESIGN.md`/`PLAN.md` 为范围与执行记录。
