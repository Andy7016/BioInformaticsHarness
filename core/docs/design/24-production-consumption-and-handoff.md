# D47：从真实产物到可接手的研究结果

日期：2026-10-20。本文总结三项真实交接中暴露的问题，并说明可复用的处理办法。

## 从实际接缝改进指导

- **先序列化，再由共享reader消费。** 用研究中的真实实体或位置表生成记录，让下游实际读取并计数。保留来源、ID和长度等关键信息；成熟reader、共享表接口或已有同版本结果都可以作为实现路线。
- **发布单一明确入口。** 发布者说明材料范围和用途，接收者根据给定入口和项目根目录读取。已有不可变数据存储或成熟发布平台可承担相同工作。
- **把程序对象说清楚。** 解释全长entity、位置、序列和天然蛋白之间的关系，让人知道每项程序记录对应什么研究对象。

这三项做法将原来抽象的字段要求落实到真实接缝。研究者根据交接风险选择入口，不需要所有项目使用同一种发布工具。

操作示例见[生产—消费接口](../methods/guides/artifact-producer-consumer.md)和[接手研究操作](../methods/guides/receiving-research-work.md)。历史设计和案例来源见[统一索引](../../../docs/REFERENCES-AND-DESIGN.md)及[机器文档记录](../../../machine/document-notes/core/docs/design/24-production-consumption-and-handoff.md.json)。
