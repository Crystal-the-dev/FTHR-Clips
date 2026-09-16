"""FFmpeg filters for the animated export/share Capture Card.

The card slides in, holds, and exits without shortening the video. FFmpeg
primitives provide the card geometry without a separate background bitmap.
"""
from __future__ import annotations

from pathlib import Path

from core.theme_manager import ThemeManager


# Match the compact CaptureCard notification so the export watermark reads as
# the same product surface rather than as a separate logo treatment.
CARD_WIDTH = 320
CARD_HEIGHT = 76
CARD_MARGIN = 24
CARD_EXIT_S = 2.90


def _filter_path(path: Path) -> str:
    """Return a filesystem path escaped for an FFmpeg filter string."""
    # FFmpeg filter paths use forward slashes on every platform and treat the
    # Windows drive colon as a filter-option separator unless it is escaped.
    return str(path).replace('\\', '/').replace(':', r'\:').replace("'", r"\'")


def _font_filter_path() -> str:
    """Return the bundled Oswald path escaped for an FFmpeg filter string."""
    return _filter_path(
        Path(__file__).resolve().parent.parent / 'assets' / 'fonts' / 'Oswald-Bold.ttf'
    )


def _ffmpeg_color(value: str) -> str:
    """Convert a theme hex color to an FFmpeg ``0xRRGGBB`` literal."""
    clean = str(value).strip().lstrip('#')
    if len(clean) == 3:
        clean = ''.join(char * 2 for char in clean)
    if len(clean) != 6:
        clean = '000000'
    return f'0x{clean}'


def _brand_icon_filter_path() -> str:
    """Return the supplied favicon path escaped for FFmpeg's movie filter."""
    return _filter_path(
        Path(__file__).resolve().parent.parent / 'assets' / 'favicon.ico'
    )


def capture_card_watermark_filters(
        base_label: str, output_label: str = 'vout',
        card_label: str = 'fthr_watermark') -> list[str]:
    """Return filter-complex snippets that overlay the animated mini card.

    ``base_label`` and ``output_label`` are label names without square
    brackets.  The returned snippets can be appended to an existing filter
    graph; timestamps on the base stream must begin at zero.
    """
    font = _font_filter_path()
    colors = ThemeManager().get_all_capture_card_colors()
    card_bg = _ffmpeg_color(colors['CAPTURE_CARD_BG'])
    card_accent = _ffmpeg_color(colors['CAPTURE_CARD_ACCENT'])
    card_text = _ffmpeg_color(colors['CAPTURE_CARD_TEXT'])
    card_divider = _ffmpeg_color(colors['CAPTURE_CARD_DIVIDER'])
    card_stats_dim = _ffmpeg_color(colors['CAPTURE_CARD_STATS_DIM'])
    card = (
        f'color=c={card_bg}@0.92:s={CARD_WIDTH}x{CARD_HEIGHT}:r=60,'
        'format=rgba,colorchannelmixer=aa=0.92,'
        # Match the notification card: left rail, brand mark, divider, and two text lines.
        f'drawbox=x=0:y=0:w=3:h=ih:color={card_accent}@1:t=fill,'
        f'drawbox=x=76:y=12:w=1:h=52:color={card_divider}@1:t=fill,'
        f"drawtext=fontfile='{font}':text='CAPTURED WITH':"
        f'fontcolor={card_stats_dim}:fontsize=10:x=94:y=10,'
        f"drawtext=fontfile='{font}':text='FTHRClips':"
        f'fontcolor={card_text}:fontsize=22:x=94:y=29'
        f'[{card_label}]'
    )

    icon_label = f'{card_label}_icon'
    composed_label = f'{card_label}_composed'
    brand_icon = (
        f"movie=filename='{_brand_icon_filter_path()}':loop=1,"
        'format=rgba,scale=48:48:flags=neighbor,'
        'pad=48:48:(ow-iw)/2:(oh-ih)/2:color=black@0'
        f'[{icon_label}]'
    )
    card_with_icon = (
        f'[{card_label}][{icon_label}]overlay='
        "x=14:y=14:format=auto:eof_action=repeat"
        f'[{composed_label}]'
    )

    # Quote the animation expression so its commas stay inside overlay's x option.
    x_expr = (
        f"if(lt(t,0.38),W-(w+{CARD_MARGIN})*"
        "(1-pow(1-t/0.38,3)),"
        f"if(lt(t,2.55),W-w-{CARD_MARGIN},"
        f"W-w-{CARD_MARGIN}+(w+{CARD_MARGIN})*"
        "pow(min(1,(t-2.55)/0.35),3)))"
    )
    overlay = (
        f'[{base_label}][{composed_label}]overlay='
        f"x='{x_expr}':y='H-h-{CARD_MARGIN}':"
        f"enable='between(t,0,{CARD_EXIT_S})':eof_action=pass"
        f'[{output_label}]'
    )
    return [card, brand_icon, card_with_icon, overlay]
