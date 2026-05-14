"""Embedding 模型封装 — BGE-M3。

BGE-M3 输出 1024 维稠密向量，~2GB 模型权重，首次使用时自动下载/加载。
"""

import logging

logger = logging.getLogger(__name__)


class EmbeddingModel:
    """BGE-M3 embedding 模型，本地部署，惰性加载。"""

    def __init__(self, model_name: str = "BAAI/bge-m3", use_fp16: bool = True):
        self._model_name = model_name
        self._use_fp16 = use_fp16
        self._model = None

    @property
    def model(self):
        if self._model is None:
            logger.info("Loading BGE-M3 model: %s (fp16=%s)", self._model_name, self._use_fp16)
            from FlagEmbedding import BGEM3FlagModel
            self._model = BGEM3FlagModel(self._model_name, use_fp16=self._use_fp16)
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        """将文本列表编码为 embedding 向量列表（每行 1024 维）。"""
        if not texts:
            return []
        output = self.model.encode(
            texts,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        dense = output["dense_vecs"]
        # 转为 list[list[float]]（可能已是 numpy array）
        if hasattr(dense, "tolist"):
            return dense.tolist()
        return dense


# ── 模块级单例 ──

_default_model: EmbeddingModel | None = None


def get_embedding_model() -> EmbeddingModel:
    """返回模块级单例 EmbeddingModel（惰性初始化）。"""
    global _default_model
    if _default_model is None:
        _default_model = EmbeddingModel()
    return _default_model


def set_embedding_model(model: EmbeddingModel) -> None:
    """注入自定义 EmbeddingModel（eval 用）。"""
    global _default_model
    _default_model = model
