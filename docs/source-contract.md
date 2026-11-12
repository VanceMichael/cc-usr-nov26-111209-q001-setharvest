# 来源契约

本地演练使用 OAI-PMH 2.0 命名空间的 ListRecords、ListIdentifiers、Identify 和错误响应；记录元数据使用 oai_dc 与工程机构自有 eng 两种 metadataPrefix。不是通用 OAI 服务实现。相同 identifier 仅在同一来源内有意义，setSpec 为集合成员证据，不是来源记录的主键。

GET /oai?verb=Identify 返回 granularity 与 deletedRecord；/oai?verb=ListRecords&set=bridge&metadataPrefix=oai_dc&from=2026-11-01&until=2026-11-02 返回第一页；后续仅带 verb=ListRecords&resumptionToken=next%2B1。token 中加号必须编码。另有材料集合与第二来源 /partner/oai。from/until 包含两端，来源 advertised granularity 是 YYYY-MM-DD 或 YYYY-MM-DDThh:mm:ssZ。日期较旧的记录可在页中重现。

GET /control/phase?value=withdraw 将 bridge 集合移除报告但 materials 仍保留；value=deleted 后两集合能看到 deleted header；value=expired 让 token 请求返回 badResumptionToken。value=conflict 用相同 datestamp 返回不同元数据。控制接口仅用于演练，不是采集业务接口。

raw.xml 可直接查看；其他 xml 包含下一页、日精度窗口、秒精度来源、空结果、token 错误和冲突。响应可能使用默认命名空间，不保证前缀。metadata 内原文可为空，仅 deleted header 无 metadata。日期和元数据命名空间不是响应命名空间的替代。
