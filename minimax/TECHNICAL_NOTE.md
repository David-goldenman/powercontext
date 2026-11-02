# 一页技术说明：面向局部演进的 SDD 上下文

**当前状态**：基础工程已准备，实际 MiniMax API 参与和目标代码修改待用户获得凭据与可用额度。业务样例为本次新建，不能把目标功能未实现称作历史缺陷，不能以此交付声称 20 分完成。

**数据流**：task id + manifest → 文件来源校验 → seekdb `minimax_sdd.sdd_documents` → 参数化 SQL 查询 → 规格/代码/测试与来源行号 → PowerContext 保存状态的读取 → 开发上下文和下一步动作 →（待执行）MiniMax 代码修改 → 同组测试 → PowerContext 保存验证状态。

**seekdb 的实际分工**：保存四个完整工程源文件，按 task id/position 查询。导入以事务替换该任务全部资料。v1 与 v2 规格均为真实文件，重新导入 v2 后查询采用新内容和新路径。上下文内容取自 SQL 返回行，不采用固定答案；文件丢失或与导入内容不一致时拒绝输出，要求重新导入。精确 manifest 依赖集是本样例的选择方法，不冒充全仓语义检索。

**PowerContext 的实际分工**：使用参赛 fork `David-goldenman/powercontext` 固定提交 `1be152d6` 的真实 `SQLiteProfile`、`ScopeApplication`、`ScopeBindingKey` 与版本化 `ScopeMutation`，原始来源为 OceanBase 官方项目。task binding 指向 Scope，summary 保存 completed/todo/verification。每个进程打开/关闭真实持久化后端，后续进程用同一 binding 读取状态，再把 todo 注入 `next_action`，不依赖驻留内存或 JSON 文件替代状态库。

**业务验证任务**：保持状态筛选、priority 降序/id 升序、复制结果和空集行为；新增整数 1..5 的包含式 min_priority 筛选，排除 bool，且空输入也先验证参数。基线对非 None 参数明确抛 NotImplementedError。5 个回归测试应 GREEN，5 个目标测试应因缺失新功能 RED；后续 MiniMax 必须修改同一业务代码、保持同组测试不变并全部 GREEN。

**MiniMax 的预定分工**：默认 prepare-only，输出真实提示词；未来 `--execute` 请求一次并保存公共代码提案、diff、模型/响应标识和分阶段回执，随后停止。父级实际审阅代码后才另行 `--apply-reviewed`，该步骤不是 Python 沙箱。测试子进程不继承密钥，前后核对冻结测试正文，并要求回归/目标各 5 项、无 skip、全部通过。私有思维链、密钥和认证头不进入记录。当前没有真实模型结果。

**可审计证据**：`scripts/verify_foundation.py` 生成版本、命令/退出码、基线快照、完整测试输出、真实 SQL 结果、空状态、新进程恢复及总结。所有临时数据仅在本目录，数据库写入限定专属 schema；不重启服务、不修改核心源码或共享 venv。
