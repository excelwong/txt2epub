# txt2epub — TXT 转 EPUB 电子书

把一个目录（含子目录）下的所有 txt 文件合并转换为一个 EPUB 电子书，
并保持原目录结构。tkinter GUI，纯标准库实现（tkinter + zipfile + xml），
无需安装任何第三方包。

## 用法

双击 `txt2epub.bat`，或：

```bash
python txt2epub.py
```

## 功能

- 每个 txt 文件 → EPUB 中一个章节（xhtml），相对路径保持原目录层级
- 自动生成两级目录（Nav / NCB）：一级 = 子目录名，二级 = txt 文件
- 自动识别章节标题，书名/作者信息可在界面填写
