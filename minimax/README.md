# MiniMax × OceanBase：SDD 工程上下文与真实代码演进

本项目把规格、业务代码和测试导入 seekdb，结合真实 PowerContext Scope 状态构造开发上下文，并通过 Qoder 中明确选择的 **MiniMax-M3.1-Flash** 完成了一次代码修改。修改已独立审阅并应用；同一组目标测试从5项失败变为5项通过，原有5项回归保持通过，完整25项测试通过。官方20分仍待正式提交与评测。

最新实跑材料位于 [logs/minimax-prepared-20261102T080537193489Z](logs/minimax-prepared-20261102T080537193489Z)：[结果汇总](logs/minimax-prepared-20261102T080537193489Z/submission-summary.json)、[MiniMax上下文](logs/minimax-prepared-20261102T080537193489Z/context.json)、[代码差异](logs/minimax-prepared-20261102T080537193489Z/change.diff)、[完整测试](logs/minimax-prepared-20261102T080537193489Z/full-suite-green.txt)、[状态恢复](logs/minimax-prepared-20261102T080537193489Z/after-context-new-process.txt)。目录名保留最初 prepare-only 阶段的名称；后续真实模型调用、审阅和应用状态见 status.json。

完整25项还在全新官方 seekdb 1.4.0.0 镜像上通过；测试前专属数据库不存在，见 [官方镜像验证](logs/minimax-prepared-20261102T080537193489Z/stock-seekdb-validation.json)及[原始输出](logs/minimax-prepared-20261102T080537193489Z/stock-seekdb-full-suite.txt)。修改前独立目录重放结果见 [baseline-isolated-replay.json](logs/minimax-prepared-20261102T080537193489Z/baseline-isolated-replay.json)。

## 从干净环境运行

需要 Linux/WSL、Python≥3.11、Git；使用自带数据库部署方式时还需要 Docker Compose。实测 Python 3.12.16，依赖固定于 requirements.txt。命令从本 minimax/ 目录执行。

```bash
# 可选：独立的官方 seekdb 镜像，绑定本机2946端口，不影响已有数据库。
docker compose -p minimax-sdd-demo up -d --wait

PYTHON_BIN=python3.12 bash scripts/bootstrap.sh
export PYTHONPATH="$PWD/_work/powercontext/src"
export PYTHONDONTWRITEBYTECODE=1
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2946 SEEKDB_USER=root SEEKDB_DATABASE=minimax_sdd
.venv/bin/python -m unittest discover -s tests -v
```

默认数据库配置不设密码，仅绑定本机隔离开发端口。已有 seekdb 时可跳过 Compose 并设置其地址、端口及 SEEKDB_PASSWORD。CLI只允许专属数据库 minimax_sdd，不启动或停止现有服务。.env.example 仅为环境变量示例，CLI不会自动加载 .env。

