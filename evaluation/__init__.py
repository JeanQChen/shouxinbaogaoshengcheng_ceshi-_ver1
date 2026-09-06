"""V1 Retrieval Baseline Runner 评估模块。

对当前 retrieval.retriever.retrieve() 做逐题检索评价，判断正确本地
文档页是否进入 Top-K，并输出逐题结果、总体/分组指标、数据质量问题和
失败分析。本模块不修改 V1 Retriever/Indexer/Embedding 行为。
"""
