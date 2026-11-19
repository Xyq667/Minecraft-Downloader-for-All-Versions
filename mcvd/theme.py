"""Colour themes, accent presets and the Windows frosted-glass effect.

The whole GUI reads its colours from a single mutable palette dict
(:data:`mcvd.config.COLORS`).  Switching a theme rewrites that dict in place
and the main window rebuilds itself, so every widget picks up the new colours.

Default appearance is the light theme; a dark theme and five accent colours are
available, plus an optional acrylic/glass blur for the main window on Windows.
"""

from __future__ import annotations

import os
from typing import Any

# Colour used as the window "key": everything painted with it becomes
# transparent so the acrylic blur behind the window shows through.
GLASS_KEY = "#010203"

# Cards are not truly per-pixel transparent in Tk; instead their colour is
# blended toward this tint so the surface reads as frosted glass while text
# stays readable.
GLASS_CARD_TINT = {"light": "#c9d5e2", "dark": "#0a1119"}

THEME_LABELS = {"light": "明亮", "dark": "暗黑"}
THEME_ORDER = ("light", "dark")

ACCENT_LABELS = {
    "green": "森林绿",
    "blue": "天空蓝",
    "purple": "紫罗兰",
    "orange": "熔岩橙",
    "red": "红石红",
}
ACCENT_ORDER = ("green", "blue", "purple", "orange", "red")

_LIGHT_BASE: dict[str, str] = {
    "bg": "#f2f5f9",
    "bg_alt": "#ffffff",
    "card": "#ffffff",
    "card_alt": "#eef2f7",
    "border": "#d7e0ea",
    "border_soft": "#e6edf4",
    "text": "#101a24",
    "text_soft": "#2c3a49",
    "muted": "#5d6c80",
    "muted_dark": "#93a1b3",
    "blue": "#2563eb",
    "warning": "#b45309",
    "danger": "#d92d20",
    "danger_soft": "#fdeceb",
    "danger_hover": "#f6d5d2",
    "disabled_bg": "#e9eef4",
    "disabled_fg": "#a9b4c2",
    "hover_bg": "#e8eef5",
    "selection_bg": "#d9f2e4",
    "selection_fg": "#0b3d24",
    "glass_tint": "40FFFFFF",
}

_DARK_BASE: dict[str, str] = {
    "bg": "#0b0f14",
    "bg_alt": "#0d131b",
    "card": "#111823",
    "card_alt": "#151d27",
    "border": "#222d3a",
    "border_soft": "#1a2430",
    "text": "#e8eef6",
    "text_soft": "#c5d0dd",
    "muted": "#8492a5",
    "muted_dark": "#617086",
    "blue": "#76a9ff",
    "warning": "#f0b35b",
    "danger": "#ff7777",
    "danger_soft": "#341d22",
    "danger_hover": "#4a252c",
    "disabled_bg": "#151d27",
    "disabled_fg": "#5b6b7d",
    "hover_bg": "#1d2937",
    "selection_bg": "#1b3025",
    "selection_fg": "#eafff2",
    "glass_tint": "40000000",
}

# accent, accent_hover, accent_dark, accent_soft, foreground on accent
_ACCENTS: dict[str, dict[str, tuple[str, str, str, str, str]]] = {
    "green": {
        "light": ("#16a34a", "#15803d", "#0f6b33", "#e4f6ec", "#ffffff"),
        "dark": ("#46df87", "#69eba0", "#2cbf6b", "#173426", "#06130b"),
    },
    "blue": {
        "light": ("#2563eb", "#1d4ed8", "#1e40af", "#e7efff", "#ffffff"),
        "dark": ("#76a9ff", "#93bbff", "#4f83e8", "#16233a", "#0a1420"),
    },
    "purple": {
        "light": ("#7c3aed", "#6d28d9", "#5b21b6", "#f0e9fe", "#ffffff"),
        "dark": ("#b18cff", "#c3a6ff", "#8b5cf6", "#241a38", "#120a20"),
    },
    "orange": {
        "light": ("#ea580c", "#c2410c", "#9a3412", "#fdeee2", "#ffffff"),
        "dark": ("#ffa257", "#ffb877", "#f97316", "#33210f", "#1a0f05"),
    },
    "red": {
        "light": ("#dc2626", "#b91c1c", "#991b1b", "#fdeaea", "#ffffff"),
        "dark": ("#ff8080", "#ff9d9d", "#ef4444", "#341a1d", "#1a0a0c"),
    },
}


