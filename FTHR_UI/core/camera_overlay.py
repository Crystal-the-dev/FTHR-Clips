"""Shared geometry helpers for the editable camera overlay."""
from __future__ import annotations


DEFAULT_OVERLAY_RECT = {
    'x': 0.72,
    'y': 0.64,
    'w': 0.25,
    'h': 0.34,
}
DEFAULT_IMAGE_OVERLAY_RECT = {
    'x': 0.76,
    'y': 0.04,
    'w': 0.20,
    'h': 0.24,
}
MIN_OVERLAY_SIZE = 0.08

_IMAGE_LAYER_POSITIONS = (
    (0.76, 0.04),
    (0.04, 0.04),
    (0.04, 0.72),
    (0.76, 0.72),
    (0.40, 0.38),
)


def clamp_overlay_rect(rect, default=None) -> dict[str, float]:
    """Return a safe normalized x/y/w/h rectangle inside the clip frame."""
    default = default if isinstance(default, dict) else DEFAULT_OVERLAY_RECT
    rect = rect if isinstance(rect, dict) else {}
    try:
        width = float(rect.get('w', default['w']))
    except (TypeError, ValueError):
        width = default['w']
    try:
        height = float(rect.get('h', default['h']))
    except (TypeError, ValueError):
        height = default['h']
    try:
        x = float(rect.get('x', default['x']))
    except (TypeError, ValueError):
        x = default['x']
    try:
        y = float(rect.get('y', default['y']))
    except (TypeError, ValueError):
        y = default['y']

    width = max(MIN_OVERLAY_SIZE, min(width, 0.92))
    height = max(MIN_OVERLAY_SIZE, min(height, 0.92))
    x = max(0.0, min(x, 1.0 - width))
    y = max(0.0, min(y, 1.0 - height))
    return {
        'x': round(x, 4),
        'y': round(y, 4),
        'w': round(width, 4),
        'h': round(height, 4),
    }


def legacy_overlay_rect(position='bottom-right', size='medium') -> dict[str, float]:
    """Translate the former corner/size settings into the new freeform model."""
    width = {'small': 0.20, 'medium': 0.25, 'large': 0.33}.get(size, 0.25)
    # A common webcam frame is 4:3. In a 16:9 clip canvas that means the
    # normalized height is wider than the normalized width.
    height = width * 1.3333
    margin = 0.02
    x = margin if 'left' in str(position) else 1.0 - width - margin
    y = margin if str(position).startswith('top') else 1.0 - height - margin
    return clamp_overlay_rect({'x': x, 'y': y, 'w': width, 'h': height})


def new_image_overlay_layer(path: str, index: int = 0) -> dict:
    """Create a normalized image layer at a visible, non-overlapping start."""
    x, y = _IMAGE_LAYER_POSITIONS[int(index) % len(_IMAGE_LAYER_POSITIONS)]
    rect = clamp_overlay_rect(
        {'x': x, 'y': y, 'w': 0.20, 'h': 0.24},
        DEFAULT_IMAGE_OVERLAY_RECT)
    return {
        'path': str(path or ''),
        'enabled': True,
        'opacity': 100,
        'fit': 'fit',
        'rect': rect,
    }


def image_overlay_layers(settings) -> list[dict]:
    """Return safe image-layer data with a legacy single-image fallback."""
    getter = settings.get
    raw = getter('image_overlays', None)
    if raw is None:
        legacy_path = str(getter('image_overlay_path', '') or '')
        if not legacy_path:
            return []
        raw = [{
            'path': legacy_path,
            'enabled': bool(getter('image_overlay_enabled', False)),
            'opacity': getter('image_overlay_opacity', 100),
            'fit': getter('image_overlay_fit', 'fit'),
            'rect': getter('image_overlay_rect', DEFAULT_IMAGE_OVERLAY_RECT),
        }]
    if not isinstance(raw, list):
        return []

    global_enabled = bool(getter('image_overlay_enabled', bool(raw)))
    layers = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        path = str(item.get('path', '') or '')
        if not path:
            continue
        try:
            opacity = int(item.get('opacity', 100))
        except (TypeError, ValueError):
            opacity = 100
        layers.append({
            'path': path,
            'enabled': global_enabled and bool(item.get('enabled', True)),
            'opacity': max(10, min(100, opacity)),
            'fit': 'fill' if item.get('fit') == 'fill' else 'fit',
            'rect': clamp_overlay_rect(
                item.get('rect'), new_image_overlay_layer('', index)['rect']),
        })
    return layers
