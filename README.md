# setharvest

机构资料 OAI-PMH 响应的查看、原文留存与请求实验工具。

## 现有能力

inspect 查看一份源文件的结构；capture 发起一次本机 HTTP 请求并保存响应状态、原始字节和摘要；show 列出接收记录。原文对象按 SHA-256 共用，独立请求分别记账。错误 HTTP 状态也会保存，原文归档不等于业务接收完成。

harvest 对一个来源窗口持续翻页采集，断点进度只随已接收的页一起确认，进程重启或失败后可按原游标续行。集合成员关系、来源记录与元数据表示各有独立身份：只有某集合的完整重扫确认缺席才撤销该处成员关系（增量窗口缺席不算退出，退出也不是删除）；只有 deleted header 才撤回来源记录，且不影响其他来源的同名 identifier；不同 metadataPrefix 的表示互不覆盖，相同 datestamp 的不同正文作为冲突同时保留。resumptionToken 按来源原样续用，过期后在同一扫描内重开窗口，已落下的成员证据保留；noRecordsMatch 是空窗口，badArgument 是请求失败，二者与 badResumptionToken 分别处理。详细语义见 `docs/harvest.md`。

query 查看当前可检索表示（默认不含已删除记录，--all 含撤销痕迹）；explain 给出单条记录的成员变化与表示冲突各自依据的页。

## 运行

使用 Python 3.11 或更新版本，仅依赖标准库，无安装步骤。全部示例均为本地合成资料，不包含真实个人信息、商业凭据或线上地址。

```sh
python3 -m setharvest inspect setharvest/fixtures/raw.xml
python3 -m setharvest demo
python3 -m unittest discover -s tests -v
python3 -m compileall -q setharvest
```

需要手动发请求时运行 `python3 -m setharvest.source_server --port 8765`，再在另一终端运行 `python3 -m setharvest capture http://127.0.0.1:8765/sample`。默认会在当前目录创建 captures.sqlite3 和 objects；可通过 --db 和 --objects 指定独立位置。演示自己启动源服务，使用随机本机端口，并在退出时清理临时目录。

持续采集与查询（源服务仍在 8765 端口时）：

```sh
python3 -m setharvest harvest --source http://127.0.0.1:8765/oai --prefix oai_dc --set bridge
python3 -m setharvest harvest --source http://127.0.0.1:8765/oai --prefix oai_dc --set bridge --from 2026-11-02 --until 2026-11-02
python3 -m setharvest query --set materials
python3 -m setharvest explain --source http://127.0.0.1:8765/oai --identifier report:7
```

harvest 与 capture 共用默认库文件和原文目录。--max-pages 限制单次运行的翻页数，便于断点演练；未完成或失败的扫描按相同窗口参数再次运行即续行。

## 模块

`setharvest/parser.py` 负责单文档观察，`archive.py` 保存不可变原文与请求事实，`harvest.py` 维护扫描游标、成员关系与表示的持久化采集，`transport.py` 限制实验请求到本机，`source_server.py` 提供来源示例。来源边界及字段含义见 `docs/source-contract.md`，采集语义见 `docs/harvest.md`。这些入口不会声称一次响应已经构成完整目录或业务批次。

## 容器

Dockerfile 使用 Python 3.11 标准库镜像，不需要应用依赖下载。可用 `docker build -t setharvest:local .` 构建，再执行 `docker run --rm --network none setharvest:local`。本地 HTTP 与 PTY 演练不需要外部网络，测试及单次演示通常数秒完成。
