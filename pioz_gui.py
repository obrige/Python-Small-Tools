# -*- coding: utf-8 -*-
"""
网盘资源搜索客户端 (基于 www.pioz.cn 公开 API)  v4
==============================================
纯标准库实现（tkinter + urllib），无需 pip 安装任何依赖，双击即用。

功能:
  - 全网深度搜索（夸克/百度/迅雷/UC/阿里/115 实时聚合）+ 本地索引联想
  - 双击结果行 / 点"获取链接" -> 取真实网盘链接 + 提取码, 一键复制/打开
  - 结果按发布时间 新 -> 旧 排序; 无日期记录(Go零值 0001-01-01)显示 "—" 并沉底
  - "隐藏AI资源"过滤: & 规则仅适用【短剧】类——标题带 & 是人工双标题,
    不带 & 的短剧视为 AI 短剧, 勾选后默认隐藏
  - 分类列: 本地联想用接口 tags, 深度搜索从标题启发式提取

运行:
  python pioz_gui.py
"""

import json
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

BASE = "https://www.pioz.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

CLOUD_NAMES = {
    "quark": "夸克网盘", "baidu": "百度网盘", "xunlei": "迅雷云盘",
    "uc": "UC网盘", "alipan": "阿里云盘", "115": "115网盘",
}

# ---------- 主题 ----------
C_BG = "#eef1f5"
C_PANEL = "#ffffff"
C_TEXT = "#0f172a"
C_MUTED = "#64748b"
C_BORDER = "#d3dae3"
C_PRIMARY = "#2563eb"
C_PRIMARY_ACT = "#1d4ed8"
C_ACCENT2 = "#7c3aed"
C_ROW_EVEN = "#f1f5fb"
C_OK = "#0f9d58"
C_BAD = "#d93025"
C_UNK = "#9aa3af"

F_TITLE = ("Microsoft YaHei UI", 17, "bold")
F_SUB = ("Microsoft YaHei UI", 9)
F_BODY = ("Microsoft YaHei UI", 10)
F_BTN = ("Microsoft YaHei UI", 10)
F_HEAD = ("Microsoft YaHei UI", 10, "bold")
F_MONO = ("Consolas", 10)


class ApiError(Exception):
    def __init__(self, msg, kind="error"):
        super().__init__(msg)
        self.kind = kind  # error | ratelimit | captcha | expired | notfound


# ---------- 数据层 ----------
def fetch_json(path: str):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise ApiError("请求过于频繁，已被限流(429)，请降低频率或稍后再试", "ratelimit")
        raise ApiError(f"HTTP {e.code}")
    except Exception as e:
        raise ApiError(f"网络错误: {e}")
    try:
        return status, json.loads(raw)
    except Exception:
        raise ApiError("返回内容不是有效 JSON")


def deep_search(kw: str):
    status, data = fetch_json("/api/deep-search?kw=" + urllib.parse.quote(kw))
    if data.get("code") == -2 or data.get("message") == "captcha_required":
        raise ApiError("触发人机验证(Cloudflare Turnstile)，请放慢速度后再试", "captcha")
    if data.get("code") != 0:
        raise ApiError(data.get("message") or "搜索失败")
    return data.get("results") or [], data.get("total") or 0


def suggestions(kw: str):
    status, data = fetch_json("/api/suggestions?q=" + urllib.parse.quote(kw))
    return data or []


def transfer(rid: str):
    status, data = fetch_json("/api/transfer?id=" + urllib.parse.quote(str(rid)))
    if data.get("code") == "captcha_required":
        raise ApiError("触发人机验证(Cloudflare Turnstile)，请放慢速度后再试", "captcha")
    if data.get("success") and data.get("data"):
        return data["data"].get("url", ""), data["data"].get("pwd", ""), data["data"].get("title", "")
    url = data.get("orig_url") or ""
    pwd = data.get("pwd") or ""
    err = data.get("error") or data.get("message") or "获取失败"
    if url:
        return url, pwd, err + "（原链接仍可用）"
    raise ApiError(err, "expired")


# ---------- 日期工具 ----------
def is_unknown_dt(dt) -> bool:
    """后端无时间时返回 Go 零值 '0001-01-01T00:00:00Z'。"""
    return not dt or str(dt).startswith("0001-01-01")


def fmt_dt(dt) -> str:
    if is_unknown_dt(dt):
        return "—"
    s = str(dt).replace("T", " ").replace("Z", "").strip()
    return s[:16] if len(s) >= 16 else s


