"""Tkinter interface for the Minecraft version downloader."""

from __future__ import annotations

import ctypes
import logging
import os
import queue
import re
import shutil
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable

from . import __version__, __version_display__
from .config import (
    APP_NAME,
    COLORS,
    Settings,
    ensure_app_data_dir,
    format_bytes,
    format_duration,
    format_speed,
    free_space_text,
    friendly_date,
    has_drive,
    logs_dir,
    normalize_game_dir,
    resource_path,
    truncate_middle,
)
from .core import (
    DownloadEngine,
    EngineResult,
    Manifest,
    ProgressSnapshot,
    VersionEntry,
    display_version_type,
    fetch_manifest,
    scan_local_versions,
)
from .detect import LauncherDir, detect_launchers
from .loaders import (
    LOADER_LABELS,
    LoaderChoice,
    install_loader,
    list_loader_versions,
)

C = COLORS
BASE_W = 1180
BASE_H = 760
SCALE = 1.0
CATEGORIES = (
    ("all", "全部"),
    ("release", "正式版"),
    ("snapshot", "快照"),
    ("old", "旧版"),
    ("installed", "已安装"),
)
LOGGER = logging.getLogger("mcvd")


def s(value: float) -> int:
    return max(1, int(round(value * SCALE)))


def setup_logging() -> None:
    if LOGGER.handlers:
        return
    LOGGER.setLevel(logging.INFO)
    try:
        handler = RotatingFileHandler(
            logs_dir() / "downloader.log",
            maxBytes=1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        LOGGER.addHandler(handler)
    except OSError:
        pass


def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _set_dark_titlebar(window: tk.Misc) -> None:
    if os.name != "nt":
        return
    try:
        raw = window.winfo_id()
        hwnd = ctypes.windll.user32.GetAncestor(raw, 2)  # GA_ROOT
        if not hwnd:
            hwnd = raw
        enabled = ctypes.c_int(1)
        result = None
        for attribute in (20, 19):
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
            if result == 0:
                break
        LOGGER.debug("dark titlebar raw=%s hwnd=%s result=%s", raw, hwnd, result)
        # Windows 11 title bar colours (harmless on older builds).
        caption = ctypes.c_int(0x000B0F14)
        text = ctypes.c_int(0x00E8EEF6)
        for attribute, value in ((35, caption), (36, text)):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
    except Exception:
        LOGGER.debug("dark titlebar failed", exc_info=True)


def _try_icon(window: tk.Misc) -> None:
    try:
        icon = resource_path("assets/icon.ico")
        if icon.is_file():
            window.wm_iconbitmap(str(icon))
    except Exception:
        pass


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, radius, **kwargs):
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def _draw_logo(canvas: tk.Canvas, size: int) -> None:
    canvas.delete("all")
    canvas.configure(width=size, height=size, bg=canvas.master.cget("bg"))
    u = size / 100.0
    _round_rect(canvas, 1 * u, 1 * u, 99 * u, 99 * u, 22 * u, fill="#0d151f", outline="#263241", width=max(1, int(1.2 * u)))
    _round_rect(canvas, 5 * u, 5 * u, 95 * u, 95 * u, 19 * u, outline="#173b2a", width=max(1, int(0.8 * u)))
    top = [(50 * u, 15 * u), (81 * u, 33 * u), (50 * u, 51 * u), (19 * u, 33 * u)]
    left = [(19 * u, 33 * u), (50 * u, 51 * u), (50 * u, 82 * u), (19 * u, 64 * u)]
    right = [(81 * u, 33 * u), (50 * u, 51 * u), (50 * u, 82 * u), (81 * u, 64 * u)]
    canvas.create_polygon(left, fill="#7c5238", outline="")
    canvas.create_polygon(right, fill="#5f3d2a", outline="")
    canvas.create_polygon(top, fill="#53e38a", outline="")
    canvas.create_polygon(
        [(19 * u, 33 * u), (50 * u, 51 * u), (50 * u, 60 * u), (19 * u, 42 * u)],
        fill="#36b968",
        outline="",
    )
    canvas.create_polygon(
        [(81 * u, 33 * u), (50 * u, 51 * u), (50 * u, 60 * u), (81 * u, 42 * u)],
        fill="#2e9f5a",
        outline="",
    )
    cx, cy = 69 * u, 69 * u
    canvas.create_oval(
        cx - 15 * u, cy - 15 * u, cx + 15 * u, cy + 15 * u,
        fill="#0b1118", outline=C["accent"], width=max(1, int(1.6 * u)),
    )
    canvas.create_line(cx, cy - 8 * u, cx, cy + 1 * u, fill="#eafff2", width=max(1, int(3 * u)))
    canvas.create_polygon(
        [(cx - 6 * u, cy - 1 * u), (cx + 6 * u, cy - 1 * u), (cx, cy + 7 * u)],
        fill="#eafff2", outline="",
    )
    canvas.create_line(cx - 6 * u, cy + 10 * u, cx + 6 * u, cy + 10 * u, fill="#eafff2", width=max(1, int(2.4 * u)))


class Card(tk.Frame):
    def __init__(self, master: tk.Misc, **kwargs):
        super().__init__(
            master,
            bg=C["card"],
            highlightbackground=C["border"],
            highlightthickness=max(1, s(1)),
            bd=0,
            **kwargs,
        )


class Button(tk.Button):
    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Callable[[], None] | None = None,
        *,
        kind: str = "ghost",
        font: tkfont.Font | tuple | None = None,
        padx: int | None = None,
        pady: int | None = None,
        **kwargs,
    ):
        self.kind = kind
        self._normal_bg, self._normal_fg, self._active_bg, self._disabled_bg, self._disabled_fg = {
            "primary": (C["accent"], C["black"], C["accent_hover"], "#1d3328", "#6e8d7a"),
            "ghost": (C["card_alt"], C["text_soft"], "#1d2937", "#101720", C["muted_dark"]),
            "danger": (C["danger_soft"], C["danger"], "#4a252c", "#21171a", "#76535a"),
            "subtle": (C["card"], C["muted"], C["card_alt"], C["card"], C["muted_dark"]),
            "link": (C["card"], C["accent"], C["card"], C["card"], C["muted_dark"]),
        }.get(kind, ("#1b2531", C["text"], "#243240", "#151d27", C["muted_dark"]))
        super().__init__(
            master,
            text=text,
            command=command,
            bg=self._normal_bg,
            fg=self._normal_fg,
            activebackground=self._active_bg,
            activeforeground=self._normal_fg,
            disabledforeground=self._disabled_fg,
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=font,
            padx=s(14) if padx is None else padx,
            pady=s(8) if pady is None else pady,
            **kwargs,
        )
        self._enabled = True
        if kind == "link":
            self.configure(anchor="w")
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_enter(self, _event=None):
        if self._enabled and str(self.cget("state")) != "disabled":
            self.configure(bg=self._active_bg)

    def _on_leave(self, _event=None):
        if self._enabled:
            self.configure(bg=self._normal_bg)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.configure(
            state="normal" if enabled else "disabled",
            bg=self._normal_bg if enabled else self._disabled_bg,
            fg=self._normal_fg if enabled else self._disabled_fg,
            cursor="hand2" if enabled else "arrow",
        )


class PlaceholderEntry(tk.Entry):
    def __init__(self, master: tk.Misc, placeholder: str, **kwargs):
        self.placeholder = placeholder
        self._showing_placeholder = False
        super().__init__(
            master,
            bg=C["card_alt"],
            fg=C["muted"],
            insertbackground=C["text"],
            relief="flat",
            bd=0,
            highlightthickness=max(1, s(1)),
            highlightbackground=C["border"],
            highlightcolor=C["accent"],
            font=kwargs.pop("font", None),
            **kwargs,
        )
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self._show_placeholder()

    def _show_placeholder(self) -> None:
        if not super().get():
            self._showing_placeholder = True
            self.configure(fg=C["muted_dark"])
            super().insert(0, self.placeholder)

    def _focus_in(self, _event=None):
        if self._showing_placeholder:
            super().delete(0, "end")
            self.configure(fg=C["text"])
            self._showing_placeholder = False

    def _focus_out(self, _event=None):
        if not super().get():
            self._show_placeholder()

    def get_value(self) -> str:
        return "" if self._showing_placeholder else super().get()

    def set_value(self, value: str) -> None:
        self._showing_placeholder = False
        super().delete(0, "end")
        self.configure(fg=C["text"])
        if value:
            super().insert(0, value)
        else:
            if self.focus_get() is not self:
                self._show_placeholder()


class CheckRow(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        text: str,
        value: bool,
        command: Callable[[bool], None] | None = None,
        *,
        bg: str | None = None,
    ):
        self._bg = bg or C["card"]
        super().__init__(master, bg=self._bg)
        self.value = bool(value)
        self.command = command
        self.box = tk.Canvas(self, width=s(18), height=s(18), bg=self._bg, highlightthickness=0, cursor="hand2")
        self.box.pack(side="left", padx=(0, s(8)))
        self.label = tk.Label(
            self,
            text=text,
            bg=self._bg,
            fg=C["text_soft"],
            font=("Microsoft YaHei UI", 9),
            cursor="hand2",
            anchor="w",
        )
        self.label.pack(side="left", fill="x", expand=True)
        for widget in (self, self.box, self.label):
            widget.bind("<Button-1>", self._toggle)
        self._draw()

    def _draw(self) -> None:
        self.box.delete("all")
        u = s(18)
        if self.value:
            _round_rect(self.box, 1, 1, u - 1, u - 1, s(4), fill=C["accent"], outline=C["accent"])
            self.box.create_line(
                s(4.5), s(9), s(7.5), s(12.5), s(13.5), s(5.5),
                fill=C["black"], width=max(1, s(1.8)), capstyle="round", joinstyle="round",
            )
        else:
            _round_rect(self.box, 1, 1, u - 1, u - 1, s(4), fill=C["card_alt"], outline=C["border"], width=max(1, s(1)))

    def _toggle(self, _event=None):
        if str(self.cget("cursor")) == "arrow":
            return
        self.value = not self.value
        self._draw()
        if self.command:
            self.command(self.value)

    def set_enabled(self, enabled: bool) -> None:
        cursor = "hand2" if enabled else "arrow"
        for widget in (self, self.box, self.label):
            widget.configure(cursor=cursor)
        self.label.configure(fg=C["text_soft"] if enabled else C["muted_dark"])


