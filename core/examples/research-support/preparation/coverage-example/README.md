# 覆盖与收束小例

这组材料由项目编写，使用虚构的说明文本和补查计量，演示问题地图、来源阅读、主文和动作回执怎样连接。研究时用实际材料与观测替换。

## 先看三个版本

| 地图 | 材料状态与用途 | 预期下一步 |
|---|---|---|
| review-map-v1.json | 目标与评价已综合，缺失输入处理已读但未进入选择关系 | 在时间边界内先整理已读内容，新增材料上限为零 |
| review-map-v2.json | 保留首次地图，补上关键关系并记录动作结果 | 交付当前方法地图，软件实现仍列为非关键开放分支 |
| review-map-narrow.json | 作为另一条路线，将用途收窄到任务定义，保留原分支与排除理由 | 交付较窄的定向综述 |

v2 与 narrow 是从 v1 出发的两个可选出口。正式研究中，改变重要用途或方向时沿[研究推进主线](../../../../docs/methods/guides/research-guidance-cycle.md)讨论，其他补查按已有授权推进。

## 运行

复制本目录到新的研究工作目录。以下命令从 core 目录运行，将 REVIEW_ROOT 替换为所复制目录的绝对路径：

```console
python -B -X utf8 -m scripts.research_support.review_coverage --root REVIEW_ROOT --record review-map-v1.json --out checks/v1.json
python -B -X utf8 -m scripts.research_support.review_coverage --root REVIEW_ROOT --record review-map-v2.json --out checks/v2.json
python -B -X utf8 -m scripts.research_support.review_coverage --root REVIEW_ROOT --record review-map-narrow.json --out checks/narrow.json
```

第一次返回继续定向整理，后两次返回可按声明用途交付。每次使用新的输出位置。机器状态与完整参数见[接口](../../../../machine/design/review-coverage.json)。

## 怎样评议

逐项看条件和选择关系是否进入主文，引用是否支持这些关系，以及重要空白会不会改变用途。检查器核对显式记录与文件之间的衔接；主文摘录的科学含义仍需阅读判断。

本例的材料、时间与新增来源数量均为虚构。真实任务分别保留首次地图、第一次交付、补查实际计量和修订，并说明哪些证据改变或保留了决定。[操作指南](../../../../docs/methods/guides/review-coverage-and-closure.md)给出触发、合理替代和收束条件。