def is_dark(theme: str) -> bool:
    return theme == "dark"


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def blend(color: str, target: str, ratio: float) -> str:
    """Mix ``color`` toward ``target``; ratio 0 keeps color, 1 returns target."""
    ratio = max(0.0, min(1.0, float(ratio)))
    source_rgb = _rgb(color)
    target_rgb = _rgb(target)
    mixed = [
        int(round(a + (b - a) * ratio)) for a, b in zip(source_rgb, target_rgb)
    ]
    return "#" + "".join(f"{value:02x}" for value in mixed)


def apply_card_opacity(palette: dict[str, Any], opacity: int) -> None:
    """Blend card surfaces toward the glass tint for a translucent look.

    ``opacity`` is the percentage of the solid colour kept (100 = opaque).
    """
    try:
        value = int(opacity)
    except (TypeError, ValueError):
        value = 100
    value = max(50, min(100, value))
    palette["card_opacity"] = value
    if value >= 100:
        return
    ratio = (100 - value) / 100.0
    tint = GLASS_CARD_TINT.get(str(palette.get("theme", "light")), GLASS_CARD_TINT["light"])
    for key in ("card", "card_alt"):
        palette[key] = blend(str(palette[key]), tint, ratio)
    palette["border"] = blend(str(palette["border"]), tint, ratio * 0.6)


def accent_color(accent: str, theme: str = "light") -> str:
    palette = _ACCENTS.get(accent) or _ACCENTS["green"]
    return palette["dark" if is_dark(theme) else "light"][0]


def palette_for(theme: str = "light", accent: str = "green") -> dict[str, Any]:
    """Build a complete palette for the requested theme and accent."""
    theme = theme if theme in THEME_LABELS else "light"
    accent = accent if accent in _ACCENTS else "green"
    palette: dict[str, Any] = dict(_DARK_BASE if is_dark(theme) else _LIGHT_BASE)
    values = _ACCENTS[accent]["dark" if is_dark(theme) else "light"]
    palette["accent"], palette["accent_hover"], palette["accent_dark"] = values[0:3]
    palette["accent_soft"] = values[3]
    palette["accent_fg"] = values[4]
    # ``black`` is the historical key for "text drawn on an accent surface".
    palette["black"] = values[4]
    palette["theme"] = theme
    palette["accent_name"] = accent
    return palette


def apply_palette(target: dict[str, Any], theme: str, accent: str) -> dict[str, Any]:
    """Rewrite ``target`` in place so every module sees the new colours."""
    palette = palette_for(theme, accent)
    target.clear()
    target.update(palette)
    return target


def _window_handle(window) -> int:
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = int(window.winfo_id())
    parent = user32.GetParent(hwnd)
    return int(parent) if parent else hwnd


def apply_window_glass(window, enabled: bool, theme: str = "light") -> bool:
    """Enable/disable acrylic blur behind a Tk toplevel (Windows only).

    Returns True when the DWM call succeeded. Tk cannot render per-pixel
    transparency, so the whole window is slightly translucent while the blur
    is active, which gives the frosted look.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        class ACCENTPOLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(ACCENTPOLICY)),
                ("SizeOfData", ctypes.c_size_t),
            ]

        if enabled:
            try:
                window.attributes("-transparentcolor", GLASS_KEY)
            except Exception:
                pass
        else:
            try:
                window.attributes("-transparentcolor", "")
            except Exception:
                pass

        accent = ACCENTPOLICY()
        if enabled:
            accent.AccentState = 4  # ACCENT_ENABLE_ACRYLICBLURBEHIND
            accent.AccentFlags = 2
            tint = palette_for(theme, "green").get("glass_tint", "1A1A1A1A")
            accent.GradientColor = int(str(tint), 16)
        else:
            accent.AccentState = 0  # ACCENT_DISABLED
            accent.AccentFlags = 0
            accent.GradientColor = 0

        data = WINCOMPATTRDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.SizeOfData = ctypes.sizeof(accent)
        data.Data = ctypes.pointer(accent)

        function = ctypes.windll.user32.SetWindowCompositionAttribute
        function.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINCOMPATTRDATA)]
        function.restype = ctypes.c_int
        result = function(_window_handle(window), ctypes.byref(data))
        return bool(result)
    except Exception:
        try:
            window.attributes("-alpha", 1.0)
        except Exception:
            pass
        return False


def describe(theme: str, accent: str, glass: bool) -> str:
    """Short human-readable summary used by the about dialog."""
    text = f"{THEME_LABELS.get(theme, '明亮')} · {ACCENT_LABELS.get(accent, '森林绿')}"
    if glass:
        text += " · 毛玻璃"
    return text