class ModeCard(tk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        title: str,
        description: str,
        value: str,
        command: Callable[[str], None],
    ):
        super().__init__(
            master,
            bg=C["card_alt"],
            highlightbackground=C["border"],
            highlightthickness=max(1, s(1)),
            cursor="hand2",
        )
        self.value = value
        self.command = command
        self._selected = False
        self._hover = False
        self._children: list[tk.Widget] = []
        self.dot = tk.Canvas(self, width=s(17), height=s(17), bg=C["card_alt"], highlightthickness=0, cursor="hand2")
        self.dot.pack(side="left", padx=(s(10), s(8)), pady=s(10))
        text_frame = tk.Frame(self, bg=C["card_alt"])
        text_frame.pack(side="left", fill="both", expand=True, pady=s(8), padx=(0, s(9)))
        self.title_label = tk.Label(
            text_frame,
            text=title,
            bg=C["card_alt"],
            fg=C["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
            cursor="hand2",
        )
        self.title_label.pack(fill="x")
        self.desc_label = tk.Label(
            text_frame,
            text=description,
            bg=C["card_alt"],
            fg=C["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="w",
            justify="left",
            wraplength=s(240),
            cursor="hand2",
        )
        self.desc_label.pack(fill="x", pady=(s(2), 0))
        self._children = [self.dot, text_frame, self.title_label, self.desc_label]
        for widget in self._children:
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)
        self._paint()
        self._draw_dot()

    def _click(self, _event=None):
        if str(self.cget("cursor")) == "hand2":
            self.command(self.value)

    def _enter(self, _event=None):
        self._hover = True
        self._paint()

    def _leave(self, _event=None):
        self._hover = False
        self._paint()

    def _paint(self) -> None:
        bg = "#13251c" if self._selected else ("#18222e" if self._hover else C["card_alt"])
        border = C["accent_dark"] if self._selected else (C["border"] if not self._hover else "#304052")
        self.configure(bg=bg, highlightbackground=border)
        for widget in self._children:
            try:
                widget.configure(bg=bg)
            except tk.TclError:
                pass
        self.dot.configure(bg=bg)
        self.title_label.configure(bg=bg)
        self.desc_label.configure(bg=bg)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self._paint()
        self._draw_dot()

    def _draw_dot(self) -> None:
        self.dot.delete("all")
        mid = s(8.5)
        if self._selected:
            self.dot.create_oval(
                mid - s(6.5), mid - s(6.5), mid + s(6.5), mid + s(6.5),
                fill=C["accent"], outline=C["accent"],
            )
            self.dot.create_oval(
                mid - s(2.4), mid - s(2.4), mid + s(2.4), mid + s(2.4),
                fill=C["black"], outline="",
            )
        else:
            self.dot.create_oval(
                mid - s(6.3), mid - s(6.3), mid + s(6.3), mid + s(6.3),
                fill=C["card_alt"], outline=C["muted_dark"], width=max(1, s(1)),
            )

    def set_enabled(self, enabled: bool) -> None:
        cursor = "hand2" if enabled else "arrow"
        for widget in (self, self.dot, self.title_label, self.desc_label):
            widget.configure(cursor=cursor)
        self._paint()
        self._draw_dot()


class ProgressBar(tk.Canvas):
    def __init__(
        self, master: tk.Misc, height: int | None = None, bg: str | None = None
    ):
        self.bar_height = height or s(9)
        super().__init__(
            master,
            height=self.bar_height,
            bg=bg or C["bg_alt"],
            highlightthickness=0,
            bd=0,
        )
        self.value = 0.0
        self.fill_color = C["accent"]
        self.bind("<Configure>", lambda _event: self.redraw())

    def set(self, value: float, color: str | None = None) -> None:
        self.value = max(0.0, min(1.0, float(value)))
        if color:
            self.fill_color = color
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = self.bar_height
        if width <= 2:
            return
        y = height / 2
        self.create_line(
            0, y, width, y, fill=C["border_soft"], width=height, capstyle="round"
        )
        if self.value > 0:
            end = max(height, width * self.value)
            self.create_line(
                0, y, end, y, fill=self.fill_color, width=height, capstyle="round"
            )


class SlimScrollbar(tk.Canvas):
    """A dark, arrow-less scrollbar that avoids the white ttk trough."""

    def __init__(
        self,
        master: tk.Misc,
        command: Callable[..., None],
        *,
        bg: str = C["card"],
    ):
        super().__init__(
            master,
            width=s(9),
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="arrow",
        )
        self.command = command
        self.first = 0.0
        self.last = 1.0
        self._drag_offset: float | None = None
        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def set(self, first, last) -> None:
        try:
            self.first = max(0.0, min(1.0, float(first)))
            self.last = max(self.first, min(1.0, float(last)))
        except (TypeError, ValueError):
            return
        self._draw()

    def _thumb_bounds(self) -> tuple[float, float, float]:
        height = max(1, self.winfo_height())
        top = self.first * height
        bottom = self.last * height
        minimum = s(22)
        if bottom - top < minimum:
            bottom = min(height, top + minimum)
            top = max(0, bottom - minimum)
        return top, bottom, height

    def _draw(self) -> None:
        self.delete("all")
        height = max(1, self.winfo_height())
        width = max(1, self.winfo_width())
        mid = width / 2
        self.create_line(
            mid, s(2), mid, height - s(2),
            fill=C["border_soft"], width=max(2, s(3)), capstyle="round",
        )
        if self.last - self.first >= 0.999:
            return
        top, bottom, _height = self._thumb_bounds()
        self.create_line(
            mid, top + s(2), mid, bottom - s(2),
            fill=C["muted_dark"], width=max(2, s(4)), capstyle="round",
        )

    def _on_press(self, event) -> None:
        top, bottom, height = self._thumb_bounds()
        if self.last - self.first >= 0.999:
            return
        if top <= event.y <= bottom:
            self._drag_offset = event.y - top
            return
        self._drag_offset = (bottom - top) / 2
        fraction = max(0.0, min(1.0, (event.y - self._drag_offset) / height))
        self.command("moveto", fraction)

    def _on_drag(self, event) -> None:
        if self._drag_offset is None:
            return
        _top, _bottom, height = self._thumb_bounds()
        fraction = max(0.0, min(1.0, (event.y - self._drag_offset) / height))
        self.command("moveto", fraction)

    def _on_release(self, _event) -> None:
        self._drag_offset = None


class InfoDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        message: str,
        *,
        kind: str = "info",
        details: str = "",
        primary_text: str = "知道了",
        secondary_text: str | None = None,
        secondary_command: Callable[[], None] | None = None,
        primary_command: Callable[[], None] | None = None,
        width: int = 560,
        height: int = 350,
    ):
        super().__init__(parent)
        self.parent = parent
        self.configure(bg=C["bg"])
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self._result = False
        self._secondary_command = secondary_command
        self._primary_command = primary_command
        self._dialog_width = width
        _try_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._close)

        shell = Card(self)
        shell.pack(fill="both", expand=True, padx=s(1), pady=s(1))
        inner = tk.Frame(shell, bg=C["card"])
        inner.pack(fill="both", expand=True, padx=s(22), pady=s(18))

        header = tk.Frame(inner, bg=C["card"])
        header.pack(fill="x")
        icon_color = {
            "info": C["blue"],
            "success": C["accent"],
            "warning": C["warning"],
            "error": C["danger"],
        }.get(kind, C["blue"])
        icon = tk.Canvas(header, width=s(34), height=s(34), bg=C["card"], highlightthickness=0)
        icon.pack(side="left", padx=(0, s(12)))
        icon.create_oval(s(2), s(2), s(32), s(32), outline=icon_color, width=max(1, s(2)), fill=C["card_alt"])
        symbol = {"success": "✓", "error": "!", "warning": "!", "info": "i"}.get(kind, "i")
        icon.create_text(s(17), s(17), text=symbol, fill=icon_color, font=("Microsoft YaHei UI", 13, "bold"))
        title_label = tk.Label(
            header,
            text=title,
            bg=C["card"],
            fg=C["text"],
            font=("Microsoft YaHei UI", 14, "bold"),
            anchor="w",
        )
        title_label.pack(side="left", fill="x", expand=True)

        message_label = tk.Label(
            inner,
            text=message,
            bg=C["card"],
            fg=C["text_soft"],
            font=("Microsoft YaHei UI", 10),
            justify="left",
            anchor="w",
            wraplength=s(width - 50),
        )
        message_label.pack(fill="x", pady=(s(14), 0))

        buttons = tk.Frame(inner, bg=C["card"])
        buttons.pack(side="bottom", fill="x", pady=(s(16), 0))
        primary = Button(
            buttons,
            primary_text,
            self._primary,
            kind="primary",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        primary.pack(side="right")
        if secondary_text and secondary_command:
            secondary = Button(
                buttons,
                secondary_text,
                self._secondary,
                kind="ghost",
                font=("Microsoft YaHei UI", 9),
            )
            secondary.pack(side="right", padx=(0, s(8)))

        if details:
            text_frame = tk.Frame(inner, bg=C["card_alt"], highlightbackground=C["border"], highlightthickness=max(1, s(1)))
            text_frame.pack(fill="both", expand=True, pady=(s(12), 0))
            text = tk.Text(
                text_frame,
                height=7,
                bg=C["card_alt"],
                fg=C["muted"],
                insertbackground=C["text"],
                relief="flat",
                bd=0,
                wrap="word",
                font=("Consolas", 9),
                padx=s(9),
                pady=s(7),
            )
            scroll = SlimScrollbar(
                text_frame, command=text.yview, bg=C["card_alt"]
            )
            text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            text.pack(side="left", fill="both", expand=True)
            text.insert("1.0", details)
            text.configure(state="disabled")

        self.update_idletasks()
        self.geometry(f"{s(width)}x{s(height)}")
        self._center()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _center(self) -> None:
        self.update_idletasks()
        parent = self.parent
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
        except tk.TclError:
            px = py = 0
            pw, ph = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1:
            width = s(self._dialog_width)
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 2)
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _primary(self) -> None:
        if self._primary_command:
            try:
                self._primary_command()
            except Exception:
                LOGGER.exception("primary dialog action failed")
                self._close()
            return
        self._close()

    def _secondary(self) -> None:
        if self._secondary_command:
            try:
                self._secondary_command()
            except Exception:
                LOGGER.exception("secondary dialog action failed")
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def show(self) -> None:
        try:
            self.wait_window(self)
        except tk.TclError:
            pass


class ConfirmDialog(InfoDialog):
    def __init__(self, parent, title, message, *, confirm_text="继续", cancel_text="取消", kind="warning"):
        self.confirmed = False
        super().__init__(
            parent,
            title,
            message,
            kind=kind,
            primary_text=confirm_text,
            secondary_text=cancel_text,
            secondary_command=self._cancel,
            primary_command=self._confirm,
            width=540,
            height=300,
        )

    def _confirm(self) -> None:
        self.confirmed = True
        self._close()

    def _cancel(self) -> None:
        self.confirmed = False
        self._close()

    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class SummaryDialog(InfoDialog):
    def __init__(self, parent, result: EngineResult, game_dir: Path):
        self.result = result
        if result.cancelled:
            title = "下载已取消"
            kind = "warning"
        elif result.failed:
            title = "下载完成，但有失败"
            kind = "warning"
        else:
            title = "下载完成"
            kind = "success"
        lines = [
            f"新安装 {result.installed} 个版本 · 跳过 {result.skipped} 个 · 失败 {result.failed} 个",
            f"本次下载 {format_bytes(result.session_bytes)}，耗时 {format_duration(result.elapsed)}。",
        ]
        if result.failed:
            lines.append("")
            lines.append("失败详情：")
            for failure in result.failures[:12]:
                lines.append(f"· {failure.version}: {failure.message}")
            if len(result.failures) > 12:
                lines.append(f"……以及另外 {len(result.failures) - 12} 个错误")
        details = "\n".join(lines[2:]) if result.failed else ""
        message = "\n".join(lines[:2])
        self._game_dir = game_dir
        InfoDialog.__init__(
            self,
            parent,
            title,
            message,
            kind=kind,
            details=details,
            primary_text="关闭",
            secondary_text="打开下载目录",
            secondary_command=lambda: open_path(self._game_dir),
            width=590,
            height=400 if details else 255,
        )