def dt_sort_key(dt):
    if is_unknown_dt(dt):
        return 0.0
    s = str(dt).replace("T", " ").replace("Z", "").strip()[:16]
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M"))
    except Exception:
        return 0.0


# ---------- 分类 & AI 判定 ----------
CAT_RULES = [
    ("动漫", ["动漫", "国漫", "动画"]),
    ("短剧", ["短剧"]),
    ("电影", ["电影", "remux", "web-dl", "原盘", "4k"]),
    ("音乐", ["音乐", "flac", "无损", "ost", "专辑"]),
    ("教育", ["课程", "训练营", "教程", "课堂"]),
    ("文学", ["小说", "epub", "txt", "文学"]),
    ("游戏", ["游戏", "手游", "单机"]),
    ("设计", ["设计", "素材", "psd", "模板"]),
]


def derive_category(title) -> str:
    if not title:
        return "—"
    low = str(title).lower()
    for cat, kws in CAT_RULES:
        if any(k in low for k in kws):
            return cat
    return "—"


def row_category(r) -> str:
    tags = r.get("tags")
    if tags:
        return " / ".join(str(t) for t in tags)
    return derive_category(r.get("title"))


def is_ai_resource(r) -> bool:
    """AI 短剧判定:
    1) 标题自带「AI短剧 / Ai短剧 / ai短剧」字样 -> 一律算 AI（哪怕带 &）
    2) 否则仅短剧类: 标题不带 & 也算 AI；带 & 且没写 AI短剧 的当人工
    非短剧、标题也没写 AI短剧 的不算 AI。"""
    title = r.get("title") or ""
    low = title.lower()
    if "ai短剧" in low:
        return True
    if "短剧" not in row_category(r):
        return False
    return "&" not in title


