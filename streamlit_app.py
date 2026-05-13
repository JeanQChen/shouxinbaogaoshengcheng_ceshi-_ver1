"""Streamlit 主入口 — 只负责 UI + 调 agent，不写业务逻辑。

UI 职责：
  - 接收用户输入（公司名、文件、模板选择）
  - 调用各 agent 并展示进度
  - Markdown 实时预览 + 回检标注展示
  - Word 下载按钮
"""


def main() -> None:
    raise NotImplementedError


if __name__ == "__main__":
    main()