class DetectionDialog(tk.Toplevel):
    def __init__(self, app: "Application"):
        super().__init__(app.root)
        self.app = app
        self.configure(bg=C["bg"])
        self.title("检测启动器目录")
        self.transient(app.root)
        self.resizable(True, True)
        self.minsize(s(640), s(420))
        _try_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._closed = False
        self._last_results: list[LauncherDir] = []

        shell = Card(self)
        shell.pack(fill="both", expand=True, padx=s(1), pady=s(1))
        inner = tk.Frame(shell, bg=C["card"])
        inner.pack(fill="both", expand=True, padx=s(20), pady=s(18))

        top = tk.Frame(inner, bg=C["card"])
        top.pack(fill="x")
        tk.Label(
            top, text="选择启动器目录", bg=C["card"], fg=C["text"],
            font=("Microsoft YaHei UI", 15, "bold"), anchor="w",
        ).pack(side="left")
        self.count_label = tk.Label(
            top, text="正在扫描…", bg=C["card"], fg=C["muted"],
            font=("Microsoft YaHei UI", 9), anchor="e",
        )
        self.count_label.pack(side="right")
        tk.Label(
            inner,
            text="自动查找官方启动器、HMCL、PCL2、Prism / MultiMC、BakaXL 等常见目录。下载会写入所选目录的 versions 文件夹。",
            bg=C["card"],
            fg=C["muted"],
            font=("Microsoft YaHei UI", 9),
            justify="left",
            anchor="w",
            wraplength=s(700),
        ).pack(fill="x", pady=(s(6), s(12)))

        table_frame = tk.Frame(inner, bg=C["card"])
        table_frame.pack(fill="both", expand=True)
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        columns = ("launcher", "path", "count")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="Versions.Treeview",
        )
        self.tree.heading("launcher", text="启动器")
        self.tree.heading("path", text="目录")
        self.tree.heading("count", text="版本数")
        self.tree.column("launcher", width=s(150), minwidth=s(100), stretch=False)
        self.tree.column("path", width=s(480), minwidth=s(230), stretch=True)
        self.tree.column("count", width=s(64), minwidth=s(50), stretch=False, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = SlimScrollbar(table_frame, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _event: self.use_selected())

        self.empty = tk.Label(
            table_frame, text="正在扫描常见启动器位置…", bg=C["card"], fg=C["muted"],
            font=("Microsoft YaHei UI", 10),
        )
        self.empty.place(relx=0.5, rely=0.45, anchor="center")

        bottom = tk.Frame(inner, bg=C["card"])
        bottom.pack(fill="x", pady=(s(12), 0))
        self.rescan_btn = Button(bottom, "重新检测", self._rescan, kind="ghost", font=("Microsoft YaHei UI", 9))
        self.rescan_btn.pack(side="left")
        manual_btn = Button(bottom, "手动选择…", self._manual, kind="subtle", font=("Microsoft YaHei UI", 9))
        manual_btn.pack(side="left", padx=(s(8), 0))
        use_btn = Button(bottom, "使用此目录", self.use_selected, kind="primary", font=("Microsoft YaHei UI", 9, "bold"))
        use_btn.pack(side="right")
        close_btn = Button(bottom, "取消", self._close, kind="ghost", font=("Microsoft YaHei UI", 9))
        close_btn.pack(side="right", padx=(0, s(8)))

        self.update_idletasks()
        width, height = s(820), s(560)
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(width, int(screen_w * 0.92))
        height = min(height, int(screen_h * 0.86))
        x = app.root.winfo_rootx() + max(0, (app.root.winfo_width() - width) // 2)
        y = app.root.winfo_rooty() + max(0, (app.root.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.set_results(app.detect_results)
        if app.detection_loading:
            self._set_loading(True)
        self.after(300, self._poll)

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            if self.app.detection_loading:
                self._set_loading(True)
            elif self.app.detect_results is not self._last_results:
                self.set_results(self.app.detect_results)
        except tk.TclError:
            return
        self.after(350, self._poll)

    def _set_loading(self, loading: bool) -> None:
        self.rescan_btn.set_enabled(not loading)
        if loading:
            self.count_label.configure(text="正在扫描…", fg=C["warning"])
            self.empty.configure(text="正在扫描常见启动器位置…")
            self.empty.place(relx=0.5, rely=0.45, anchor="center")

    def set_results(self, results: list[LauncherDir]) -> None:
        self._last_results = results
        self.count_label.configure(
            text=f"发现 {len(results)} 个目录" if results else "未发现已安装的启动器",
            fg=C["accent"] if results else C["muted"],
        )
        selected = self.tree.selection()
        selected_path = None
        if selected:
            try:
                selected_path = self.tree.item(selected[0], "values")[1]
            except (tk.TclError, IndexError):
                selected_path = None
        self.tree.delete(*self.tree.get_children())
        for item in results:
            self.tree.insert(
                "",
                "end",
                iid=str(item.path),
                values=(item.label, str(item.path), item.version_count),
            )
        if results:
            self.empty.place_forget()
            if selected_path and self.tree.exists(selected_path):
                self.tree.selection_set(selected_path)
                self.tree.see(selected_path)
            else:
                self.tree.selection_set(str(results[0].path))
        else:
            if not self.app.detection_loading:
                self.empty.configure(text="未发现包含 versions 的启动器目录\n可以点击“手动选择…”添加")
                self.empty.place(relx=0.5, rely=0.45, anchor="center")

    def _on_select(self, _event=None) -> None:
        pass

    def _rescan(self) -> None:
        self._set_loading(True)
        self.app.start_detection(force=True)

    def _manual(self) -> None:
        path = filedialog.askdirectory(
            parent=self,
            title="选择 Minecraft 游戏目录（内含 versions）",
            initialdir=str(self.app.game_dir) if self.app.game_dir.exists() else str(Path.home()),
        )
        if path:
            self.app.apply_target(path, notify=True)
            self._close()

    def use_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        try:
            values = self.tree.item(selected[0], "values")
            path = values[1]
        except (tk.TclError, IndexError):
            return
        self.app.apply_target(path, notify=True)
        self._close()

    def _close(self) -> None:
        self._closed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def open_path(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))
    except OSError as exc:
        LOGGER.warning("cannot open %s: %s", path, exc)


class LoaderDialog(tk.Toplevel):
    """Create a Fabric/Quilt version merged with an official base version."""

    def __init__(self, app: "Application", base: VersionEntry):
        super().__init__(app.root)
        self.app = app
        self.base = base
        self.configure(bg=C["bg"])
        self.title("安装模组加载器")
        self.transient(app.root)
        self.resizable(False, False)
        self._closed = False
        self._fetch_token = 0
        self._fetch_cancel = threading.Event()
        self._install_cancel = threading.Event()
        self._installing = False
        self._queue: queue.Queue = queue.Queue()
        self._choices: list[LoaderChoice] = []
        self.result_id: str | None = None
        _try_icon(self)
        self.protocol("WM_DELETE_WINDOW", self._close)

        shell = Card(self)
        shell.pack(fill="both", expand=True, padx=s(1), pady=s(1))
        inner = tk.Frame(shell, bg=C["card"])
        inner.pack(fill="both", expand=True, padx=s(22), pady=s(18))

        tk.Label(
            inner,
            text="安装模组加载器",
            bg=C["card"],
            fg=C["text"],
            font=self.app.f_h1,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            inner,
            text="将 Fabric / Quilt 与原版版本合并，生成一个新的可启动版本文件夹。",
            bg=C["card"],
            fg=C["muted"],
            font=self.app.f_small,
            anchor="w",
            justify="left",
            wraplength=s(600),
        ).pack(fill="x", pady=(s(4), s(12)))

        base_box = tk.Frame(
            inner,
            bg=C["card_alt"],
            highlightbackground=C["border"],
            highlightthickness=max(1, s(1)),
        )
        base_box.pack(fill="x")
        base_inner = tk.Frame(base_box, bg=C["card_alt"])
        base_inner.pack(fill="x", padx=s(12), pady=s(9))
        tk.Label(
            base_inner,
            text="基础版本",
            bg=C["card_alt"],
            fg=C["muted"],
            font=self.app.f_tiny,
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            base_inner,
            text=f"{base.id}  ·  {base.type_label}",
            bg=C["card_alt"],
            fg=C["text"],
            font=self.app.f_body_bold,
            anchor="w",
        ).pack(anchor="w", pady=(s(2), 0))

        selectors = tk.Frame(inner, bg=C["card"])
        selectors.pack(fill="x", pady=(s(12), 0))
        tk.Label(
            selectors,
            text="加载器",
            bg=C["card"],
            fg=C["muted"],
            font=self.app.f_small,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, s(8)))
        self.loader_var = tk.StringVar(value="Fabric")
        self.loader_combo = ttk.Combobox(
            selectors,
            textvariable=self.loader_var,
            values=list(LOADER_LABELS.values()),
            state="readonly",
            width=12,
            style="Dark.TCombobox",
            font=self.app.f_small,
        )
        self.loader_combo.grid(row=1, column=0, sticky="w", padx=(0, s(12)))
        self.loader_combo.bind("<<ComboboxSelected>>", lambda _event: self._fetch_versions())

        tk.Label(
            selectors,
            text="加载器版本",
            bg=C["card"],
            fg=C["muted"],
            font=self.app.f_small,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(0, s(8)))
        self.version_var = tk.StringVar()
        self.version_combo = ttk.Combobox(
            selectors,
            textvariable=self.version_var,
            values=[],
            state="readonly",
            width=28,
            style="Dark.TCombobox",
            font=self.app.f_small,
        )
        self.version_combo.grid(row=1, column=1, sticky="ew")
        selectors.grid_columnconfigure(1, weight=1)

        self.full_row = CheckRow(
            inner,
            "同时完整下载基础版本资源",
            True,
            bg=C["card"],
        )
        self.full_row.pack(fill="x", pady=(s(12), 0))
        tk.Label(
            inner,
            text="关闭后将只补齐基础版本核心文件；开启后会确保库、资源等完整。",
            bg=C["card"],
            fg=C["muted_dark"],
            font=self.app.f_tiny,
            anchor="w",
            justify="left",
            wraplength=s(590),
        ).pack(fill="x", pady=(s(3), 0))

        self.progress = ProgressBar(inner, bg=C["card"])
        self.progress.pack(fill="x", pady=(s(16), s(7)))
        status_line = tk.Frame(inner, bg=C["card"])
        status_line.pack(fill="x")
        self.status_label = tk.Label(
            status_line,
            text="正在获取加载器版本…",
            bg=C["card"],
            fg=C["muted"],
            font=self.app.f_small,
            anchor="w",
            justify="left",
            wraplength=s(500),
        )
        self.status_label.pack(side="left", fill="x", expand=True)
        self.percent_label = tk.Label(
            status_line,
            text="0%",
            bg=C["card"],
            fg=C["accent"],
            font=self.app.f_body_bold,
            anchor="e",
        )
        self.percent_label.pack(side="right")

        buttons = tk.Frame(inner, bg=C["card"])
        buttons.pack(side="bottom", fill="x", pady=(s(14), 0))
        self.start_btn = Button(
            buttons,
            "下载并合并",
            self._start,
            kind="primary",
            font=self.app.f_button,
            padx=s(22),
            pady=s(9),
        )
        self.start_btn.pack(side="right")
        self.close_btn = Button(
            buttons,
            "取消",
            self._close,
            kind="ghost",
            font=self.app.f_small,
            padx=s(16),
            pady=s(9),
        )
        self.close_btn.pack(side="right", padx=(0, s(8)))
        self.start_btn.set_enabled(False)

        self.update_idletasks()
        width, height = s(660), s(500)
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(width, int(screen_w * 0.92))
        height = min(height, int(screen_h * 0.88))
        x = app.root.winfo_rootx() + max(0, (app.root.winfo_width() - width) // 2)
        y = app.root.winfo_rooty() + max(0, (app.root.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.after(80, self._poll)
        self._fetch_versions()

    def _loader_kind(self) -> str:
        label = self.loader_var.get()
        for key, value in LOADER_LABELS.items():
            if value == label:
                return key
        return "fabric"

    def _fetch_versions(self) -> None:
        if self._installing:
            return
        self._fetch_cancel.set()
        self._fetch_cancel = threading.Event()
        self._fetch_token += 1
        token = self._fetch_token
        self._choices = []
        self.version_combo.configure(values=[], state="disabled")
        self.start_btn.set_enabled(False)
        self.status_label.configure(text="正在获取加载器版本…", fg=C["muted"])
        self.progress.set(0)
        self.percent_label.configure(text="0%", fg=C["accent"])
        kind = self._loader_kind()
        mc_version = self.base.id
        fetch_cancel = self._fetch_cancel

        def worker() -> None:
            try:
                choices = list_loader_versions(kind, mc_version, cancel=fetch_cancel)
            except Exception as exc:
                self._queue.put(("fetch_error", token, str(exc)))
            else:
                self._queue.put(("fetch", token, choices))

        threading.Thread(target=worker, name="mcvd-loader-meta", daemon=True).start()

    def _on_fetch(self, choices: list[LoaderChoice]) -> None:
        self._choices = choices
        if not choices:
            self.status_label.configure(
                text=f"没有找到适用于 {self.base.id} 的加载器版本。",
                fg=C["warning"],
            )
            self.start_btn.set_enabled(False)
            return
        self.version_combo.configure(
            values=[choice.label for choice in choices],
            state="readonly",
        )
        preferred = next((choice for choice in choices if choice.stable), choices[0])
        self.version_var.set(preferred.label)
        self.status_label.configure(
            text=f"找到 {len(choices)} 个版本，默认选择最稳定的版本。",
            fg=C["muted"],
        )
        self.start_btn.set_enabled(not self._installing)

    def _selected_choice(self) -> LoaderChoice | None:
        label = self.version_var.get()
        return next((choice for choice in self._choices if choice.label == label), None)

    def _start(self) -> None:
        if self._installing:
            return
        choice = self._selected_choice()
        if choice is None:
            self.status_label.configure(text="请先选择一个加载器版本。", fg=C["warning"])
            return
        self._installing = True
        self._install_cancel = threading.Event()
        self.loader_combo.configure(state="disabled")
        self.version_combo.configure(state="disabled")
        self.full_row.set_enabled(False)
        self.start_btn.set_enabled(False)
        self.close_btn.configure(text="取消合并")
        self.status_label.configure(text="准备下载基础版本…", fg=C["text_soft"])
        mode = "full" if self.full_row.value else "core"

        def worker() -> None:
            try:
                version_id = install_loader(
                    self.app.game_dir,
                    self.base,
                    choice,
                    mode=mode,
                    workers=16,
                    cancel=self._install_cancel,
                    on_progress=lambda fraction, message: self._queue.put(
                        ("progress", fraction, message)
                    ),
                    on_status=lambda message: self._queue.put(("status", message)),
                )
            except Exception as exc:
                self._queue.put(("install_error", str(exc)))
            else:
                self._queue.put(("install_done", version_id))

        threading.Thread(target=worker, name="mcvd-loader-install", daemon=True).start()

    def _poll(self) -> None:
        if self._closed:
            return
        try:
            while True:
                try:
                    event = self._queue.get_nowait()
                except queue.Empty:
                    break
                if not event:
                    continue
                kind = event[0]
                if kind == "fetch" and len(event) >= 3:
                    if event[1] == self._fetch_token:
                        self._on_fetch(event[2])
                elif kind == "fetch_error" and len(event) >= 3:
                    if event[1] == self._fetch_token:
                        self.status_label.configure(
                            text=f"加载器版本获取失败：{event[2]}",
                            fg=C["danger"],
                        )
                        self.start_btn.set_enabled(False)
                elif kind == "status" and len(event) >= 2:
                    self.status_label.configure(text=str(event[1]), fg=C["text_soft"])
                elif kind == "progress" and len(event) >= 3:
                    fraction = max(0.0, min(1.0, float(event[1])))
                    self.progress.set(fraction, C["accent"])
                    self.percent_label.configure(
                        text=f"{int(fraction * 100)}%", fg=C["accent"]
                    )
                    self.status_label.configure(
                        text=str(event[2]), fg=C["text_soft"]
                    )
                elif kind == "install_done" and len(event) >= 2:
                    self.result_id = str(event[1])
                    self._finish()
                    return
                elif kind == "install_error" and len(event) >= 2:
                    self._installing = False
                    self.progress.set(0, C["danger"])
                    self.percent_label.configure(text="失败", fg=C["danger"])
                    self.status_label.configure(
                        text=f"安装失败：{event[1]}", fg=C["danger"]
                    )
                    self.loader_combo.configure(state="readonly")
                    self.version_combo.configure(state="readonly")
                    self.full_row.set_enabled(True)
                    self.start_btn.set_enabled(bool(self._choices))
                    self.close_btn.configure(text="关闭")
        except tk.TclError:
            return
        self.after(120, self._poll)

    def _finish(self) -> None:
        self._closed = True
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _close(self) -> None:
        if self._installing:
            if not self._install_cancel.is_set():
                self._install_cancel.set()
                self.status_label.configure(
                    text="正在取消合并，当前文件完成后停止…",
                    fg=C["warning"],
                )
                self.close_btn.configure(text="取消中…")
            return
        self._closed = True
        self._fetch_cancel.set()
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class Application:
    def __init__(self, root: tk.Tk, settings: Settings):
        self.root = root
        self.settings = settings
        self.game_dir = normalize_game_dir(settings.game_dir)
        self.mode = settings.mode
        self.skip_existing = settings.skip_existing
        self.manifest: Manifest | None = None
        self.detect_results: list[LauncherDir] = []
        self.detection_loading = False
        self.local_versions: dict[str, str] = {}
        self.selected: set[str] = set()
        self.busy = False
        self._closing = False
        self._manifest_token = 0
        self._detect_token = 0
        self._download_token = 0
        self._manifest_loading = False
        self._initial_manifest = True
        self._last_progress: ProgressSnapshot | None = None
        self._filter_job: str | None = None
        self._detection_dialog: DetectionDialog | None = None
        self.cancel_event = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.download_thread: threading.Thread | None = None
        self._offline_notified = False
        self._start_time = 0.0

        self.root.title(f"{APP_NAME}  {__version_display__}")
        self.root.configure(bg=C["bg"])
        self.root.option_add("*tearOff", False)
        self.root.option_add("*TCombobox*Listbox.background", C["card_alt"])
        self.root.option_add("*TCombobox*Listbox.foreground", C["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", C["accent_soft"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", C["text"])
        self.root.option_add("*TCombobox*Listbox.font", (self._family(), 9))

        self._setup_window()
        self._setup_fonts()
        self._setup_styles()
        self._build_header()
        self._build_footer()
        self._build_body()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._report_callback_exception
        self.root.after(80, self._poll_events)
        self.root.after(350, lambda: _set_dark_titlebar(self.root))
        self.root.bind("<Map>", lambda _event: _set_dark_titlebar(self.root), add="+")
        self.local_versions = scan_local_versions(self.game_dir)
        self._set_status("正在连接版本库…", "从 Mojang 官方源获取版本清单", "working")
        self._load_manifest_async()
        self.root.after(250, lambda: self.start_detection(force=False))

    # -- setup ----------------------------------------------------------
    def _family(self) -> str:
        try:
            families = set(tkfont.families(self.root))
        except tk.TclError:
            families = set()
        if "Microsoft YaHei UI" in families:
            return "Microsoft YaHei UI"
        if "Microsoft YaHei" in families:
            return "Microsoft YaHei"
        return "Segoe UI"

    def _setup_fonts(self) -> None:
        family = self._family()
        self.family = family
        self.f_title = tkfont.Font(family=family, size=20, weight="bold")
        self.f_h1 = tkfont.Font(family=family, size=13, weight="bold")
        self.f_h2 = tkfont.Font(family=family, size=11, weight="bold")
        self.f_body = tkfont.Font(family=family, size=10)
        self.f_body_bold = tkfont.Font(family=family, size=10, weight="bold")
        self.f_small = tkfont.Font(family=family, size=9)
        self.f_small_bold = tkfont.Font(family=family, size=9, weight="bold")
        self.f_tiny = tkfont.Font(family=family, size=8)
        self.f_button = tkfont.Font(family=family, size=10, weight="bold")
        self.f_percent = tkfont.Font(family=family, size=18, weight="bold")
        self.f_mono = tkfont.Font(family="Consolas", size=9)

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Versions.Treeview",
            background=C["card"],
            fieldbackground=C["card"],
            foreground=C["text_soft"],
            borderwidth=0,
            relief="flat",
            lightcolor=C["card"],
            darkcolor=C["card"],
            bordercolor=C["card"],
            rowheight=s(34),
            font=self.f_small,
        )
        try:
            style.layout(
                "Versions.Treeview",
                [("Treeview.treearea", {"sticky": "nswe"})],
            )
        except tk.TclError:
            pass
        style.map(
            "Versions.Treeview",
            background=[("selected", "#1b3025")],
            foreground=[("selected", "#eafff2")],
        )
        style.configure(
            "Versions.Treeview.Heading",
            background=C["bg_alt"],
            foreground=C["muted"],
            relief="flat",
            borderwidth=0,
            padding=(s(8), s(9)),
            font=self.f_small_bold,
        )
        style.map(
            "Versions.Treeview.Heading",
            background=[("active", C["card_alt"])],
            foreground=[("active", C["text_soft"])],
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=C["card_alt"],
            background=C["card_alt"],
            foreground=C["text_soft"],
            arrowcolor=C["muted"],
            bordercolor=C["border"],
            lightcolor=C["border"],
            darkcolor=C["border"],
            padding=(s(7), s(5)),
            relief="flat",
            font=self.f_small,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", C["card_alt"]), ("disabled", C["card"])],
            foreground=[("readonly", C["text_soft"]), ("disabled", C["muted_dark"])],
            selectbackground=[("readonly", C["card_alt"])],
            selectforeground=[("readonly", C["text_soft"])],
        )

    def _setup_window(self) -> None:
        global SCALE
        dpi = 96.0
        if os.name == "nt":
            try:
                dpi = float(ctypes.windll.user32.GetDpiForSystem())
            except Exception:
                try:
                    hdc = ctypes.windll.user32.GetDC(0)
                    dpi = float(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))
                    ctypes.windll.user32.ReleaseDC(0, hdc)
                except Exception:
                    dpi = 96.0
        else:
            try:
                dpi = float(self.root.winfo_fpixels("1i"))
            except tk.TclError:
                pass
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        scale = min(
            dpi / 96.0,
            screen_w * 0.94 / BASE_W,
            screen_h * 0.89 / BASE_H,
        )
        scale = max(0.82, min(2.35, scale))
        SCALE = scale
        try:
            self.root.tk.call("tk", "scaling", 1.333333 * scale)
        except tk.TclError:
            pass
        self.scale = scale
        LOGGER.info(
            "display dpi=%.0f screen=%sx%s scale=%.3f", dpi, screen_w, screen_h, scale
        )
        width, height = s(BASE_W), s(BASE_H)
        self.root.minsize(min(s(1000), int(screen_w * 0.92)), min(s(660), int(screen_h * 0.82)))
        geometry = self.settings.geometry
        restored = False
        if geometry:
            match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
            if match:
                gw, gh, gx, gy = (int(part) for part in match.groups())
                if (
                    s(900) <= gw <= screen_w
                    and s(600) <= gh <= screen_h
                    and -10 <= gx < screen_w - 200
                    and 0 <= gy < screen_h - 100
                ):
                    self.root.geometry(f"{gw}x{gh}+{gx}+{gy}")
                    restored = True
        if not restored:
            x = max(0, (screen_w - width) // 2)
            y = max(0, (screen_h - height) // 2 - s(8))
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        _try_icon(self.root)

    # -- header ---------------------------------------------------------
    def _build_header(self) -> None:
        header = tk.Frame(self.root, bg=C["bg_alt"], height=s(78))
        header.pack(side="top", fill="x")
        header.pack_propagate(False)
        inner = tk.Frame(header, bg=C["bg_alt"])
        inner.pack(fill="both", expand=True, padx=s(22), pady=s(13))

        logo = tk.Canvas(inner, width=s(48), height=s(48), bg=C["bg_alt"], highlightthickness=0)
        logo.pack(side="left", padx=(0, s(12)))
        _draw_logo(logo, s(48))

        titles = tk.Frame(inner, bg=C["bg_alt"])
        titles.pack(side="left", fill="y")
        tk.Label(
            titles,
            text=APP_NAME,
            bg=C["bg_alt"],
            fg=C["text"],
            font=self.f_title,
            anchor="w",
        ).pack(anchor="w")
        self.header_subtitle = tk.Label(
            titles,
            text="Minecraft Java Edition · 官方版本库 · 自动检测启动器目录",
            bg=C["bg_alt"],
            fg=C["muted"],
            font=self.f_small,
            anchor="w",
        )
        self.header_subtitle.pack(anchor="w", pady=(s(1), 0))

        right = tk.Frame(inner, bg=C["bg_alt"])
        right.pack(side="right", fill="y")
        version_label = tk.Label(
            right,
            text=__version_display__,
            bg=C["bg_alt"],
            fg=C["muted_dark"],
            font=self.f_small,
            anchor="e",
        )
        version_label.pack(side="right", padx=(s(12), 0))
        self.net_pill = tk.Frame(
            right,
            bg=C["card_alt"],
            highlightbackground=C["border"],
            highlightthickness=max(1, s(1)),
        )
        self.net_pill.pack(side="right", pady=s(10))
        self.net_dot = tk.Canvas(self.net_pill, width=s(9), height=s(9), bg=C["card_alt"], highlightthickness=0)
        self.net_dot.pack(side="left", padx=(s(10), s(6)), pady=s(7))
        self.net_label = tk.Label(
            self.net_pill,
            text="连接版本库…",
            bg=C["card_alt"],
            fg=C["muted"],
            font=self.f_small,
            anchor="e",
        )
        self.net_label.pack(side="left", padx=(0, s(11)))
        self._set_net_status("working", "连接版本库…")
        tk.Frame(self.root, bg=C["border_soft"], height=max(1, s(1))).pack(side="top", fill="x")

    def _set_net_status(self, tone: str, text: str) -> None:
        colors = {
            "online": C["accent"],
            "working": C["warning"],
            "offline": C["warning"],
            "error": C["danger"],
        }
        color = colors.get(tone, C["muted"])
        try:
            self.net_dot.delete("all")
            self.net_dot.create_oval(s(1), s(1), s(8), s(8), fill=color, outline="")
            self.net_label.configure(text=truncate_middle(text, 42), fg=C["text_soft"] if tone == "online" else C["muted"])
        except tk.TclError:
            pass

    # -- footer ---------------------------------------------------------
    def _build_footer(self) -> None:
        footer = tk.Frame(self.root, bg=C["bg_alt"], height=s(136))
        footer.pack(side="bottom", fill="x")
        footer.pack_propagate(False)
        tk.Frame(footer, bg=C["border_soft"], height=max(1, s(1))).pack(side="top", fill="x")
        inner = tk.Frame(footer, bg=C["bg_alt"])
        inner.pack(fill="both", expand=True, padx=s(22), pady=(s(10), s(11)))

        top = tk.Frame(inner, bg=C["bg_alt"])
        top.pack(fill="x")
        self.status_dot = tk.Canvas(top, width=s(10), height=s(10), bg=C["bg_alt"], highlightthickness=0)
        self.status_dot.pack(side="left", padx=(0, s(7)), pady=(s(4), 0))
        self.status_label = tk.Label(
            top,
            text="准备就绪",
            bg=C["bg_alt"],
            fg=C["text"],
            font=self.f_body_bold,
            anchor="w",
        )
        self.status_label.pack(side="left")
        self.percent_label = tk.Label(
            top,
            text="0%",
            bg=C["bg_alt"],
            fg=C["accent"],
            font=self.f_percent,
            anchor="e",
        )
        self.percent_label.pack(side="right")

        self.progress = ProgressBar(inner)
        self.progress.pack(fill="x", pady=(s(7), s(8)))

        bottom = tk.Frame(inner, bg=C["bg_alt"])
        bottom.pack(fill="x")
        self.detail_label = tk.Label(
            bottom,
            text="选择版本后开始下载",
            bg=C["bg_alt"],
            fg=C["muted"],
            font=self.f_small,
            anchor="w",
        )
        self.detail_label.pack(side="left", fill="x", expand=True)

        actions = tk.Frame(bottom, bg=C["bg_alt"])
        actions.pack(side="right")
        self.start_btn = Button(
            actions,
            "开始下载",
            self.start_download,
            kind="primary",
            font=self.f_button,
            padx=s(22),
            pady=s(9),
        )
        self.start_btn.grid(row=0, column=3, padx=(s(8), 0))
        self.cancel_btn = Button(
            actions,
            "取消下载",
            self.cancel_download,
            kind="danger",
            font=self.f_small_bold,
            padx=s(14),
            pady=s(9),
        )
        self.cancel_btn.grid(row=0, column=2, padx=(0, s(8)))
        self.cancel_btn.grid_remove()
        self.open_btn = Button(
            actions,
            "打开目录",
            self.open_target,
            kind="ghost",
            font=self.f_small,
            padx=s(14),
            pady=s(9),
        )
        self.open_btn.grid(row=0, column=1)
        self.start_btn.set_enabled(False)

    # -- body -----------------------------------------------------------
    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=s(20), pady=(s(15), s(13)))

        sidebar = tk.Frame(body, bg=C["bg"], width=s(332))
        sidebar.pack(side="right", fill="y")
        sidebar.pack_propagate(False)

        self.main_card = Card(body)
        self.main_card.pack(side="left", fill="both", expand=True, padx=(0, s(14)))
        self._build_version_card(self.main_card)
        self._build_target_card(sidebar)
        self._build_mode_card(sidebar)

    def _build_version_card(self, parent: tk.Misc) -> None:
        inner = tk.Frame(parent, bg=C["card"])
        inner.pack(fill="both", expand=True, padx=s(17), pady=(s(15), s(11)))

        header = tk.Frame(inner, bg=C["card"])
        header.pack(fill="x")
        title_box = tk.Frame(header, bg=C["card"])
        title_box.pack(side="left", fill="x", expand=True)
        title_line = tk.Frame(title_box, bg=C["card"])
        title_line.pack(anchor="w")
        tk.Label(
            title_line,
            text="版本库",
            bg=C["card"],
            fg=C["text"],
            font=self.f_h1,
            anchor="w",
        ).pack(side="left")
        self.count_badge = tk.Label(
            title_line,
            text="加载中",
            bg=C["accent_soft"],
            fg=C["accent"],
            font=self.f_tiny,
            padx=s(8),
            pady=s(2),
        )
        self.count_badge.pack(side="left", padx=(s(9), 0))
        self.latest_label = tk.Label(
            title_box,
            text="正在获取官方版本清单…",
            bg=C["card"],
            fg=C["muted"],
            font=self.f_small,
            anchor="w",
        )
        self.latest_label.pack(anchor="w", pady=(s(2), 0))

        controls = tk.Frame(header, bg=C["card"])
        controls.pack(side="right", anchor="n", pady=(s(1), 0))
        self.search_entry = PlaceholderEntry(
            controls,
            "搜索版本，如 1.20.1",
            width=25,
            font=self.f_small,
        )
        self.search_entry.pack(side="left", ipady=s(6), padx=(0, s(7)))
        self.search_entry.bind("<KeyRelease>", self._on_search_key)
        self.filter_var = tk.StringVar(value="all")
        self.refresh_btn = Button(
            controls,
            "刷新",
            lambda: self._load_manifest_async(force=True),
            kind="ghost",
            font=self.f_small,
            padx=s(11),
            pady=s(7),
        )
        self.refresh_btn.pack(side="left")

        toolbar = tk.Frame(inner, bg=C["card"])
        toolbar.pack(fill="x", pady=(s(9), s(3)))
        self.select_visible_btn = Button(
            toolbar, "全选当前", lambda: self.select_visible(), kind="link",
            font=self.f_small_bold, padx=0, pady=s(3),
        )
        self.select_visible_btn.pack(side="left", padx=(0, s(13)))
        self.select_release_btn = Button(
            toolbar, "全选正式版", lambda: self.select_releases(), kind="link",
            font=self.f_small_bold, padx=0, pady=s(3),
        )
        self.select_release_btn.pack(side="left", padx=(0, s(13)))
        self.clear_btn = Button(
            toolbar, "清空选择", self.clear_selection, kind="link",
            font=self.f_small, padx=0, pady=s(3),
        )
        self.clear_btn.pack(side="left")
        self.selected_badge = tk.Label(
            toolbar,
            text="已选 0",
            bg=C["card_alt"],
            fg=C["muted"],
            font=self.f_tiny,
            padx=s(9),
            pady=s(3),
        )
        self.selected_badge.pack(side="right")
        self.loader_btn = Button(
            toolbar, "模组加载器", self.open_loader_dialog, kind="link",
            font=self.f_small_bold, padx=0, pady=s(3),
        )
        self.loader_btn.pack(side="right", padx=(0, s(14)))

        category_bar = tk.Frame(inner, bg=C["card"])
        category_bar.pack(fill="x", pady=(s(6), 0))
        self.category_buttons: dict[str, Button] = {}
        for key, label in CATEGORIES:
            button = Button(
                category_bar,
                label,
                lambda category=key: self.set_category(category),
                kind="ghost",
                font=self.f_small_bold,
                padx=s(10),
                pady=s(4),
            )
            button.pack(side="left", padx=(0, s(6)))
            self.category_buttons[key] = button
        self._refresh_category_chips()

        table_frame = tk.Frame(inner, bg=C["card"])
        table_frame.pack(fill="both", expand=True, pady=(s(4), 0))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        columns = ("sel", "version", "type", "date", "local")
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
            style="Versions.Treeview",
        )
        self.tree.heading("sel", text="")
        self.tree.heading("version", text="版本")
        self.tree.heading("type", text="类型")
        self.tree.heading("date", text="发布时间")
        self.tree.heading("local", text="本地")
        self.tree.column("sel", width=s(34), minwidth=s(30), stretch=False, anchor="center")
        self.tree.column("version", width=s(290), minwidth=s(150), stretch=True)
        self.tree.column("type", width=s(118), minwidth=s(90), stretch=False)
        self.tree.column("date", width=s(105), minwidth=s(88), stretch=False)
        self.tree.column("local", width=s(82), minwidth=s(64), stretch=False, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = SlimScrollbar(table_frame, command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.empty_label = tk.Label(
            table_frame,
            text="正在获取版本清单…",
            bg=C["card"],
            fg=C["muted"],
            font=self.f_body,
        )
        self.empty_label.place(relx=0.5, rely=0.48, anchor="center")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<space>", self._on_tree_space)
        self.tree.bind("<Control-a>", self._on_tree_select_all)
        self.tree.bind("<Control-A>", self._on_tree_select_all)

        hint = tk.Frame(inner, bg=C["card"])
        hint.pack(fill="x", pady=(s(5), 0))
        tk.Label(
            hint,
            text="单击左侧方框勾选 · 支持 Ctrl / Shift 多选 · Ctrl+F 搜索",
            bg=C["card"],
            fg=C["muted_dark"],
            font=self.f_tiny,
            anchor="w",
        ).pack(side="left")
        self.visible_label = tk.Label(
            hint,
            text="",
            bg=C["card"],
            fg=C["muted_dark"],
            font=self.f_tiny,
            anchor="e",
        )
        self.visible_label.pack(side="right")

    def _build_target_card(self, sidebar: tk.Misc) -> None:
        card = Card(sidebar)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=C["card"])
        inner.pack(fill="x", padx=s(15), pady=s(14))

        head = tk.Frame(inner, bg=C["card"])
        head.pack(fill="x")
        tk.Label(
            head, text="下载位置", bg=C["card"], fg=C["text"],
            font=self.f_h2, anchor="w",
        ).pack(side="left")
        self.default_badge = tk.Label(
            head,
            text="默认 D 盘",
            bg=C["card_alt"],
            fg=C["accent"],
            font=self.f_tiny,
            padx=s(7),
            pady=s(2),
        )
        self.default_badge.pack(side="right")

        path_row = tk.Frame(inner, bg=C["card"])
        path_row.pack(fill="x", pady=(s(9), 0))
        self.path_var = tk.StringVar(value=str(self.game_dir))
        self.path_entry = tk.Entry(
            path_row,
            textvariable=self.path_var,
            bg=C["card_alt"],
            fg=C["text_soft"],
            insertbackground=C["text"],
            relief="flat",
            bd=0,
            highlightthickness=max(1, s(1)),
            highlightbackground=C["border"],
            highlightcolor=C["accent"],
            font=self.f_small,
        )
        self.path_entry.pack(side="left", fill="x", expand=True, ipady=s(7))
        self.path_entry.bind("<Return>", lambda _event: self.apply_target(self.path_var.get(), notify=False))
        self.path_entry.bind("<FocusOut>", lambda _event: self.apply_target(self.path_var.get(), notify=False))
        self.browse_btn = Button(
            path_row, "选择", self.browse_target, kind="ghost",
            font=self.f_small, padx=s(10), pady=s(7),
        )
        self.browse_btn.pack(side="right", padx=(s(6), 0))

        self.version_path_label = tk.Label(
            inner,
            text="",
            bg=C["card"],
            fg=C["muted_dark"],
            font=self.f_tiny,
            anchor="w",
            justify="left",
            wraplength=s(285),
        )
        self.version_path_label.pack(fill="x", pady=(s(5), 0))

        bottom = tk.Frame(inner, bg=C["card"])
        bottom.pack(fill="x", pady=(s(8), 0))
        self.free_label = tk.Label(
            bottom, text="", bg=C["card"], fg=C["muted"],
            font=self.f_tiny, anchor="w",
        )
        self.free_label.pack(side="left")
        self.detect_btn = Button(
            bottom, "检测启动器", self.open_detection, kind="subtle",
            font=self.f_tiny, padx=s(8), pady=s(5),
        )
        self.detect_btn.pack(side="right")
        self.detect_badge = tk.Label(
            bottom, text="0", bg=C["card_alt"], fg=C["muted"],
            font=self.f_tiny, padx=s(5), pady=s(3),
        )
        self.detect_badge.pack(side="right", padx=(0, s(5)))

        self._update_target_labels()

    def _build_mode_card(self, sidebar: tk.Misc) -> None:
        card = Card(sidebar)
        card.pack(fill="x", pady=(s(12), 0))
        inner = tk.Frame(card, bg=C["card"])
        inner.pack(fill="x", padx=s(15), pady=s(14))
        head = tk.Frame(inner, bg=C["card"])
        head.pack(fill="x")
        tk.Label(
            head, text="下载模式", bg=C["card"], fg=C["text"],
            font=self.f_h2, anchor="w",
        ).pack(side="left")
        tk.Label(
            head, text="可选", bg=C["card_alt"], fg=C["muted"],
            font=self.f_tiny, padx=s(7), pady=s(2),
        ).pack(side="right")

        self.mode_cards: dict[str, ModeCard] = {}
        self.core_card = ModeCard(
            inner,
            "仅版本文件",
            "版本 JSON + client.jar · 适合已有启动器",
            "core",
            self.set_mode,
        )
        self.core_card.pack(fill="x", pady=(s(9), 0))
        self.full_card = ModeCard(
            inner,
            "完整安装",
            "库文件 + 资源 + 日志 · 适合全新目录",
            "full",
            self.set_mode,
        )
        self.full_card.pack(fill="x", pady=(s(6), 0))
        self.mode_cards = {"core": self.core_card, "full": self.full_card}

        self.skip_row = CheckRow(
            inner,
            "跳过已安装的版本",
            self.skip_existing,
            self._on_skip_changed,
            bg=C["card"],
        )
        self.skip_row.pack(fill="x", pady=(s(10), 0))

        self.mode_tip = tk.Label(
            inner,
            text="",
            bg=C["card"],
            fg=C["muted_dark"],
            font=self.f_tiny,
            justify="left",
            anchor="w",
            wraplength=s(290),
        )
        self.mode_tip.pack(fill="x", pady=(s(6), 0))
        self._refresh_mode_cards()
        self._update_mode_tip()

        tip_card = Card(sidebar)
        tip_card.pack(fill="x", pady=(s(12), 0))
        tip_inner = tk.Frame(tip_card, bg=C["card"])
        tip_inner.pack(fill="x", padx=s(14), pady=s(11))
        tk.Label(
            tip_inner,
            text="提示",
            bg=C["card"],
            fg=C["warning"],
            font=self.f_small_bold,
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            tip_inner,
            text="下载只包含游戏文件，启动游戏仍需 Java。完整安装不会自动下载 Java 运行时。",
            bg=C["card"],
            fg=C["muted"],
            font=self.f_tiny,
            justify="left",
            anchor="w",
            wraplength=s(275),
        ).pack(fill="x", pady=(s(3), 0))

    # -- manifest / target ----------------------------------------------
    def _load_manifest_async(self, *, force: bool = False) -> None:
        if self._manifest_loading:
            return
        self._manifest_loading = True
        self._manifest_token += 1
        token = self._manifest_token
        self.refresh_btn.set_enabled(False)
        self.refresh_btn.configure(text="刷新中")
        self._set_net_status("working", "正在刷新版本库…" if force else "连接版本库…")
        if not force:
            self._set_status("正在连接版本库…", "从 Mojang 官方源获取版本清单", "working")

        def worker() -> None:
            try:
                result = fetch_manifest(cancel=None)
            except Exception as exc:
                LOGGER.exception("manifest fetch failed")
                self.events.put(("manifest_error", token, exc))
            else:
                self.events.put(("manifest", token, result))

        threading.Thread(target=worker, name="mcvd-manifest", daemon=True).start()

    def _on_manifest(self, manifest: Manifest) -> None:
        first = self.manifest is None
        self.manifest = manifest
        self._manifest_loading = False
        self.refresh_btn.set_enabled(True)
        self.refresh_btn.configure(text="刷新")
        source_text = f"{manifest.source_label} · {len(manifest.versions)} 个版本"
        self._set_net_status("online" if not manifest.offline else "offline", source_text)
        self.count_badge.configure(text=f"{len(manifest.versions)} 个版本")
        latest = manifest.latest_release or (manifest.versions[0].id if manifest.versions else "")
        self.latest_label.configure(
            text=f"最新正式版 {latest} · 支持正式版 / 快照 / 旧版 Alpha 与 Beta"
        )
        self.header_subtitle.configure(
            text=f"Minecraft Java Edition · {len(manifest.versions)} 个官方版本 · 自动检测启动器目录"
        )
        self.local_versions = scan_local_versions(self.game_dir)
        if first and not self.selected and latest:
            self.selected.add(latest)
        self.refresh_tree(scroll_to=latest if first else None)
        if manifest.offline and not self._offline_notified:
            self._offline_notified = True
            self._set_status(
                "已使用离线版本清单",
                "联网后可点击右上角“刷新”重新获取；下载文件仍需网络",
                "warning",
            )
        elif first:
            self._set_status(
                f"已选择最新正式版 {latest}" if latest in self.selected else "版本库已就绪",
                "选择版本后点击右下角“开始下载”",
                "idle",
            )
        if first:
            self._initial_manifest = False

    def _on_manifest_error(self, exc: Exception) -> None:
        self._manifest_loading = False
        self.refresh_btn.set_enabled(True)
        self.refresh_btn.configure(text="刷新")
        self._set_net_status("error", "版本库连接失败")
        self._set_status("无法获取版本清单", str(exc), "error")
        if self.manifest is None:
            self.count_badge.configure(text="连接失败")
            self.latest_label.configure(text="请检查网络后点击“刷新”重试")
            self.empty_label.configure(text="暂时无法获取版本清单\n请检查网络连接后刷新")
        LOGGER.warning("manifest error: %s", exc)

    def refresh_tree(self, *, scroll_to: str | None = None) -> None:
        if self.manifest is None:
            self._refresh_category_chips()
            return
        query = self.search_entry.get_value().strip().lower()
        selected_filter = self.filter_var.get() or "all"
        current_top = self.tree.yview()[0] if self.tree.get_children() else 0.0
        self.tree.delete(*self.tree.get_children())
        visible = 0
        for entry in self.manifest.versions:
            if query and query not in entry.id.lower():
                continue
            if selected_filter == "installed":
                if entry.id not in self.local_versions:
                    continue
            elif selected_filter == "old":
                if entry.type not in ("old_beta", "old_alpha"):
                    continue
            elif selected_filter in ("release", "snapshot"):
                if entry.type != selected_filter:
                    continue
            check = "✓" if entry.id in self.selected else ""
            local = {"installed": "已安装", "partial": "部分"}.get(
                self.local_versions.get(entry.id, ""), ""
            )
            values = (
                check,
                entry.id,
                display_version_type(entry, self.manifest.latest_release),
                friendly_date(entry.release_time),
                local,
            )
            self.tree.insert("", "end", iid=entry.id, values=values)
            visible += 1
        self.visible_label.configure(text=f"显示 {visible} / {len(self.manifest.versions)}")
        if visible == 0:
            self.empty_label.configure(
                text="没有匹配的版本\n尝试更换搜索词或筛选条件"
            )
            self.empty_label.place(relx=0.5, rely=0.48, anchor="center")
            self.empty_label.lift()
        else:
            self.empty_label.place_forget()
        if scroll_to and self.tree.exists(scroll_to):
            self.tree.see(scroll_to)
            self.tree.selection_set(scroll_to)
        elif current_top and not query and not scroll_to:
            self.tree.yview_moveto(current_top)
        self._update_selected_ui()
        self._refresh_category_chips()

    def _on_search_key(self, event=None) -> None:
        if event is not None and event.keysym in ("Up", "Down", "Return", "Escape"):
            return
        if self._filter_job:
            try:
                self.root.after_cancel(self._filter_job)
            except tk.TclError:
                pass
        self._filter_job = self.root.after(160, self._apply_filter)

    def _apply_filter(self) -> None:
        self._filter_job = None
        self.refresh_tree()

    def _reset_search(self) -> None:
        self.search_entry.set_value("")
        self.refresh_tree()
        self.search_entry.focus_set()

    def _update_target_labels(self) -> None:
        versions = self.game_dir / "versions"
        self.version_path_label.configure(text=f"版本目录：{truncate_middle(str(versions), 58)}")
        drive = self.game_dir.drive.upper().rstrip(":") if self.game_dir.drive else ""
        if drive == "D":
            self.default_badge.configure(text="默认 D 盘", fg=C["accent"])
        else:
            self.default_badge.configure(text="自定义目录", fg=C["muted"])
        try:
            self.free_label.configure(text=free_space_text(self.game_dir))
        except Exception:
            self.free_label.configure(text="空间未知")

    def apply_target(self, value: str | None = None, *, notify: bool = False) -> bool:
        raw = value if value is not None else self.path_var.get()
        if not raw.strip():
            self._set_status("下载位置不能为空", "请选择一个游戏目录", "warning")
            return False
        try:
            path = normalize_game_dir(raw)
        except (OSError, ValueError) as exc:
            self._set_status("下载位置无效", str(exc), "error")
            return False
        if os.name == "nt" and path.drive and not has_drive(path):
            self._set_status("磁盘不存在", f"找不到 {path.drive} 盘", "error")
            return False
        if path.exists() and not path.is_dir():
            self._set_status("下载位置无效", "该路径不是文件夹", "error")
            return False
        self.game_dir = path
        self.settings.game_dir = str(path)
        self.path_var.set(str(path))
        self.local_versions = scan_local_versions(path)
        self._update_target_labels()
        if self.manifest is not None:
            self.refresh_tree()
        if notify:
            self._set_status("下载位置已切换", str(path), "idle")
        LOGGER.info("target set to %s", path)
        return True

    def browse_target(self) -> None:
        initial = self.game_dir if self.game_dir.exists() else Path.home()
        chosen = filedialog.askdirectory(
            parent=self.root,
            title="选择 Minecraft 游戏目录（会自动使用其中的 versions 文件夹）",
            initialdir=str(initial),
        )
        if chosen:
            self.apply_target(chosen, notify=True)

    def _on_skip_changed(self, value: bool) -> None:
        self.skip_existing = value
        self.settings.skip_existing = value

    def set_mode(self, mode: str) -> None:
        if self.busy or mode not in ("core", "full"):
            return
        self.mode = mode
        self.settings.mode = mode
        self._refresh_mode_cards()
        self._update_mode_tip()

    def _refresh_mode_cards(self) -> None:
        for key, card in self.mode_cards.items():
            card.set_selected(key == self.mode)

    def _update_mode_tip(self) -> None:
        if self.mode == "core":
            self.mode_tip.configure(
                text="写入已有启动器时最省空间；启动器会自行补全运行库。"
            )
        else:
            self.mode_tip.configure(
                text="下载客户端、库、原生库与全部资源；适合全新目录。"
            )

    # -- detection ------------------------------------------------------
    def start_detection(self, *, force: bool = False) -> None:
        if self.detection_loading and not force:
            return
        self._detect_token += 1
        token = self._detect_token
        self.detection_loading = True
        self.detect_btn.set_enabled(False)
        self.detect_badge.configure(text="…", fg=C["warning"])

        def worker() -> None:
            try:
                results = detect_launchers(timeout=5.0)
            except Exception:
                LOGGER.exception("launcher detection failed")
                results = []
            self.events.put(("detected", token, results))

        threading.Thread(target=worker, name="mcvd-detect", daemon=True).start()

    def _on_detected(self, results: list[LauncherDir]) -> None:
        self.detection_loading = False
        self.detect_results = results
        self.detect_btn.set_enabled(not self.busy)
        self.detect_badge.configure(
            text=str(len(results)), fg=C["accent"] if results else C["muted"]
        )
        if self._detection_dialog is not None:
            try:
                self._detection_dialog.set_results(results)
            except tk.TclError:
                self._detection_dialog = None
        LOGGER.info("detected %s launcher directories", len(results))

    def open_detection(self) -> None:
        if self.busy:
            return
        try:
            if self._detection_dialog is not None and self._detection_dialog.winfo_exists():
                self._detection_dialog.lift()
                self._detection_dialog.focus_force()
                return
        except tk.TclError:
            pass
        self._detection_dialog = DetectionDialog(self)
        try:
            self._detection_dialog.wait_window()
        finally:
            self._detection_dialog = None

    def open_loader_dialog(self) -> None:
        if self.busy:
            return
        if self.manifest is None:
            InfoDialog(
                self.root,
                "版本库尚未就绪",
                "请等待版本清单加载完成后再安装模组加载器。",
                kind="warning",
                width=480,
                height=250,
            ).show()
            return
        if len(self.selected) != 1:
            InfoDialog(
                self.root,
                "请先选择一个基础版本",
                "安装模组加载器需要以一个原版版本为基础。\n"
                "请先在左侧勾选且只勾选一个版本，再点击“模组加载器”。",
                kind="info",
                width=520,
                height=280,
            ).show()
            return
        version_id = next(iter(self.selected))
        base = next((entry for entry in self.manifest.versions if entry.id == version_id), None)
        if base is None:
            return
        dialog = LoaderDialog(self, base)
        try:
            dialog.wait_window()
        finally:
            pass
        if dialog.result_id:
            self.local_versions = scan_local_versions(self.game_dir)
            self.refresh_tree()
            self._set_status(
                "模组加载器已合并",
                f"已创建本地版本 {dialog.result_id} · 可在启动器中选择",
                "done",
            )
            InfoDialog(
                self.root,
                "模组加载器已安装",
                f"已创建版本：\n{dialog.result_id}\n\n"
                "基础版本和加载器库已经合并到下载目录。"
                "请重启启动器或刷新版本列表后选择该版本。",
                kind="success",
                secondary_text="打开下载目录",
                secondary_command=lambda: open_path(self.game_dir),
                width=580,
                height=300,
            ).show()

    # -- selection ------------------------------------------------------
    def _visible_ids(self) -> list[str]:
        return list(self.tree.get_children())

    def _toggle_id(self, version_id: str) -> None:
        if version_id in self.selected:
            self.selected.discard(version_id)
        else:
            self.selected.add(version_id)
        if self.tree.exists(version_id):
            self.tree.set(version_id, "sel", "✓" if version_id in self.selected else "")
        self._update_selected_ui()

    def _toggle_ids(self, version_ids: list[str]) -> None:
        for version_id in version_ids:
            if version_id in self.selected:
                self.selected.discard(version_id)
            else:
                self.selected.add(version_id)
            if self.tree.exists(version_id):
                self.tree.set(version_id, "sel", "✓" if version_id in self.selected else "")
        self._update_selected_ui()

    def _on_tree_click(self, event):
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        region = self.tree.identify_region(event.x, event.y)
        if region == "heading":
            return None
        if column == "#1" and row:
            if not self.busy:
                self._toggle_id(row)
            return "break"
        return None

    def _on_tree_double_click(self, event):
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        region = self.tree.identify_region(event.x, event.y)
        if row and region == "cell" and column != "#1" and not self.busy:
            self._toggle_id(row)
            return "break"
        return None

    def _on_tree_space(self, _event=None):
        if self.busy:
            return "break"
        rows = list(self.tree.selection())
        if rows:
            self._toggle_ids(rows)
        return "break"

    def _on_tree_select_all(self, _event=None):
        if self.busy:
            return "break"
        self.select_visible()
        return "break"

    def select_visible(self) -> None:
        if self.busy:
            return
        visible = self._visible_ids()
        for version_id in visible:
            self.selected.add(version_id)
        for version_id in visible:
            self.tree.set(version_id, "sel", "✓")
        self._update_selected_ui()

    def select_releases(self) -> None:
        if self.busy or self.manifest is None:
            return
        for entry in self.manifest.versions:
            if entry.type == "release":
                self.selected.add(entry.id)
        for version_id in self._visible_ids():
            if version_id in self.selected:
                self.tree.set(version_id, "sel", "✓")
        self._update_selected_ui()

    def clear_selection(self) -> None:
        if self.busy:
            return
        self.selected.clear()
        for version_id in self._visible_ids():
            self.tree.set(version_id, "sel", "")
        self._update_selected_ui()

    def _update_selected_ui(self) -> None:
        count = len(self.selected)
        self.selected_badge.configure(
            text=f"已选 {count}",
            bg=C["accent_soft"] if count else C["card_alt"],
            fg=C["accent"] if count else C["muted"],
        )
        if self.manifest is not None:
            self.count_badge.configure(text=f"{len(self.manifest.versions)} 个版本")
        self.start_btn.set_enabled(bool(count) and not self.busy and self.manifest is not None)

    def set_category(self, key: str) -> None:
        if self.busy or key not in {item[0] for item in CATEGORIES}:
            return
        self.filter_var.set(key)
        self.refresh_tree()

    def _refresh_category_chips(self) -> None:
        labels = dict(CATEGORIES)
        counts = {key: 0 for key, _label in CATEGORIES}
        if self.manifest is not None:
            counts["all"] = len(self.manifest.versions)
            for entry in self.manifest.versions:
                if entry.type in ("release", "snapshot"):
                    counts[entry.type] += 1
                elif entry.type in ("old_beta", "old_alpha"):
                    counts["old"] += 1
                if entry.id in self.local_versions:
                    counts["installed"] += 1
            text_suffix = lambda key: str(counts[key])
        else:
            text_suffix = lambda _key: "—"
        active = self.filter_var.get() or "all"
        for key, button in getattr(self, "category_buttons", {}).items():
            button.configure(text=f"{labels[key]} {text_suffix(key)}")
            if key == active:
                button.configure(
                    bg=C["accent_soft"],
                    fg=C["accent"],
                    activebackground=C["accent_soft"],
                    activeforeground=C["accent"],
                )
                button._normal_bg = C["accent_soft"]
                button._active_bg = C["accent_soft"]
            else:
                button.configure(
                    bg=C["card_alt"],
                    fg=C["muted"],
                    activebackground="#1c2734",
                    activeforeground=C["text_soft"],
                )
                button._normal_bg = C["card_alt"]
                button._active_bg = "#1c2734"

    # -- status / busy --------------------------------------------------
    def _set_status(self, text: str, detail: str = "", tone: str = "idle") -> None:
        colors = {
            "idle": C["accent"],
            "working": C["warning"],
            "done": C["accent"],
            "warning": C["warning"],
            "error": C["danger"],
        }
        color = colors.get(tone, C["muted"])
        try:
            self.status_dot.delete("all")
            self.status_dot.create_oval(s(1), s(1), s(9), s(9), fill=color, outline="")
            self.status_label.configure(text=truncate_middle(text, 64))
            self.detail_label.configure(text=truncate_middle(detail, 120))
        except tk.TclError:
            pass

    def _set_busy(self, busy: bool) -> None:
        self.busy = bool(busy)
        self.path_entry.configure(state="disabled" if busy else "normal")
        self.search_entry.configure(state="disabled" if busy else "normal")
        for button in (
            self.browse_btn,
            self.detect_btn,
            self.select_visible_btn,
            self.select_release_btn,
            self.clear_btn,
            self.loader_btn,
            *self.category_buttons.values(),
        ):
            button.set_enabled(not busy)
        self.skip_row.set_enabled(not busy)
        for card in self.mode_cards.values():
            card.set_enabled(not busy)
        if not busy:
            self.refresh_btn.set_enabled(not self._manifest_loading)
        else:
            self.refresh_btn.set_enabled(False)
        if busy:
            self.start_btn.configure(text="下载中…")
            self.start_btn.set_enabled(False)
            self.cancel_btn.set_enabled(True)
            self.cancel_btn.grid()
        else:
            self.start_btn.configure(text="开始下载")
            self.cancel_btn.grid_remove()
            self._update_selected_ui()
        self._refresh_category_chips()

    # -- download -------------------------------------------------------
    def start_download(self) -> None:
        if self.busy:
            return
        if self.manifest is None:
            InfoDialog(
                self.root,
                "版本库尚未就绪",
                "版本清单还在加载中，请稍后再试。",
                kind="warning",
                width=470,
                height=260,
            ).show()
            return
        if not self.apply_target(self.path_var.get(), notify=False):
            return
        entries = [
            version for version in self.manifest.versions if version.id in self.selected
        ]
        if not entries:
            self._set_status("还没有选择版本", "在左侧勾选一个或多个版本后再开始", "warning")
            return

        count = len(entries)
        if self.mode == "full":
            message = (
                f"即将完整安装 {count} 个版本。\n\n"
                "完整模式会下载客户端、库文件、原生库、资源索引和全部资源，"
                "单个版本可能占用 100 MB 到 1 GB 以上，耗时也会明显更长。\n\n"
                f"下载位置：{self.game_dir}\n\n确认继续吗？"
            )
            dialog = ConfirmDialog(self.root, "确认完整下载", message, confirm_text="开始下载")
            dialog.show()
            if not dialog.confirmed:
                return
        elif count >= 30:
            message = (
                f"即将下载 {count} 个版本，文件数量和耗时都可能较多。\n\n"
                f"下载位置：{self.game_dir}\n\n确认继续吗？"
            )
            dialog = ConfirmDialog(self.root, "确认批量下载", message, confirm_text="开始下载")
            dialog.show()
            if not dialog.confirmed:
                return

        try:
            versions_dir = self.game_dir / "versions"
            versions_dir.mkdir(parents=True, exist_ok=True)
            probe = versions_dir / ".mcvd_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError as exc:
            InfoDialog(
                self.root,
                "无法写入下载目录",
                f"请换一个可写的目录，或检查磁盘权限。\n\n{self.game_dir}\n\n{exc}",
                kind="error",
                width=560,
                height=320,
            ).show()
            return

        try:
            free = shutil.disk_usage(self.game_dir).free
            if free < 300 * 1024 * 1024:
                dialog = ConfirmDialog(
                    self.root,
                    "磁盘空间不足",
                    f"目标磁盘剩余空间只有 {format_bytes(free)}，可能不够完成下载。\n仍要继续吗？",
                    confirm_text="继续",
                )
                dialog.show()
                if not dialog.confirmed:
                    return
        except OSError:
            pass

        self.settings.game_dir = str(self.game_dir)
        self.settings.mode = self.mode
        self.settings.skip_existing = self.skip_existing
        self.settings.save()
        self.cancel_event = threading.Event()
        self._download_token += 1
        token = self._download_token
        self._start_time = time.monotonic()
        self._last_progress = None
        self.progress.set(0, C["accent"])
        self.percent_label.configure(text="0%", fg=C["accent"])
        self._set_status(
            f"准备下载 {count} 个版本",
            f"共 {count} 个版本 · 正在读取版本信息",
            "working",
        )
        self._set_busy(True)
        self.download_thread = threading.Thread(
            target=self._download_worker,
            args=(entries, token),
            name="mcvd-download",
            daemon=True,
        )
        self.download_thread.start()
        LOGGER.info("download started: %s versions to %s (%s)", count, self.game_dir, self.mode)

    def _download_worker(self, entries: list[VersionEntry], token: int) -> None:
        def on_progress(snapshot: ProgressSnapshot) -> None:
            self.events.put(("progress", token, snapshot))

        def on_log(message: str) -> None:
            LOGGER.info("engine: %s", message)

        try:
            engine = DownloadEngine(
                self.game_dir,
                self.mode,
                skip_existing=self.skip_existing,
                workers=16,
                cancel=self.cancel_event,
                on_progress=on_progress,
                on_log=on_log,
            )
            result = engine.run(entries)
        except Exception as exc:
            LOGGER.exception("download engine crashed")
            self.events.put(("download_error", token, exc))
            return
        self.events.put(("download_done", token, result))

    def cancel_download(self) -> None:
        if not self.busy:
            return
        self.cancel_event.set()
        self.cancel_btn.set_enabled(False)
        self._set_status("正在取消下载…", "当前文件下载完成后停止，已下载文件会保留", "warning")

    def _on_progress(self, snapshot: ProgressSnapshot) -> None:
        if not self.busy:
            return
        self._last_progress = snapshot
        percent = int(round(snapshot.overall * 100))
        color = C["accent"]
        if snapshot.phase == "failed":
            color = C["danger"]
        self.progress.set(snapshot.overall, color)
        self.percent_label.configure(text=f"{percent}%", fg=color)
        if snapshot.phase == "downloading":
            status = f"正在下载 {snapshot.version}"
            tone = "working"
        elif snapshot.phase == "finalizing":
            status = snapshot.message or "整理资源"
            tone = "working"
        elif snapshot.phase == "done":
            status = snapshot.message or "下载完成"
            tone = "done"
        elif snapshot.phase == "failed":
            status = snapshot.message or "下载失败"
            tone = "error"
        else:
            status = snapshot.message or "准备中"
            tone = "working"
        parts = [f"版本 {snapshot.version_index}/{snapshot.version_total}"]
        if snapshot.completed_versions:
            parts.append(f"已完成 {snapshot.completed_versions}")
        if snapshot.files_total:
            parts.append(f"{snapshot.files_done}/{snapshot.files_total} 个文件")
        if snapshot.bytes_total:
            parts.append(
                f"{format_bytes(snapshot.bytes_done)} / {format_bytes(snapshot.bytes_total)}"
            )
        if snapshot.speed:
            parts.append(format_speed(snapshot.speed))
        elif snapshot.phase == "downloading":
            parts.append("正在连接…")
        self._set_status(status, " · ".join(parts), tone)

    def _on_download_done(self, result: EngineResult) -> None:
        if self._closing:
            return
        self._set_busy(False)
        self.local_versions = scan_local_versions(self.game_dir)
        self.refresh_tree()
        if result.cancelled:
            self._set_status(
                "下载已取消",
                f"已完成 {result.installed} 个 · 跳过 {result.skipped} 个 · 失败 {result.failed} 个",
                "warning",
            )
            if self._last_progress:
                self.progress.set(self._last_progress.overall, C["warning"])
        elif result.failed:
            self._set_status(
                "下载结束，但有失败",
                f"成功 {result.installed} 个 · 跳过 {result.skipped} 个 · 失败 {result.failed} 个",
                "error",
            )
            self.progress.set(1.0, C["danger"])
            self.percent_label.configure(text="有失败", fg=C["danger"])
        else:
            self._set_status(
                "下载完成",
                f"新安装 {result.installed} 个 · 跳过 {result.skipped} 个 · {format_bytes(result.session_bytes)}",
                "done",
            )
            self.progress.set(1.0, C["accent"])
            self.percent_label.configure(text="100%", fg=C["accent"])
        self.download_thread = None
        LOGGER.info(
            "download finished: installed=%s skipped=%s failed=%s cancelled=%s",
            result.installed,
            result.skipped,
            result.failed,
            result.cancelled,
        )
        try:
            SummaryDialog(self.root, result, self.game_dir).show()
        except Exception:
            LOGGER.exception("cannot show summary")
            InfoDialog(
                self.root,
                "下载结束",
                f"安装 {result.installed} 个，跳过 {result.skipped} 个，失败 {result.failed} 个。",
                kind="warning" if result.failed or result.cancelled else "success",
                width=500,
                height=300,
            ).show()

    def _on_download_error(self, exc: Exception) -> None:
        if self._closing:
            return
        self._set_busy(False)
        self.cancel_btn.grid_remove()
        self.progress.set(0, C["danger"])
        self.percent_label.configure(text="错误", fg=C["danger"])
        self._set_status("下载任务出错", str(exc), "error")
        InfoDialog(
            self.root,
            "下载任务出错",
            "下载线程意外结束，已下载的文件会保留。你可以重新开始，程序会跳过已存在的文件。",
            kind="error",
            details=str(exc),
            width=580,
            height=360,
        ).show()

    def open_target(self) -> None:
        if not self.apply_target(self.path_var.get(), notify=False):
            return
        try:
            self.game_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.game_dir))
        except OSError as exc:
            InfoDialog(
                self.root,
                "无法打开目录",
                f"{self.game_dir}\n\n{exc}",
                kind="error",
                width=520,
                height=280,
            ).show()

    # -- events / lifecycle ---------------------------------------------
    def _poll_events(self) -> None:
        if self._closing:
            return
        try:
            for _ in range(100):
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    break
                if not isinstance(event, tuple) or not event:
                    continue
                kind = event[0]
                if kind == "manifest" and len(event) >= 3:
                    token, manifest = event[1], event[2]
                    if token == self._manifest_token:
                        self._on_manifest(manifest)
                elif kind == "manifest_error" and len(event) >= 3:
                    token, exc = event[1], event[2]
                    if token == self._manifest_token:
                        self._on_manifest_error(exc)
                elif kind == "detected" and len(event) >= 3:
                    token, results = event[1], event[2]
                    if token == self._detect_token:
                        self._on_detected(results)
                elif kind == "progress" and len(event) >= 3:
                    token, snapshot = event[1], event[2]
                    if token == self._download_token:
                        self._on_progress(snapshot)
                elif kind == "download_done" and len(event) >= 3:
                    token, result = event[1], event[2]
                    if token == self._download_token:
                        self._on_download_done(result)
                elif kind == "download_error" and len(event) >= 3:
                    token, exc = event[1], event[2]
                    if token == self._download_token:
                        self._on_download_error(exc)
        finally:
            if not self._closing:
                try:
                    self.root.after(80, self._poll_events)
                except tk.TclError:
                    pass

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-f>", self._focus_search)
        self.root.bind_all("<Control-F>", self._focus_search)
        self.root.bind_all("<F5>", lambda _event: self._load_manifest_async(force=True))
        self.search_entry.bind("<Escape>", lambda _event: self._reset_search())

    def _focus_search(self, _event=None):
        if self.busy:
            return "break"
        self.search_entry.focus_set()
        try:
            self.search_entry.select_range(0, "end")
        except tk.TclError:
            pass
        return "break"

    def _save_settings(self) -> None:
        self.settings.game_dir = str(self.game_dir)
        self.settings.mode = self.mode
        self.settings.skip_existing = self.skip_existing
        try:
            self.settings.geometry = self.root.geometry()
        except tk.TclError:
            pass
        self.settings.save()

    def _on_close(self) -> None:
        if self.busy:
            dialog = ConfirmDialog(
                self.root,
                "退出下载器",
                "下载仍在进行中。退出会停止下载，但已经下载完成的文件会保留。\n\n确定退出吗？",
                confirm_text="退出",
                cancel_text="继续下载",
                kind="warning",
            )
            dialog.show()
            if not dialog.confirmed:
                return
        self._closing = True
        self.cancel_event.set()
        self._save_settings()
        try:
            if self._detection_dialog is not None:
                self._detection_dialog._close()
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _report_callback_exception(self, exc_type, exc_value, exc_traceback) -> None:
        LOGGER.error(
            "unhandled UI exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        if self._closing:
            return
        try:
            InfoDialog(
                self.root,
                "界面出现异常",
                "操作没有完成，详情已写入日志。你可以重试或重启程序。",
                kind="error",
                details="".join(
                    __import__("traceback").format_exception(
                        exc_type, exc_value, exc_traceback
                    )
                ),
                width=600,
                height=400,
            ).show()
        except Exception:
            pass


def _cleanup_stale_runtime_dirs() -> None:
    """Remove old one-file extraction folders left by interrupted sessions."""
    mei = getattr(sys, "_MEIPASS", None)
    if not mei:
        return
    current = Path(mei)
    if not current.name.startswith("_MEI"):
        return
    cutoff = time.time() - 6 * 60 * 60
    try:
        candidates = list(current.parent.glob("_MEI*"))
    except OSError:
        return
    for candidate in candidates:
        if candidate.resolve(strict=False) == current.resolve(strict=False):
            continue
        try:
            if not (candidate / "assets" / "icon.ico").is_file():
                continue
            if candidate.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(candidate, ignore_errors=True)
        except OSError:
            continue


def run_app() -> None:
    ensure_app_data_dir()
    setup_logging()
    _cleanup_stale_runtime_dirs()
    _enable_dpi_awareness()
    settings = Settings.load()
    root = tk.Tk()
    try:
        Application(root, settings)
        root.mainloop()
    except Exception:
        LOGGER.exception("fatal application error")
        try:
            from tkinter import messagebox

            messagebox.showerror(
                APP_NAME,
                "程序启动失败，请查看日志：\n"
                + str(logs_dir() / "downloader.log"),
            )
        except Exception:
            pass
        raise
    finally:
        LOGGER.info("application exited")


if __name__ == "__main__":
    run_app()
