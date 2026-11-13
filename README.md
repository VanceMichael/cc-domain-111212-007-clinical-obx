# clinical-obx

检验观察报文的类型化采集所用的来源检查、原件留存与观察记录工具。

## 当前能力

`source.py` 查看来源的现有结构，不把检查结果冒充完整业务采集。
`hl7.py` 按来源约定解释 ORU^R01：MSH 自定义分隔符、HL7 转义、重复值、组件/子组件，以及 OBX-2 值类型（NM 数值、ST 文本、CE 编码、未知类型隔离）对 OBX-5 的含义。
`repository.py` 在本地 SQLite 建立类型化观察记录库：按发送机构、申请单、项目编码系统/编码与子观察号确定观察身份；相同报文重送幂等不增结果，更正（C）追加版本且旧值可回溯，撤销（D）只作用于对应观察而不删除同一患者的其他结果。单位与参考范围原样留证，无换算配置不自动转单位。
`storage.py` 将原始字节、来源标识、摘要和检查结果一起保存在本地 SQLite，重复原件保留同一收件编号。
`sampledata.py` 构造可重复的本地数据；`transport.py` 提供独立的回环 HTTP 演练源与请求记录。
解释范围和来源变体见 `docs/source-contract.md`，观察身份与去重原理见 `docs/observation-identity.md`，当前检查测试与将来新增业务能力的测试分开保留（`tests/test_source.py` 与 `tests/test_hl7.py`、`tests/test_repository.py`）。

## 环境

Python 3.11 或以上，仅标准库，无需凭据和外部数据。使用容器时可执行 `docker build -t clinical-obx .`，依赖及镜像须在正式运行前准备。
运行入口、测试与演示均不安装包，不访问公网。数据库使用临时路径，不向代码目录写入业务文件。

## 测试与构建

构建工具版本固定在 `requirements-build.txt`，在环境准备阶段执行 `python3 -m pip install -r requirements-build.txt`；容器构建已包含此步骤。应用运行仍仅使用标准库，测试及演示不安装依赖。

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q app.py source.py sampledata.py storage.py repository.py hl7.py transport.py
python3 -m build --wheel --no-isolation
```

## 快速演示

```sh
python3 app.py demo
```

演示真实启动本地来源、领取原件、执行检查并保存临时数据库，展示检查结果与重复收件身份；随后在独立临时库演示类型化采集的重送幂等、更正版本、子观察撤销、隔离原因与自定义分隔符重复值，退出后自动清理。正常在数秒内完成，不以预录日志代替执行。

## 单独检查、收件与类型化采集

```sh
python3 app.py fixtures /tmp/clinical-obx-fixtures
python3 app.py inspect /tmp/clinical-obx-fixtures/sample.hl7
python3 app.py capture /tmp/clinical-obx-fixtures/sample.hl7 --database /tmp/clinical-obx.sqlite3 --source local-partner
python3 app.py list --database /tmp/clinical-obx.sqlite3
python3 app.py collect /tmp/clinical-obx-fixtures/sample.hl7 --database /tmp/observations.sqlite3 --source local-partner
python3 app.py observations --database /tmp/observations.sqlite3 --include-cancelled
python3 app.py history --database /tmp/observations.sqlite3
```

`collect` 执行类型化采集并返回每个观察的身份、版本与状态；`observations` 列出当前观察（不含患者姓名，默认隐藏已撤销项）；`history` 输出含更正前旧值与撤销记录的完整版本线。生成夹具不会覆盖已有文件。运行错误以 JSON 输出到标准错误并返回退出码 2；原始输入无效时不生成成功收件。内部原件与任何对外展示副本应分开管理，不写入真实个人数据。
