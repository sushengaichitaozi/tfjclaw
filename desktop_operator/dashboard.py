from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from PIL import Image, ImageTk

from .config import AgentConfig
from .runner import AgentRunner
from .runtime import AgentRuntime

PALETTE = {
    "canvas": "#F4EDE3",
    "surface": "#FFF9F1",
    "surface_alt": "#F9F1E4",
    "ink": "#17313A",
    "muted": "#6B675F",
    "line": "#D8C8B4",
    "teal": "#1C746B",
    "teal_dark": "#14574F",
    "rust": "#C76034",
    "amber": "#B67B18",
    "green": "#2E7D4E",
    "red": "#A63A3A",
    "screen": "#DCE4DE",
}

STATUS_COLORS = {
    "idle": PALETTE["muted"],
    "running": PALETTE["teal"],
    "starting": PALETTE["amber"],
    "running doctor": PALETTE["amber"],
    "doctor failed": PALETTE["red"],
    "completed": PALETTE["green"],
    "blocked": PALETTE["amber"],
    "stopped": PALETTE["amber"],
    "stop requested": PALETTE["amber"],
    "error": PALETTE["red"],
    "max steps": PALETTE["amber"],
}

TASK_TEMPLATES = [
    ("Browser Workflow", "Open the browser, complete the website workflow, and summarize the result in Chinese. Use DOM tools before blind clicks. Stop before any irreversible step unless I approve it."),
    ("Cross-App Update", "Work across desktop apps and browser tabs to gather the needed information, then produce a concise Chinese checklist. Use OCR and UI Automation when DOM is unavailable."),
    ("Desktop Ops", "Open the required Windows apps, switch between them, type the needed content, and verify the final screen state before reporting completion."),
    ("Review First", "Inspect the current screen, identify the next safe action, and explain the plan in Chinese before making any irreversible change."),
]