# ---------- GUI ----------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("网盘资源搜索客户端")
        root.geometry("1040x720")
        root.minsize(880, 600)
        root.configure(bg=C_BG)

        self.results = []
        self.last_raw = []
        self.last_total = 0
        self.busy = False

        self._setup_style()
        self._build_ui()
        self._set_status("就绪 — 输入关键词回车搜索；双击结果行直接取链接")

    # ---------- 样式 ----------
    def _setup_style(self):
        style = ttk.Style(self.root)
        for theme in ("vista", "clam"):
            try:
                style.theme_use(theme)
                break
            except Exception:
                continue
        style.configure("Treeview", rowheight=30, font=F_BODY,
                        background=C_PANEL, fieldbackground=C_PANEL, bordercolor=C_BORDER)
        style.configure("Treeview.Heading", font=F_HEAD,
                        background="#e6ebf2", foreground=C_TEXT, relief="flat", padding=6)
        style.map("Treeview", background=[("selected", "#dbe7ff")],
                  foreground=[("selected", C_TEXT)])
        style.map("Treeview.Heading", background=[("active", "#dbe4ee")])

    @staticmethod
    def _btn(parent, text, command, primary=False, **kw):
        if primary:
            return tk.Button(parent, text=text, command=command,
                             bg=C_PRIMARY, fg="white", activebackground=C_PRIMARY_ACT,
                             activeforeground="white", relief="flat", bd=0,
                             padx=18, pady=7, font=F_BTN, cursor="hand2", **kw)
        return tk.Button(parent, text=text, command=command,
                         bg=C_PANEL, fg=C_TEXT, activebackground="#e2e8f0",
                         activeforeground=C_TEXT, relief="flat", bd=1,
                         highlightthickness=1, highlightbackground=C_BORDER,
                         highlightcolor=C_BORDER, padx=14, pady=7, font=F_BTN,
                         cursor="hand2", **kw)

    # ---------- UI ----------
    def _build_ui(self):
        # 渐变头部
        self.header = tk.Canvas(self.root, height=78, highlightthickness=0)
        self.header.pack(fill="x")
        self._draw_header(1040)
        self.root.bind("<Configure>", self._on_resize)

        # 搜索卡片
        card = tk.Frame(self.root, bg=C_PANEL, bd=1, highlightthickness=1,
                        highlightbackground=C_BORDER)
        card.pack(fill="x", padx=14, pady=(14, 4))

        inner = tk.Frame(card, bg=C_PANEL)
        inner.pack(fill="x", padx=14, pady=12)

        tk.Label(inner, text="关键词", bg=C_PANEL, fg=C_MUTED, font=F_BODY).pack(side="left")
        self.var_q = tk.StringVar()
        self.ent_q = tk.Entry(inner, textvariable=self.var_q, width=32, font=F_BODY,
                              relief="flat", bd=1, highlightthickness=1,
                              highlightbackground=C_BORDER, highlightcolor=C_PRIMARY,
                              insertbackground=C_TEXT)
        self.ent_q.pack(side="left", padx=8, ipady=5)
        self.ent_q.bind("<Return>", lambda e: self.do_search())

        self.var_mode = tk.StringVar(value="deep")
        for text, val in (("全网深度搜索", "deep"), ("本地索引", "local")):
            tk.Radiobutton(inner, text=text, value=val, variable=self.var_mode,
                           bg=C_PANEL, fg=C_TEXT, activebackground=C_PANEL,
                           selectcolor=C_PANEL, font=F_BODY,
                           cursor="hand2").pack(side="left", padx=4)

        self.var_hide_ai = tk.BooleanVar(value=True)
        tk.Checkbutton(inner, text="隐藏AI资源", variable=self.var_hide_ai,
                       command=self._apply_filter, bg=C_PANEL, fg=C_TEXT,
                       activebackground=C_PANEL, selectcolor=C_PANEL, font=F_BODY,
                       cursor="hand2").pack(side="left", padx=10)

        self.btn_search = self._btn(inner, "搜  索", self.do_search, primary=True)
        self.btn_search.pack(side="left", padx=(10, 4))
        self.btn_link = self._btn(inner, "获取链接", self.get_link_selected)
        self.btn_link.pack(side="left", padx=4)

        row_filter = tk.Frame(card, bg=C_PANEL)
        row_filter.pack(fill="x", padx=14, pady=(0, 12))
        tk.Label(row_filter, text="只看分类", bg=C_PANEL, fg=C_MUTED, font=F_BODY).pack(side="left")
        self.var_cat = tk.StringVar(value="全部分类")
        self.cat_box = ttk.Combobox(
            row_filter, textvariable=self.var_cat, state="readonly", width=14, font=F_BODY,
            values=("全部分类", "短剧", "动漫", "电影", "音乐", "教育", "文学", "游戏", "设计", "其他/未分类"),
        )
        self.cat_box.pack(side="left", padx=8)
        self.cat_box.bind("<<ComboboxSelected>>", lambda e: self._apply_filter())
        tk.Label(row_filter, text="选中后只显示该分类，其余丢弃",
                 bg=C_PANEL, fg=C_MUTED, font=F_SUB).pack(side="left", padx=6)

        # 结果表
        mid = tk.Frame(self.root, bg=C_PANEL, bd=1, highlightthickness=1,
                       highlightbackground=C_BORDER)
        mid.pack(fill="both", expand=True, padx=14, pady=(4, 6))

        cols = ("title", "cat", "cloud", "time", "status")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        # 标题列 stretch=True，窗口变宽时跟着变宽；其余列固定宽度
        for c, w, anc, stretch in (
            ("title", 720, "w", True),
            ("cat", 110, "center", False),
            ("cloud", 110, "center", False),
            ("time", 150, "center", False),
            ("status", 100, "center", False),
        ):
            self.tree.heading(c, text={"title": "资源标题", "cat": "分类", "cloud": "网盘",
                                       "time": "发布时间", "status": "状态"}[c])
            self.tree.column(c, width=w, minwidth=w if c != "title" else 240, anchor=anc, stretch=stretch)
        self.tree.tag_configure("odd", background=C_PANEL)
        self.tree.tag_configure("even", background=C_ROW_EVEN)
        self.tree.tag_configure("st_ok", foreground=C_OK)
        self.tree.tag_configure("st_bad", foreground=C_BAD)
        self.tree.tag_configure("st_unk", foreground=C_UNK)
        self.tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        sbx = ttk.Scrollbar(mid, orient="horizontal", command=self.tree.xview)
        sbx.pack(side="bottom", fill="x")
        self.tree.configure(yscrollcommand=sb.set, xscrollcommand=sbx.set)
        self.tree.bind("<Double-1>", lambda e: self.get_link_selected())
        self.tree.bind("<Motion>", self._on_tree_motion)
        self._tip = None

        # 链接结果卡片
        bottom = tk.Frame(self.root, bg=C_PANEL, bd=1, highlightthickness=1,
                          highlightbackground=C_BORDER)
        bottom.pack(fill="x", padx=14, pady=(0, 8))

        tk.Label(bottom, text="链接结果", bg=C_PANEL, fg=C_MUTED, font=F_BODY).pack(
            anchor="w", padx=14, pady=(8, 0))

        row1 = tk.Frame(bottom, bg=C_PANEL)
        row1.pack(fill="x", padx=14, pady=(4, 0))
        tk.Label(row1, text="网盘链接", bg=C_PANEL, fg=C_MUTED, font=F_BODY).pack(side="left")
        self.var_url = tk.StringVar()
        tk.Entry(row1, textvariable=self.var_url, state="readonly", font=F_MONO,
                 readonlybackground=C_PANEL, fg=C_PRIMARY,
                 relief="flat", bd=1, highlightthickness=1,
                 highlightbackground=C_BORDER).pack(side="left", fill="x", expand=True, padx=8, ipady=5)
        tk.Label(row1, text="提取码", bg=C_PANEL, fg=C_MUTED, font=F_BODY).pack(side="left")
        self.var_pwd = tk.StringVar()
        tk.Entry(row1, textvariable=self.var_pwd, width=8, state="readonly", font=F_MONO,
                 readonlybackground=C_PANEL, fg=C_TEXT,
                 relief="flat", bd=1, highlightthickness=1,
                 highlightbackground=C_BORDER).pack(side="left", padx=(4, 0), ipady=5)

        row2 = tk.Frame(bottom, bg=C_PANEL)
        row2.pack(fill="x", padx=14, pady=(8, 10))
        self._btn(row2, "复制链接", self.copy_link).pack(side="left")
        self._btn(row2, "复制(含提取码)", self.copy_with_pwd).pack(side="left", padx=6)
        self._btn(row2, "浏览器打开", self.open_link).pack(side="left")
        tk.Label(row2, text="双击取链接 · 分类下拉只留指定类 · 隐藏AI默认开(仅短剧)",
                 bg=C_PANEL, fg=C_MUTED, font=F_SUB).pack(side="right", padx=6)

        # 状态栏
        self.var_status = tk.StringVar()
        status_bar = tk.Label(self.root, textvariable=self.var_status, anchor="w",
                              bg="#e3e9f1", fg=C_TEXT, padx=14, pady=5, font=F_SUB)
        status_bar.pack(fill="x", side="bottom")

    # ---------- 头部渐变 ----------
    def _draw_header(self, w):
        c = self.header
        c.delete("all")
        steps = 90
        sr, sg, sb = 56, 152, 235   # #3898EB (蓝)
        er, eg, eb = 124, 58, 237   # #7C3AED (紫)
        for i in range(steps):
            t = i / (steps - 1)
            x0 = int(i * w / steps)
            x1 = int((i + 1) * w / steps) + 1
            r = int(sr + (er - sr) * t)
            g = int(sg + (eg - sg) * t)
            b = int(sb + (eb - sb) * t)
            c.create_rectangle(x0, 0, x1, 78, fill=f"#{r:02x}{g:02x}{b:02x}", outline="")
        c.create_text(18, 30, anchor="w", text="网盘资源搜索客户端",
                      fill="#ffffff", font=F_TITLE)
        c.create_text(18, 58, anchor="w", text="Deep Search · Transfer · Suggestions",
                      fill="#dbe7ff", font=F_SUB)
        c.create_text(w - 18, 20, anchor="ne", text="聚合搜索 · 一键取链",
                      fill="#ffffff", font=F_SUB)

    def _on_resize(self, e):
        if e.widget is self.root:
            self._draw_header(max(e.width, 300))

    # ---------- 逻辑 ----------
    def _set_status(self, text):
        self.var_status.set(text)

    def do_search(self):
        if self.busy:
            return
        q = self.var_q.get().strip()
        if not q:
            messagebox.showwarning("提示", "请输入关键词")
            return
        mode = self.var_mode.get()
        self.busy = True
        self.btn_search.config(state="disabled")
        self._set_status("搜索中…")

        def work():
            try:
                if mode == "deep":
                    res, total = deep_search(q)
                    self.root.after(0, lambda: self._show_results(res, total, local=False))
                else:
                    res = suggestions(q)
                    self.root.after(0, lambda: self._show_results(res, len(res), local=True))
            except ApiError as e:
                self.root.after(0, lambda: self._show_error(e))

        threading.Thread(target=work, daemon=True).start()

    def _show_results(self, raw, total, local=False):
        self.busy = False
        self.btn_search.config(state="normal")
        self.last_raw = list(raw)
        self.last_total = total
        self.last_raw.sort(key=lambda r: dt_sort_key(r.get("datetime")), reverse=True)
        self._apply_filter()
        self._set_status(f"共 {total} 条" + ("（联想接口最多返回10条）" if local else ""))

    def _match_category(self, r, cat: str) -> bool:
        got = row_category(r)
        if cat == "全部分类":
            return True
        if cat == "其他/未分类":
            return got == "—"
        return cat in got

    def _apply_filter(self):
        """重建表格(不重新请求): 先隐藏 AI，再只留下拉选中的分类。"""
        data = list(self.last_raw)
        hidden = 0
        if self.var_hide_ai.get():
            kept = [r for r in data if not is_ai_resource(r)]
            hidden = len(data) - len(kept)
            data = kept

        cat = self.var_cat.get()
        dropped_cat = 0
        if cat != "全部分类":
            kept = [r for r in data if self._match_category(r, cat)]
            dropped_cat = len(data) - len(kept)
            data = kept

        self.results = list(data)
        self.tree.delete(*self.tree.get_children())

        from collections import Counter
        cnt = Counter(r.get("cloud_type") or "-" for r in self.results)
        detail = " / ".join(f"{CLOUD_NAMES.get(k, k)} {v}" for k, v in cnt.most_common())

        for i, r in enumerate(self.results):
            status = r.get("status")
            if status == 0:
                st_text, st_tag = "● 转存成功", "st_ok"
            elif status == 1:
                st_text, st_tag = "● 已失效", "st_bad"
            else:
                st_text, st_tag = "● 未验证", "st_unk"
            self.tree.insert("", "end", iid=str(i),
                             tags=("even" if i % 2 else "odd", st_tag),
                             values=(r.get("title", ""),
                                     row_category(r),
                                     CLOUD_NAMES.get(str(r.get("cloud_type", "")).lower(), r.get("cloud_type", "")),
                                     fmt_dt(r.get("datetime")), st_text))

        bits = []
        if hidden:
            bits.append(f"已隐藏 AI 短剧 {hidden} 条")
        if dropped_cat:
            bits.append(f"分类[{cat}]丢弃 {dropped_cat} 条")
        tail = ("（" + " / ".join(bits) + "）") if bits else ""
        if self.results:
            self._set_status(f"共 {self.last_total} 条 → 显示 {len(self.results)} 条{tail}（{detail}）— 双击取链接")
        else:
            self._set_status(f"共 {self.last_total} 条，过滤后无结果{tail}")

    def _show_error(self, e: ApiError):
        self.busy = False
        self.btn_search.config(state="normal")
        self._set_status(f"⚠ {e}")
        if e.kind == "ratelimit":
            messagebox.showwarning("限流", str(e))
        elif e.kind == "captcha":
            messagebox.showwarning("人机验证", str(e))
        else:
            messagebox.showerror("错误", str(e))

    def _on_tree_motion(self, e):
        """标题被列宽截断时，鼠标悬停显示完整标题。"""
        row = self.tree.identify_row(e.y)
        col = self.tree.identify_column(e.x)
        if not row or col != "#1":
            self._hide_tip()
            return
        title = self.tree.item(row, "values")[0]
        if self._tip and getattr(self._tip, "_text", None) == title:
            return
        self._hide_tip()
        tip = tk.Toplevel(self.root)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{e.x_root + 12}+{e.y_root + 16}")
        tk.Label(tip, text=title, bg="#1e293b", fg="#f8fafc", font=F_BODY,
                 padx=8, pady=4, wraplength=640, justify="left").pack()
        tip._text = title
        self._tip = tip

    def _hide_tip(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def get_link_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在结果中选中一行")
            return
        idx = int(sel[0])
        item = self.results[idx]
        self._set_status(f"获取链接中: {str(item.get('title', ''))[:40]}…")

        def work():
            try:
                url, pwd, title = transfer(item["id"])
                self.root.after(0, lambda: self._set_link(url, pwd, title))
            except ApiError as e:
                self.root.after(0, lambda: self._set_status(f"⚠ {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _set_link(self, url, pwd, title):
        self.var_url.set(url)
        self.var_pwd.set(pwd)
        self._set_status(f"✅ 已获取链接" + (f"（{title}）" if title else ""))

    def copy_link(self):
        url = self.var_url.get()
        if not url:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self._set_status("链接已复制")

    def copy_with_pwd(self):
        url = self.var_url.get()
        pwd = self.var_pwd.get()
        if not url:
            return
        text = url + (f"  提取码:{pwd}" if pwd else "")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._set_status("已复制 链接+提取码")

    def open_link(self):
        url = self.var_url.get()
        if url:
            webbrowser.open(url)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
