.PHONY: setup run demo-data eval test clean clean-db help

# 默认 target
help:
	@echo "Available commands:"
	@echo "  make setup        - 安装依赖、初始化数据库、下载 BGE-M3 模型"
	@echo "  make run          - 启动 Streamlit 应用"
	@echo "  make demo-data    - 预处理演示样本（解析 + 向量化 + 缓存）"
	@echo "  make eval         - 运行 evaluation harness"
	@echo "  make test         - 运行单元测试"
	@echo "  make clean        - 清掉 data/cache, data/chroma, logs/"
	@echo "  make clean-db     - 清掉 data/credit.db"

setup:
	python -m pip install -r requirements.txt
	mkdir -p data/cache data/chroma data/samples/300750/financial data/samples/300750/announcements data/samples/300750/industry
	mkdir -p logs/retrieval logs/llm
	python -c "from financial.db import init_db; init_db()"
	python -c "from FlagEmbedding import BGEM3FlagModel; BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)"
	@echo ""
	@echo "✓ Setup complete."
	@echo "Next: copy .env.example to .env and fill in API keys."
	@echo "Then: prepare sample data per README.md, and run 'make demo-data'."

run:
	streamlit run streamlit_app.py

demo-data:
	python -m scripts.prepare_demo_data --company 300750

eval:
	python -m evals.run_evals

test:
	pytest tests/ -v

clean:
	rm -rf data/cache/* data/chroma/* logs/retrieval/* logs/llm/*
	@echo "✓ Cleaned cache, chroma, logs."

clean-db:
	rm -f data/credit.db
	python -c "from financial.db import init_db; init_db()"
	@echo "✓ Database reset."
