---
name: document-analysis
slug: document-analysis
description: "复用 Yuxi OCR/文档解析链路，将 PDF、Office 和图片附件提取为 Markdown。"
---

# 文档与图片分析

当用户上传 PDF、Office 文档或图片并要求提取内容时：

1. 使用 `ocr_parse_file` 读取当前沙箱中的附件。
2. 先返回简短摘要；需要展示完整 Markdown 文件时再调用 `present_artifacts`。
3. 不写入 uploads 或 workspace 的源文件，不绕过沙箱路径校验。
