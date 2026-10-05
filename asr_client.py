#!/usr/bin/env python3
"""
支持 9 种音频格式、3 种响应格式、auto/ast/ist 三种模式.
需要: requests (内置 pip), 可选 tkinterdnd2 (拖放)
启动:  python asr_client.py
"""
from __future__ import annotations

import io
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import wave
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import requests
except ImportError:
    print("pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    import tkinterdnd2
    DND_OK = True
except ImportError:
    DND_OK = False


APP_TITLE = "灵犀ASR客户端"
API_DEFAULT = "http://127.0.0.1:8000/v1/audio/transcriptions"
SUPPORTED_FORMATS = ["wav", "mp3", "m4a", "amr", "opus", "pcm", "aac", "ogg", "flac"]
LANGS = ["zh", "en", "ja", "ko", "ru"]
MODES = ["auto", "ast", "ist"]
RESPONSE_FORMATS = ["json", "verbose_json", "text"]
ASR_MAX_FILE = 500 * 1024 * 1024
COL_BG       = "#f5f7fa"   # 主背景（浅灰）
COL_PANEL    = "#ffffff"   # 面板/输入框（纯白）
COL_CARD     = "#ffffff"   # 卡片（纯白）
COL_ACCENT   = "#3b82f6"   # 主按钮（蓝）
COL_ACCENT_HI= "#2563eb"   # 主按钮悬停
COL_TEXT     = "#1f2937"   # 主文字（深灰）
COL_DIM      = "#6b7280"   # 次文字（中灰）
COL_OK       = "#16a34a"   # 成功（绿）
COL_ERR      = "#dc2626"   # 错误（红）

if DND_OK:
    class _TkBase(tkinterdnd2.TkinterDnD.Tk):
        pass
else:
    class _TkBase(tk.Tk):
        pass


class ASRClient(_TkBase):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("960x680")
        self.minsize(820, 580)
        self.configure(bg=COL_BG)

        self._configure_styles()
        self._build_ui()

        self._file_path: str | None = None
        self._audio_meta: dict = {}
        self._result_q: queue.Queue = queue.Queue()
        self._busy = False
        self.after(80, self._drain_queue)

    def _configure_styles(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("Card.TFrame", background=COL_CARD)
        s.configure("Panel.TFrame", background=COL_PANEL)
        s.configure("TLabel", background=COL_BG, foreground=COL_TEXT, font=("Segoe UI", 10))
        s.configure("Card.TLabel", background=COL_CARD, foreground=COL_TEXT, font=("Segoe UI", 10))
        s.configure("Dim.TLabel", background=COL_CARD, foreground=COL_DIM, font=("Segoe UI", 9))
        s.configure("Title.TLabel", background=COL_BG, foreground=COL_TEXT, font=("Segoe UI Semibold", 16))
        s.configure("Sub.TLabel", background=COL_BG, foreground=COL_DIM, font=("Segoe UI", 9))
        s.configure("TCombobox",
            fieldbackground=COL_PANEL, background=COL_PANEL,
            foreground=COL_TEXT, arrowcolor=COL_DIM, borderwidth=0, relief="flat",
        )
        s.map("TCombobox",
            fieldbackground=[("readonly", COL_PANEL)],
            foreground=[("readonly", COL_TEXT)],
            selectbackground=[("readonly", COL_PANEL)],
            selectforeground=[("readonly", COL_TEXT)],
        )
        s.configure("Accent.TButton",
            background=COL_ACCENT, foreground="#0a0e15",
            font=("Segoe UI Semibold", 11), borderwidth=0, relief="flat", padding=(20, 10),
        )
        s.map("Accent.TButton",
            background=[("active", COL_ACCENT_HI), ("disabled", "#d1d5db")],
            foreground=[("disabled", COL_DIM)],
        )
        s.configure("Ghost.TButton",
            background=COL_CARD, foreground=COL_TEXT,
            font=("Segoe UI", 10), borderwidth=1, relief="flat", padding=(12, 6),
        )
        s.map("Ghost.TButton",
            background=[("active", COL_PANEL)],
        )
        s.configure("Treeview",
            background=COL_PANEL, fieldbackground=COL_PANEL,
            foreground=COL_TEXT, rowheight=26, borderwidth=0,
        )
        s.configure("Treeview.Heading",
            background=COL_CARD, foreground=COL_DIM,
            font=("Segoe UI", 9, "bold"), relief="flat", borderwidth=0,
        )
        s.map("Treeview.Heading", background=[("active", COL_PANEL)])

    def _build_ui(self):
        top = tk.Frame(self, bg=COL_BG, height=70)
        top.pack(fill="x", padx=20, pady=(18, 0))
        top.pack_propagate(False)
        ttk.Label(top, text=APP_TITLE, style="Title.TLabel").pack(side="left", pady=8)
        ttk.Label(top, text="OpenAI-compatible · ASR · 9 formats · AST/IST auto",
                  style="Sub.TLabel").pack(side="left", padx=(14, 0), pady=12)

        main = tk.Frame(self, bg=COL_BG)
        main.pack(fill="both", expand=True, padx=20, pady=18)

        left = tk.Frame(main, bg=COL_BG)
        left.pack(side="left", fill="y", padx=(0, 14))
        right = tk.Frame(main, bg=COL_BG)
        right.pack(side="left", fill="both", expand=True)

        self._build_file_card(left)
        self._build_options_card(left)
        self._build_action_card(left)

        self._build_result_card(right)

        status = tk.Frame(self, bg=COL_BG)
        status.pack(fill="x", padx=20, pady=(0, 12))
        self.status_lbl = ttk.Label(status, text="就绪 · 拖入音频文件或点击选择", style="Sub.TLabel")
        self.status_lbl.pack(side="left")
        self.elapsed_lbl = ttk.Label(status, text="", style="Sub.TLabel")
        self.elapsed_lbl.pack(side="right")

    def _build_file_card(self, parent):
        card = tk.Frame(parent, bg=COL_CARD, highlightthickness=1, highlightbackground="#e5e7eb")
        card.pack(fill="x", pady=(0, 12))

        ttk.Label(card, text="① 音频文件", style="Card.TLabel",
                  font=("Segoe UI Semibold", 11)).grid(row=0, column=0, sticky="w",
                                                         padx=14, pady=(12, 6))

        drop = tk.Frame(card, bg=COL_PANEL, height=120, highlightthickness=1,
                       highlightbackground="#e5e7eb")
        drop.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        drop.pack_propagate(False)
        card.columnconfigure(0, weight=1)

        self.drop_lbl = tk.Label(drop, text="拖入文件 / 点击选择", bg=COL_PANEL, fg=COL_DIM,
                                  font=("Segoe UI", 10), cursor="hand2")
        self.drop_lbl.pack(expand=True, fill="both")
        self.drop_lbl.bind("<Button-1>", lambda e: self._pick_file())

        if DND_OK:
            self.drop_lbl.drop_target_register(tkinterdnd2.DND_FILES)
            self.drop_lbl.dnd_bind("<<Drop>>", self._on_drop)

        meta_row = tk.Frame(card, bg=COL_CARD)
        meta_row.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        self.meta_name = ttk.Label(meta_row, text="—", style="Dim.TLabel")
        self.meta_name.pack(side="left")
        self.meta_size = ttk.Label(meta_row, text="", style="Dim.TLabel")
        self.meta_size.pack(side="right")

    def _build_options_card(self, parent):
        card = tk.Frame(parent, bg=COL_CARD, highlightthickness=1, highlightbackground="#e5e7eb")
        card.pack(fill="x", pady=(0, 12))

        ttk.Label(card, text="② 转写选项", style="Card.TLabel",
                  font=("Segoe UI Semibold", 11)).grid(row=0, column=0, columnspan=2,
                                                         sticky="w", padx=14, pady=(12, 8))

        opts = [
            ("API URL", "api_url", API_DEFAULT, None),
            ("格式", "fmt", "wav", SUPPORTED_FORMATS),
            ("语言", "lang", "zh", LANGS),
            ("模式", "mode", "auto", MODES),
            ("响应格式", "resp", "json", RESPONSE_FORMATS),
        ]
        self._vars = {}
        for i, (label, key, default, choices) in enumerate(opts, start=1):
            ttk.Label(card, text=label, style="Card.TLabel").grid(
                row=i, column=0, sticky="w", padx=(14, 8), pady=4)
            if choices is None:
                var = tk.StringVar(value=default)
                ent = ttk.Entry(card, textvariable=var, width=28)
                ent.grid(row=i, column=1, sticky="ew", padx=(0, 14), pady=4)
            else:
                var = tk.StringVar(value=default)
                cb = ttk.Combobox(card, textvariable=var, values=choices,
                                   state="readonly", width=26)
                cb.grid(row=i, column=1, sticky="ew", padx=(0, 14), pady=4)
            self._vars[key] = var
        card.columnconfigure(1, weight=1)

    def _build_action_card(self, parent):
        card = tk.Frame(parent, bg=COL_CARD, highlightthickness=1, highlightbackground="#e5e7eb")
        card.pack(fill="x")
        ttk.Label(card, text="③ 执行", style="Card.TLabel",
                  font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=14, pady=(12, 8))

        self.run_btn = ttk.Button(card, text="▶  开始转写", style="Accent.TButton",
                                   command=self._run)
        self.run_btn.pack(fill="x", padx=14, pady=(0, 6))

        row = tk.Frame(card, bg=COL_CARD)
        row.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(row, text="复制结果", style="Ghost.TButton",
                   command=self._copy_result).pack(side="left")
        ttk.Button(row, text="保存...", style="Ghost.TButton",
                   command=self._save_result).pack(side="left", padx=8)
        ttk.Button(row, text="清空", style="Ghost.TButton",
                   command=self._clear).pack(side="right")

    def _build_result_card(self, parent):
        card = tk.Frame(parent, bg=COL_CARD, highlightthickness=1, highlightbackground="#e5e7eb")
        card.pack(fill="both", expand=True)
        ttk.Label(card, text="识别结果", style="Card.TLabel",
                  font=("Segoe UI Semibold", 11)).pack(anchor="w", padx=14, pady=(12, 6))

        self.text_box = tk.Text(card, bg=COL_PANEL, fg=COL_TEXT,
                                insertbackground=COL_TEXT, relief="flat",
                                font=("Consolas", 11), wrap="word",
                                padx=14, pady=12, height=10)
        self.text_box.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.text_box.configure(state="disabled")

        seg_frame = tk.Frame(card, bg=COL_CARD)
        seg_frame.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Label(seg_frame, text="segments", style="Dim.TLabel").pack(anchor="w")
        self.seg_tree = ttk.Treeview(seg_frame, columns=("sn", "msgtype", "start", "end", "text"),
                                     show="headings", height=5)
        self.seg_tree.heading("sn", text="sn")
        self.seg_tree.heading("msgtype", text="类型")
        self.seg_tree.heading("start", text="开始 (ms)")
        self.seg_tree.heading("end", text="结束 (ms)")
        self.seg_tree.heading("text", text="文本")
        self.seg_tree.column("sn", width=50, anchor="e")
        self.seg_tree.column("msgtype", width=80, anchor="w")
        self.seg_tree.column("start", width=80, anchor="e")
        self.seg_tree.column("end", width=80, anchor="e")
        self.seg_tree.column("text", width=320, anchor="w")
        self.seg_tree.pack(fill="x", pady=(4, 0))

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[
                 ("音频文件", " ".join(f"*.{f}" for f in SUPPORTED_FORMATS)),
                 ("所有文件", "*.*"),
            ],
        )
        if path:
            self._set_file(path)

    def _on_drop(self, event):
        path = event.data.strip("{}").strip()
        if os.path.isfile(path):
            self._set_file(path)

    def _set_file(self, path: str):
        if self._busy:
            return
        size = os.path.getsize(path)
        if size > ASR_MAX_FILE:
            messagebox.showerror("文件过大",
                                f"文件 {size/1024/1024:.1f}MB 超过 {ASR_MAX_FILE//1024//1024}MB 上限")
            return
        self._file_path = path
        name = os.path.basename(path)
        self.meta_name.configure(text=name)
        self.meta_size.configure(text=f"{size/1024/1024:.2f} MB")
        self.drop_lbl.configure(text=f"✓  {name}", fg=COL_OK)
        ext = Path(path).suffix.lstrip(".").lower()
        if ext in SUPPORTED_FORMATS:
            self._vars["fmt"].set(ext)
        self._audio_meta = {"name": name, "size": size, "ext": ext}

    def _run(self):
        if self._busy:
            return
        if not self._file_path:
            messagebox.showwarning("未选文件", "请先选择音频文件")
            return
        url = self._vars["api_url"].get().strip() or API_DEFAULT
        fmt = self._vars["fmt"].get().strip().lower().lstrip(".")
        lang = self._vars["lang"].get().strip()
        mode = self._vars["mode"].get().strip()
        resp = self._vars["resp"].get().strip()

        if fmt not in SUPPORTED_FORMATS:
            messagebox.showerror("格式错误", f"不支持格式: {fmt}")
            return

        self._busy = True
        self.run_btn.configure(state="disabled", text="转写中...")
        self._set_status(f"上传 → {url}")
        self.elapsed_lbl.configure(text="")
        self._set_text("⏳正在处理")
        self._clear_segments()

        t0 = time.monotonic()
        threading.Thread(target=self._transcribe_worker,
                          kwargs=dict(path=self._file_path, url=url, fmt=fmt,
                                      lang=lang, mode=mode, resp=resp,
                                      t0=t0), daemon=True).start()

    def _transcribe_worker(self, path, url, fmt, lang, mode, resp, t0):
        """Worker thread. 推到 _result_q 的消息统一用 dict, 避免之前 tuple 解包歧义.
        统一格式: {"kind": str, "text": str, "segs": list, "raw": dict|None, "dt": float, "error": str|None}
        """
        try:
            with open(path, "rb") as f:
                files = {"file": (os.path.basename(path), f, f"audio/{fmt}")}
                data = {"language": lang, "response_format_extra": resp, "format": fmt}
                if mode and mode != "auto":
                    data["mode"] = mode
                r = requests.post(url, files=files, data=data, timeout=1800)
            dt = time.monotonic() - t0
            ct = r.headers.get("Content-Type", "")
            if r.status_code != 200:
                # BUG fix: 服务器对 4xx/5xx 可能返回 JSON {"error":...} 或纯文本
                err_msg = ""
                try:
                    err_j = r.json()
                    err_obj = err_j.get("error") if isinstance(err_j, dict) else None
                    if isinstance(err_obj, dict):
                        err_msg = err_obj.get("message", "") or str(err_obj)
                    else:
                        err_msg = str(err_j) if err_j else r.text[:300]
                except Exception:
                    err_msg = r.text[:300]
                self._result_q.put({
                    "kind": "error", "text": "", "segs": [], "raw": None,
                    "dt": dt, "error": f"HTTP {r.status_code}: {err_msg}"
                })
                return
            # BUG fix: 服务器 response_format=text 返回 text/plain (裸字符串, 不是 JSON)
            if "text/plain" in ct:
                self._result_q.put({
                    "kind": "text", "text": r.text or "", "segs": [], "raw": None, "dt": dt, "error": None
                })
                return
            # 默认按 JSON 解析 (json / verbose_json)
            try:
                j = r.json()
            except Exception as e:
                self._result_q.put({
                    "kind": "error", "text": "", "segs": [], "raw": None,
                    "dt": time.monotonic() - t0, "error": f"JSON 解析失败: {e}: {r.text[:200]}"
                })
                return
            text = (j.get("text") or "") if isinstance(j, dict) else ""
            segs = (j.get("segments") or []) if isinstance(j, dict) else []
            self._result_q.put({
                "kind": "json", "text": text, "segs": segs, "raw": j if isinstance(j, dict) else None,
                "dt": dt, "error": None
            })
        except Exception as e:
            self._result_q.put({
                "kind": "error", "text": "", "segs": [], "raw": None,
                "dt": time.monotonic() - t0, "error": f"{type(e).__name__}: {e}"
            })

    def _drain_queue(self):
        """消费 _result_q. 所有消息用 dict 统一结构, 无 unpack 歧义."""
        try:
            msg = self._result_q.get_nowait()
        except queue.Empty:
            self.after(80, self._drain_queue)
            return
        kind = msg.get("kind", "error")
        dt = msg.get("dt", 0.0)
        err = msg.get("error")
        text = msg.get("text", "")
        segs = msg.get("segs", [])
        raw = msg.get("raw")
        if kind == "error":
            self._set_text(f"❌ {err}")
            self._set_status("失败")
        elif kind == "text":
            self._set_text(text)
            self._set_status(f"✓ 完成 · {len(text)} 字")
        elif kind == "json":
            self._set_text(text)
            self._fill_segments(segs)
            mode_used = (raw or {}).get("mode", "?")
            seg_types = sum(1 for s in segs if isinstance(s, dict) and s.get("msgtype") == "sentence")
            self._set_status(
                f"✓ 识别成功 · 模式={mode_used} · {len(text)} 字 · {len(segs)} 段 (sentence={seg_types})"
            )
        else:
            self._set_text(f"⚠ 未知消息类型: {kind}")
            self._set_status("未知响应")
        self.elapsed_lbl.configure(text=f"{dt:.2f}s")
        self._busy = False
        self.run_btn.configure(state="normal", text="▶  开始转写")
        self.after(80, self._drain_queue)

    def _set_text(self, s: str):
        self.text_box.configure(state="normal")
        self.text_box.delete("1.0", "end")
        self.text_box.insert("1.0", s)
        self.text_box.configure(state="disabled")

    def _clear_segments(self):
        for iid in self.seg_tree.get_children():
            self.seg_tree.delete(iid)

    def _fill_segments(self, segs):
        self._clear_segments()
        for s in segs or []:
            # BUG fix: server may return bg/ed (migu upstream) or start/end (normalized)
            start = s.get("start", s.get("bg", "—"))
            end = s.get("end", s.get("ed", "—"))
            self.seg_tree.insert("", "end", values=(
                s.get("sn", "—"), s.get("msgtype", "—"),
                start, end, (s.get("text") or "")[:120]
            ))

    def _set_status(self, msg: str):
        self.status_lbl.configure(text=msg)

    def _copy_result(self):
        text = self.text_box.get("1.0", "end").strip()
        if not text or text.startswith("⏳") or text.startswith("❌"):
            messagebox.showinfo("空", "没有可复制的结果")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("✓ 已复制")

    def _save_result(self):
        text = self.text_box.get("1.0", "end").strip()
        if not text or text.startswith("⏳") or text.startswith("❌"):
            messagebox.showinfo("空", "没有可保存的结果")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("JSON", "*.json"), ("All", "*.*")],
        )
        if not path:
            return
        if path.endswith(".json"):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"text": text}, f, ensure_ascii=False, indent=2)
        else:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        self._set_status(f"✓ 已保存 → {os.path.basename(path)}")

    def _clear(self):
        self._file_path = None
        self.meta_name.configure(text="—")
        self.meta_size.configure(text="")
        self.drop_lbl.configure(text="拖入文件 / 点击选择", fg=COL_DIM)
        self._set_text("")
        self._clear_segments()
        self.elapsed_lbl.configure(text="")
        self._set_status("就绪")


if __name__ == "__main__":
    app = ASRClient()
    app.mainloop()
