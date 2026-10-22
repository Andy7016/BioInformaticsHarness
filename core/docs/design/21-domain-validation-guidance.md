# D44：按科学问题设计条件验证

日期：2026-10-18。本文记录三类领域验证指南的设计理由：验证什么，以及什么结果会改变结论。

## 选择适用指南

- **功能与效用：** 先定义目标构念和可观察读出，再选择对照、独立单位和实际使用界限。功能、效用与机制主张需要不同的结果。
- **序列新颖性：** 说明比较对象是全长序列、局部区域还是模板；明确搜索背景和覆盖范围，再解释相似性或富集结果。
- **结构几何：** 先确定原子与装配范围，再选择精修、碰撞、单角扰动或整体误差比较，并保留其分母。

每种方法只在触发条件成立时使用。可以选择适合研究目标的替代方法，但要说明它能回答什么、哪些结论仍需要额外证据。

完整操作见[功能与效用验证](../methods/guides/function-and-utility-validation.md)、[序列新颖性与匹配背景](../methods/guides/sequence-novelty-and-background.md)和[结构几何与扰动](../methods/guides/structural-geometry-and-perturbation.md)。设计讨论与来源见[统一索引](../../../docs/REFERENCES-AND-DESIGN.md)和[机器文档记录](../../../machine/document-notes/core/docs/design/21-domain-validation-guidance.md.json)。
