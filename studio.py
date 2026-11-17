#!/usr/bin/env python3
"""Live controls and terminal rehearsal for the TimeBox Mini studio."""
import argparse
from contextlib import ExitStack, contextmanager
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import tty

sys.dont_write_bytecode = True

from faces import DEFAULT_FACE, FACES, clock_pixels, render


PROJECT = Path(__file__).resolve().parent
DEFAULT_LINE = "Good thing I don't have wheels."
ACCENTS = (None, (255, 172, 65), (74, 202, 225), (206, 129, 255))
MOUTH_INTERVAL = 0.16
IDLE_BLINK_INTERVAL = 6.0
ROLE_FACES = {"daily": DEFAULT_FACE, "shoot": "director"}


def parse_accent(value):
    if value.lower() == "default":
        return None
    if not re.fullmatch(r"#?[0-9a-fA-F]{6}", value):
        raise ValueError("цвет должен быть в виде '#RRGGBB' или default")
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def accent_argument(value):
    try:
        return parse_accent(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def accent_label(accent):
    if accent is None:
        return "default"
    return "#" + "".join(f"{channel:02X}" for channel in accent)


def preference_file():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "xyz.abvx.divoom-studio.json"
    directory = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return directory / "divoom-studio" / "preferences.json"


def read_preferences(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    # Version one stored only the last face. Keep its direction and tint for that face.
    roles = data.get("faces")
    if not isinstance(roles, dict):
        old_face = data.get("face")
        roles = {old_face: data} if isinstance(old_face, str) and old_face in FACES else {}
    settings = {}
    for face in FACES:
        role = roles.get(face)
        if not isinstance(role, dict):
            role = {}
        side = role.get("side")
        try:
            accent = parse_accent(str(role.get("accent", "default")))
        except ValueError:
            accent = None
        settings[face] = (side if side in ("left", "right") else "left", accent)
    return settings


def save_preferences(path, face, side, accent):
    settings = read_preferences(path)
    settings[face] = (side, accent)
    data = {"faces": {name: {"side": direction, "accent": accent_label(tint)}
                      for name, (direction, tint) in settings.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
            name = Path(handle.name)
            json.dump(data, handle)
            handle.write("\n")
        name.replace(path)
    finally:
        if name:
            name.unlink(missing_ok=True)


@lru_cache(maxsize=1)
def russian_voice():
    """Pick from voices reported by macOS with a Russian locale."""
    try:
        result = subprocess.run(["say", "-v", "?"], capture_output=True, text=True,
                                timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for entry in result.stdout.splitlines():
        match = re.match(r"^(.+?)\s+ru(?:[_-][A-Za-z]+)?\s+#", entry, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def pick_voice(line, voice):
    if voice is not None:
        return voice
    letters = [c.lower() for c in line if c.isalpha()]
    russian = sum("а" <= c <= "я" or c == "ё" for c in letters)
    return russian_voice() if russian > len(letters) / 2 else None


def prepare_take(line, voice):
    if not shutil.which("say") or not shutil.which("afplay"):
        raise RuntimeError("Для озвучки нужны системные программы macOS say и afplay.")
    voice = pick_voice(line, voice)
    digest = hashlib.sha256(json.dumps([line, voice], ensure_ascii=False).encode()).hexdigest()[:16]
    directory = PROJECT / "takes"
    directory.mkdir(exist_ok=True)
    path = directory / f"line-{digest}.aiff"
    if path.is_file() and path.stat().st_size > 0:
        return path
    with tempfile.TemporaryDirectory(dir=directory) as tmp_dir:
        temporary_take = Path(tmp_dir) / "line.aiff"
        args = ["say", "-o", str(temporary_take)]
        if voice:
            args.extend(["-v", voice])
        result = subprocess.run(args, input=line, text=True, capture_output=True, check=False)
        if result.returncode or not temporary_take.is_file() or temporary_take.stat().st_size == 0:
            raise RuntimeError(result.stderr.strip() or "Программа say не смогла записать реплику.")
        temporary_take.replace(path)
    return path


@contextmanager
def keyboard():
    fd = sys.stdin.fileno()
    old = None
    if sys.stdin.isatty():
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    try:
        yield fd
    finally:
        if old is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


@contextmanager
def screen():
    interactive = sys.stdout.isatty()
    if interactive:
        print("\x1b[?1049h\x1b[?25l", end="", flush=True)
    try:
        yield interactive
    finally:
        if interactive:
            print("\x1b[0m\x1b[?25h\x1b[?1049l", end="", flush=True)


def show_screen(pixels, labels, interactive):
    if not interactive:
        print(" | ".join(labels[:2]), flush=True)
        return
    size = math.isqrt(len(pixels))
    lines = []
    for y in range(size):
        row = []
        for red, green, blue in pixels[y * size:(y + 1) * size]:
            row.append(f"\x1b[48;2;{red};{green};{blue}m  ")
        lines.append("".join(row) + "\x1b[0m")
    print("\x1b[H" + "\x1b[K\n".join(lines + [""] + labels) + "\x1b[J", end="", flush=True)


def load_skins(explicit=None):
    """A role file takes precedence over character.json from the first version."""
    from skin import Skin
    skins = {}
    for role in ("character", "observer", "director"):
        path = PROJECT / (role + ".json")
        if not path.is_file():
            continue
        try:
            skin = Skin.load(path)
            if role != "character" and skin.face != role:
                raise ValueError("В этом файле сохранён другой персонаж.")
        except (ValueError, OSError) as error:
            print(f"Пропускаю {path.name}: {error}", file=sys.stderr)
            continue
        skins[skin.face] = skin
    if explicit is not None:
        skin = Skin.load(explicit)
        skins[skin.face] = skin
    return skins


class Studio:
    def __init__(self, args, skins, device, take):
        self.skins = skins
        self.device = device
        self.take = take
        self.line = args.line
        self.path = preference_file()
        self.voice = args.voice
        self.settings = read_preferences(self.path)
        for skin in skins.values():
            self.settings[skin.face] = (skin.side, None)
        self.face = args.face or ROLE_FACES[args.mode]
        self.side, self.accent = self.settings[self.face]
        if args.side:
            self.side = args.side
        if hasattr(args, "accent"):
            self.accent = args.accent
        self.gaze = "camera" if self.face == "observer" else "look"
        self.show_clock = False
        self.next_blink = time.monotonic() + IDLE_BLINK_INTERVAL
        self.blink_until = 0.0
        self.started = None
        self.audio = None
        self.preview_length = max(1.0, len(self.line.split()) / 2.6)
        self.note = ("Речь в предпросмотре без звука." if device is None
                     else "T озвучивает реплику через текущий аудиовыход Mac.")
        if args.face or args.side or hasattr(args, "accent"):
            self.remember()

    def remember(self):
        self.settings[self.face] = (self.side, self.accent)
        try:
            save_preferences(self.path, self.face, self.side, self.accent)
        except OSError as error:
            self.note = f"Выбор действует в этой сессии. Не удалось сохранить настройки: {error}"

    def stop_speech(self):
        if self.audio is not None:
            if self.audio.poll() is None:
                self.audio.terminate()
                try:
                    self.audio.wait(timeout=0.4)
                except subprocess.TimeoutExpired:
                    self.audio.kill()
                    self.audio.wait()
            self.audio = None
        self.started = None

    def speak(self):
        self.stop_speech()
        self.blink_until = 0.0
        self.show_clock = False
        if self.device is not None:
            try:
                if self.take is None:
                    self.take = prepare_take(self.line, self.voice)
                self.audio = subprocess.Popen(["afplay", str(self.take)], stdin=subprocess.DEVNULL)
            except (OSError, RuntimeError) as error:
                self.note = f"Не удалось озвучить реплику: {error}"
                return
            self.note = "Файл реплики: " + str(self.take)
        self.started = time.monotonic()

    def pose(self, now):
        if self.started is not None:
            elapsed = now - self.started
            if self.audio is None:
                finished = elapsed >= self.preview_length
            else:
                status = self.audio.poll()
                finished = status is not None
                if finished and status:
                    self.note = f"afplay завершился с кодом {status}. Проверьте аудиовыход Mac."
            if not finished:
                return "talk", int(elapsed / MOUTH_INTERVAL) % 2
            self.stop_speech()
        if self.face == "observer" and not self.show_clock and now >= self.next_blink:
            self.next_blink = now + IDLE_BLINK_INTERVAL
            self.blink_until = now + 0.18
        if now < self.blink_until:
            return "blink", 0
        return self.gaze, 0

    def key(self, key):
        if key in ("d", "3", "s", "1"):
            self.stop_speech()
            self.face = "observer" if key in ("d", "3") else "director"
            self.side, self.accent = self.settings[self.face]
            self.show_clock = False
            self.blink_until = 0.0
            self.next_blink = time.monotonic() + IDLE_BLINK_INTERVAL
            self.gaze = "camera" if self.face == "observer" else "look"
        elif key == "h" and self.face == "observer":
            self.stop_speech()
            self.show_clock = not self.show_clock
            self.next_blink = time.monotonic() + IDLE_BLINK_INTERVAL
        elif key == "m":
            self.side = "right" if self.side == "left" else "left"
            self.remember()
        elif key == "p":
            index = ACCENTS.index(self.accent) if self.accent in ACCENTS else -1
            self.accent = ACCENTS[(index + 1) % len(ACCENTS)]
            self.remember()
        elif key in ("g", "c", "b"):
            self.stop_speech()
            self.show_clock = False
            self.next_blink = time.monotonic() + IDLE_BLINK_INTERVAL
            if key == "b":
                self.blink_until = time.monotonic() + 0.25
            else:
                self.gaze = "look" if key == "g" else "camera"
                self.blink_until = 0.0
        elif key == "t":
            self.speak()

    def run(self):
        with keyboard() as fd, screen() as interactive:
            previous_frame = previous_status = None
            while True:
                now = time.monotonic()
                pose, phase = self.pose(now)
                if self.show_clock:
                    local = time.localtime()
                    frame = clock_pixels(local.tm_hour, local.tm_min, self.accent)
                    pose = time.strftime("%H:%M", local)
                elif self.face in self.skins:
                    frame = self.skins[self.face].render(pose, phase, self.side, self.accent)
                else:
                    frame = render(self.face, pose, phase, self.side, self.accent)
                mode = "LIVE" if self.device is not None else "PREVIEW"
                labels = [f"{mode}   {FACES[self.face]}   {pose}   {self.side}   {accent_label(self.accent)}",
                          self.note,
                          "Реплика: " + " ".join(self.line.split()),
                          "D Дежурный   S Режиссёр   H часы Дежурного",
                          "G взгляд   C в камеру   B моргнуть   T реплика",
                          "M сменить сторону   P цвет   Q выход"]
                if frame != previous_frame and self.device is not None:
                    self.device.show(frame)
                if frame != previous_frame or labels != previous_status:
                    show_screen(frame, labels, interactive)
                    previous_frame, previous_status = frame, labels
                if self.show_clock:
                    timeout = 60.0 - (time.time() % 60.0)
                elif self.started is not None or now < self.blink_until:
                    timeout = 0.05
                elif self.face == "observer":
                    timeout = max(0.0, self.next_blink - now)
                else:
                    timeout = None
                ready, _, _ = select.select([fd], [], [], timeout)
                if ready:
                    value = os.read(fd, 1)
                    if value in (b"", b"q", b"Q", b"\x04"):
                        return
                    self.key(value.decode("ascii", errors="ignore").lower())


def main():
    parser = argparse.ArgumentParser(description="Пульт лица и реплики Divoom TimeBox Mini.")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preview", action="store_true", help="проверить лица и позы в терминале")
    modes.add_argument("--live", action="store_true", help="выводить на колонку и озвучивать реплики (macOS)")
    parser.add_argument("--mode", choices=ROLE_FACES, default="daily",
                        help="daily для Дежурного (по умолчанию), shoot для Режиссёра")
    parser.add_argument("--face", choices=FACES, help=argparse.SUPPRESS)
    parser.add_argument("--side", choices=["left", "right"], help="выбрать и запомнить сторону взгляда")
    parser.add_argument("--accent", type=accent_argument, default=argparse.SUPPRESS,
                        help="выбрать цвет, например '#FFAC41', или default")
    parser.add_argument("--skin", type=Path, help="явно указать файл персонажа, созданный в редакторе")
    parser.add_argument("--line", default=DEFAULT_LINE, help="произносимый текст")
    parser.add_argument("--voice", help="имя системного голоса macOS для say")
    args = parser.parse_args()
    if not args.line.strip():
        parser.error("Реплика не может быть пустой.")
    with ExitStack() as resources:
        skins = load_skins(args.skin)
        take = device = None
        if args.live:
            if sys.platform != "darwin":
                raise RuntimeError("Живой вывод требует macOS. Для репетиции используйте --preview.")
            from device import Device
            device = Device()
            resources.callback(device.close)
        studio = Studio(args, skins, device, take)
        resources.callback(studio.stop_speech)
        studio.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Не удалось запустить или продолжить сессию: {error}", file=sys.stderr)
        sys.exit(1)
