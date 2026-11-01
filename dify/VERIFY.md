# 验证样例与实际运行记录

> 所有结果均来自本机实际执行，非构造。命令与输出按原样记录。

## 0. 复现方式与自检

本节所有结果来自**用仓库内 `dsl/fusion_lab.yml` 全新导入的 Dify 应用**，数据层用 `scripts/start-data-layer.sh` 以容器方式起（seekdb + fusion-api 独立于开发环境），图鉴重置为 24 条种子数据后执行。

```bash
$ bash scripts/start-data-layer.sh /opt/dify/docker
...
 fusion-seekdb  Started
 fusion-api     Started
==> 就绪：{"ok":true,"table":"fusions","total":24}

$ docker exec fusion-api wget -qO- http://127.0.0.1:3000/health
{"ok":true,"table":"fusions","total":24}
```

Dify 侧：**应用 → 导入 DSL 文件 → `dsl/fusion_lab.yml`**，导入状态 `completed-with-warnings`（0.3.0 → 0.4.0 版本迁移提示，节点与连线完整），发布后新建 API Key。

> DSL 里的数据层地址是 `http://fusion-api:3000`（compose 服务名），按 README §4 部署即可直接连通，**无需改地址**。

```bash
$ docker exec fusion-seekdb bash -c "mysql -h 127.0.0.1 -P 2881 -u root -e 'SHOW CREATE TABLE fusion.fusions\G'"
       Table: fusions
Create Table: CREATE TABLE `fusions` (
  `id` varchar(64) NOT NULL,
  `item_a` varchar(40) NOT NULL,
  `item_b` varchar(40) NOT NULL,
  `name` varchar(80) NOT NULL,
  `effect` varchar(400) NOT NULL,
  `side_effect` varchar(400) NOT NULL DEFAULT '',
  `rarity` varchar(10) NOT NULL DEFAULT '普通',
  `creator` varchar(40) NOT NULL DEFAULT '匿名发明家',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  FULLTEXT KEY `ft_content` (`name`,`effect`) WITH PARSER ngram PARSER_PROPERTIES=(ngram_token_size=2),
  KEY `idx_a` (`item_a`),
  KEY `idx_b` (`item_b`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
```

---

## 样例 A：命中已有配方（不调模型）

**初始状态**：图鉴含 24 条种子发明，其中「猫 + 微波炉」已存在，名为「猫波炉」。

**输入**：`猫 + 微波炉`

**预期**：返回图鉴里那条记录，不应重新发明；耗时明显短于生成路径。

**实测输出**：

```
### 图鉴已有：猫波炉

**猫 + 微波炉** 这组配方早就被人试过了，稀有度 传说。

效果：把猫放进微波炉，会得到一台会自己加热的机器

副作用：它只加热自己，不加热食物

（图鉴边上有人小声说：这个思路有点像 「反向遛猫器」。）

---

换两件别的东西试试，说不定能造出还没人想到的。
```

**观察**：
- 稀有度「传说」、效果、副作用与种子数据**逐字一致** → 确认是读库，模型没有重写。
- 末尾的「反向遛猫器」是相似检索召回的（共享「猫」这件物品）。
- 响应耗时 **0.37s**，对比样例 B1 的 1.26s。

---

## 样例 B：新发明 + 写入 + 复用

**初始状态**：图鉴 24 条，「面条 + 路由器」不存在。

**B1 输入**：`面条 + 路由器`

**实测输出**：

```
### 新发明：拉面信号增强碗

**面条 + 路由器**　稀有度 稀有

效果：把煮熟的面条盘绕在天线上做成螺旋振子，路由器的 Wi-Fi 信号会顺着面条的筋道程度延伸，
面越筋道覆盖越远，在书房阳台都能满格。

副作用：用久了面条会吸附周围所有设备的信号并开始发烫，最后整碗面变成一坨信号过载的硬疙瘩，
得用筷子捅断才能断网，而且从此家里所有路由器都只认这碗面的味道。

---

已收进图鉴，现在是第 25 条发明。
再给我两件东西，继续。
```

> 发明名与文案由模型现场生成，**每次跑不保证相同**（开发过程中同一组合曾得到「无线吸汤路由」）。这是作品的预期行为 —— 唯一确定的是它会入库，并且第二次必被复用。

**B2 数据库核对**：

```bash
$ mysql -h 127.0.0.1 -P 2895 -u root --default-character-set=utf8mb4 -e \
  "SELECT id,item_a,item_b,name,rarity,created_at FROM fusion.fusions ORDER BY created_at DESC LIMIT 1;"

id                        item_a  item_b  name            rarity  created_at
f_1793557...             面条    路由器  拉面信号增强碗   稀有    2026-11-02 ...
```