class AgentDashboard:
    def __init__(self, root: tk.Tk, env_file: Path) -> None:
        self.root = root
        self.env_file = env_file
        self.event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.runner: AgentRunner | None = None
        self.worker: threading.Thread | None = None
        self.doctor_worker: threading.Thread | None = None
        self.current_run_dir = ""
        self.current_screenshot_path = ""
        self.run_history_map: list[Path] = []
        self._screenshot_image: ImageTk.PhotoImage | None = None

        self.root.title("Desktop Agent Command Deck")
        self.root.geometry("1580x980")
        self.root.minsize(1280, 820)
        self.root.configure(bg=PALETTE["canvas"])
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.status_var = tk.StringVar(value="Idle")
        self.status_detail_var = tk.StringVar(value="Waiting for a task.")
        self.run_dir_var = tk.StringVar(value="-")
        self.step_var = tk.StringVar(value="-")
        self.active_window_var = tk.StringVar(value="-")
        self.model_var = tk.StringVar(value="-")
        self.base_url_var = tk.StringVar(value="-")
        self.api_status_var = tk.StringVar(value="-")
        self.trust_env_var = tk.StringVar(value="-")
        self.admin_status_var = tk.StringVar(value="-")
        self.desktop_status_var = tk.StringVar(value="-")
        self.browser_status_var = tk.StringVar(value="-")
        self.ocr_status_var = tk.StringVar(value="-")
        self.uia_status_var = tk.StringVar(value="-")
        self.allow_shell_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.max_steps_var = tk.IntVar(value=60)
        self.browser_strategy_var = tk.StringVar(value="reuse")

        self._configure_fonts()
        self._configure_styles()
        self._build_layout()
        self._load_config_summary()
        self.root.after(200, self._drain_events)
        self._schedule_initial_doctor()

    def _configure_fonts(self) -> None:
        tkfont.nametofont("TkDefaultFont").configure(family="Segoe UI", size=10)
        tkfont.nametofont("TkTextFont").configure(family="Segoe UI", size=10)
        tkfont.nametofont("TkFixedFont").configure(family="Consolas", size=10)
        self.title_font = tkfont.Font(family="Bahnschrift SemiBold", size=24)
        self.section_font = tkfont.Font(family="Bahnschrift SemiBold", size=13)
        self.card_title_font = tkfont.Font(family="Bahnschrift SemiBold", size=11)
        self.card_value_font = tkfont.Font(family="Segoe UI Semibold", size=12)
        self.meta_font = tkfont.Font(family="Segoe UI", size=9)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Deck.TButton", background=PALETTE["surface_alt"], foreground=PALETTE["ink"], bordercolor=PALETTE["line"], focuscolor=PALETTE["surface_alt"], padding=(12, 8))
        style.map("Deck.TButton", background=[("active", "#F2E4D2")])
        style.configure("Accent.TButton", background=PALETTE["teal"], foreground="#FFFFFF", bordercolor=PALETTE["teal"], focuscolor=PALETTE["teal"], padding=(14, 9))
        style.map("Accent.TButton", background=[("active", PALETTE["teal_dark"])])
        style.configure("Danger.TButton", background=PALETTE["rust"], foreground="#FFFFFF", bordercolor=PALETTE["rust"], focuscolor=PALETTE["rust"], padding=(14, 9))
        style.map("Danger.TButton", background=[("active", "#A94F2A")])
        style.configure("Deck.TCheckbutton", background=PALETTE["surface"], foreground=PALETTE["ink"])
        style.configure("Deck.TRadiobutton", background=PALETTE["surface"], foreground=PALETTE["ink"])
        style.configure("Deck.TNotebook", background=PALETTE["canvas"], borderwidth=0)
        style.configure("Deck.TNotebook.Tab", background=PALETTE["surface_alt"], foreground=PALETTE["muted"], padding=(12, 8), font=self.card_title_font)
        style.map("Deck.TNotebook.Tab", background=[("selected", PALETTE["surface"])], foreground=[("selected", PALETTE["ink"])])
        style.configure("Deck.Horizontal.TProgressbar", troughcolor=PALETTE["surface_alt"], background=PALETTE["teal"], bordercolor=PALETTE["surface_alt"], lightcolor=PALETTE["teal"], darkcolor=PALETTE["teal"])

    def _build_layout(self) -> None:
        outer = tk.Frame(self.root, bg=PALETTE["canvas"])
        outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        self._build_hero(outer).grid(row=0, column=0, sticky="ew")
        self._build_session_strip(outer).grid(row=1, column=0, sticky="ew", pady=(14, 14))
        self._build_control_bar(outer).grid(row=2, column=0, sticky="ew", pady=(0, 14))

        body = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        body.grid(row=3, column=0, sticky="nsew")

        left = tk.Frame(body, bg=PALETTE["canvas"])
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        body.add(left, weight=3)

        right = tk.Frame(body, bg=PALETTE["canvas"])
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        body.add(right, weight=2)

        self._build_task_studio(left).grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self._build_screen_stage(left).grid(row=1, column=0, sticky="nsew")
        self._build_workspace(right).grid(row=0, column=0, sticky="nsew")

    def _build_hero(self, parent: tk.Widget) -> tk.Frame:
        hero = tk.Frame(parent, bg=PALETTE["ink"], padx=20, pady=18)
        hero.columnconfigure(0, weight=1)
        title_wrap = tk.Frame(hero, bg=PALETTE["ink"])
        title_wrap.grid(row=0, column=0, sticky="w")
        tk.Label(title_wrap, text="Desktop Agent Command Deck", bg=PALETTE["ink"], fg="#F9F4EC", font=self.title_font).pack(anchor="w")
        tk.Label(title_wrap, text="A local control console for browser workflows, cross-app automation, OCR, Windows UI Automation, mouse, and keyboard.", bg=PALETTE["ink"], fg="#D7E6E2", font=self.meta_font).pack(anchor="w", pady=(6, 0))
        self.status_chip = tk.Label(hero, text="Idle", bg=STATUS_COLORS["idle"], fg="#FFFFFF", padx=14, pady=8, font=self.card_title_font)
        self.status_chip.grid(row=0, column=1, sticky="ne")

        grid = tk.Frame(hero, bg=PALETTE["ink"])
        grid.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        for index in range(4):
            grid.columnconfigure(index, weight=1)
        self._build_info_card(grid, "Model", self.model_var, "Connected language model", PALETTE["teal"]).grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self._build_info_card(grid, "API Status", self.api_status_var, "Key presence and relay health", PALETTE["rust"]).grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self._build_info_card(grid, "Privileges", self.admin_status_var, "Same desktop rights as this process", PALETTE["amber"]).grid(row=0, column=2, sticky="ew", padx=(0, 10))
        self._build_info_card(grid, "Network Mode", self.trust_env_var, "Proxy inheritance for relay requests", PALETTE["green"]).grid(row=0, column=3, sticky="ew")
        return hero

    def _build_session_strip(self, parent: tk.Widget) -> tk.Frame:
        strip = tk.Frame(parent, bg=PALETTE["surface"], highlightbackground=PALETTE["line"], highlightthickness=1)
        strip.columnconfigure(1, weight=1)
        strip.columnconfigure(4, weight=1)
        tk.Label(strip, text="Environment File", bg=PALETTE["surface"], fg=PALETTE["muted"], font=self.meta_font).grid(row=0, column=0, sticky="w", padx=(16, 10), pady=(12, 2))
        tk.Label(strip, text="Relay Base URL", bg=PALETTE["surface"], fg=PALETTE["muted"], font=self.meta_font).grid(row=0, column=2, sticky="w", padx=(16, 10), pady=(12, 2))
        tk.Label(strip, text="Current Run", bg=PALETTE["surface"], fg=PALETTE["muted"], font=self.meta_font).grid(row=0, column=4, sticky="w", padx=(16, 10), pady=(12, 2))
        self.env_entry = ttk.Entry(strip)
        self.env_entry.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(16, 10), pady=(0, 12))
        self.env_entry.insert(0, str(self.env_file))
        tk.Label(strip, textvariable=self.base_url_var, bg=PALETTE["surface"], fg=PALETTE["ink"], font=self.card_value_font, anchor="w").grid(row=1, column=2, columnspan=2, sticky="ew", padx=(16, 10), pady=(0, 12))
        tk.Label(strip, textvariable=self.run_dir_var, bg=PALETTE["surface"], fg=PALETTE["ink"], font=self.meta_font, anchor="w", wraplength=320, justify=tk.LEFT).grid(row=1, column=4, sticky="ew", padx=(16, 16), pady=(0, 12))
        return strip

    def _build_control_bar(self, parent: tk.Widget) -> tk.Frame:
        bar = tk.Frame(parent, bg=PALETTE["canvas"])
        bar.columnconfigure(2, weight=1)
        self.start_button = ttk.Button(bar, text="Start Agent", style="Accent.TButton", command=self.start_run)
        self.start_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(bar, text="Stop", style="Danger.TButton", command=self.stop_run, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.progress = ttk.Progressbar(bar, mode="indeterminate", style="Deck.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=2, sticky="ew", padx=(18, 18))
        quick = tk.Frame(bar, bg=PALETTE["canvas"])
        quick.grid(row=0, column=3, sticky="e")
        self.doctor_button = ttk.Button(quick, text="Run Doctor", style="Deck.TButton", command=self.run_doctor)
        self.doctor_button.pack(side=tk.LEFT)
        ttk.Button(quick, text="Open Run Dir", style="Deck.TButton", command=self.open_run_dir).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(quick, text="Open Screenshot", style="Deck.TButton", command=self.open_screenshot).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(quick, text="Relaunch as Admin", style="Deck.TButton", command=self.relaunch_as_admin).pack(side=tk.LEFT, padx=(8, 0))
        self.status_line = tk.Frame(parent, bg=PALETTE["canvas"])
        self.status_line.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.status_line.columnconfigure(1, weight=1)
        tk.Label(self.status_line, textvariable=self.status_var, bg=PALETTE["canvas"], fg=PALETTE["ink"], font=self.card_title_font).grid(row=0, column=0, sticky="w")
        tk.Label(self.status_line, textvariable=self.status_detail_var, bg=PALETTE["canvas"], fg=PALETTE["muted"], font=self.meta_font, wraplength=1100, justify=tk.LEFT).grid(row=0, column=1, sticky="w", padx=(14, 0))
        return bar

    def _build_task_studio(self, parent: tk.Widget) -> tk.Frame:
        studio = tk.Frame(parent, bg=PALETTE["surface"], highlightbackground=PALETTE["line"], highlightthickness=1)
        studio.columnconfigure(0, weight=1)
        header = tk.Frame(studio, bg=PALETTE["surface"])
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 10))
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="Task Studio", bg=PALETTE["surface"], fg=PALETTE["ink"], font=self.section_font).grid(row=0, column=0, sticky="w")
        tk.Label(header, text="Write the end goal, mention target apps or sites, define the final output, and say which steps should pause for approval. Live agent replies stay visible here.", bg=PALETTE["surface"], fg=PALETTE["muted"], font=self.meta_font).grid(row=1, column=0, sticky="w", pady=(4, 0))
        presets = tk.Frame(studio, bg=PALETTE["surface"])
        presets.grid(row=1, column=0, sticky="ew", padx=16)
        for label, template in TASK_TEMPLATES:
            ttk.Button(presets, text=label, style="Deck.TButton", command=lambda value=template: self.apply_template(value)).pack(side=tk.LEFT, padx=(0, 8), pady=(0, 10))
        self.task_text = ScrolledText(studio, height=8, wrap=tk.WORD, relief=tk.FLAT, bd=0, font=("Segoe UI", 10), bg="#FFFDF8", fg=PALETTE["ink"], insertbackground=PALETTE["ink"], padx=12, pady=12)
        self.task_text.grid(row=2, column=0, sticky="nsew", padx=16)
        self.task_text.insert("1.0", TASK_TEMPLATES[0][1])
        reply_panel = tk.Frame(studio, bg=PALETTE["surface"], highlightbackground=PALETTE["line"], highlightthickness=1)
        reply_panel.grid(row=3, column=0, sticky="ew", padx=16, pady=(12, 0))
        reply_panel.columnconfigure(0, weight=1)
        reply_header = tk.Frame(reply_panel, bg=PALETTE["surface"])
        reply_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 8))
        reply_header.columnconfigure(0, weight=1)
        tk.Label(reply_header, text="Live Reply", bg=PALETTE["surface"], fg=PALETTE["ink"], font=self.card_title_font).grid(row=0, column=0, sticky="w")
        tk.Label(reply_header, text="Assistant progress updates and short replies appear here immediately.", bg=PALETTE["surface"], fg=PALETTE["muted"], font=self.meta_font).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(reply_header, text="Copy Reply", style="Deck.TButton", command=self.copy_live_reply).grid(row=0, column=1, rowspan=2, sticky="e")
        self.reply_text = ScrolledText(reply_panel, height=7, wrap=tk.WORD, relief=tk.FLAT, bd=0, font=("Segoe UI", 10), bg="#FFFDF8", fg=PALETTE["ink"], insertbackground=PALETTE["ink"], padx=12, pady=12, state=tk.DISABLED)
        self.reply_text.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.reply_text.tag_configure("assistant_label", foreground=PALETTE["teal"], font=("Segoe UI Semibold", 10))
        self.reply_text.tag_configure("assistant_body", foreground=PALETTE["ink"])
        self.reply_text.tag_configure("final_label", foreground=PALETTE["green"], font=("Segoe UI Semibold", 10))
        self.reply_text.tag_configure("final_body", foreground=PALETTE["ink"])
        self.reply_text.tag_configure("error_label", foreground=PALETTE["red"], font=("Segoe UI Semibold", 10))
        self.reply_text.tag_configure("error_body", foreground=PALETTE["red"])
        options = tk.Frame(studio, bg=PALETTE["surface"])
        options.grid(row=4, column=0, sticky="ew", padx=16, pady=(12, 14))
        ttk.Checkbutton(options, text="Dry run", style="Deck.TCheckbutton", variable=self.dry_run_var).pack(side=tk.LEFT)
        ttk.Checkbutton(options, text="Allow shell launch", style="Deck.TCheckbutton", variable=self.allow_shell_var).pack(side=tk.LEFT, padx=(14, 0))
        browser_mode = tk.Frame(options, bg=PALETTE["surface"])
        browser_mode.pack(side=tk.LEFT, padx=(18, 0))
        tk.Label(browser_mode, text="Browser mode", bg=PALETTE["surface"], fg=PALETTE["muted"], font=self.meta_font).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Radiobutton(browser_mode, text="Reuse current browser", style="Deck.TRadiobutton", variable=self.browser_strategy_var, value="reuse").pack(side=tk.LEFT)
        ttk.Radiobutton(browser_mode, text="Allow new controlled browser", style="Deck.TRadiobutton", variable=self.browser_strategy_var, value="managed").pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(options, text="Max steps (0 = unlimited)", bg=PALETTE["surface"], fg=PALETTE["muted"], font=self.meta_font).pack(side=tk.LEFT, padx=(18, 8))
        ttk.Spinbox(options, from_=0, to=999999, textvariable=self.max_steps_var, width=8).pack(side=tk.LEFT)
        ttk.Button(options, text="Clear Task", style="Deck.TButton", command=self.clear_task).pack(side=tk.RIGHT)
        return studio

    def _build_screen_stage(self, parent: tk.Widget) -> tk.Frame:
        stage = tk.Frame(parent, bg=PALETTE["surface"], highlightbackground=PALETTE["line"], highlightthickness=1)
        stage.columnconfigure(0, weight=1)
        stage.rowconfigure(1, weight=1)
        header = tk.Frame(stage, bg=PALETTE["surface"])
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 10))
        header.columnconfigure(1, weight=1)
        tk.Label(header, text="Live Screen", bg=PALETTE["surface"], fg=PALETTE["ink"], font=self.section_font).grid(row=0, column=0, sticky="w")
        tk.Label(header, textvariable=self.step_var, bg=PALETTE["surface_alt"], fg=PALETTE["ink"], padx=10, pady=4, font=self.meta_font).grid(row=0, column=2, sticky="e")
        tk.Label(header, textvariable=self.active_window_var, bg=PALETTE["surface"], fg=PALETTE["muted"], font=self.meta_font, wraplength=780, justify=tk.LEFT).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        frame = tk.Frame(stage, bg=PALETTE["screen"], padx=14, pady=14)
        frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.screenshot_label = tk.Label(frame, text="Doctor or run the agent to load the latest screenshot.", bg=PALETTE["screen"], fg=PALETTE["muted"], font=self.meta_font, anchor=tk.CENTER, justify=tk.CENTER)
        self.screenshot_label.grid(row=0, column=0, sticky="nsew")
        return stage

    def _build_workspace(self, parent: tk.Widget) -> tk.Frame:
        frame = tk.Frame(parent, bg=PALETTE["canvas"])
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.workspace_notebook = ttk.Notebook(frame, style="Deck.TNotebook")
        self.workspace_notebook.grid(row=0, column=0, sticky="nsew")
        self.overview_tab = tk.Frame(self.workspace_notebook, bg=PALETTE["surface"])
        self.log_tab = tk.Frame(self.workspace_notebook, bg=PALETTE["surface"])
        self.windows_tab = tk.Frame(self.workspace_notebook, bg=PALETTE["surface"])
        self.runs_tab = tk.Frame(self.workspace_notebook, bg=PALETTE["surface"])
        self.workspace_notebook.add(self.overview_tab, text="Overview")
        self.workspace_notebook.add(self.log_tab, text="Log")
        self.workspace_notebook.add(self.windows_tab, text="Windows")
        self.workspace_notebook.add(self.runs_tab, text="Runs")
        self._build_overview_tab()
        self._build_log_tab()
        self._build_windows_tab()
        self._build_runs_tab()
        return frame

    def _build_overview_tab(self) -> None:
        self.overview_tab.columnconfigure(0, weight=1)
        self.overview_tab.rowconfigure(1, weight=1)
        grid = tk.Frame(self.overview_tab, bg=PALETTE["surface"])
        grid.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        for index in range(2):
            grid.columnconfigure(index, weight=1)
        self._build_info_card(grid, "Desktop Control", self.desktop_status_var, "Runtime capability snapshot", PALETTE["teal"]).grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=(0, 10))
        self._build_info_card(grid, "Browser DOM", self.browser_status_var, "Runtime capability snapshot", PALETTE["rust"]).grid(row=0, column=1, sticky="ew", pady=(0, 10))
        self._build_info_card(grid, "OCR", self.ocr_status_var, "Runtime capability snapshot", PALETTE["amber"]).grid(row=1, column=0, sticky="ew", padx=(0, 10))
        self._build_info_card(grid, "UI Automation", self.uia_status_var, "Runtime capability snapshot", PALETTE["green"]).grid(row=1, column=1, sticky="ew")

        panel = tk.Frame(self.overview_tab, bg=PALETTE["surface"], highlightbackground=PALETTE["line"], highlightthickness=1)
        panel.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        header = tk.Frame(panel, bg=PALETTE["surface"])
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="Final Answer", bg=PALETTE["surface"], fg=PALETTE["ink"], font=self.section_font).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Copy", style="Deck.TButton", command=self.copy_final_answer).grid(row=0, column=1, sticky="e")
        self.final_text = ScrolledText(panel, wrap=tk.WORD, relief=tk.FLAT, bd=0, font=("Segoe UI", 10), bg="#FFFDF8", fg=PALETTE["ink"], insertbackground=PALETTE["ink"], padx=12, pady=12, state=tk.DISABLED)
        self.final_text.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

    def _build_log_tab(self) -> None:
        self.log_tab.columnconfigure(0, weight=1)
        self.log_tab.rowconfigure(1, weight=1)
        header = tk.Frame(self.log_tab, bg=PALETTE["surface"])
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="Run Log", bg=PALETTE["surface"], fg=PALETTE["ink"], font=self.section_font).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Copy Log", style="Deck.TButton", command=self.copy_log).grid(row=0, column=1, sticky="e")
        ttk.Button(header, text="Clear Log", style="Deck.TButton", command=self._clear_log).grid(row=0, column=2, sticky="e", padx=(8, 0))
        self.log_text = ScrolledText(self.log_tab, wrap=tk.WORD, relief=tk.FLAT, bd=0, font=("Consolas", 10), bg="#FFFDF8", fg=PALETTE["ink"], insertbackground=PALETTE["ink"], padx=12, pady=12, state=tk.DISABLED)
        self.log_text.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        for name, color in {"run": PALETTE["teal"], "tool": PALETTE["rust"], "doctor": PALETTE["amber"], "error": PALETTE["red"], "final": PALETTE["green"], "assistant": PALETTE["ink"]}.items():
            self.log_text.tag_configure(name, foreground=color)

    def _build_windows_tab(self) -> None:
        self.windows_tab.columnconfigure(0, weight=1)
        self.windows_tab.rowconfigure(1, weight=1)
        header = tk.Frame(self.windows_tab, bg=PALETTE["surface"])
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="Visible Windows", bg=PALETTE["surface"], fg=PALETTE["ink"], font=self.section_font).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Insert Into Task", style="Deck.TButton", command=self.insert_selected_window_into_task).grid(row=0, column=1, sticky="e")
        ttk.Button(header, text="Copy Title", style="Deck.TButton", command=self.copy_selected_window_title).grid(row=0, column=2, sticky="e", padx=(8, 0))
        self.window_list = tk.Listbox(self.windows_tab, bg="#FFFDF8", fg=PALETTE["ink"], relief=tk.FLAT, bd=0, activestyle="none", font=("Segoe UI", 10), selectbackground=PALETTE["teal"], selectforeground="#FFFFFF")
        self.window_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.window_list.bind("<Double-Button-1>", lambda _event: self.insert_selected_window_into_task())

    def _build_runs_tab(self) -> None:
        self.runs_tab.columnconfigure(0, weight=1)
        self.runs_tab.rowconfigure(1, weight=1)
        header = tk.Frame(self.runs_tab, bg=PALETTE["surface"])
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header.columnconfigure(0, weight=1)
        tk.Label(header, text="Recent Runs", bg=PALETTE["surface"], fg=PALETTE["ink"], font=self.section_font).grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Refresh", style="Deck.TButton", command=self.refresh_recent_runs).grid(row=0, column=1, sticky="e")
        ttk.Button(header, text="Open Selected", style="Deck.TButton", command=self.open_selected_run).grid(row=0, column=2, sticky="e", padx=(8, 0))
        self.run_history_list = tk.Listbox(self.runs_tab, bg="#FFFDF8", fg=PALETTE["ink"], relief=tk.FLAT, bd=0, activestyle="none", font=("Consolas", 10), selectbackground=PALETTE["rust"], selectforeground="#FFFFFF")
        self.run_history_list.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.run_history_list.bind("<Double-Button-1>", lambda _event: self.open_selected_run())

    def _build_info_card(self, parent: tk.Widget, title: str, value_var: tk.StringVar, subtitle: str, accent: str) -> tk.Frame:
        card = tk.Frame(parent, bg=PALETTE["surface"], highlightbackground=PALETTE["line"], highlightthickness=1)
        card.columnconfigure(0, weight=1)
        tk.Frame(card, bg=accent, height=5).grid(row=0, column=0, sticky="ew")
        tk.Label(card, text=title, bg=PALETTE["surface"], fg=PALETTE["muted"], font=self.meta_font).grid(row=1, column=0, sticky="w", padx=12, pady=(10, 0))
        tk.Label(card, textvariable=value_var, bg=PALETTE["surface"], fg=PALETTE["ink"], font=self.card_value_font, wraplength=240, justify=tk.LEFT).grid(row=2, column=0, sticky="w", padx=12, pady=(4, 0))
        tk.Label(card, text=subtitle, bg=PALETTE["surface"], fg=PALETTE["muted"], font=self.meta_font, wraplength=240, justify=tk.LEFT).grid(row=3, column=0, sticky="w", padx=12, pady=(6, 12))
        return card

    def _load_config_summary(self, sync_controls: bool = True) -> None:
        try:
            config = self._load_config()
        except Exception as exc:
            self.model_var.set("<error>")
            self.base_url_var.set("<error>")
            self.api_status_var.set(str(exc))
            return
        self._apply_config_summary(config, sync_controls=sync_controls)

    def _apply_config_summary(self, config: AgentConfig, sync_controls: bool) -> None:
        self.model_var.set(config.model or "<missing>")
        self.base_url_var.set(config.base_url or "<OpenAI default>")
        self.api_status_var.set("Configured" if config.api_key else "Missing key")
        self.trust_env_var.set("Inherit system proxy" if config.openai_trust_env else "Ignore system proxy")
        if sync_controls:
            self.allow_shell_var.set(config.allow_shell_launch)
            self.dry_run_var.set(config.dry_run)
            self.max_steps_var.set(config.max_steps)
            self.browser_strategy_var.set(
                "reuse" if config.prefer_existing_browser_window else "managed"
            )
        self.admin_status_var.set("Administrator" if self._is_admin() else "Standard user")
        self.refresh_recent_runs()

    def reload_env(self, log_change: bool = True) -> None:
        browser_strategy = self.browser_strategy_var.get()
        self.env_file = Path(self.env_entry.get().strip() or self.env_file)
        self._load_config_summary()
        if browser_strategy in {"reuse", "managed"}:
            self.browser_strategy_var.set(browser_strategy)
        if log_change:
            self._append_log(f"[run] reloaded env from {self.env_file}")

    def apply_template(self, template: str) -> None:
        self.task_text.delete("1.0", tk.END)
        self.task_text.insert("1.0", template)
        self.task_text.focus_set()

    def clear_task(self) -> None:
        self.task_text.delete("1.0", tk.END)
        self.task_text.focus_set()

    def _prefer_existing_browser_window(self) -> bool:
        return self.browser_strategy_var.get() != "managed"

    def _browser_mode_label(self) -> str:
        if self._prefer_existing_browser_window():
            return "reuse_current_browser"
        return "allow_new_controlled_browser"

    def _max_steps_label(self) -> str:
        if self.max_steps_var.get() <= 0:
            return "unlimited"
        return str(self.max_steps_var.get())

    def start_run(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            messagebox.showinfo("Agent running", "A run is already in progress.")
            return
        if self._doctor_running():
            messagebox.showinfo("Doctor running", "Wait for doctor to finish before starting the agent.")
            return
        task = self.task_text.get("1.0", tk.END).strip()
        if not task:
            messagebox.showerror("Missing task", "Enter a task before starting the agent.")
            return
        self.env_file = Path(self.env_entry.get().strip() or self.env_file)
        self._load_config_summary(sync_controls=False)
        self.current_run_dir = ""
        self.current_screenshot_path = ""
        self.run_dir_var.set("-")
        self.step_var.set("-")
        self.active_window_var.set("-")
        max_steps_label = self._max_steps_label()
        mode_detail = (
            "Reusing your current browser window when available."
            if self._prefer_existing_browser_window()
            else "A separate Playwright browser may be launched when needed."
        )
        self._set_status("Starting", f"Booting the local control loop and preparing the first screenshot. {mode_detail} Step limit: {max_steps_label}.")
        self._set_final_answer("")
        self._clear_live_reply()
        self.window_list.delete(0, tk.END)
        self._clear_log()
        self._append_log(f"[run] task: {task}")
        self._append_log(f"[run] browser_mode={self._browser_mode_label()}")
        self._append_log(f"[run] max_steps={max_steps_label}")
        self.runner = AgentRunner(
            task=task,
            env_file=self.env_file,
            max_steps=self.max_steps_var.get(),
            dry_run=self.dry_run_var.get(),
            allow_shell_launch=self.allow_shell_var.get(),
            prefer_existing_browser_window=self._prefer_existing_browser_window(),
            event_handler=self.event_queue.put,
        )
        self.worker = threading.Thread(target=self.runner.run, daemon=True)
        self.worker.start()
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.doctor_button.configure(state=tk.DISABLED)
        self.progress.start(12)

    def stop_run(self) -> None:
        if self.runner is None:
            return
        self.runner.request_stop()
        self._set_status("Stop requested", "Waiting for the current tool call to finish cleanly.")
        self._append_log("[run] stop requested")

    def open_run_dir(self) -> None:
        if not self.current_run_dir:
            messagebox.showinfo("No run directory", "Start the agent or run doctor first.")
            return
        if hasattr(os, "startfile"):
            os.startfile(self.current_run_dir)

    def open_screenshot(self) -> None:
        if not self.current_screenshot_path:
            messagebox.showinfo("No screenshot", "Run doctor or the agent first.")
            return
        if hasattr(os, "startfile"):
            os.startfile(self.current_screenshot_path)

    def run_doctor(self, auto: bool = False) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not auto:
                messagebox.showinfo("Agent running", "Stop the current run before running doctor.")
            return
        if self._doctor_running():
            if not auto:
                messagebox.showinfo("Doctor running", "Doctor is already in progress.")
            return
        self.reload_env(log_change=not auto)
        self._set_status("Running doctor", "Capturing the desktop and checking browser, OCR, and UI Automation.")
        self.progress.start(12)
        self.start_button.configure(state=tk.DISABLED)
        self.doctor_button.configure(state=tk.DISABLED)
        env_file = Path(self.env_entry.get().strip() or self.env_file)
        self.doctor_worker = threading.Thread(
            target=self._run_doctor_worker,
            args=(env_file, auto),
            daemon=True,
        )
        self.doctor_worker.start()

    def relaunch_as_admin(self) -> None:
        if self._is_admin():
            messagebox.showinfo("Already elevated", "This console is already running as Administrator.")
            return
        run_dashboard = Path(__file__).resolve().parents[1] / "run_dashboard.py"
        parameters = f'"{run_dashboard}" --env-file "{self.env_entry.get().strip() or self.env_file}"'
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, parameters, str(run_dashboard.parent), 1)
        if result <= 32:
            messagebox.showerror("Elevation failed", f"Windows returned error code {result}.")
            return
        self._append_log("[run] launched an elevated copy of the dashboard")

    def insert_selected_window_into_task(self) -> None:
        selection = self.window_list.curselection()
        if not selection:
            return
        self.task_text.insert(tk.END, f'\nTarget window: "{self.window_list.get(selection[0])}"\n')
        self.task_text.see(tk.END)
        self.workspace_notebook.select(0)

    def copy_selected_window_title(self) -> None:
        selection = self.window_list.curselection()
        if selection:
            self._copy_to_clipboard(self.window_list.get(selection[0]))

    def refresh_recent_runs(self) -> None:
        try:
            runs_dir = self._load_config().runs_dir
        except Exception:
            return
        if not runs_dir.exists():
            return
        items = sorted([path for path in runs_dir.iterdir() if path.is_dir()], key=lambda path: path.stat().st_mtime, reverse=True)[:40]
        self.run_history_list.delete(0, tk.END)
        self.run_history_map = []
        for path in items:
            self.run_history_list.insert(tk.END, path.name)
            self.run_history_map.append(path)

    def open_selected_run(self) -> None:
        selection = self.run_history_list.curselection()
        if selection and hasattr(os, "startfile"):
            os.startfile(self.run_history_map[selection[0]])

    def copy_final_answer(self) -> None:
        text = self.final_text.get("1.0", tk.END).strip()
        if text:
            self._copy_to_clipboard(text)

    def copy_log(self) -> None:
        text = self.log_text.get("1.0", tk.END).strip()
        if text:
            self._copy_to_clipboard(text)

    def copy_live_reply(self) -> None:
        text = self.reply_text.get("1.0", tk.END).strip()
        if text:
            self._copy_to_clipboard(text)

    def _copy_to_clipboard(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()

    def _load_config(self) -> AgentConfig:
        env_path = Path(self.env_entry.get().strip() or self.env_file)
        return AgentConfig.from_env(env_path if env_path.exists() else None)

    def _schedule_initial_doctor(self) -> None:
        try:
            auto_run_doctor = self._load_config().auto_run_doctor
        except Exception:
            return
        if auto_run_doctor:
            self.root.after(350, lambda: self.run_doctor(auto=True))

    def _doctor_running(self) -> bool:
        return self.doctor_worker is not None and self.doctor_worker.is_alive()

    def _run_doctor_worker(self, env_file: Path, auto: bool) -> None:
        runtime: AgentRuntime | None = None
        try:
            config = AgentConfig.from_env(env_file if env_file.exists() else None)
            run_dir = config.runs_dir / f"doctor_gui_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            runtime = AgentRuntime(config=config, run_dir=run_dir)
            observation = runtime.capture_observation(step=0)
            report = runtime.doctor_report()
            self.event_queue.put(
                {
                    "type": "doctor_finished",
                    "run_dir": str(run_dir),
                    "screenshot_path": str(observation.screenshot_path),
                    "report": report,
                }
            )
        except Exception as exc:
            self.event_queue.put(
                {
                    "type": "doctor_failed",
                    "error": str(exc),
                    "show_dialog": not auto,
                }
            )
        finally:
            if runtime is not None:
                runtime.close()

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        self.root.after(200, self._drain_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type", ""))
        if event_type == "doctor_finished":
            self.progress.stop()
            self.current_run_dir = str(event.get("run_dir", ""))
            self.current_screenshot_path = str(event.get("screenshot_path", ""))
            self.run_dir_var.set(self.current_run_dir or "-")
            report = event.get("report", {})
            desktop = report.get("desktop", {}) if isinstance(report, dict) else {}
            self.step_var.set("doctor")
            self.active_window_var.set(str(desktop.get("active_window") or "<none>"))
            self._replace_window_list(desktop.get("visible_windows", []))
            self._render_screenshot(self.current_screenshot_path)
            self.desktop_status_var.set(
                f"Ready {desktop.get('screen_width', '?')}x{desktop.get('screen_height', '?')}"
            )
            self.browser_status_var.set(
                self._format_browser_status(report.get("browser", {}))
            )
            self.ocr_status_var.set(self._format_ocr_status(report.get("ocr", {})))
            self.uia_status_var.set(self._format_uia_status(report.get("uia", {})))
            self._set_status(
                "Idle",
                "Doctor finished. Review the live screen and capability cards before starting a run.",
            )
            self._append_log(
                f"[doctor] desktop={self.desktop_status_var.get()} browser={self.browser_status_var.get()} "
                f"ocr={self.ocr_status_var.get()} uia={self.uia_status_var.get()}"
            )
            self.doctor_worker = None
            if self.worker is None:
                self.start_button.configure(state=tk.NORMAL)
                self.doctor_button.configure(state=tk.NORMAL)
            self.refresh_recent_runs()
            return
        if event_type == "doctor_failed":
            self.progress.stop()
            error = str(event.get("error", "") or "Doctor failed.")
            self._set_status("Doctor failed", error)
            self._append_log(f"[error] doctor failed: {error}")
            self.doctor_worker = None
            if self.worker is None:
                self.start_button.configure(state=tk.NORMAL)
                self.doctor_button.configure(state=tk.NORMAL)
            if event.get("show_dialog", True):
                messagebox.showerror("Doctor failed", error)
            return
        if event_type == "run_started":
            self.current_run_dir = str(event.get("run_dir", ""))
            self.run_dir_var.set(self.current_run_dir or "-")
            raw_max_steps = int(event.get("max_steps", 0) or 0)
            max_steps_label = "unlimited" if raw_max_steps <= 0 else str(raw_max_steps)
            self._set_status("Running", f"Model {event.get('model', '')} is driving the desktop with max_steps={max_steps_label}.")
            browser_mode = (
                "reuse_current_browser"
                if event.get("prefer_existing_browser_window", True)
                else "allow_new_controlled_browser"
            )
            self._append_log(f"[run] model={event.get('model', '')} dry_run={event.get('dry_run', False)} allow_shell={event.get('allow_shell_launch', False)} browser_mode={browser_mode} max_steps={max_steps_label} trust_env={event.get('openai_trust_env', False)}")
            self.refresh_recent_runs()
            return
        if event_type == "step_started":
            self.step_var.set(f"step {event.get('step', '-')}")
            self.active_window_var.set(str(event.get("active_window", "-")))
            self.current_screenshot_path = str(event.get("screenshot_path", ""))
            self._render_screenshot(self.current_screenshot_path)
            self._replace_window_list(event.get("visible_windows", []))
            self._set_status("Running", f"Step {event.get('step', '?')} is active. Inspect the screenshot and log for the latest action.")
            self._append_log(f"[run] step={event.get('step', '?')} active_window={event.get('active_window', '<none>')} cursor=({event.get('cursor_x', '?')}, {event.get('cursor_y', '?')})")
            return
        if event_type == "assistant_message":
            content = str(event.get("content", "")).strip()
            if content:
                if event.get("streamed"):
                    self._set_live_reply("Assistant", content, "assistant")
                else:
                    self._append_live_reply("Assistant", content, "assistant")
                self._append_log(f"[assistant] {content}")
            return
        if event_type == "assistant_message_delta":
            content = str(event.get("content", "")).strip()
            if content:
                self._set_live_reply("Assistant", content, "assistant")
            return
        if event_type == "tool_result":
            self._append_log(f"[tool] {event.get('tool_name', '')}\n{json.dumps(event.get('result', {}), ensure_ascii=False, indent=2)}")
            return
        if event_type == "runner_warning":
            self._append_log(f"[run] warning: {event.get('warning', '')}")
            return
        if event_type != "run_finished":
            return
        self.progress.stop()
        status = str(event.get("status", ""))
        final_text = str(event.get("final_text", "") or "")
        error = str(event.get("error", "") or "")
        if final_text:
            self._set_final_answer(final_text)
            self._append_live_reply("Final Answer", final_text, "final")
            self._append_log(f"[final] {final_text}")
        if error:
            self._set_final_answer(error)
            self._append_live_reply("Error", error, "error")
            self._append_log(f"[error] {error}")
        detail = {"completed": "Run finished successfully.", "blocked": "The agent stopped because it reported a concrete blocker.", "stopped": "The agent stopped after your interruption request.", "error": error or "The run failed.", "max_steps": "The run stopped because it hit the step limit."}.get(status, "Run finished.")
        self._set_status(status.replace("_", " ").title(), detail)
        if final_text or error:
            self.workspace_notebook.select(self.overview_tab)
        if status == "completed" and not final_text:
            self._append_log("[run] completed without a final answer")
        elif status == "max_steps":
            self._append_log("[run] stopped because max steps was reached")
        elif status == "stopped":
            self._append_log("[run] stopped by operator")
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.doctor_button.configure(state=tk.NORMAL)
        self.runner = None
        self.worker = None
        self.refresh_recent_runs()

    def _replace_window_list(self, windows: Any) -> None:
        self.window_list.delete(0, tk.END)
        if isinstance(windows, list):
            for title in windows:
                self.window_list.insert(tk.END, str(title))

    def _render_screenshot(self, screenshot_path: str) -> None:
        path = Path(screenshot_path)
        if not screenshot_path or not path.exists():
            return
        try:
            with Image.open(path) as image:
                image.thumbnail((920, 700))
                rendered = image.copy()
            self._screenshot_image = ImageTk.PhotoImage(rendered)
        except Exception as exc:
            self._append_log(f"[error] screenshot load failed: {exc}")
            return
        self.screenshot_label.configure(image=self._screenshot_image, text="")

    def _append_log(self, message: str) -> None:
        prefix = "assistant"
        for name in ("run", "tool", "doctor", "error", "final", "assistant"):
            if message.startswith(f"[{name}]"):
                prefix = name
                break
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message.rstrip() + "\n\n", prefix)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _append_live_reply(self, title: str, text: str, tag: str) -> None:
        if not text.strip():
            return
        self.reply_text.configure(state=tk.NORMAL)
        existing = self.reply_text.get("1.0", tk.END).strip()
        if existing:
            self.reply_text.insert(tk.END, "\n\n")
        self.reply_text.insert(tk.END, f"{title}\n", f"{tag}_label")
        self.reply_text.insert(tk.END, text.strip(), f"{tag}_body")
        self.reply_text.see(tk.END)
        self.reply_text.configure(state=tk.DISABLED)

    def _set_live_reply(self, title: str, text: str, tag: str) -> None:
        self.reply_text.configure(state=tk.NORMAL)
        self.reply_text.delete("1.0", tk.END)
        if text.strip():
            self.reply_text.insert(tk.END, f"{title}\n", f"{tag}_label")
            self.reply_text.insert(tk.END, text.strip(), f"{tag}_body")
            self.reply_text.see(tk.END)
        self.reply_text.configure(state=tk.DISABLED)

    def _clear_live_reply(self) -> None:
        self.reply_text.configure(state=tk.NORMAL)
        self.reply_text.delete("1.0", tk.END)
        self.reply_text.configure(state=tk.DISABLED)

    def _set_final_answer(self, text: str) -> None:
        self.final_text.configure(state=tk.NORMAL)
        self.final_text.delete("1.0", tk.END)
        if text:
            self.final_text.insert("1.0", text)
        self.final_text.configure(state=tk.DISABLED)

    def _format_browser_status(self, browser: dict[str, Any]) -> str:
        if not browser.get("available"):
            return f"Unavailable: {browser.get('error', 'unknown error')}"
        if browser.get("error"):
            return f"Installed with warning: {browser['error']}"
        executable_path = browser.get("executable_path")
        return f"Ready via {Path(executable_path).name}" if executable_path else "Ready"

    def _format_ocr_status(self, ocr: dict[str, Any]) -> str:
        return f"Ready with {ocr.get('default_lang', 'unknown lang')}" if ocr.get("available") else f"Unavailable: {ocr.get('error', 'unknown error')}"

    def _format_uia_status(self, uia: dict[str, Any]) -> str:
        if not uia.get("available"):
            return f"Unavailable: {uia.get('error', 'unknown error')}"
        sample_windows = uia.get("sample_windows", [])
        return f"Ready across {len(sample_windows)} sample windows" if sample_windows else "Ready"

    def _set_status(self, title: str, detail: str) -> None:
        self.status_var.set(title)
        self.status_detail_var.set(detail)
        self.status_chip.configure(text=title, bg=STATUS_COLORS.get(title.lower(), PALETTE["muted"]))

    def _is_admin(self) -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno("Agent still running", "The agent is still running. Close the window anyway?"):
                return
        if self._doctor_running():
            if not messagebox.askyesno("Doctor still running", "Doctor is still collecting diagnostics. Close the window anyway?"):
                return
        self.root.destroy()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Desktop Agent GUI")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to the .env file containing API settings.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = tk.Tk()
    AgentDashboard(root=root, env_file=args.env_file)
    root.mainloop()
