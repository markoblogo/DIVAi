"""Original 11×11 character art for the Divoom TimeBox Mini.

Coordinates are seen from the camera. A left glance points to the left of
the filmed image. Each character's accent colours its whole palette.
"""

FACES = {
    "director": "Режиссёр",
    "crt": "Кинескоп",
    "observer": "Дежурный",
}
DEFAULT_FACE = "observer"

ACCENTS = {
    "director": (192, 213, 241),
    "crt": (57, 232, 164),
    "observer": (255, 153, 43),
}

SIZE = 11


def _stamp(grid, x, y, *rows):
    """Draw a tiny sprite. Dots leave the layer below untouched."""
    for dy, row in enumerate(rows):
        for dx, ink in enumerate(row):
            if ink != ".":
                grid[y + dy][x + dx] = ink


def _director(grid, pose, phase):
    # An unframed face with one raised eyebrow and a crooked half-smile.
    _stamp(grid, 7, 1, "aaa")
    _stamp(grid, 1, 2, "a..", ".aa")
    for x in (1, 7):
        if pose == "blink":
            _stamp(grid, x, 5, "aaa")
        else:
            _stamp(grid, x, 4, "hhh", "hhh", "aaa")
            pupil_x = x if pose == "look" else x + 1
            _stamp(grid, pupil_x, 5, "0", "0")
    _stamp(grid, 5, 6, "s")
    if pose == "talk":
        if phase == 0:
            _stamp(grid, 3, 8, "aaaa", ".aa.")
        else:
            _stamp(grid, 3, 8, "ahha", "a00a", ".aa.")
    else:
        _stamp(grid, 3, 8, "....a", "aaaa.")


def _crt(grid, pose, phase):
    # Rounded phosphor screen on a stand, with a two-pixel smile inside.
    _stamp(grid, 0, 0,
           "..sssssss..",
           ".s.......s.",
           "s.........s",
           "s.........s",
           "s.........s",
           "s.........s",
           "s.........s",
           ".s.......s.",
           "..sssssss..",
           "....ss.....",
           "..ssssss...")
    for x in (2, 7):
        if pose == "blink":
            _stamp(grid, x, 4, "aa")
        else:
            eye_x = x - 1 if pose == "look" else x
            _stamp(grid, eye_x, 2, "ah", "aa")
    if pose == "talk" and phase == 1:
        _stamp(grid, 3, 5, "ahhha", ".a0a.", "..a..")
    elif pose == "talk":
        _stamp(grid, 4, 5, "aha", ".a.")
    else:
        _stamp(grid, 3, 5, "a...a", ".aaa.")


def _observer(grid, pose, phase):
    # A single camera lens above a speaker grille. The iris is the eye.
    if pose == "blink":
        _stamp(grid, 1, 3, "..sssss..", "aaahhhaaa", "..sssss..")
    else:
        _stamp(grid, 0, 1,
               "...aaaaa...",
               "..asdddsa..",
               ".asdddddsa.",
               "asdddddddsa",
               ".asdddddsa.",
               "..asdddsa..",
               "...aaaaa...")
        iris_x = 2 if pose == "look" else 4
        _stamp(grid, iris_x, 3, ".h.", "hah", ".h.")
    if pose == "talk" and phase == 1:
        _stamp(grid, 2, 8, ".a.h.a.", "ahahaha", ".a.h.a.")
    elif pose == "talk":
        _stamp(grid, 3, 9, "a.h.a", "..a..")
    else:
        _stamp(grid, 3, 9, "s.s.s")


_DRAW = {"director": _director, "crt": _crt, "observer": _observer}


def render(face, pose, phase=0, side="left", accent=None):
    """Return 121 row-major RGB tuples for look, camera, talk or blink.

    The camera, talking and blinking poses face the audience. The look pose
    glances toward the laptop, on the specified side of the picture. For
    talking, alternate phase 0 (small opening) and 1 (open mouth). Set
    accent to an RGB tuple to recolour this drawing without changing others.
    """
    if face not in FACES:
        raise ValueError(f"Unknown face: {face!r}")
    if pose not in ("look", "camera", "talk", "blink"):
        raise ValueError(f"Unknown pose: {pose!r}")
    if side not in ("left", "right") or phase not in (0, 1):
        raise ValueError("side must be left/right and phase must be 0/1")
    rgb = ACCENTS[face] if accent is None else accent
    if len(rgb) != 3 or any(type(c) is not int or not 0 <= c <= 255 for c in rgb):
        raise ValueError("accent must contain three integer values in 0..255")

    palette = {
        "0": (0, 0, 0),
        "d": tuple(round(c * 0.18) for c in rgb),
        "s": tuple(round(c * 0.52) for c in rgb),
        "a": tuple(rgb),
        "h": tuple(round(c + (255 - c) * 0.43) for c in rgb),
    }
    grid = [["0"] * SIZE for _ in range(SIZE)]
    _DRAW[face](grid, pose, phase)
    if pose == "look" and side == "right":
        grid = [row[::-1] for row in grid]
    return [palette[ink] for row in grid for ink in row]


# Two rows of compact, three-pixel digits fit HH above MM on the 11×11 display.
_DIGITS = (
    ("111", "101", "101", "101", "111"),
    ("010", "110", "010", "010", "111"),
    ("111", "001", "111", "100", "111"),
    ("111", "001", "111", "001", "111"),
    ("101", "101", "111", "001", "001"),
    ("111", "100", "111", "001", "111"),
    ("111", "100", "111", "101", "111"),
    ("111", "001", "010", "010", "010"),
    ("111", "101", "111", "101", "111"),
    ("111", "101", "111", "001", "111"),
)


def clock_pixels(hour, minute, accent=None):
    """Display the local hour above the minute in the Observer's palette."""
    pixels = [(0, 0, 0)] * (SIZE * SIZE)
    bright = accent or ACCENTS["observer"]
    dim = tuple(round(channel * 0.70) for channel in bright)
    for value, top, color in ((hour, 0, bright), (minute, 6, dim)):
        for digit, left in zip(divmod(value, 10), (2, 6)):
            for dy, row in enumerate(_DIGITS[digit]):
                for dx, cell in enumerate(row):
                    if cell == "1":
                        pixels[(top + dy) * SIZE + left + dx] = color
    return pixels
