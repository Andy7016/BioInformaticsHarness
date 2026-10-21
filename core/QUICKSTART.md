# Quickstart

解压后在包根目录运行（Python 3.11+）。核心入口只使用 Python 标准库；不安装依赖、不
联网、不需要 GPU、账号、论文全文、模型权重或私有研究档案。下面的输入
来自 `examples/research-support/`，全部是虚构数据。

## 三个最小操作入口

```text
python -B -m scripts.research_support.handoff --root . --manifest examples/research-support/handoff.json --actor demo-producer --phase producer --out .scratch/research-support/producer.json
python -B -m scripts.research_support.diagnostics --root . --plan examples/research-support/diagnostic-plan.json --matrix examples/research-support/diagnostic-matrix.tsv --out .scratch/research-support/diagnostic.json
python -B -m scripts.research_support.evidence --root . --manifest examples/research-support/evidence.json --out-dir .scratch/research-support/evidence
```

预期范围：handoff 会记录虚构 producer 的当前观察；diagnostics 会报告
`u2/u3` 残差、局部候选相容与没有共同全相容候选；evidence 会按显式文件
生成同源索引。输出目录必须是新的；`.scratch/` 是用户自己的临时输出，
不要把它当作包内证据。

## 独立 receiver 重跑

生产者命令结束后，接收者应自行从同一份显式清单重跑：

```text
python -B -m scripts.research_support.handoff --root . --manifest examples/research-support/handoff.json --actor demo-receiver --phase receiver --out .scratch/research-support/receiver.json
```

这两个输出分别代表两个实际观察；第一个命令不会替接收者生成签字或
证明历史消费已经完成。需要环境预检时，按 handoff 的 `environment` 契约
提供包内 root 相对 cwd、明确解释器和有限超时，不自动安装或重试。

## 指纹反例与证据边界

文件 SHA-256 或工具 fingerprint 只说明被读取的字节身份。两个语义等价
但字节排版不同的表可以有不同指纹；一个指纹相同也不说明样本独立、标签
正确、模型等价或论文结果已经复现。因此应同时检查输入用途、统计单位、
测量定义、真实消费和科学改判条件。任何 CLI `pass` 都只表示本工具范围
的检查通过，不是科学接受。

## 验证发布包

```text
python -B verify_release.py
```

验证器会核对清单中的每个文件、拒绝路径穿越和符号链接，并拒绝清单外
的未知 payload；清单本身只标记为 `self_hashed: false`，不对自己做循环
哈希。

## 标准库核心测试

包内测试也只使用标准库。请让 `PYTHONDONTWRITEBYTECODE=1` 传递到整个测试
进程树，避免测试或其子进程在受清单保护的源码树中写入 `__pycache__/`；这项
环境设置只用于本次测试。两个测试目录需分别发现；Python 3.11不会自动递归
这里没有`__init__.py`的命名空间子目录，直接使用`-s tests`会漏测。POSIX shell 示例：

```text
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s tests/research_support -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python -B -m unittest discover -s tests/verification -p 'test_*.py' -v
```

PowerShell 示例（命令结束后恢复当前进程环境）：

```powershell
$harnessPreviousBytecode = $env:PYTHONDONTWRITEBYTECODE
try {
    $env:PYTHONDONTWRITEBYTECODE = "1"
    python -B -m unittest discover -s tests/research_support -p "test_*.py" -v
    python -B -m unittest discover -s tests/verification -p "test_*.py" -v
} finally {
    if ($null -eq $harnessPreviousBytecode) {
        Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONDONTWRITEBYTECODE = $harnessPreviousBytecode
    }
}
```


## 封存和首次审计

- [计划封存小例](examples/research-support/plan-seal/README.md)：从明确plan-root生产READY，再按相同契约检查和封存，修订使用新目录。
- [首次审计小例](examples/research-support/first-audit/README.md)：显式列明代码/配置/输入，在子进程启动前保存快照。实际资源采样需另备psutil；不捆绑环境或自动安装。

上述新增工具的测试另在`tests/`根下发现：

```text
python -B -m unittest discover -s tests -p 'test_*.py' -v
```

同样向子进程传递`PYTHONDONTWRITEBYTECODE=1`。缺可选资源采样库时，相关测试应列出skip，不能记为实际资源采样通过。根目录发现不会替代上面两组子目录的测试。

真实UCI例在公开仓库`examples/`另行说明，消费已训练参数并与固定留出输出比较。本core包不包含其依赖或实际数据。
