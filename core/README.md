# BioInformaticsHarness core 0.1.0-alpha.2

可独立使用的生物模型研究原型：条件方法指南、研究关系与修订检查、工件/交接工具，以及标准库可运行的虚构小例。按照研究问题选择工具和方法，不要求每项研究填写所有表格。

先读 [QUICKSTART](QUICKSTART.md)，再按 [领域方法入口](docs/methods/README.md) 从研究对象、测量和主张出发。涉及真实执行时可用 [计划封存与首次审计](docs/methods/guides/plan-seal-and-first-execution.md)；对外分发时按 [发行交接](docs/methods/guides/publication-handoff.md)说明许可。

本版增加规范计划封存、执行前代码/配置捕获和许可交接。bounded runner的资源采样需使用者另备psutil；其他核心命令只依赖Python 3.11+标准库。公开仓库另有两个真实UCI保存状态消费例，安装需求和许可由它们的独立清单说明，不在这个core包内。

[RELEASE-MANIFEST.json](RELEASE-MANIFEST.json)逐文件记录来源、变换、身份和许可；[verify_release.py](verify_release.py)核当前文件。先验包，再运行会在自己的`.scratch/`下写输出的演示。

工具通过说明相应结构或计算检查通过；研究判断还要结合单位、划分、信息用途与实际结果。[证据范围](EVIDENCE.zh.md)说明已经做过什么，参考文献见 [REFERENCES.md](REFERENCES.md)。排除档案的 [导航指针](docs/ARCHIVE_POINTERS.md)供追溯来源使用，不是运行依赖。
