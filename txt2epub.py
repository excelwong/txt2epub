# -*- coding: utf-8 -*-
"""
Txt2Epub - 将目录（含子目录）下所有 txt 文件合并转换为一个 EPUB 电子书，并保持目录结构。

- 每个 txt 文件 → 一个 EPUB 章节（xhtml），相对路径保持原目录层级
- 目录层级自动生成两级目录（Nav / NCX）：一级 = 子目录名，二级 = txt 文件
- 纯标准库实现（tkinter + zipfile + xml），无需安装任何第三方包
"""

import os
import re
import sys
import html
import zipfile
import threading
import datetime
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

UUID_NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


# ---------------------------------------------------------------- utilities

def is_txt(path: str) -> bool:
    return path.lower().endswith(".txt")


def read_text(path: str) -> str:
    """尝试多种常见编码读取 txt。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "utf-16"):
        try:
            with open(path, "r", encoding=enc) as f:
                data = f.read()
            # utf-16 误判会产生大量 NUL/奇怪字符，做个简单校验
            if enc == "utf-16" and data.count("\x00") > 0:
                continue
            return data
        except (UnicodeDecodeError, UnicodeError):
            continue
    # 兜底：忽略错误
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def split_paragraphs(text: str):
    """按空行分段，返回段落列表。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        # 没有空行就按单行分段
        paras = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return paras


def text_to_xhtml(title: str, text: str) -> str:
    paras = split_paragraphs(text)
    body = "\n".join(
        "    <p>%s</p>" % html.escape(p, quote=False) for p in paras
    ) or "    <p></p>"
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh" lang="zh">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        "  <title>%s</title>\n"
        "</head>\n"
        "<body>\n%s\n</body>\n</html>\n" % (html.escape(title), body)
    )


def safe_id(name: str) -> str:
    """把任意字符串转成安全的 id 片段。"""
    s = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").lower()
    return s or "item"


def nav_label(name: str) -> str:
    stem = os.path.splitext(name)[0]
    return stem if stem.strip() else name


# ---------------------------------------------------------------- EPUB builder

