#!/usr/bin/env python3
"""One-cue filming player for the TimeBox Mini and a local HTML5 video."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.dont_write_bytecode = True
from faces import render
from skin import Skin
from studio import load_skins, preference_file, prepare_take, read_preferences

BASE = Path(__file__).resolve().parent


def director_skin(selected):
    if selected:
        skin = Skin.load(selected)
        if skin.face != "director":
            raise ValueError("Для съёмки выбери файл персонажа Режиссёр.")
        return skin
    return load_skins().get("director")


class Shoot:
    def __init__(self, device, skin=None, side=None, voice=None):
        self.device = device
        self.voice = voice
        self.frames = {}
        saved_side, saved_accent = read_preferences(preference_file())["director"]
        side = side or (skin.side if skin else saved_side)
        for key, pose, phase in (("look", "look", 0), ("camera", "camera", 0),
                                 ("talk0", "talk", 0), ("talk1", "talk", 1)):
            self.frames[key] = (skin.render(pose, phase, side) if skin
                                else render("director", pose, phase, side, saved_accent))
        self.audio = self.path = self.take = self.frame = None
        self.phase = "idle"
        self.error = ""
        self.duration = 0
        self.since = 0
        self.display("look")

    def display(self, frame):
        if frame != self.frame:
            if self.device:
                self.device.show(self.frames[frame])
            self.frame = frame

    def stop_audio(self):
        if self.audio and self.audio.poll() is None:
            self.audio.terminate()
            try:
                self.audio.wait(timeout=0.4)
            except subprocess.TimeoutExpired:
                self.audio.kill()
                self.audio.wait()
        self.audio = None

    def stop(self):
        self.stop_audio()
        self.phase = "idle"
        self.take = self.path = None
        self.error = ""
        self.display("look")

    def command(self, action, data):
        take = data.get("id")
        if not isinstance(take, str) or not re.fullmatch(r"[a-f0-9]{24}", take):
            raise ValueError("Некорректный номер дубля.")
        if action == "prepare":
            line = data.get("line")
            if not isinstance(line, str) or not (1 <= len(line.strip()) <= 600):
                raise ValueError("Введи реплику длиной от 1 до 600 знаков.")
            self.stop()
            self.path = prepare_take(line.strip(), self.voice) if self.device else None
            self.duration = max(0.9, len(line.split()) / 2.6)
            self.take, self.phase = take, "armed"
        elif take == self.take:
            if action == "fire" and self.phase == "armed":
                self.display("camera")
                self.phase, self.since = "camera", time.monotonic()
            elif action == "stop":
                self.stop()
        return self.status()

    def status(self):
        return dict(id=self.take, phase=self.phase, frame=self.frame, error=self.error)

    def tick(self):
        now = time.monotonic()
        try:
            if self.phase == "camera" and now - self.since >= 0.45:
                if self.device:
                    self.audio = subprocess.Popen(["afplay", str(self.path)], stdin=subprocess.DEVNULL)
                self.since, self.phase = now, "talking"
            if self.phase == "talking":
                elapsed = now - self.since
                status = self.audio.poll() if self.audio else None
                if (self.audio and status is not None) or (not self.audio and elapsed >= self.duration):
                    if status:
                        raise RuntimeError("Не удалось воспроизвести реплику. Проверь аудиовыход Mac.")
                    self.audio = None
                    self.phase = "done"
                    self.display("look")
                else:
                    self.display("talk" + str(int(elapsed / 0.16) % 2))
        except (OSError, RuntimeError) as error:
            self.stop_audio()
            self.phase, self.error = "error", str(error)


def byte_range(header, size):
    if not header:
        return 0, size - 1
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()):
        raise ValueError
    start, end = match.groups()
    if start:
        a, b = int(start), min(size-1, int(end)) if end else size-1
    else:
        a, b = max(0, size-int(end)), size-1
    if a > b or a >= size:
        raise ValueError
    return a, b


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, body, mime, status=200, headers=None, head=False):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def json_reply(self, obj, status=200):
        self.reply(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def serve(self, head=False):
        app = self.server
        if self.headers.get("Host") != app.authority:
            self.send_error(403)
        elif self.path == "/":
            self.reply(app.page, "text/html; charset=utf-8", head=head)
        elif self.path == "/state":
            with app.controls:
                state = app.rig.status()
            self.json_reply(state)
        elif self.path == "/demo/take.mp4" and app.demo:
            try:
                a, b = byte_range(self.headers.get("Range"), len(app.demo))
            except ValueError:
                self.reply(b"", "video/mp4", 416, {"Content-Range": f"bytes */{len(app.demo)}"}, head)
                return
            headers = {"Accept-Ranges": "bytes"}
            partial = bool(self.headers.get("Range"))
            if partial:
                headers["Content-Range"] = f"bytes {a}-{b}/{len(app.demo)}"
            self.reply(app.demo[a:b+1], "video/mp4", 206 if partial else 200, headers, head)
        else:
            self.send_error(404)

    def do_GET(self):
        self.serve()

    def do_HEAD(self):
        self.serve(True)

    def do_POST(self):
        app = self.server
        if self.headers.get("Host") != app.authority or self.headers.get("X-Divoom") != app.token:
            self.send_error(403)
            return
        if self.path not in ("/prepare", "/fire", "/stop"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192:
                raise ValueError("Слишком большой запрос.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Некорректный запрос.")
            with app.controls:
                result = app.rig.command(self.path[1:], data)
            self.json_reply(result)
        except (ValueError, TypeError) as error:
            self.json_reply({"error": str(error)}, 400)
        except (OSError, RuntimeError) as error:
            self.json_reply({"error": str(error)}, 500)


class PlayerServer(ThreadingHTTPServer):
    # Browser preconnections may wait for requests independently of the clock.
    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(4)
        return conn, address


def main():
    parser = argparse.ArgumentParser(description="Режиссёр реагирует на таймкод локального видео.")
    parser.add_argument("--preview", action="store_true", help="экранная репетиция без колонки и голоса")
    parser.add_argument("--no-browser", action="store_true", help="только напечатать адрес пульта")
    parser.add_argument("--skin", type=Path)
    parser.add_argument("--side", choices=["left", "right"])
    parser.add_argument("--voice", help="имя системного голоса macOS")
    args = parser.parse_args()
    if not args.preview and sys.platform != "darwin":
        parser.error("Для репетиции на этом компьютере добавь --preview.")
    skin = director_skin(args.skin)
    os.environ.setdefault("DIVOOM_STUDIO_CACHE", str(BASE / ".cache"))
    device = rig = None
    try:
        if not args.preview:
            from device import Device
            device = Device()
        rig = Shoot(device, skin, args.side, args.voice)
        with PlayerServer(("127.0.0.1", 0), Handler) as server:
            server.authority = f"127.0.0.1:{server.server_port}"
            server.token = secrets.token_hex(24)
            server.rig = rig
            server.controls = threading.Lock()
            demo = BASE / "demo/take.mp4"
            server.demo = demo.read_bytes() if demo.is_file() else None
            settings = dict(token=server.token, frames=rig.frames, preview=args.preview, demo=bool(server.demo))
            server.page = (BASE / "shoot.html").read_text(encoding="utf8").replace(
                "__DIVOOM_CONFIG__", json.dumps(settings)).encode()
            server.timeout = 0.05
            url = "http://" + server.authority + "/"
            print(f"Пульт съёмки: {url}\nДля выхода нажми Ctrl+C в Терминале.", flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            while True:
                with server.controls:
                    rig.tick()
                server.handle_request()
    finally:
        try:
            if rig:
                rig.stop_audio()
        finally:
            if device:
                device.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, OSError) as error:
        sys.exit(str(error))