写入成功，`total` 由 24 变 25 → 与回复里的「第 25 条」一致。

**B3 再问一次同样的组合**：`面条 + 路由器`

**实测输出**：

```
### 图鉴已有：拉面信号增强碗

**面条 + 路由器** 这组配方早就被人试过了，稀有度 稀有。

效果：把煮熟的面条盘绕在天线上做成螺旋振子，路由器的 Wi-Fi 信号会顺着面条的筋道程度延伸，
面越筋道覆盖越远，在书房阳台都能满格。

副作用：用久了面条会吸附周围所有设备的信号并开始发烫，……只认这碗面的味道。

---

换两件别的东西试试，说不定能造出还没人想到的。
```

**观察**：第二次走的是命中分支（标题为「图鉴已有」），效果文本与 B1 的生成结果**逐字相同**，耗时从 1.26s 降到 **0.29s**。**这证明写入真的落到了 seekdb，并且查重能读到。**

---

## 样例 C：顺序无关

**输入**：`路由器 + 面条`（与 B 的输入顺序相反）

**实测输出**：

```
### 图鉴已有：拉面信号增强碗

**路由器 + 面条** 这组配方早就被人试过了，稀有度 稀有。
...
```

**观察**：识别为同一个配方，耗时 0.30s。对应 SQL 的 `(item_a=? AND item_b=?) OR (item_a=? AND item_b=?)` 双向匹配。

---

## 样例 D：只给一件（分支兜底）

**输入**：`猫`

**实测输出**：

```
要有两件东西才能开炉。

比如：**猫 + 微波炉**、**拖鞋 + 无人机**。
再补一件？
```

**观察**：走 IF-ELSE 的 else 分支，耗时 0.25s，没有报错、没有空回复。

---

## 样例 E：相似检索（seekdb 结构化 + 全文组合排序）

**输入**：`拖鞋 + 洗衣机`（这对组合本身不存在）

**`/lookup` 直接返回**（图鉴 27 条时的实测）：

```json
{
  "input": "拖鞋 + 洗衣机",
  "exact_hit": false,
  "exact": null,
  "similar": [
    {"name":"拖鞋巡洋舰","combo":"拖鞋 + 无人机","struct_score":1,"ft_score":3.7397},
    {"name":"通勤洗衣一体机","combo":"自行车 + 洗衣机","struct_score":1,"ft_score":2.6572},
    {"name":"三分钟出面机","combo":"泡面 + 洗衣机","struct_score":1,"ft_score":0}
  ]
}
```

**观察**：
- 三条都共享了其中一件物品，`struct_score` 均为 1，排在 `MATCH` 单独召回的条目之前；
- 前两条之间由全文相关性 `ft_score` 分先后（3.7397 > 2.6572）；
- 第三条 `ft_score=0`，纯靠结构化条件带进来 —— 说明两段召回是**取并集**，不是只靠全文。

> 注：`ft_score` 会随图鉴条数变化（BM25 类的 IDF 效应）。图鉴 26 条时同一查询返回 2.770 / 1.750，条数增加后升到 3.7397 / 2.6572。这是正常的，不是测量误差。

---

## 样例 F：工作流执行记录

工作流共 15 节点，DOM 实测渲染结果：

```
 1. 开始
 2. 解析两件物品
 3. 两件都齐了吗 / IF
 4. 提示补一件 / 回复
 5. 查图鉴历史 / POST
 6. 读检索结果
 7. 图鉴里有吗 / IF
 8. 组装已有条目
 9. 回复已有条目 / 回复
10. 拼提示词
11. 发明生成 / deepseek-pro
12. 解析发明
13. 存档进图鉴 / POST
14. 组装新条目
15. 回复新发明 / 回复
```

无报错徽标。见 `screenshots/01_workflow_canvas.png`。

---

## 如何恢复初始状态

```bash
# 方式一：清空后重新灌种子（容器内执行）
docker exec fusion-api sh -c 'node seed.mjs --reset'

# 方式二：只清空，再重启 fusion-api（启动时会自动灌种子）
docker exec fusion-api wget -qO- --post-data='' http://127.0.0.1:3000/reset
docker restart fusion-api
```

`--reset` 会 `TRUNCATE TABLE fusions` 后重写 24 条种子数据，回到样例 A 的前提。

---

## 证据清单

| 文件 | 内容 |
|---|---|
| `screenshots/01_workflow_canvas.png` | Dify 工作流画布，15 节点全览 |
| `screenshots/02_new_invention.png` | 新发明「保鲜告白贴」的完整交互 |
| `screenshots/03_hit_existing.png` | 同一会话内命中「猫波炉」 |
| `evidence/seekdb_query.txt` | seekdb 直接查库的原始输出：总数、表结构、索引、查重、全文命中分数 |
