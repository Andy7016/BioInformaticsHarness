# 第三方材料、来源与分发范围

本core alpha.2分发项目自身的研究指南、Python工具、测试和虚构夹具；真实UCI保存模型消费例由公开仓库另以独立清单分发。具体文件以`RELEASE-MANIFEST.json`为准；本项目许可范围见[LICENSING.md](LICENSING.md)。

## 包内材料

| 材料 | 来源与处理 | 许可/归属 |
|---|---|---|
| 研究支持/校验工具、测试、虚构夹具 | 项目自身实现；无第三方生物模型实现或vendored Python依赖 | Apache-2.0，逐文件映射 |
| 原创方法指南、说明及精选核验摘要 | 项目自身整理；学术思想、事实和读出保留出处，不收录原论文全文或图版 | CC BY 4.0；文档内可执行示例另可按Apache-2.0使用 |
| Apache-2.0与CC BY 4.0法律文本 | 从各官方站点取得，按原字节保留 | 上游法律文本，非项目原创；不得用项目版权覆盖 |
| `describe_entities.py` | 项目自行编写的RCSB/PDB实体元数据解析例；包内测试数据由项目虚构 | 程序Apache-2.0；实际数据库数据另行取得 |

## 单独取得的外部资源

| 资源 | 已核信息 | 本core包的处理 |
|---|---|---|
| Optimus 5-Prime论文/SI/作者notebook | [Sample等，2019](https://doi.org/10.1038/s41587-019-0164-5)；本地保存作者稿、补充和官方代码。本次未取得足以统一授权其notebook/全部附属材料再分发的许可记录 | 只提供引用和本项目核验摘要；不收全文、补充或作者代码 |
| Optimus GEO测量表 | [GSM3130435](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM3130435)、[GSM3130443](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM3130443)；公开数据的读取来源和版本在私有研究回执中另存 | 不收测量表、序列、逐行预测或训练checkpoint；使用者从原来源核条款后取得 |
| gkm-SVM官方实现及修复副本 | [作者站点](https://www.beerlab.org/gkmsvm/)，本地源码及文件头确认GPL-3.0；研究使用的源包commit locator为`bdfcb90e32cae95ec3520039aa4412005f6f8b09` | 不收官方/修改源码、patch、二进制、核矩阵或模型；不把GPL材料改授Apache |
| gkm-SVM论文及数据 | [Ghandi等，2014](https://doi.org/10.1371/journal.pcbi.1003711)，[更正](https://doi.org/10.1371/journal.pcbi.1004035)；作者FASTA/BED及论文材料各有来源，不能据论文开放许可概括所有代码/数据条款 | 仅引用与项目摘要；不收作者序列、坐标或全文/SI |
| S4PRED源码 | [论文](https://doi.org/10.1093/bioinformatics/btab491)，[上游实现](https://github.com/psipred/s4pred)，本地源码commit为`7f309dfc9a8155efdf30113fb4cb0affb40aeb7e`，GPL-3.0 | 不收源码或模型实现 |
| S4PRED权重 | [作者权重归档](https://bioinf.cs.ucl.ac.uk/downloads/s4pred/weights.tar.gz)可定位；本地获取范围内未独立确认权重专属许可 | 不收权重或以其为依赖的开箱演示 |
| RCSB/PDB实际数据 | [RCSB](https://www.rcsb.org/)；程序只引用格式字段语义，本包没有真实实体JSON、坐标、序列或派生标签 | 按使用者的具体研究另核数据和来源；包内测试用虚构对象 |
| Python | [Python官方许可](https://docs.python.org/3/license.html)；使用者另备Python 3.11+，其自身及所含组件保持原许可 | 不捆绑解释器；离线core工具仅用标准库 |
| PyTorch/NumPy/pandas/绘图库及生信服务 | 用于部分真实研究，或作为指南中的可选外部工具 | 不捆绑或安装；core最小演示不依赖它们 |

原研究中作者辅助代码、下载数据、训练状态、论文全文与原始运行/会话记录保存在研究档案。精选包使用显式allowlist，不从这些目录递归取材。学术引用表示来源；不代表作者或机构认可本项目。

## 核验方式

包内清单记录每个文件的实际bytes/SHA、来源和必要的发布版修改。首次验包应在运行演示前执行；新使用者在自己的目录产生的环境/绝对路径属于其运行观察，不作为项目预先分发的机器资料。公开分享这类后续回执时，应按该回执的实际内容另行整理。

正文链接中的历史档案只承担来源导航。随包可运行的命令和所需输入由QUICKSTART逐项给出；引用资源的获取、原模型训练和完整论文复现各按相应研究另行准备。

## 2026-10-21：完整仓库的归属补充

独立包冻结后，已从 Optimus 作者官方仓库确认 GPL-3.0，并核同历史档案实际摘录的九份 notebook。上表“本次未取得”的表述保留独立包整理时点；现行档案归属以 [THIRD_PARTY_ARCHIVE.md](docs/ARCHIVE_POINTERS.md#archive-pointer-57913d813647fd12) 和[逐文件来源核验](docs/ARCHIVE_POINTERS.md#archive-pointer-e730b8dea2ba71f4)为准。该补充没有将作者代码纳入 core 包，也没有改变其已验收的 ZIP 或许可清单。

## 2026-10-21：alpha.2 公开 checkpoint 新增范围

| 材料 | 一手来源与归属 | 本次分发处理 |
| --- | --- | --- |
| UCI Molecular Biology (Splice-junction Gene Sequences) | [数据页](https://archive.ics.uci.edu/dataset/69/molecular-biology-splice-junction-gene-sequences)、[DOI](https://doi.org/10.24432/C5M888)；G. Towell、M. Noordewier、J. Shavlik | 公开案例保留官方归档和少量派生行；CC-BY-4.0；按行选择与修改另记 |
| UCI Yeast | [数据页](https://archive.ics.uci.edu/dataset/110/yeast)、[DOI](https://doi.org/10.24432/C5KG68)；Kenta Nakai、Paul Horton，字段角色按原元数据说明 | 官方归档及一个留出fold派生表；CC-BY-4.0；预计算特征的原训练重叠未知保持 |
| 本项目的两个 sklearn 保存参数 | 原研究使用官方UCI归档自行从零拟合，非借用作者预训练权重 | 新增参数/封装按Apache-2.0；状态中源数据/支持向量等保留CC-BY-4.0，混合scope逐文件标明；本次只消费，无重训 |
| NumPy / SciPy / scikit-learn / joblib / psutil | 使用者另行安装，实际版本进入运行回执 | 不捆绑源码、轮子或二进制；core通用检查仍仅用标准库，模型例和bounded资源采样另有依赖 |

上表的两个真实案例不属于旧alpha.1的虚构演示范围。它们有独立许可/来源清单，与core的RELEASE-MANIFEST分别验收。新公开Git仅继承已筛选的安全历史，不引入私有旧历史。未列的其他数据、论文全文、外部模型权重和原始会话仍不分发。
