from __future__ import annotations

import unittest

from mcvd.config import Settings
from mcvd.theme import (
    ACCENT_LABELS,
    apply_card_opacity,
    blend,
    ACCENT_ORDER,
    THEME_LABELS,
    THEME_ORDER,
    accent_color,
    apply_palette,
    describe,
    is_dark,
    palette_for,
)

REQUIRED_KEYS = {
    "bg",
    "bg_alt",
    "card",
    "card_alt",
    "border",
    "border_soft",
    "text",
    "text_soft",
    "muted",
    "muted_dark",
    "accent",
    "accent_hover",
    "accent_dark",
    "accent_soft",
    "accent_fg",
    "blue",
    "warning",
    "danger",
    "danger_soft",
    "danger_hover",
    "disabled_bg",
    "disabled_fg",
    "hover_bg",
    "selection_bg",
    "selection_fg",
    "black",
    "glass_tint",
}


def luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


class PaletteTest(unittest.TestCase):
    def test_every_theme_and_accent_has_all_keys(self) -> None:
        for theme in THEME_ORDER:
            for accent in ACCENT_ORDER:
                palette = palette_for(theme, accent)
                missing = REQUIRED_KEYS - set(palette)
                self.assertFalse(missing, f"{theme}/{accent} missing {missing}")

    def test_light_theme_is_bright_with_dark_text(self) -> None:
        palette = palette_for("light", "green")
        self.assertGreater(luminance(palette["bg"]), 0.8)
        self.assertGreater(luminance(palette["card"]), 0.9)
        self.assertLess(luminance(palette["text"]), 0.3)
        self.assertEqual(palette["accent_fg"], "#ffffff")
        self.assertFalse(is_dark("light"))

    def test_dark_theme_has_dark_surfaces(self) -> None:
        palette = palette_for("dark", "green")
        self.assertLess(luminance(palette["bg"]), 0.2)
        self.assertGreater(luminance(palette["text"]), 0.7)
        self.assertTrue(is_dark("dark"))

    def test_accent_changes_colour(self) -> None:
        self.assertNotEqual(
            palette_for("light", "green")["accent"],
            palette_for("light", "blue")["accent"],
        )
        self.assertEqual(accent_color("blue", "light"), palette_for("light", "blue")["accent"])

    def test_unknown_values_fall_back(self) -> None:
        palette = palette_for("neon", "rainbow")
        self.assertEqual(palette["theme"], "light")
        self.assertEqual(palette["accent_name"], "green")

    def test_apply_palette_rewrites_in_place(self) -> None:
        target = {"keep": "me", "bg": "#000000"}
        result = apply_palette(target, "dark", "purple")
        self.assertIs(result, target)
        self.assertNotIn("keep", target)
        self.assertEqual(target["theme"], "dark")
        self.assertEqual(target["accent_name"], "purple")


class CardOpacityTest(unittest.TestCase):
    def test_blend_endpoints(self) -> None:
        self.assertEqual(blend("#ffffff", "#000000", 0.0), "#ffffff")
        self.assertEqual(blend("#ffffff", "#000000", 1.0), "#000000")
        self.assertEqual(blend("#ffffff", "#000000", 0.5), "#808080")

    def test_opacity_keeps_text_contrast(self) -> None:
        palette = palette_for("light", "green")
        apply_card_opacity(palette, 50)
        self.assertEqual(palette["card_opacity"], 50)
        self.assertNotEqual(palette["card"], "#ffffff")
        self.assertGreater(luminance(palette["card"]), 0.8)
        self.assertLess(luminance(palette["text"]), 0.3)

    def test_opaque_is_a_noop(self) -> None:
        palette = palette_for("light", "green")
        before = palette["card"]
        apply_card_opacity(palette, 100)
        self.assertEqual(palette["card"], before)

    def test_opacity_is_clamped(self) -> None:
        palette = palette_for("dark", "green")
        apply_card_opacity(palette, 5)
        self.assertEqual(palette["card_opacity"], 50)
        apply_card_opacity(palette, "oops")
        self.assertEqual(palette["card_opacity"], 100)


class SettingsTest(unittest.TestCase):
    def test_defaults_are_light_theme(self) -> None:
        settings = Settings()
        self.assertEqual(settings.theme, "light")
        self.assertEqual(settings.accent, "green")
        self.assertFalse(settings.glass)
        self.assertEqual(settings.card_opacity, 100)

    def test_invalid_values_are_normalised(self) -> None:
        settings = Settings(theme="neon", accent="rainbow", glass=1, card_opacity=10)
        self.assertEqual(settings.theme, "light")
        self.assertEqual(settings.accent, "green")
        self.assertTrue(settings.glass)
        self.assertEqual(settings.card_opacity, 50)

    def test_labels_cover_all_options(self) -> None:
        self.assertEqual(set(THEME_LABELS), set(THEME_ORDER))
        self.assertEqual(set(ACCENT_LABELS), set(ACCENT_ORDER))

    def test_describe(self) -> None:
        self.assertEqual(describe("light", "green", False), "明亮 · 森林绿")
        self.assertEqual(describe("dark", "blue", True), "暗黑 · 天空蓝 · 毛玻璃")


if __name__ == "__main__":
    unittest.main()