class EpubBuilder:
    """收集章节并打包为 EPUB 3。"""

    def __init__(self, book_title: str):
        self.book_title = book_title
        self.book_uuid = str(uuid.uuid5(UUID_NS, book_title))
        self.chapters = []  # (href, title, xhtml_content)
        self._used_hrefs = set()
        self._used_ids = set()

    def add_chapter(self, rel_path: str, text: str):
        """rel_path: 相对源目录的路径，如 sub/dir/xxx.txt"""
        norm = rel_path.replace("\\", "/")
        href = norm[:-4] + ".xhtml" if norm.lower().endswith(".txt") else norm + ".xhtml"
        # 防重名
        base, i = href, 2
        while href in self._used_hrefs:
            root, ext = os.path.splitext(base)
            href = "%s_%d%s" % (root, i, ext)
            i += 1
        self._used_hrefs.add(href)

        title = nav_label(os.path.basename(norm))
        cid = safe_id(os.path.splitext(os.path.basename(norm))[0])
        while cid in self._used_ids:
            cid += "_x"
        self._used_ids.add(cid)

        self.chapters.append((cid, href, title, text_to_xhtml(title, text)))

    # ---- 目录树（由章节 href 层级聚合）----
    def _tree(self):
        """返回嵌套目录树: {"sub": {"file1": (cid,href,title), ...}, ...}"""
        tree = {}
        for cid, href, title, _ in self.chapters:
            parts = href.split("/")
            node = tree
            for p in parts[:-1]:
                node = node.setdefault(p, {})
                if isinstance(node, tuple):  # 理论不会发生
                    break
            node[parts[-1]] = (cid, href, title)
        return tree

    # ---- 各 xml/xhtml 部件 ----
    def _container_xml(self):
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            "  <rootfiles>\n"
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>\n'
            "  </rootfiles>\n"
            "</container>\n"
        )

    def _nav_xhtml(self):
        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<!DOCTYPE html>",
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh" lang="zh">',
            "<head><meta charset=\"utf-8\"/><title>目录</title></head>",
            "<body>",
            '  <nav epub:type="toc" id="toc">',
            "    <h1>目录</h1>",
            "    <ol>",
        ]

        def emit(node, depth):
            files = sorted([(k, v) for k, v in node.items() if isinstance(v, tuple)],
                           key=lambda kv: kv[1][2])
            dirs = sorted([(k, v) for k, v in node.items() if isinstance(v, dict)])
            for name, (cid, href, title) in files:
                lines.append("%s<li><a href=\"%s\">%s</a></li>" % (" " * (4 + depth * 2), html.escape(href), html.escape(title)))
            for name, sub in dirs:
                lines.append("%s<li><span>%s</span>" % (" " * (4 + depth * 2), html.escape(name)))
                lines.append("%s  <ol>" % (" " * (4 + depth * 2)))
                emit(sub, depth + 1)
                lines.append("%s  </ol>" % (" " * (4 + depth * 2)))
                lines.append("%s</li>" % (" " * (4 + depth * 2)))

        emit(self._tree(), 0)
        lines += ["    </ol>", "  </nav>", "</body>", "</html>"]
        return "\n".join(lines) + "\n"

    def _nav_doc_xhtml(self):
        return (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!DOCTYPE html>\n"
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh" lang="zh">\n'
            "<head><meta charset=\"utf-8\"/><title>目录</title></head>\n"
            "<body>\n  <nav epub:type=\"landmarks\" hidden=\"\">\n    <ol>\n"
            '      <li><a epub:type="toc" href="nav.xhtml#toc">目录</a></li>\n'
            "    </ol>\n  </nav>\n</body>\n</html>\n"
        )

    def _toc_ncx(self):
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
            "  <head>",
            '    <meta name="dtb:uid" content="urn:uuid:%s"/>' % self.book_uuid,
            '    <meta name="dtb:depth" content="2"/>',
            '    <meta name="dtb:totalPageCount" content="0"/>',
            '    <meta name="dtb:maxPageNumber" content="0"/>',
            "  </head>",
            "  <docTitle><text>%s</text></docTitle>" % html.escape(self.book_title),
            '  <navMap>',
        ]
        order = [0]

        def emit(node, depth, parent):
            idx = 0
            for name, sub in sorted(node.items()):
                if name == "_first_href":
                    continue
                if isinstance(sub, dict):
                    idx += 1
                    order[0] += 1
                    lines.append('%s<navPoint id="np_%d_%d" playOrder="%d">' % (" " * (4 + depth * 2), depth + 1, idx, order[0]))
                    lines.append('%s  <navLabel><text>%s</text></navLabel>' % (" " * (4 + depth * 2), html.escape(name)))
                    lines.append('%s  <content src="%s"/>' % (" " * (4 + depth * 2), html.escape(sub["_first_href"])))
                    sub_children = {k: v for k, v in sub.items() if k != "_first_href"}
                    emit(sub_children, depth + 1, lines)
                    lines.append('%s</navPoint>' % (" " * (4 + depth * 2)))
                else:
                    cid, href, title = sub
                    order[0] += 1
                    lines.append('%s<navPoint id="%s" playOrder="%d">' % (" " * (4 + depth * 2), cid, order[0]))
                    lines.append('%s  <navLabel><text>%s</text></navLabel>' % (" " * (4 + depth * 2), html.escape(title)))
                    lines.append('%s  <content src="%s"/>' % (" " * (4 + depth * 2), html.escape(href)))
                    lines.append('%s</navPoint>' % (" " * (4 + depth * 2)))

        def annotate(node):
            first = None
            for k, v in node.items():
                if isinstance(v, tuple):
                    if first is None:
                        first = v[1]
                else:
                    h = annotate(v)
                    if first is None and h:
                        first = h
            if first:
                node["_first_href"] = first
            return first

        tree = self._tree()
        annotate(tree)
        emit(tree, 0, lines)
        lines += ["  </navMap>", "</ncx>"]
        return "\n".join(lines) + "\n"

    def _content_opf(self):
        manifest, spine = [], []
        for cid, href, title, _ in self.chapters:
            manifest.append('    <item id="%s" href="%s" media-type="application/xhtml+xml"/>' % (cid, html.escape(href)))
            spine.append('    <itemref idref="%s"/>' % cid)
        manifest.append('    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        manifest.append('    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
        now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="zh">\n'
            "  <metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">\n"
            '    <dc:identifier id="bookid">urn:uuid:%s</dc:identifier>\n'
            "    <dc:title>%s</dc:title>\n"
            "    <dc:language>zh</dc:language>\n"
            "    <meta property=\"dcterms:modified\">%s</meta>\n"
            "  </metadata>\n"
            "  <manifest>\n%s\n  </manifest>\n"
            "  <spine toc=\"ncx\">\n%s\n  </spine>\n"
            "</package>\n"
            % (self.book_uuid, html.escape(self.book_title), now,
               "\n".join(manifest), "\n".join(spine))
        )

    def write(self, out_path: str):
        os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            # mimetype 必须第一个且不压缩
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml", self._container_xml())
            z.writestr("OEBPS/content.opf", self._content_opf())
            z.writestr("OEBPS/toc.ncx", self._toc_ncx())
            z.writestr("OEBPS/nav.xhtml", self._nav_xhtml())
            z.writestr("OEBPS/nav_doc.xhtml", self._nav_doc_xhtml())
            for cid, href, title, content in self.chapters:
                z.writestr("OEBPS/" + href, content)


# ---------------------------------------------------------------- core scan/convert

def collect_txt_files(root_dir: str):
    """递归收集所有 txt（排除隐藏文件），返回相对路径列表（排序保证稳定）。"""
    result = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith(".") or not is_txt(fn):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root_dir)
            result.append(rel.replace("\\", "/"))
    # 排序：先按目录路径、再按文件名（自然排序数字段）
    def natkey(s):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
    result.sort(key=natkey)
    return result


