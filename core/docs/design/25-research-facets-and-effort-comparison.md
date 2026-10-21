# D48：补充研究面，并在具体讨论节点比较推理强度

2026-10-20。来源为用户对三个研究面和下一项revisor验证的授权，以及[覆盖分析](../ARCHIVE_POINTERS.md#archive-pointer-d8bba86ce73ab351)、S4PRED中“共同组成与链内定位”的具体判别问题。

## 要解决的问题

当前Harness能组织目标、材料、方法、运行、产物和接受范围。进一步指导具体研究，还需帮助研究者选择能区分解释的比较，并说明什么结果足以改变结论。来源案例出现过某个词，并不意味着相应能力已经有完整指导；例如置信排序、概率校准和使用决定应分别检验。

细化设计还依赖辅助模型的判断能力。因此不能只因一次生成遗漏某步骤，就推断Harness文本缺失；也不能把更长、更复杂的报告当作更强指导。

## 本轮决定

1. **先补三个条件工作例。** 共同/特异信号用GEARS、Systema与线性基线评测联读；置信到行动用Evidential工作；动态解释用veloVI。出处与新增建议分开，保留用途、条件、选择理由、合法替代和科学改判。不把质心、加性模型、某个UQ算法或置换检验变成所有研究的固定要求。
2. **当前指导与新工作例分别识别。** 分支输入固定为34份原有方法文件及该节点的一手材料，新工作例不回填进本轮首稿。它们是本轮文献综合产物，其后续应用效果另行验收。原顶层对象、冻结评分和旧案例分母保持。
3. **使用新源的精确聊天分支。** 新memory-off源只准备中性事实；在完成的同一turn后，原源继续medium，两个分支使用high、xhigh，模型均为GPT-6.1 Sol。同节点材料与分析任务字节一致。逐线程核有效配置、实际上下文、继承边界与文件身份，保留首稿、修订和父方介入。
4. **前瞻revisor先冻结后揭示。** veloVI的动态专题和早稿审阅使用两个独立源。审阅首稿只读最早公开稿及对应补充；后续才引入公开评审、作者回复和发表版。最早预印本与送审稿的精确映射未知时保留该边界。
5. **按具体判断评价。** 逐目标、范围、选路与指南版本核来源、可执行取舍、判别力、改判及人读解释。路线完成与原目标覆盖分别记录；不设混合总分或推理档位总体排名。

## 接受与解释范围

文献支撑工作例按原件定位与引用核验；控制器/包的检查按实际技术接口验收；模型首稿及科学建议再对照独立证据。没有无Harness组和多次独立生成时，比较用于定位值得复核的差异，不估计Harness净收益或模型档位的普遍效应。

独立目录和memory-off可降低旧项目记忆干扰，不等于清除模型一般先验或建立完整操作系统隔离。实际配置、意外暴露、失败与容量等待在回执中报告。

## 追溯入口

- [三份条件工作例](../methods/worked-examples/README.md)
- [本轮问题、分支与资料边界](../ARCHIVE_POINTERS.md#archive-pointer-59bf7112ebbd9b75)
- [首稿前接受锚点](../ARCHIVE_POINTERS.md#archive-pointer-b1443d6250372332)
- [来源与完整引文](../ARCHIVE_POINTERS.md#archive-pointer-02f81d56dc027e5e)
- [父方介入记录](../ARCHIVE_POINTERS.md#archive-pointer-52e3e67f620c11ec)
- [执行与回收的当前入口](../ARCHIVE_POINTERS.md#archive-pointer-7bd64721fb7964ce)
- [回收综合与逐义务评估](../ARCHIVE_POINTERS.md#archive-pointer-c5c0ba326eddd49e)、[UQ](../ARCHIVE_POINTERS.md#archive-pointer-aed7965571f7eb3a)、[共同/特异信号](../ARCHIVE_POINTERS.md#archive-pointer-82235abf6ea8370b)、[动态](../ARCHIVE_POINTERS.md#archive-pointer-e8d1928ed9ac32ca)、[revisor](../ARCHIVE_POINTERS.md#archive-pointer-61ed88e83e1c09e9)
- [signal回收入口](../ARCHIVE_POINTERS.md#archive-pointer-9d3628d4463b4549)与[状态快照](../ARCHIVE_POINTERS.md#archive-pointer-471f30dc64fda147)
- [方法与附件引用检查](../ARCHIVE_POINTERS.md#archive-pointer-7e10fc2e5cfc1353)与[文档/JSON机械检查](../ARCHIVE_POINTERS.md#archive-pointer-b817f582c35b840c)
