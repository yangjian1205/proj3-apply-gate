# data.json · 工作台与后端之间的唯一契约

> 这份文件冻结之后，**工作台**和**后端七个能力单元**就可以并行开发，不用互相等。
> 规则：后端只负责产出这份结构；工作台只负责渲染这份结构。谁都不许偷偷加字段。

- 生成方式：`python scripts/build_workbench.py --in data/workbench_snapshot.json --out data/generated/dashboard.html`
- 示例快照：`workbench/data.sample.json`
- 校验：构建脚本会检查顶层字段、`records` 数组、以及每条记录的必填字段，缺字段直接报错退出

---

## 一、顶层结构

| 字段 | 类型 | 谁产出 | 说明 |
|---|---|---|---|
| `generated_at` | string | build_workbench | 快照时间，格式 `YYYY-MM-DD HH:MM`。**界面必须显示**，否则你不知道看的是不是旧数据 |
| `source.db` | string | build_workbench | 快照来源（如 `data/audit.db`） |
| `source.rules_version` | string | hard_gate | 规则版本，如 `42 条规则 / v1` |
| `source.records` | int | build_workbench | 记录条数 |
| `stats` | object | build_workbench | 顶部统计条（见下） |
| `records` | array | 各单元汇总 | 一行一个 JD |

## 二、`stats`

| 字段 | 类型 | 说明 |
|---|---|---|
| `total` | int | JD 总数 |
| `apply` / `reject` / `borderline` | int | 三分类各自条数 |
| `pending` | int | 待审批条数（**最需要人动手，界面放最显眼**） |
| `cost` | float | 累计成本（元） |

## 三、`records[]`

| 字段 | 类型 | 谁产出 | 界面用在哪 |
|---|---|---|---|
| `jd_id` | string | jd_intake | 行标识、详情标题 |
| `company` / `title` / `city` | string | jd_intake | 列表主/副标题、详情头 |
| `fetched_at` | string | jd_intake | 详情头「抓取 YYYY-MM-DD」 |
| `stale` | bool | jd_intake | 为 true 时详情头标「可能已过期」 |
| `verdict` | enum | hard_gate / match_score | **对外三分类**：`apply` / `reject` / `borderline` |
| `verdict_5` | string | match_score | **对内五态**：已匹配 / 表达缺口 / 证据不足 / 真实缺口 / 待确认 |
| `hard_flags` | array | hard_gate | `[{rule_id, quote}]`；`quote` 是 **JD 原句**，界面拿它去原文里高亮 |
| `score` | object | match_score | `{relevant, level, skill}`，各 10 分制 |
| `reasons` | array[string] | match_score | 判定理由，逐条显示 |
| `jd_text` | string | jd_intake | **JD 原文全文，必须原样保留** |
| `draft_path` | string \| null | tailor | 改写稿路径；`null` = 没改稿（不该投不改） |
| `resume_base` | array | 简历账本 | `[{n, text}]` 原简历按行（**只读**） |
| `draft_lines` | array | tailor | `[{n, text, atoms[]}]` 改写稿按行；`atoms` 标出该行里的事实原子 |
| `atoms[]` | array | provenance | `{atom, type(A/B/C), found, where}` |
| `evidence_map` | array | provenance | 全部事实原子 → 出处，同上结构 |
| `missed` | array[string] | provenance | 找不到出处的原子清单 |
| `rewrite_round` | int | provenance | 重写轮数（上限 3） |
| `cost` / `elapsed` | float | cost | 本条花多少钱、多少秒 |
| `ticket_id` | string \| null | approval | 审批单号 |
| `approval_status` | enum | approval | `not_needed` / `pending` / `approved` / `rejected` |
| `audit` | array | 全链路 | `[{ts, from, to, note}]` 节点交接记录 |

---

## 四、三条不能违反的规则

### 1. `jd_text` 必须是原文，一个字都不许清洗

清洗（合并空格、删换行、全角转半角）之后，`hard_flags[].quote` 可能不再它的子串 → **高亮失效**，
而且硬指标①的自动校验（`quote` 必须是 `jd_text` 的子串）会直接挂。

### 2. `verdict` 和 `verdict_5` 必须同时给

- 只给 `verdict` → 界面上没法显示「表达缺口」这种有信息量的细分
- 只给 `verdict_5` → 你的 60 题评测口径就变了

**两个都存，界面上一起显示。** 三分类是对外接口，五态是内部精度。

### 3. `approval_status` 的来源是后端，不是这份快照

快照只是某一刻的照片。**界面显示「待审批」不等于后端真的挂着**（服务可能重启过，会话早就不在挂起态了）。
所以：批准动作必须走接口重新确认，绝不能靠读快照判断，更不能把审批结果写回本地存储。

这条是硬指标③（未审批不放行率 = 0%）的界面侧防线。

---

## 五、加字段的流程

1. 先问一句：**这个字段谁产出？**
2. 答不上来 → 不加（说明它还只是个想法）
3. 答得上来 → 改这份契约 + 改 `data.sample.json` + 改模板渲染，三处一起改
4. 跑 `python scripts/build_workbench.py`，报错就说明契约没对齐