def convert(root_dir: str, out_path: str, title: str, progress_cb=None):
    files = collect_txt_files(root_dir)
    if not files:
        raise RuntimeError("目录下没有找到任何 txt 文件：%s" % root_dir)
    builder = EpubBuilder(title)
    total = len(files)
    for i, rel in enumerate(files, 1):
        full = os.path.join(root_dir, rel.replace("/", os.sep))
        text = read_text(full)
        builder.add_chapter(rel, text)
        if progress_cb:
            progress_cb(i, total, rel)
    builder.write(out_path)
    return total


# ---------------------------------------------------------------- GUI

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Txt → EPUB 合并转换器")
        self.geometry("720x420")
        self.minsize(640, 360)

        self.src_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.title_var = tk.StringVar()

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, **pad)

        # 源目录
        row1 = ttk.Frame(frm); row1.pack(fill="x", pady=4)
        ttk.Label(row1, text="源目录：").pack(side="left")
        ttk.Entry(row1, textvariable=self.src_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row1, text="浏览...", command=self.pick_src).pack(side="left")

        # 输出文件
        row2 = ttk.Frame(frm); row2.pack(fill="x", pady=4)
        ttk.Label(row2, text="输出文件：").pack(side="left")
        ttk.Entry(row2, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row2, text="浏览...", command=self.pick_out).pack(side="left")

        # 书名
        row3 = ttk.Frame(frm); row3.pack(fill="x", pady=4)
        ttk.Label(row3, text="书名：").pack(side="left")
        ttk.Entry(row3, textvariable=self.title_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Label(row3, text="（留空则使用源目录名）").pack(side="left")

        # 按钮
        row4 = ttk.Frame(frm); row4.pack(fill="x", pady=8)
        self.btn = ttk.Button(row4, text="开始转换", command=self.start)
        self.btn.pack(side="left")
        ttk.Label(row4, text="  章节 = 每个 txt 文件；目录层级 = 子目录结构").pack(side="left")

        # 进度
        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.pack(fill="x", pady=4)
        self.status = tk.StringVar(value="就绪")
        ttk.Label(frm, textvariable=self.status, anchor="w").pack(fill="x")

        # 日志
        self.log = tk.Text(frm, height=10, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=4)

    def pick_src(self):
        d = filedialog.askdirectory(title="选择包含 txt 的目录")
        if d:
            self.src_var.set(os.path.normpath(d))
            if not self.out_var.get():
                self.out_var.set(os.path.join(d, os.path.basename(os.path.normpath(d)) + ".epub"))
            if not self.title_var.get():
                self.title_var.set(os.path.basename(os.path.normpath(d)))

    def pick_out(self):
        f = filedialog.asksaveasfilename(
            title="保存 EPUB 为", defaultextension=".epub",
            filetypes=[("EPUB 电子书", "*.epub")])
        if f:
            self.out_var.set(f)

    def log_line(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self):
        src = self.src_var.get().strip()
        out = self.out_var.get().strip()
        title = self.title_var.get().strip() or (
            os.path.basename(os.path.normpath(src)) if src else "")

        if not src or not os.path.isdir(src):
            messagebox.showwarning("提示", "请先选择有效的源目录。")
            return
        if not out:
            messagebox.showwarning("提示", "请指定输出 epub 文件路径。")
            return
        if not out.lower().endswith(".epub"):
            out += ".epub"
            self.out_var.set(out)
        if os.path.abspath(out) == os.path.abspath(os.path.join(src, os.path.basename(out))):
            # 输出文件放在源目录下没问题，但不应该被当成输入（txt 过滤天然规避）
            pass

        self.btn.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self.status.set("正在扫描...")
        self.log_line("=== 开始转换 ===")
        self.log_line("源目录: %s" % src)
        self.log_line("输出:   %s" % out)

        threading.Thread(target=self._work, args=(src, out, title), daemon=True).start()

    def _work(self, src, out, title):
        try:
            def cb(i, total, rel):
                pct = i * 100 // total
                self.after(0, lambda p=pct: self.progress.configure(value=p))
                self.after(0, lambda r=rel, t=total, ii=i: self.status.set(
                    "转换中 %d/%d：%s" % (ii, t, r)))

            n = convert(src, out, title, progress_cb=cb)
            size_mb = os.path.getsize(out) / 1024 / 1024
            self.after(0, lambda: self._done(True,
                "完成！共 %d 个章节 → %s (%.2f MB)" % (n, os.path.basename(out), size_mb)))
        except Exception as e:
            self.after(0, lambda: self._done(False, "失败：%s" % e))

    def _done(self, ok, msg):
        self.btn.configure(state="normal")
        self.status.set(msg)
        self.log_line(msg)
        if ok:
            if messagebox.askyesno("完成", msg + "\n\n是否打开所在文件夹？"):
                os.startfile(os.path.dirname(os.path.abspath(self.out_var.get())))
        else:
            messagebox.showerror("错误", msg)


def main():
    # 高分屏适配
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    App().mainloop()


if __name__ == "__main__":
    main()
