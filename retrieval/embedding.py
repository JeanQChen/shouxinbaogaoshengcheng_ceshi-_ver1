"""Embedding 模型封装 — BGE-M3。"""


class EmbeddingModel:
    """BGE-M3 embedding 模型，本地部署，~2GB 模型权重。"""

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        raise NotImplementedError

    def encode(self, texts: list[str]) -> list[list[float]]:
        """将文本列表编码为 embedding 向量列表。"""
        raise NotImplementedError
