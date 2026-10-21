# 研究操作支持最小示例

这些输入全部虚构，用来试用路径、角色与有限矩阵整理，没有生物学结论。从仓库根目录运行；root是唯一的清单/输入/输出路径基准。输出新建，重跑请换名称。

~~~text
python -m scripts.research_support.handoff --root . --manifest examples/research-support/handoff.json --actor demo-producer --phase producer --out .scratch/research-support/producer.json
python -m scripts.research_support.diagnostics --root . --plan examples/research-support/diagnostic-plan.json --matrix examples/research-support/diagnostic-matrix.tsv --out .scratch/research-support/diagnostic.json
python -m scripts.research_support.evidence --root . --manifest examples/research-support/evidence.json --out-dir .scratch/research-support/evidence
~~~

诊断例预计base残差为u2/u3，u3无候选相容，u2有另一候选局部相容；没有共同全相容候选。base与alias当前面板向量相同，这不证明通用数学等价或历史机制。

此handoff例没有环境要求。需要预检时填入真实environment：python绝对路径、root内cwd、imports、required=true和timeout_seconds，再加--probe。接手者自己另运行phase=receiver；不要为演示替其生成检查。

真实[DeepSEED回归与mRNABERT迁移](../../docs/ARCHIVE_POINTERS.md#archive-pointer-d08da09bce056944)另存来源和实际运行，勿把这个虚构例当研究证据。
