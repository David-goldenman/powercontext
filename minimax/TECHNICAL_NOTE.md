# 一页技术说明：SDD 上下文驱动的实际代码演进

**任务与结果**：在本次新建的任务列表业务中，用MiniMax完成min_priority包含下界筛选，保持状态筛选、排序、返回副本和空集行为。相同5项目标测试修改前因功能未实现失败，修改后全部通过；5回归保持通过；完整25项本地测试通过。官方20分待提交与评测。

**数据流**：task id＋manifest → 校验真实文件 → seekdb存储与参数化SQL查询 → 带原文/路径/符号/行号的规格、代码、测试 → 读取PowerContext已保存待办并注入next_action → Qoder中指定MiniMax-M3.1-Flash → 代码提案 → 独立审阅 → 应用与冻结测试 → 重新导入代码 → PowerContext保存验证状态 → 新进程读取。

**seekdb分工**：专属minimax_sdd.sdd_documents保存四个完整源文件，按task/position检索。导入事务替换任务全部资料，v1→v2采用更新规格；来源不存在或变旧时拒绝输出。manifest为本样例的精确依赖集，未声称全仓语义检索。真实查询结果直接进入模型提示词，既不是硬编码答案，也不只是连接测试。

**PowerContext分工**：使用参赛fork固定1be152d6的真实SQLiteProfile、ScopeApplication、ScopeBindingKey及版本化ScopeMutation，注明原始官方来源。Scope summary保存completed/todo/verification，后续独立进程读取相同binding，把todo用于next_action。实际模型输入含恢复的待办；验证后在同Scope写入完成状态，并在新进程读回更新代码与结果。

**MiniMax分工**：实际通过Qoder桌面内置指定MiniMax-M3.1-Flash生成完整after.py，仅改list_tasks。模型选择与完成截图、公开对话、SQL上下文、before/after和diff均交付。使用已有Qoder访问权限，没有直连MiniMax个人API；api_called=false与model_called=true按此区分，未伪造API响应标识。

**审阅和测试**：模型只写提案，未执行代码或修改测试。父级完整读取代码，确认指定函数外AST、签名、冻结测试不变后，另行--apply-reviewed。测试子进程不继承模型密钥，校验正文、用例数量与skip；该流程不声称为沙箱。非法参数在扫描前验证，因此空输入也满足v2约束。

**复现与证据**：README提供独立venv、固定SDK克隆、官方seekdb Compose、初始化、CLI、状态复用及完整测试命令；_work内可用相同冻结测试独立重放修改前RED。实际材料位于logs/minimax-prepared-20261102T080537193489Z，目录名保留初始prepare阶段；后续status已记录真实模型及应用结果。私有思维链、凭据和认证头不进入交付。
