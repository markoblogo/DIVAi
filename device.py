"""Native macOS display output for the Divoom TimeBox Mini (11 x 11)."""

import hashlib
import os
from pathlib import Path
import plistlib
import select
import subprocess
import sys
import tempfile
import time


_SOURCE = Path(__file__).resolve().parent / "vendor" / "timebox_bridge.swift"
_INFO = {
    "CFBundleIdentifier": "local.divoomstudio.timeboxmini",
    "CFBundleExecutable": "timebox-bridge",
    "CFBundleName": "Divoom Studio Bridge",
    "CFBundlePackageType": "APPL",
    "CFBundleVersion": "1",
    "LSUIElement": True,
    "NSBluetoothAlwaysUsageDescription": "Display your 11 by 11 frames on your paired TimeBox Mini.",
}


def encode_frame(pixels):
    """Encode row-major RGB8 triples using the Mini's RGB444 protocol (see vendor/UPSTREAM.txt)."""
    if len(pixels) != 121:
        raise ValueError("TimeBox Mini requires exactly 121 RGB pixels.")
    nibbles = []
    for pixel in pixels:
        if len(pixel) != 3:
            raise ValueError("Each pixel must contain three RGB components.")
        for channel in pixel:
            if not isinstance(channel, int) or not 0 <= channel <= 255:
                raise ValueError("Each RGB component must be an integer from 0 to 255.")
            nibbles.append(channel >> 4)
    nibbles.append(0)
    packed = bytes(nibbles[i] | (nibbles[i + 1] << 4) for i in range(0, len(nibbles), 2))
    payload = bytes.fromhex("bd 00 44 00 0a 0a 04") + packed
    payload += (sum(payload) & 0xffff).to_bytes(2, "little")
    encoded = bytearray([1])
    for value in payload:
        if value in (1, 2, 3):
            encoded.extend((3, value + 3))
        else:
            encoded.append(value)
    encoded.append(2)
    return bytes(encoded)


def _binary():
    """Build the ad-hoc-signed helper once, then reuse it on this Mac."""
    import fcntl
    import platform

    info = plistlib.dumps(_INFO)
    key = hashlib.sha256(_SOURCE.read_bytes() + info + platform.machine().encode()).hexdigest()[:16]
    cache = Path(os.environ.get("DIVOOM_STUDIO_CACHE") or Path.home() / "Library/Caches/DivoomStudio")
    folder = cache / key
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "Divoom Studio Bridge.app"
    executable = Path("Contents/MacOS/timebox-bridge")
    with (folder / "build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (target / executable).is_file():
            return target / executable
        with tempfile.TemporaryDirectory(dir=folder) as tmp:
            app = Path(tmp) / target.name
            (app / executable).parent.mkdir(parents=True)
            (app / "Contents/Info.plist").write_bytes(info)
            try:
                subprocess.run(
                    ["/usr/bin/xcrun", "swiftc", "-swift-version", "5", "-O", str(_SOURCE),
                     "-o", str(app / executable)], check=True, capture_output=True, text=True
                )
                subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)],
                               check=True, capture_output=True, text=True)
            except (OSError, subprocess.CalledProcessError) as exc:
                detail = getattr(exc, "stderr", None) or str(exc)
                raise RuntimeError("Could not build the native Bluetooth helper. Check Xcode Command "
                                   "Line Tools (xcode-select --install).\n" + detail.strip()) from exc
            app.rename(target)
    return target / executable


class Device:
    """One connected display. show() returns after RFCOMM writes complete, not after physical scanout."""

    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("The physical TimeBox Mini output requires macOS. Use the preview on this computer.")
        self._process = subprocess.Popen(
            [str(_binary())], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0
        )
        self._pending = bytearray()
        try:
            self._expect(b"READY", 45)
        except BaseException:
            self.close()
            raise

    def _expect(self, expected, timeout):
        deadline = time.monotonic() + timeout
        output = self._process.stdout
        while b"\n" not in self._pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([output], [], [], remaining)[0]:
                raise RuntimeError("TimeBox Mini did not respond. Check power, macOS Bluetooth "
                                   "permission and whether another app is using the display, then retry.")
            chunk = os.read(output.fileno(), 4096)
            if not chunk:
                raise RuntimeError("The Bluetooth helper exited before confirming the connection or frame.")
            self._pending.extend(chunk)
        line, _, tail = self._pending.partition(b"\n")
        self._pending = bytearray(tail)
        if line != expected:
            message = line.decode("utf-8", errors="replace")
            raise RuntimeError(message[6:] if message.startswith("ERROR ") else message)

    def show(self, pixels):
        frame = encode_frame(pixels)
        if self._process is None:
            raise RuntimeError("The TimeBox Mini display is closed.")
        try:
            self._process.stdin.write(frame.hex().encode("ascii") + b"\n")
            self._expect(b"OK", 10)
        except OSError as exc:
            self.close()
            raise RuntimeError("The Bluetooth helper stopped during a frame write.") from exc
        except RuntimeError:
            self.close()
            raise

    def close(self):
        proc, self._process = self._process, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        finally:
            proc.stdout.close()
