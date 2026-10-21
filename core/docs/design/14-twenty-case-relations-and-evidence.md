# 二十案例驱动的选择、信息用途与消费证据

日期：2026-10-14。来源：用户要求判断三类问题是否得到20项工作共同支持，并综合迭代。[证据矩阵](../ARCHIVE_POINTERS.md#archive-pointer-fcbf761eb8801cea)逐工作登记支持程度；[实施计划](../ARCHIVE_POINTERS.md#archive-pointer-5aecc0ea5dc3e165)固定本轮接口与验收。

## 判断与适用条件

前三轮已指出组件角色、选择事件、可见信息、实际算子和输入贡献的关系问题；第四轮进一步给出候选筛选、多lambda编辑、家族先验与调控图的机制。三类问题属于跨机制的工程共性候选，不能称20篇均有同一缺陷或20次独立验证。一个案例的作者设计、公开材料不足、当前源码观察与Harness缺口分别登记；旧评分、材料边界与输入暴露不被共同计数消除。

本轮继续使用ResearchQuestion、DataSnapshot、SplitPlan、MethodCard、Run、Artifact、ComparisonSpec、Claim与Review。新增内容是这些对象之间的条件关系视图。没有筛选不要求制造选择链，没有独立性主张不要求额外封存测试，纯静态研究不要求编造runtime回执。

## D27：阶段选择与信息用途

选择链记录每阶段的单位、输入、入选、排除、失败、未观测及规则来源；单位改变时记录父子映射，阶段之间不再转入的项解释去向。分母绑定具体阶段及其输入/入选集合。这样可以分别解释“百万候选”“送测100条”“成功66条”，而不要求每项研究都执行这三个阶段。

信息用途记录资产与来源、作用阶段及fit/tune/select/prior/condition/evaluator_fit/inference/evaluate/interpret用途。独立性约束依具体主张指定受保护的X/y/参考及禁止用途；沿声明的资产来源和阶段祖先检查影响。合法传导式全集X、开发性反复选择和预先声明的外部知识仍允许。程序不自动发现同源性或未声明信息路径。

相应条件指导为[逐阶段选择](../methods/cards/stage-selection.md)和[信息用途](../methods/cards/information-use.md)。研究专用阈值、AND/OR谓词、同源聚类、候选排序、图先验与动力学算法保留在版本化领域方法中。

## D28：消费证据分级与真实小工件

消费要求以stage、role、artifact绑定期望与回执，并区分：

|证据|支持的判断|仍需另证|
|---|---|---|
|declared|配置或报告表达了意图|实际执行|
|source_inspected|某固定源码路径存在|本次是否运行该路径|
|artifact_checked|采集器读到的工件字节与SHA身份|原模型是否读到、是否作用|
|runtime_read|指定采集/执行上下文确实解析或读入|参数是否载入并参与目标模型|
|parameters_applied义务|需要框架调用、载入诊断与目标模型作用的证据|本轮通用采集器不提供，保持unknown|

新增[真实工件采集器](../../scripts/verification/collect_artifact_evidence.py)只读取显式指定的小CSV/TSV或二进制文件，默认20MiB上限；哈希与表格解析来自同一份实际字节。它不加载模型、不反序列化权重、不执行文件内容。CSV解析的runtime_read只属于采集器本身，不能倒灌为历史推理行为。没有稳定ID时采用文件SHA+行定位，并保留跨文件alignment unknown。

远程模型或大型checkpoint可继续绑定provider revision、来源和外部执行回执；本地小文件检查不是全局强制下载/哈希方案。若本轮接口不能验证必要层级，应报告unknown或调用版本化适配器，不能降低科学义务以制造通过。

## D29：独立接口与能力边界

新增[research-flow-v1](../methods/flow-contract.md)，并列于原handoff-v1/relations-v1，不改变历史接口语义。它检查选择出口、阶段谱系和分母、声明的信息用途，以及消费回执等级；报告始终标明provided_manifest_only与scientific_acceptance=not_assessed。

有真实文件采集回执也不证明所有历史完整。独立评价还需核对实际信息路径；权重作用需框架适配器；指标数值与科学充分性由相关方法或Review承担。原案例E/G/V评分不重写，本轮也不把20例不同口径合并为能力总分。

评价中的V范围分为记录一致性、内容/执行检查和科学判断：人工判断本身可以不适用，但其对象/版本/引用关系仍可能产生独立可检义务。全空集合的数学互斥不能支持“已提供真实划分证据”；新选择接口对空必要集合保留unknown，旧接口的历史行为仍保留。详见[复核回执](../methods/review-receipt.md)。

## 验收与历史

验收要求正例、反例、unknown和合法替代；包括阶段间流失、跨单位一对多、未来阶段用途、传导式输入、共享工件、角色/摘要冲突、声明冒充运行、参数作用未知及真实小CSV。旧41项检查器测试保持回归。

来源与设计、实现和实际验收连接于[本轮迭代入口](../ARCHIVE_POINTERS.md#archive-pointer-c5099c1af4447516)。这不是已完成原模型复现、20例同口径重评或人的科学接受。
