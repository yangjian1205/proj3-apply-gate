# units/jd-intake · JD 接入

## 我负责什么

把三种输入统一成一种结构，交给后面的单元：

| 输入 | 例子 |
|---|---|
| 粘贴的 JD 纯文本 | `python main.py judge --jd "岗位职责：……"` |
| JD JSON 文件 | `data/jd/jd-007.json`（评测集就是这个格式，一举两得） |
| 批量目录 | `python main.py judge --batch data/jd/` |

输出字段：`jd_id / company / title / text / source_url / fetched_at / stale / city / days_old`

## 三条硬规矩

1. **`text` 一个字都不许清洗。** 合并空格、删换行、全角转半角都会破坏 `quote` 的「是原文子串」这个性质，
   直接导致硬指标①的自动校验失效。原始文本原样保留。
2. **字段缺失直接报错退出**，不许用「未知公司」糊过去。
   唯一例外：粘贴文本时 `company` / `title` 允许标成 `未标注`，但会在判定报告里显示出来提醒你。
3. **超期只提示不拦。** `fetched_at` 超过 90 天标 `stale=true`，
   提醒「该 JD 可能已失效」，但**不影响判定结果** —— 是否还招人是人的判断，不是我的。

## 不归我管的

- 我不判断这个岗位值不值得投（那是 hard-gate 与 match-score 的事）
- 我不猜 JD 里没写的信息（城市没写就是没写，标成 `未标注` 交给下游出「待确认」）
- 我不做爬虫。输入端只有粘贴与文件两种，这是项目的既定边界。

## 错误策略

| 情况 | 行为 |
|---|---|
| `text` 为空或少于 20 字 | 抛 `IntakeError`，拒绝给出判定 |
| JSON 文件不是合法 JSON | 抛 `IntakeError`，指出具体位置 |
| `fetched_at` 格式非法 | 抛 `IntakeError` |
| `fetched_at` 超 90 天 | 只标 `stale`，继续 |
| JD 没写城市 | 标 `city=""`，继续（下游会因此不出硬拦截，这是故意的） |

## 自己的用例

`cases.json` —— 字段缺失、超期 JD、粘贴纯文本、空文本四类。
改本单元只需跑这里的用例，不用跑 60 题全量集（那是端到端的事）。
