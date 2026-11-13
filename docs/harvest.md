# 采集语义

harvest 把一次来源窗口(ListRecords 与 metadataPrefix、set、from、until 的组合)记为一次扫描。扫描内按 resumptionToken 原样续用翻页;token 过期(badResumptionToken)不算失败,在同一扫描内重开窗口继续,已落下的成员证据保留。noRecordsMatch 是空窗口,扫描正常完成;badArgument 等其他错误使扫描失败,参数修正前重跑只会得到同样的失败。断点进度只随已接收的页在同一事务里确认;进程重启后按游标续行,同一游标位置的同内容重放不再落账,内容不一致会报错。

## 三个独立身份

- 来源记录:(来源, identifier)。只有协议中的 deleted header 能撤回它;集合退出、空窗口、其他来源的同名 identifier 都不影响。
- 集合成员关系:(来源, identifier, setSpec)。页内 setSpec 是成员证据;只有该集合的完整重扫(不带 from)完成且记录全程缺席才撤销,增量窗口缺席不算退出。撤销后再次见到 setSpec 证据会恢复成员关系。
- 元数据表示:(来源, identifier, metadataPrefix, datestamp, 正文摘要)。不同 metadataPrefix 互不覆盖;相同 datestamp 而正文不同作为冲突同时保留,不任选一份。

记录状态由观察推算:存在 deleted 观察且其 datestamp 不早于任何非删除观察时,记录为已删除。较旧记录迟到只增加证据,不回滚当前表示;当前表示取最大 datestamp,冲突整体呈现。到达时间只记在页上,不替代来源 datestamp。

## 查询

query 列出当前可检索记录(默认不含已删除记录,--all 含撤销痕迹),可按来源、identifier、集合过滤。explain 给出单条记录的成员变化、观察与表示冲突各自依据的页,撤销事件指向完成撤销的那次完整重扫。
