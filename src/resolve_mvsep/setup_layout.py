"""Pixel layout of the native chooser; shared by rendering and offline checks."""

MIN_CLIENT = (1080, 872)


def column_width(width: int, ratio: float = .42) -> int:
    available = width - 56
    return min(max(340, round(available * ratio)), max(340, available - 462))


def layout(width: int, height: int, ratio: float = .42, field_count: int = 4, premium: bool = False,
           *, source_height: int = 24, context_height: int = 22) -> dict[str, tuple[int, int, int, int]]:
    # Windows scales the whole native window on displays using desktop DPI scaling.
    source_height, context_height = max(24, source_height), max(22, context_height)
    header_extra = source_height - 24 + context_height - 22
    height -= header_extra
    margin, gap, inset = 20, 16, 16
    left = column_width(width, ratio)
    right_x = margin + left + gap
    right = width - margin - right_x
    bottom = height - 290
    x, rw = right_x + inset, right - 2 * inset
    detail_height = max(64, bottom - (318 + max(1, field_count) * 50))
    params_y = 274 + detail_height
    footer = height - 70
    boxes = {
        "card_profiles": (20, 110, width - 40, 66),
        "card_catalog": (20, 192, left, bottom - 192),
        "card_settings": (right_x, 192, right, bottom - 192),
        "card_output": (20, bottom + 16, width - 40, 196),
        "splitter": (20 + left, 192, gap, bottom - 192),
        "stage": (22, 18, width - 750, 30),
        "balance": (width - 710, 21, 292, 25),
        "balance_refresh": (width - 409, 17, 150, 29),
        "quota": (width - 710, 55, 451, 24),
        "source": (22, 55, width - 756, source_height),
        "context": (22, 58 + source_height, width - 50, context_height),
        "language_label": (width - 241, 19, 220, 22),
        "language": (width - 241, 46, 220, 180),
        "profiles_label": (36, 116, 260, 22),
        "remember_settings": (width - 596, 114, 350, 24),
        "remember_mode": (width - 226, 112, 190, 120),
        "profiles": (36, 143, width - 440, 220),
        "last_result": (width - 386, 141, 180, 29),
        "import": (width - 196, 141, 160, 29),
        "catalog_heading": (36, 204, left - 32, 24),
        "group_label": (36, 238, left - 32, 22),
        "group": (36, 262, left - 32, 350),
        "search_label": (36, 298, left - 32, 22),
        "search": (36, 322, left - 32, 28),
        "algorithms": (36, 362, left - 32, bottom - 434),
        "found": (36, bottom - 66, left - 32, 22),
        "reload": (36, bottom - 37, 174, 27),
        "catalog_date": (222, bottom - 33, left - 218, 22),
        "algorithm": (x, 207, rw, 46),
        "premium_badge": (x, 253, rw, 26 if premium else 0),
        "detail": (x, 286 if premium else 260, rw, detail_height - (26 if premium else 0)),
        "parameters_label": (x, params_y, rw - 183, 24),
        "reset": (x + rw - 175, params_y - 2, 175, 27),
        "format_label": (36, bottom + 31, left - 32, 23),
        "format": (36, bottom + 61, left - 32, 220),
        "name_label": (x, bottom + 31, rw, 23),
        "name": (x, bottom + 61, rw - 164, 28),
        "save": (x + rw - 154, bottom + 60, 154, 30),
        "folder_label": (36, bottom + 99, 235, 23),
        "folder_remember": (width - 392, bottom + 96, 356, 27),
        "folder_path": (36, bottom + 128, width - 436, 28),
        "folder_choose": (width - 386, bottom + 127, 182, 30),
        "folder_default": (width - 190, bottom + 127, 154, 30),
        "after_label": (36, bottom + 169, 190, 22),
        "auto_pool": (234, bottom + 165, 328, 27),
        "auto_timeline": (570, bottom + 165, width - 606, 27),
        "notice": (22, footer, width - 410, 65),
        "cancel": (width - 360, footer + 11, 122, 38),
        "start": (width - 224, footer + 11, 204, 38),
    }
    for row in range(4):
        y = params_y + 29 + row * 50
        boxes[f"field_{row}_label"] = (x, y, rw - 98, 22)
        boxes[f"field_{row}_help"] = (x + rw - 88, y - 1, 88, 22)
        boxes[f"field_{row}"] = (x, y + 21, rw, 220)
    header = {'stage', 'source', 'context', 'balance', 'balance_refresh', 'quota', 'language', 'language_label'}
    for name, (x, y, w, h) in boxes.items():
        if name not in header:
            boxes[name] = (x, y + header_extra, w, h)
    return boxes
