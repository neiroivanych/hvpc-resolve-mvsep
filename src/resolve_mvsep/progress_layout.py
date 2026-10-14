"""Stage panel and existing progress actions at normal and compact window sizes."""
MIN_PROGRESS = (704, 834)
MIN_SOURCE_HEIGHT = 48
MIN_CARD_HEIGHT = 174
DEFAULT_CARD_HEIGHT = 246


def card_height(height: int, preferred: int) -> int:
    """Reserve readable details and the entire footer, even after a large shrink."""
    return max(MIN_CARD_HEIGHT, min(preferred, height - 626))


def progress_layout(width: int, height: int, preferred_card: int = DEFAULT_CARD_HEIGHT,
                    source_height: int = MIN_SOURCE_HEIGHT) -> dict:
    wide = width - 44
    source_height = max(MIN_SOURCE_HEIGHT, source_height)
    header_extra = source_height - 24
    top_height = card_height(height - header_extra, preferred_card)
    shift = top_height - DEFAULT_CARD_HEIGHT + header_extra
    boxes = {
        'stage': (22, 20, wide, 30), 'source': (22, 58, wide - 268, source_height),
        'quota': (width - 266, 58, 244, MIN_SOURCE_HEIGHT),
        'hero_caption': (36, 209, width - 72, 19), 'hero_value': (36, 231, width - 72, 46),
        'hero_secondary': (36, 280, width - 72, 24), 'hero_note': (36, 306, width - 72, 26),
        'bar': (22, 352, wide, 10), 'activity': (22, 375, wide, 24),
        'elapsed': (22, 401, wide, 24), 'remaining': (30, 433, width - 60, 34),
        'status_card': (18, 428, width - 36, 41),
        'connection_card': (18, height - 278, width - 36, 78),
        'connection_title': (28, height - 271, wide - 170, 24),
        'connection_retry': (width - 177, height - 271, 145, 26),
        'hint': (28, height - 242, wide - 12, 40),
        'import_pool': (22, height - 192, (wide - 12) // 2, 34),
        'import_timeline': (28 + wide // 2, height - 192, (wide - 12) // 2, 34),
        'resume': (22, height - 192, wide, 34),
        'profile_label': (22, height - 96, wide, 22),
        'background_hint': (22, height - 96, wide, 64),
        'profile_name': (22, height - 71, width - 230, 28),
        'save_profile': (width - 190, height - 71, 168, 29),
        'profile_notice': (22, height - 41, wide, 40),
    }
    boxes['card'] = (18, 88 + header_extra, width - 36, top_height)
    flow_height = max(0, 60 + top_height - DEFAULT_CARD_HEIGHT)
    boxes['flow'] = (22, 98 + header_extra, wide, flow_height if flow_height >= 32 else 0)
    for name in ('hero_caption', 'hero_value', 'hero_secondary', 'hero_note',
                 'bar', 'activity', 'elapsed', 'remaining', 'status_card'):
        x, y, w, h = boxes[name]
        boxes[name] = (x, y + shift, w, h)
    boxes['splitter'] = (18, 474 + shift, width - 36, 14)
    boxes['detail_card'] = (18, 488 + shift, width - 36, height - 788 - shift)
    boxes['detail'] = (30, 495 + shift, width - 60, height - 802 - shift)
    boxes['detail_bottom'] = (18, height - 300, width - 36, 16)
    for index in range(5):
        left = 36 + round((width - 72) * index / 5)
        right = 36 + round((width - 72) * (index + 1) / 5) - 6
        boxes[f'step_{index}'] = (left, 180 + shift, right - left, 23)
        boxes[f'line_{index}'] = (left, 169 + shift, right - left, 3)
    for index, name in enumerate(('result', 'settings', 'logs', 'close')):
        left = 22 + round((wide + 12) * index / 4)
        right = 22 + round((wide + 12) * (index + 1) / 4) - 12
        boxes[name] = (left, height - 137, right - left, 32)
    return boxes