bootstrap.sh 在本目录建立独立 .venv，并克隆固定参赛 fork 提交 `1be152d62a4c6df59709f15b184c6b93085fb76f` 至 _work/powercontext；不依赖本机其他工作目录。SDK使用[原始官方项目](https://github.com/oceanbase/powercontext)的真实 SQLiteProfile/ScopeApplication；固定版本来自[参赛fork](https://github.com/David-goldenman/powercontext/tree/1be152d62a4c6df59709f15b184c6b93085fb76f)，不能称为官方提交。依赖或网络安装问题的历史排查见 [logs/SETUP.md](logs/SETUP.md)。

## 验证样例与入口

业务样例为本次新建的小型任务列表。初始数据位于 sample/tasks.py：id 1/open/priority2，id2/open/4，id3/done/4，id4/open/1。

- 保持行为：按 priority 降序、id升序返回 `[2,3,1,4]`；open筛选返回 `[2,1,4]`；结果为副本，不修改输入。
- 实际新增：min_priority为包含下界，3返回 `[2,3]`，与open及4组合返回 `[2]`；1包含全部，5返回空集。
- 参数：仅接受整数1..5并排除bool；0、6、负数、浮点和字符串均ValueError，包括空任务输入。

必要来源由 sample/manifest-v1.json、manifest-v2.json 明确列出：适用规格、sample/tasks.py::list_tasks、tests/test_target.py及tests/test_regression.py。输出携带完整原文、真实路径、符号及行号。

```bash
.venv/bin/python context_cli.py import --task review-example --manifest sample/manifest-v1.json
.venv/bin/python context_cli.py context --task review-example

# 更新规格后重新导入，输出必须采用spec-v2.md及新增的空输入验证条款。
.venv/bin/python context_cli.py import --task review-example --manifest sample/manifest-v2.json
.venv/bin/python context_cli.py context --task review-example

# 两次命令是两个独立进程；后一次把保存的todo用于next_action。
.venv/bin/python context_cli.py --store runtime/review-example.sqlite3 save-state --task review-example \
  --completed 'Imported active specification' --todo 'Review MiniMax diff and run unchanged tests' \
  --verification 'Use fixed source and test files'
.venv/bin/python context_cli.py --store runtime/review-example.sqlite3 context --task review-example
```

每次导入按task在事务中替换全部资料。上下文真正来自参数化SQL查询结果；源文件不存在或与导入原文不一致时明确拒绝输出，要求重新导入。选择方式是任务manifest的精确依赖集，未声称全仓语义检索。

PowerContext以 ScopeBindingKey(integration='minimax-sdd',kind='task',external_id=task) 定位版本化Scope，在真实SQLite持久化中保存 completed/todo/verification。JSON是Scope summary字段内容，不是JSON文件替代状态库。实际任务沿用同一Scope，并在新进程中读回完成状态及更新后的代码资料。

## MiniMax 实际参与与审阅

实际接入方式为 **Qoder桌面端内置的指定MiniMax模型**，不是Auto路由，也没有使用其他模型代替这一步。模型选择规则见[Qoder官方文档](https://docs.qoder.com/qoder/model-selector)。本次无需MiniMax个人API密钥；记录中的 api_called=false 表示没有直连MiniMax API，model_called=true 表示实际通过Qoder调用了指定模型。

实际Qoder会话：`23126d68-56c0-4bed-80d1-2b403d4825c3`，2026-11-02 16:07，界面成功状态48秒/6次模型迭代。证据包括：

- [实际模型选择截图](logs/minimax-prepared-20261102T080537193489Z/model-selected.jpg)和[实际完成截图](logs/minimax-prepared-20261102T080537193489Z/model-completed.jpg)。
- [提交给助手的请求](logs/minimax-prepared-20261102T080537193489Z/QODER_REQUEST.txt)、[真实检索上下文与提示词](logs/minimax-prepared-20261102T080537193489Z/prompt.txt)、[可见对话导出](logs/minimax-prepared-20261102T080537193489Z/dialogue-visible.txt)。
- [修改前源码](logs/minimax-prepared-20261102T080537193489Z/before.py)、[MiniMax提案](logs/minimax-prepared-20261102T080537193489Z/after.py)、[统一diff](logs/minimax-prepared-20261102T080537193489Z/change.diff)和[独立审阅](logs/minimax-prepared-20261102T080537193489Z/REVIEW.md)。
- status.json 内前后测试命令、退出码、对应源码快照及真实状态；frozen-tests/保留相同测试正文。

流程为：检索真实上下文并读取先前待办 → 选定MiniMax → 只生成after.py提案并停止 → 父级完整审阅源码和diff → 使用独立 --apply-reviewed 步骤应用并运行冻结测试 → 重新导入代码并把验证结果写回PowerContext → 新进程读取。

模型没有运行命令或测试，没有直接改动业务文件或测试。应用步骤执行经过审阅的本地Python，未声称为沙箱；测试子进程不继承模型密钥，运行前后检查冻结测试正文、实际数量及跳过情况。记录不要求或保留模型私有思维链、密钥或认证头。

minimax_change.py 的默认模式仍只准备上下文，不调用模型。可再次执行 `--task`及`--store`对应任务生成新提示词，再交给Qoder中明确选择的MiniMax。脚本的可选 --execute 是另一条直连MiniMax官方API路径，需要用户已有密钥和额度，且预检要求目标尚未实现的基线；它不是本次实际使用的接入方式。

## 测试与修改前复现

当前代码直接运行完整测试，预期25项通过、退出0：5回归+5目标+7真实DB/PowerContext集成+8适配器离线边界。实际原始输出见 full-suite-green.txt。历史基础阶段曾有5项目标功能失败；这些日志保留真实阶段，未改写为通过。scripts/verify_foundation.py 是旧基线的专用历史验证器，当前完成后的代码请运行上面的完整测试入口。

可在本模块 _work/ 的独立目录重放修改前测试，不替换当前业务代码：

```bash
replay_dir="$PWD/_work/baseline-replay-$(date +%s)"
minimax_python="$PWD/.venv/bin/python"
proof="logs/minimax-prepared-20261102T080537193489Z"
mkdir -p "$replay_dir/sample" "$replay_dir/tests"
cp sample/__init__.py "$replay_dir/sample/__init__.py"
cp "$proof/before.py" "$replay_dir/sample/tasks.py"
cp "$proof/frozen-tests/test_regression.py" "$proof/frozen-tests/test_target.py" "$replay_dir/tests/"
(cd "$replay_dir" && "$minimax_python" -m unittest discover -s tests -v)
# 预期退出1：5回归通过，5目标因新功能未实现而失败，无环境错误。
```

重新导入任一新task和新的 --store 路径即可重放空状态，不删除已有数据。当前业务文件与 after.py一致；before.py保存新增筛选前的真正可运行基线。目标及回归测试在实际修改前后保持原样。

## 范围、来源与成绩

业务代码、规格、测试和上下文适配器为本次任务编写。PowerContext用法参考固定fork的Scope测试与实现，原项目Apache-2.0；依赖版本与数据来源见日志。官方镜像固定digest见docker-compose.yml。一页技术说明见 [TECHNICAL_NOTE.md](TECHNICAL_NOTE.md)。

本地验证及真实MiniMax参与已形成材料，正式提交和官方20分尚未确认。未创建账号或购买额度，未把网站发布或本地测试当作官方成绩。
