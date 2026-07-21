import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
import threading
import re
import time
import json
import queue
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

HISTORY_FILE = Path.home() / ".linguaflow_history.json"

DEFAULT_CONFIG = {
    "deepseek": {
        "base_url": "http://localhost:3001/proxy/deepseek/v1",
        "api_key": "sk-123456",
        "model": "deepseek-v4-pro",
        "temperature": 0.3,
        "top_p": 1.0,
        "system_prompt": "You are a professional translator. Translate the following English text to Chinese. Only return the translation, no explanations."
    },
    "ollama": {
        "base_url": "http://localhost:3001/proxy/ollama/v1",
        "api_key": "sk-123456",
        "model": "gemma4:e4b",
        "temperature": 0.3,
        "top_p": 1.0,
        "system_prompt": "You are a professional translator. Translate the following Chinese text to English. Only return the translation, no explanations."
    },
    "general": {
        "concurrency": 3,
        "max_retries": 3,
        "retry_delay": 2.0,
        "timeout": 60,
        "translation_mode": "bidirectional"
    }
}

class LinguaFlowApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("LinguaFlow - 智能翻译对比工具")
        self.geometry("1300x750")
        self.minsize(1000, 600)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.config = self.load_config()
        self.sentences = []
        self.translations_cn = []
        self.back_translations_en = []
        self.is_processing = False
        self.progress_queue = queue.Queue()
        self.retry_counts = {}
        self.history = self.load_history()
        self.setup_ui()
        self.check_progress()
        self.after(500, self.fetch_all_models)
        self.bind_all("<Control-v>", lambda e: self.paste_from_clipboard())
        self.bind_all("<Control-o>", lambda e: self.load_file())
        self.bind_all("<Control-s>", lambda e: self.save_config())
        self.bind_all("<Control-Return>", lambda e: self.start_translation())
        self.bind_all("<Control-Escape>", lambda e: self.stop_translation())

    def load_config(self):
        try:
            with open("linguaflow_config.json", "r") as f:
                return json.load(f)
        except:
            return DEFAULT_CONFIG

    def save_config(self, *args):
        self.sync_ui_to_config()
        try:
            with open("linguaflow_config.json", "w") as f:
                json.dump(self.config, f, indent=2)
            self.show_toast("配置已保存")
        except Exception as e:
            messagebox.showerror("错误", f"保存配置失败: {e}")

    def load_history(self):
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except:
            return []

    def save_history(self, entry):
        self.history.insert(0, entry)
        if len(self.history) > 50:
            self.history = self.history[:50]
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(self.history, f, indent=2)
        except:
            pass

    def sync_ui_to_config(self):
        self.config["deepseek"]["base_url"] = self.deepseek_url_entry.get()
        self.config["deepseek"]["api_key"] = self.deepseek_key_entry.get()
        self.config["deepseek"]["model"] = self.deepseek_model_combo.get()
        self.config["deepseek"]["temperature"] = round(self.deepseek_temp.get(), 1)
        self.config["deepseek"]["top_p"] = round(self.deepseek_top_p.get(), 2)
        self.config["deepseek"]["system_prompt"] = self.deepseek_sys_prompt.get("1.0", "end-1c")

        self.config["ollama"]["base_url"] = self.ollama_url_entry.get()
        self.config["ollama"]["api_key"] = self.ollama_key_entry.get()
        self.config["ollama"]["model"] = self.ollama_model_combo.get()
        self.config["ollama"]["temperature"] = round(self.ollama_temp.get(), 1)
        self.config["ollama"]["top_p"] = round(self.ollama_top_p.get(), 2)
        self.config["ollama"]["system_prompt"] = self.ollama_sys_prompt.get("1.0", "end-1c")

        self.config["general"]["concurrency"] = int(self.concurrency_var.get())
        self.config["general"]["translation_mode"] = self.mode_var.get()
        self.config["general"]["max_retries"] = int(self.retries_var.get())
        self.config["general"]["retry_delay"] = float(self.retry_delay_var.get())
        self.config["general"]["timeout"] = int(self.timeout_var.get())

    def setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self.header = ctk.CTkFrame(self, corner_radius=0)
        self.header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        self.header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(self.header, text="LinguaFlow", font=ctk.CTkFont(size=20, weight="bold"),
                     text_color="#6366f1").grid(row=0, column=0, padx=(15, 10), pady=10)
        ctk.CTkLabel(self.header, text="智能翻译对比工具", font=ctk.CTkFont(size=12),
                     text_color="gray").grid(row=0, column=1, sticky="w")

        self.theme_option = ctk.CTkOptionMenu(self.header, values=["自动", "浅色", "深色"],
                                              command=self.change_theme)
        self.theme_option.set("自动")
        self.theme_option.grid(row=0, column=2, padx=10, pady=10)

        ctk.CTkButton(self.header, text="历史", width=60, command=self.show_history).grid(row=0, column=3, padx=5)
        ctk.CTkButton(self.header, text="保存配置", width=80, command=self.save_config).grid(row=0, column=4, padx=5)

        self.content_frame = ctk.CTkFrame(self)
        self.content_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        self.content_frame.grid_columnconfigure(0, weight=2)
        self.content_frame.grid_columnconfigure(1, weight=3)
        self.content_frame.grid_rowconfigure(0, weight=1)

        self.left_frame = ctk.CTkFrame(self.content_frame)
        self.left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.right_frame = ctk.CTkFrame(self.content_frame)
        self.right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        self.setup_left_panel()
        self.setup_right_panel()

        self.status_bar = ctk.CTkLabel(self, text="就绪", anchor="w", fg_color="transparent")
        self.status_bar.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))

    def setup_left_panel(self):
        self.left_frame.grid_rowconfigure(2, weight=1)
        self.left_frame.grid_columnconfigure(0, weight=1)

        tabview = ctk.CTkTabview(self.left_frame)
        tabview.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        tabview.add("DeepSeek (英→中)")
        tabview.add("Ollama (中→英)")
        tabview.add("通用设置")

        self.create_service_tab(tabview.tab("DeepSeek (英→中)"), "deepseek")
        self.create_service_tab(tabview.tab("Ollama (中→英)"), "ollama")
        self.create_general_tab(tabview.tab("通用设置"))

        control_frame = ctk.CTkFrame(self.left_frame)
        control_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        control_frame.grid_columnconfigure((0,1,2), weight=1)

        self.start_btn = ctk.CTkButton(control_frame, text="▶ 开始翻译", command=self.start_translation,
                                       fg_color="#6366f1", hover_color="#818cf8", height=35)
        self.start_btn.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        self.stop_btn = ctk.CTkButton(control_frame, text="⏹ 停止", command=self.stop_translation,
                                      state="disabled", fg_color="gray", height=35)
        self.stop_btn.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.export_btn = ctk.CTkButton(control_frame, text="💾 导出", command=self.export_results,
                                        state="disabled", height=35)
        self.export_btn.grid(row=0, column=2, padx=5, pady=5, sticky="ew")

        self.progress = ctk.CTkProgressBar(control_frame)
        self.progress.grid(row=1, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        self.progress.set(0)

        self.progress_label = ctk.CTkLabel(control_frame, text="0/0", font=ctk.CTkFont(size=10))
        self.progress_label.grid(row=2, column=0, columnspan=3, padx=5, pady=(0,5))

        self.input_frame = ctk.CTkFrame(self.left_frame)
        self.input_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=(0,5))
        self.input_frame.grid_rowconfigure(1, weight=1)
        self.input_frame.grid_columnconfigure(0, weight=1)

        input_toolbar = ctk.CTkFrame(self.input_frame, fg_color="transparent")
        input_toolbar.grid(row=0, column=0, sticky="ew", padx=5, pady=(5,0))
        ctk.CTkButton(input_toolbar, text="📁 文件", width=60, command=self.load_file).pack(side="left", padx=2)
        ctk.CTkButton(input_toolbar, text="📋 粘贴", width=60, command=self.paste_from_clipboard).pack(side="left", padx=2)
        ctk.CTkButton(input_toolbar, text="🗑️", width=30, command=lambda: self.input_text.delete("1.0", "end")).pack(side="left", padx=2)
        self.word_count_label = ctk.CTkLabel(input_toolbar, text="0 词", font=ctk.CTkFont(size=10))
        self.word_count_label.pack(side="right", padx=5)

        self.input_text = ctk.CTkTextbox(self.input_frame, wrap="word", font=ctk.CTkFont(size=11))
        self.input_text.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        self.input_text.bind("<KeyRelease>", self.update_word_count)

    def create_service_tab(self, tab, service):
        tab.grid_columnconfigure(1, weight=1)
        r = 0
        ctk.CTkLabel(tab, text="Base URL:").grid(row=r, column=0, sticky="w", padx=5, pady=(10,2))
        url_entry = ctk.CTkEntry(tab, width=300)
        url_entry.grid(row=r, column=1, sticky="ew", padx=5, pady=(10,2))
        r += 1
        ctk.CTkLabel(tab, text="API Key:").grid(row=r, column=0, sticky="w", padx=5, pady=2)
        key_entry = ctk.CTkEntry(tab, width=300, show="*")
        key_entry.grid(row=r, column=1, sticky="ew", padx=5, pady=2)
        show_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(tab, text="显示", variable=show_var,
                        command=lambda: key_entry.configure(show="" if show_var.get() else "*")).grid(row=r, column=2, padx=5)
        r += 1
        ctk.CTkLabel(tab, text="模型:").grid(row=r, column=0, sticky="w", padx=5, pady=2)
        model_combo = ctk.CTkComboBox(tab, values=[], width=250)
        model_combo.grid(row=r, column=1, sticky="ew", padx=5, pady=2)
        ctk.CTkButton(tab, text="🔄", width=30, command=lambda s=service: self.fetch_models(s)).grid(row=r, column=2, padx=5, pady=2)
        status_label = ctk.CTkLabel(tab, text="●", text_color="gray", font=ctk.CTkFont(size=14))
        status_label.grid(row=r, column=3, padx=5)
        ctk.CTkButton(tab, text="测试", width=50, command=lambda s=service: self.test_connection(s)).grid(row=r, column=4, padx=5)
        r += 1
        ctk.CTkLabel(tab, text="温度:").grid(row=r, column=0, sticky="w", padx=5, pady=2)
        temp_max = 2.0 if service == "deepseek" else 1.0
        temp_slider = ctk.CTkSlider(tab, from_=0.1, to=temp_max, number_of_steps=int((temp_max-0.1)/0.1))
        temp_slider.grid(row=r, column=1, sticky="ew", padx=5, pady=2)
        temp_entry = ctk.CTkEntry(tab, width=60)
        temp_entry.grid(row=r, column=2, padx=5)
        temp_slider.configure(command=lambda v: temp_entry.delete(0,"end") or temp_entry.insert(0, f"{v:.1f}"))
        temp_entry.bind("<Return>", lambda e, s=temp_slider, e_min=0.1, e_max=temp_max: self.sync_entry_to_slider(temp_entry, temp_slider, e_min, e_max, 0.1))
        r += 1
        ctk.CTkLabel(tab, text="Top P:").grid(row=r, column=0, sticky="w", padx=5, pady=2)
        top_p_slider = ctk.CTkSlider(tab, from_=0.05, to=1.0, number_of_steps=19)
        top_p_slider.grid(row=r, column=1, sticky="ew", padx=5, pady=2)
        top_p_entry = ctk.CTkEntry(tab, width=60)
        top_p_entry.grid(row=r, column=2, padx=5)
        top_p_slider.configure(command=lambda v: top_p_entry.delete(0,"end") or top_p_entry.insert(0, f"{v:.2f}"))
        top_p_entry.bind("<Return>", lambda e: self.sync_entry_to_slider(top_p_entry, top_p_slider, 0.05, 1.0, 0.05))
        r += 1
        ctk.CTkLabel(tab, text="系统提示词:").grid(row=r, column=0, sticky="nw", padx=5, pady=(5,2))
        sys_prompt_box = ctk.CTkTextbox(tab, height=60, font=ctk.CTkFont(size=10))
        sys_prompt_box.grid(row=r, column=1, columnspan=3, sticky="ew", padx=5, pady=2)

        if service == "deepseek":
            self.deepseek_url_entry = url_entry
            self.deepseek_key_entry = key_entry
            self.deepseek_model_combo = model_combo
            self.deepseek_status = status_label
            self.deepseek_temp = temp_slider
            self.deepseek_temp_entry = temp_entry
            self.deepseek_top_p = top_p_slider
            self.deepseek_top_p_entry = top_p_entry
            self.deepseek_sys_prompt = sys_prompt_box
        else:
            self.ollama_url_entry = url_entry
            self.ollama_key_entry = key_entry
            self.ollama_model_combo = model_combo
            self.ollama_status = status_label
            self.ollama_temp = temp_slider
            self.ollama_temp_entry = temp_entry
            self.ollama_top_p = top_p_slider
            self.ollama_top_p_entry = top_p_entry
            self.ollama_sys_prompt = sys_prompt_box

        self.load_service_values(service)

    def sync_entry_to_slider(self, entry, slider, min_val, max_val, step):
        try:
            val = float(entry.get().replace(",","."))
            val = max(min_val, min(max_val, val))
            slider.set(val)
            entry.delete(0,"end")
            if step >= 0.1:
                entry.insert(0, f"{val:.1f}")
            else:
                entry.insert(0, f"{val:.2f}")
        except:
            entry.delete(0,"end")
            entry.insert(0, f"{slider.get():.1f}" if step>=0.1 else f"{slider.get():.2f}")

    def load_service_values(self, service):
        if service == "deepseek":
            cfg = self.config["deepseek"]
            self.deepseek_url_entry.insert(0, cfg["base_url"])
            self.deepseek_key_entry.insert(0, cfg["api_key"])
            self.deepseek_model_combo.set(cfg["model"])
            self.deepseek_temp.set(cfg.get("temperature", 0.3))
            self.deepseek_temp_entry.insert(0, f"{cfg.get('temperature',0.3):.1f}")
            self.deepseek_top_p.set(cfg.get("top_p", 1.0))
            self.deepseek_top_p_entry.insert(0, f"{cfg.get('top_p',1.0):.2f}")
            self.deepseek_sys_prompt.insert("1.0", cfg.get("system_prompt", ""))
        else:
            cfg = self.config["ollama"]
            self.ollama_url_entry.insert(0, cfg["base_url"])
            self.ollama_key_entry.insert(0, cfg["api_key"])
            self.ollama_model_combo.set(cfg["model"])
            self.ollama_temp.set(cfg.get("temperature", 0.3))
            self.ollama_temp_entry.insert(0, f"{cfg.get('temperature',0.3):.1f}")
            self.ollama_top_p.set(cfg.get("top_p", 1.0))
            self.ollama_top_p_entry.insert(0, f"{cfg.get('top_p',1.0):.2f}")
            self.ollama_sys_prompt.insert("1.0", cfg.get("system_prompt", ""))

    def create_general_tab(self, tab):
        tab.grid_columnconfigure(1, weight=1)
        r = 0
        ctk.CTkLabel(tab, text="并发数:").grid(row=r, column=0, sticky="w", padx=5, pady=5)
        self.concurrency_var = ctk.StringVar(value="3")
        ctk.CTkComboBox(tab, values=["1","2","3","4","5","6","7","8","9","10","15","20","25","30","40","50","100"],
                        variable=self.concurrency_var, width=80).grid(row=r, column=1, sticky="w", padx=5)
        r += 1
        ctk.CTkLabel(tab, text="翻译模式:").grid(row=r, column=0, sticky="w", padx=5, pady=5)
        self.mode_var = ctk.StringVar(value="bidirectional")
        ctk.CTkComboBox(tab, values=["bidirectional", "en_to_cn_only", "cn_to_en_only"],
                        variable=self.mode_var, width=150).grid(row=r, column=1, sticky="w", padx=5)
        r += 1
        ctk.CTkLabel(tab, text="最大重试:").grid(row=r, column=0, sticky="w", padx=5, pady=5)
        self.retries_var = ctk.StringVar(value="3")
        ctk.CTkComboBox(tab, values=[str(i) for i in range(0,11)], variable=self.retries_var, width=80).grid(row=r, column=1, sticky="w", padx=5)
        r += 1
        ctk.CTkLabel(tab, text="重试延迟(s):").grid(row=r, column=0, sticky="w", padx=5, pady=5)
        self.retry_delay_var = ctk.StringVar(value="2.0")
        ctk.CTkEntry(tab, textvariable=self.retry_delay_var, width=80).grid(row=r, column=1, sticky="w", padx=5)
        r += 1
        ctk.CTkLabel(tab, text="超时(s):").grid(row=r, column=0, sticky="w", padx=5, pady=5)
        self.timeout_var = ctk.StringVar(value="60")
        ctk.CTkEntry(tab, textvariable=self.timeout_var, width=80).grid(row=r, column=1, sticky="w", padx=5)
        r += 1
        ctk.CTkButton(tab, text="保存配置", command=self.save_config).grid(row=r, column=0, columnspan=2, pady=10)
        r += 1
        ctk.CTkButton(tab, text="重置为默认配置", command=self.reset_default_config).grid(row=r, column=0, columnspan=2, pady=5)

        self.concurrency_var.set(str(self.config["general"]["concurrency"]))
        self.mode_var.set(self.config["general"]["translation_mode"])
        self.retries_var.set(str(self.config["general"]["max_retries"]))
        self.retry_delay_var.set(str(self.config["general"]["retry_delay"]))
        self.timeout_var.set(str(self.config["general"]["timeout"]))

    def reset_default_config(self):
        if messagebox.askyesno("确认", "确定要恢复默认配置吗？"):
            self.config = DEFAULT_CONFIG.copy()
            self.save_config()
            self.after(0, self.update_ui_from_config)

    def update_ui_from_config(self):
        self.deepseek_url_entry.delete(0,"end")
        self.deepseek_url_entry.insert(0, self.config["deepseek"]["base_url"])
        self.deepseek_key_entry.delete(0,"end")
        self.deepseek_key_entry.insert(0, self.config["deepseek"]["api_key"])
        self.deepseek_model_combo.set(self.config["deepseek"]["model"])
        self.deepseek_temp.set(self.config["deepseek"]["temperature"])
        self.deepseek_temp_entry.delete(0,"end")
        self.deepseek_temp_entry.insert(0, f"{self.config['deepseek']['temperature']:.1f}")
        self.deepseek_top_p.set(self.config["deepseek"]["top_p"])
        self.deepseek_top_p_entry.delete(0,"end")
        self.deepseek_top_p_entry.insert(0, f"{self.config['deepseek']['top_p']:.2f}")
        self.deepseek_sys_prompt.delete("1.0","end")
        self.deepseek_sys_prompt.insert("1.0", self.config["deepseek"]["system_prompt"])

        self.ollama_url_entry.delete(0,"end")
        self.ollama_url_entry.insert(0, self.config["ollama"]["base_url"])
        self.ollama_key_entry.delete(0,"end")
        self.ollama_key_entry.insert(0, self.config["ollama"]["api_key"])
        self.ollama_model_combo.set(self.config["ollama"]["model"])
        self.ollama_temp.set(self.config["ollama"]["temperature"])
        self.ollama_temp_entry.delete(0,"end")
        self.ollama_temp_entry.insert(0, f"{self.config['ollama']['temperature']:.1f}")
        self.ollama_top_p.set(self.config["ollama"]["top_p"])
        self.ollama_top_p_entry.delete(0,"end")
        self.ollama_top_p_entry.insert(0, f"{self.config['ollama']['top_p']:.2f}")
        self.ollama_sys_prompt.delete("1.0","end")
        self.ollama_sys_prompt.insert("1.0", self.config["ollama"]["system_prompt"])

        self.concurrency_var.set(str(self.config["general"]["concurrency"]))
        self.mode_var.set(self.config["general"]["translation_mode"])
        self.retries_var.set(str(self.config["general"]["max_retries"]))
        self.retry_delay_var.set(str(self.config["general"]["retry_delay"]))
        self.timeout_var.set(str(self.config["general"]["timeout"]))

    def setup_right_panel(self):
        self.right_frame.grid_rowconfigure(0, weight=1)
        self.right_frame.grid_columnconfigure(0, weight=1)

        self.result_tabview = ctk.CTkTabview(self.right_frame)
        self.result_tabview.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.result_tabview.add("对比视图")
        self.result_tabview.add("中文翻译")
        self.result_tabview.add("回译英文")

        self.comparison_text = ctk.CTkTextbox(self.result_tabview.tab("对比视图"), wrap="word", font=ctk.CTkFont(size=11))
        self.comparison_text.pack(fill="both", expand=True, padx=5, pady=5)

        self.cn_text = ctk.CTkTextbox(self.result_tabview.tab("中文翻译"), wrap="word", font=ctk.CTkFont(size=11))
        self.cn_text.pack(fill="both", expand=True, padx=5, pady=5)

        self.en_text = ctk.CTkTextbox(self.result_tabview.tab("回译英文"), wrap="word", font=ctk.CTkFont(size=11))
        self.en_text.pack(fill="both", expand=True, padx=5, pady=5)

        for txt in [self.comparison_text, self.cn_text, self.en_text]:
            txt.tag_config("header", foreground="#6366f1")
            txt.tag_config("original", foreground="#94a3b8")
            txt.tag_config("chinese", foreground="#10b981")
            txt.tag_config("english", foreground="#8b5cf6")
            txt.tag_config("separator", foreground="#475569")

    def change_theme(self, choice):
        if choice == "自动":
            ctk.set_appearance_mode("system")
        elif choice == "浅色":
            ctk.set_appearance_mode("light")
        else:
            ctk.set_appearance_mode("dark")

    def update_word_count(self, event=None):
        text = self.input_text.get("1.0", "end-1c").strip()
        count = len(text.split())
        self.word_count_label.configure(text=f"{count} 词")

    def paste_from_clipboard(self):
        try:
            text = self.clipboard_get()
            self.input_text.delete("1.0", "end")
            self.input_text.insert("1.0", text)
            self.update_word_count()
        except:
            pass

    def load_file(self):
        path = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if path:
            with open(path, "r", encoding="utf-8") as f:
                self.input_text.delete("1.0", "end")
                self.input_text.insert("1.0", f.read())
            self.update_word_count()

    def fetch_models(self, service):
        cfg = self.config[service]
        url = cfg["base_url"].rstrip("/") + "/models"
        headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg["api_key"] else {}
        def fetch():
            try:
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    models = []
                    if "data" in data:
                        models = [m["id"] for m in data["data"]]
                    elif "models" in data:
                        if isinstance(data["models"], list) and len(data["models"])>0 and isinstance(data["models"][0], dict):
                            models = [m.get("id", m.get("name", "")) for m in data["models"]]
                        else:
                            models = data["models"]
                    if models:
                        combo = self.deepseek_model_combo if service=="deepseek" else self.ollama_model_combo
                        self.after(0, lambda: combo.configure(values=models))
                        self.after(0, lambda: combo.set(models[0]) if combo.get() not in models else None)
            except:
                pass
        threading.Thread(target=fetch, daemon=True).start()

    def fetch_all_models(self):
        self.fetch_models("deepseek")
        self.fetch_models("ollama")

    def test_connection(self, service):
        self.sync_ui_to_config()
        cfg = self.config[service]
        url = cfg["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg["api_key"] else {}
        payload = {"model": cfg["model"], "messages": [{"role":"user","content":"Hello"}], "max_tokens":5}
        status_label = self.deepseek_status if service=="deepseek" else self.ollama_status
        status_label.configure(text_color="orange")
        def test():
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=10)
                if resp.status_code==200:
                    self.after(0, lambda: status_label.configure(text_color="green"))
                    self.after(0, lambda: messagebox.showinfo("成功", f"{service} 连接成功"))
                    self.after(0, lambda: self.fetch_models(service))
                else:
                    self.after(0, lambda: status_label.configure(text_color="red"))
                    err = f"HTTP {resp.status_code}"
                    try: err = resp.json().get("error",{}).get("message", err)
                    except: pass
                    self.after(0, lambda: messagebox.showerror("失败", err))
            except Exception as e:
                self.after(0, lambda: status_label.configure(text_color="red"))
                self.after(0, lambda: messagebox.showerror("错误", str(e)))
        threading.Thread(target=test, daemon=True).start()

    def show_history(self):
        win = ctk.CTkToplevel(self)
        win.title("翻译历史")
        win.geometry("500x400")
        win.grab_set()
        listbox = tk.Listbox(win, bg="#1e293b", fg="white", font=("Consolas",10))
        listbox.pack(fill="both", expand=True, padx=10, pady=10)
        for i, entry in enumerate(self.history[-30:]):
            listbox.insert("end", f"{i+1}. {entry['time']} - {entry['mode']} ({len(entry['sentences'])}句)")
        def load_selected():
            sel = listbox.curselection()
            if sel:
                idx = sel[0]
                entry = self.history[-(idx+1)] if idx < len(self.history) else self.history[idx]
                self.input_text.delete("1.0","end")
                self.input_text.insert("1.0", entry["original_text"])
                self.update_word_count()
                win.destroy()
        ctk.CTkButton(win, text="加载选中", command=load_selected).pack(pady=5)

    def start_translation(self):
        self.sync_ui_to_config()
        text = self.input_text.get("1.0","end-1c").strip()
        if not text:
            messagebox.showwarning("警告", "请输入文本")
            return
        self.sentences = self.split_sentences(text)
        if not self.sentences:
            messagebox.showwarning("警告", "未找到句子")
            return
        self.translations_cn = [""] * len(self.sentences)
        self.back_translations_en = [""] * len(self.sentences)
        self.retry_counts = {}
        self.progress.set(0)
        self.progress_label.configure(text=f"0/{len(self.sentences)}")
        self.is_processing = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.export_btn.configure(state="disabled")
        self.comparison_text.delete("1.0","end")
        self.cn_text.delete("1.0","end")
        self.en_text.delete("1.0","end")
        threading.Thread(target=self.run_translation, daemon=True).start()

    def run_translation(self):
        concurrency = int(self.config["general"]["concurrency"])
        try:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [executor.submit(self.process_sentence, i, s) for i, s in enumerate(self.sentences) if self.is_processing]
                for f in as_completed(futures):
                    if not self.is_processing:
                        break
                    f.result()
        except Exception as e:
            self.progress_queue.put(("error", -1, str(e)))
        finally:
            if self.is_processing:
                self.after(0, self.translation_complete)

    def process_sentence(self, index, sentence):
        mode = self.config["general"]["translation_mode"]
        if mode in ("bidirectional", "en_to_cn_only"):
            cn = self.translate("deepseek", sentence, index)
            if not cn or not self.is_processing:
                return
            if mode == "en_to_cn_only":
                self.progress_queue.put(("complete", index))
                return
        if mode in ("bidirectional", "cn_to_en_only"):
            src = self.translations_cn[index] if mode=="bidirectional" else sentence
            en = self.translate("ollama", src, index)
            if en and self.is_processing:
                self.progress_queue.put(("complete", index))

    def translate(self, service, text, index):
        cfg = self.config[service]
        url = cfg["base_url"].rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg["api_key"] else {}
        messages = [
            {"role": "system", "content": cfg["system_prompt"]},
            {"role": "user", "content": text}
        ]
        payload = {
            "model": cfg["model"],
            "messages": messages,
            "temperature": cfg.get("temperature", 0.3),
            "top_p": cfg.get("top_p", 1.0)
        }
        max_retries = int(self.config["general"]["max_retries"])
        retry_delay = float(self.config["general"]["retry_delay"])
        timeout = int(self.config["general"]["timeout"])
        for attempt in range(max_retries+1):
            if not self.is_processing:
                return None
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
                if resp.status_code == 200:
                    result = resp.json()["choices"][0]["message"]["content"].strip()
                    if service == "deepseek":
                        self.translations_cn[index] = result
                    else:
                        self.back_translations_en[index] = result
                    self.progress_queue.put(("update", index, service, result))
                    if attempt > 0:
                        self.retry_counts[f"{service}_{index}"] = attempt
                    return result
                elif resp.status_code == 429 and attempt < max_retries:
                    time.sleep(retry_delay * (2**attempt))
                elif resp.status_code >= 500 and attempt < max_retries:
                    time.sleep(retry_delay)
                elif attempt < max_retries and resp.status_code not in (400,401,403,404):
                    time.sleep(retry_delay)
                else:
                    raise Exception(f"HTTP {resp.status_code}")
            except requests.exceptions.RequestException as e:
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    raise e
        return None

    def check_progress(self):
        try:
            while True:
                msg = self.progress_queue.get_nowait()
                if msg[0] == "update":
                    self.update_display()
                elif msg[0] == "complete":
                    total = len(self.sentences)
                    completed = sum(1 for t in (self.back_translations_en if self.config["general"]["translation_mode"]!="en_to_cn_only" else self.translations_cn) if t)
                    self.progress.set(completed/total)
                    self.progress_label.configure(text=f"{completed}/{total}")
                    if completed == total:
                        self.translation_complete()
                elif msg[0] == "error":
                    self.status_bar.configure(text=f"句子{msg[1]+1}: {msg[2]}")
        except queue.Empty:
            pass
        self.after(100, self.check_progress)

    def update_display(self):
        self.comparison_text.delete("1.0","end")
        self.cn_text.delete("1.0","end")
        self.en_text.delete("1.0","end")
        mode = self.config["general"]["translation_mode"]
        for i, s in enumerate(self.sentences):
            cn = self.translations_cn[i] or "翻译中..."
            en = self.back_translations_en[i] or "翻译中..."
            if mode == "bidirectional":
                self.comparison_text.insert("end", f"━━ 句子 {i+1} ━━\n", "header")
                self.comparison_text.insert("end", f"原文: {s}\n", "original")
                self.comparison_text.insert("end", f"中文: {cn}\n", "chinese")
                self.comparison_text.insert("end", f"回译: {en}\n", "english")
                self.comparison_text.insert("end", "─"*50 + "\n\n", "separator")
                self.cn_text.insert("end", f"{cn}\n\n")
                self.en_text.insert("end", f"{en}\n\n")
            elif mode == "en_to_cn_only":
                self.comparison_text.insert("end", f"━━ 句子 {i+1} ━━\n", "header")
                self.comparison_text.insert("end", f"原文: {s}\n", "original")
                self.comparison_text.insert("end", f"中文: {cn}\n", "chinese")
                self.comparison_text.insert("end", "─"*50 + "\n\n", "separator")
                self.cn_text.insert("end", f"{cn}\n\n")
            else:
                self.comparison_text.insert("end", f"━━ 句子 {i+1} ━━\n", "header")
                self.comparison_text.insert("end", f"原文: {s}\n", "chinese")
                self.comparison_text.insert("end", f"英文: {en}\n", "english")
                self.comparison_text.insert("end", "─"*50 + "\n\n", "separator")
                self.en_text.insert("end", f"{en}\n\n")

    def stop_translation(self):
        self.is_processing = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def translation_complete(self):
        self.is_processing = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.export_btn.configure(state="normal")
        self.progress.set(1)
        self.progress_label.configure(text=f"{len(self.sentences)}/{len(self.sentences)}")
        self.status_bar.configure(text="翻译完成")
        history_entry = {
            "time": datetime.now().isoformat(),
            "mode": self.config["general"]["translation_mode"],
            "original_text": self.input_text.get("1.0","end-1c"),
            "sentences": self.sentences,
            "translations_cn": self.translations_cn,
            "back_translations_en": self.back_translations_en
        }
        self.save_history(history_entry)

    def export_results(self):
        if not self.sentences:
            return
        path = filedialog.asksaveasfilename(defaultextension=".txt",
                                           filetypes=[("Text","*.txt"),("HTML","*.html"),("JSON","*.json")])
        if not path:
            return
        if path.endswith(".html"):
            self.export_html(path)
        elif path.endswith(".json"):
            self.export_json(path)
        else:
            self.export_text(path)
        messagebox.showinfo("成功", "导出完成")

    def export_text(self, path):
        with open(path,"w",encoding="utf-8") as f:
            f.write(f"LinguaFlow 翻译结果\n{'='*50}\n")
            for i,s in enumerate(self.sentences):
                f.write(f"\n句子 {i+1}:\n原文: {s}\n")
                if self.translations_cn[i]: f.write(f"中文: {self.translations_cn[i]}\n")
                if self.back_translations_en[i]: f.write(f"回译: {self.back_translations_en[i]}\n")
                f.write("-"*40+"\n")

    def export_html(self, path):
        with open(path,"w",encoding="utf-8") as f:
            f.write("<html><head><meta charset='utf-8'><style>body{background:#0f172a;color:#e2e8f0;font-family:Segoe UI;padding:30px;}.s{border-left:3px solid #6366f1;padding:10px;margin:10px 0;}.c{color:#34d399;}.e{color:#a78bfa;}</style></head><body>")
            for i,s in enumerate(self.sentences):
                f.write(f"<div class='s'><b>句子 {i+1}</b><p>原文: {s}</p>")
                if self.translations_cn[i]: f.write(f"<p class='c'>中文: {self.translations_cn[i]}</p>")
                if self.back_translations_en[i]: f.write(f"<p class='e'>回译: {self.back_translations_en[i]}</p>")
                f.write("</div>")
            f.write("</body></html>")

    def export_json(self, path):
        data = [{"id":i+1, "original":s, "chinese":self.translations_cn[i], "back_translation":self.back_translations_en[i]} for i,s in enumerate(self.sentences)]
        with open(path,"w",encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def split_sentences(self, text):
        sentences = re.split(r'(?<=[.!?。！？])\s+', text.strip())
        result = []
        for s in sentences:
            result.extend([sub.strip() for sub in re.split(r'\n+', s.strip()) if sub.strip()])
        return result

    def show_toast(self, message):
        toast = ctk.CTkToplevel(self)
        toast.overrideredirect(True)
        toast.wm_attributes("-topmost", True)
        ctk.CTkLabel(toast, text=message, fg_color="#6366f1", corner_radius=8, padx=20, pady=10).pack()
        x = self.winfo_x() + self.winfo_width()//2 - 100
        y = self.winfo_y() + self.winfo_height() - 100
        toast.geometry(f"200x40+{x}+{y}")
        self.after(2000, toast.destroy)

    def on_close(self):
        self.save_config()
        self.destroy()

if __name__ == "__main__":
    app = LinguaFlowApp()
    app.mainloop()
