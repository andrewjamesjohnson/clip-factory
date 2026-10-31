"""OBS Studio, driven over obs-websocket v5 (built into OBS 28 and later), for Lab's console takes. Stdlib only.

    Obs.from_file(path)            ~/.config/clip-factory/<account>/obs.json (mode 600): host, port, password, the
                                   capture card's video and audio inputs, the scene, the console
    with ob: ob.version() ...      one connection per step (Record, Mark, Stop, the page's health check)

The client is a small RFC 6455 websocket over `socket`: the HTTP Upgrade with a random Sec-WebSocket-Key and the
Sec-WebSocket-Accept check, masked client frames, continuation frames, ping/pong and close. On it, obs-websocket's
protocol: Hello (op 0) with the sha256 challenge, Identify (op 1) subscribed to Outputs events (64), Identified
(op 2), requests (op 6) and their responses (op 7), events (op 5), and Reidentify (op 3) for the 1.5 s sound check
(InputVolumeMeters, 65536).

What it never does:
  - carry the password in a message, an error or a log line: Identify carries only the derived answer, and every
    failure says "OBS refused the password in obs.json", never the password;
  - connect from a test or an offline run (net.why_not(): "offline", "a test run"): a test talks to its own fake
    server on a local socket, whose port it names in TEST_PORTS;
  - steal the focus: OBS is opened with `open -g -a OBS`.

The requests used (OBS 30.0 at least: SetRecordDirectory came with websocket 5.3; 30.2 or later for Hybrid MP4 and
chapters): GetVersion, GetVideoSettings, GetRecordStatus, GetRecordDirectory, SetRecordDirectory, StartRecord (then the
RecordStateChanged STARTED event), StopRecord (then STOPPED with the file's path), CreateRecordChapter (a failure is
ignored), GetSourceScreenshot (the picture checks), GetStats (dropped frames, free space).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import time
import zlib
from pathlib import Path
from typing import Callable, Dict, List, Optional

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PROTOCOL = "obswebsocket.json"
SUB_OUTPUTS = 1 << 6                 # RecordStateChanged and the other output events
SUB_VOLUME = 1 << 16                 # InputVolumeMeters (high volume: only for the sound check)
CONNECT_S, REQUEST_S, STARTED_S, STOPPED_S = 3.0, 5.0, 5.0, 30.0
DEFAULT_PORT = 4455
TEST_PORTS: set = set()              # the ports of a test's own fake servers: the only ones a test may reach
MIN_OBS = (30, 0)                    # SetRecordDirectory (websocket 5.3)
WANT_OBS = (30, 2)                   # Hybrid MP4 and record chapters
REFUSED_PASSWORD = "OBS refused the password in obs.json (Tools > WebSocket Server Settings; factory.py lab obs-setup)"


class OBSError(Exception):
    """OBS said no, or could not be reached; the words are for a person (never the password)."""


class NotReachable(OBSError):
    pass


class AuthFailed(OBSError):
    pass


def refused_here(host: str, port: int) -> str:
    """'' when this process may talk to OBS at host:port, else why not. A test or an offline run reaches only a fake
    server whose port it put in TEST_PORTS, on this Mac."""
    from . import net
    why = net.why_not()
    if why in ("offline", "a test run"):
        if host in ("127.0.0.1", "localhost", "::1") and int(port) in TEST_PORTS:
            return ""
        return f"not in {why}: OBS is never driven from a test or an offline run"
    return ""


def read_settings(path: Path) -> dict:
    """obs.json: refused unless it is mode 600 (it holds OBS's websocket password). Never says the password."""
    path = Path(path).expanduser()
    try:
        st = path.stat()
    except FileNotFoundError:
        raise OBSError(f"no OBS settings at {path}: run factory.py lab obs-setup <account> in the Mac's own Terminal") \
            from None
    mode = st.st_mode & 0o777
    if mode != 0o600:
        raise OBSError(f"{path} is mode {mode:o}: it holds OBS's password, so it must be 600 (chmod 600 '{path}')")
    try:
        data = json.loads(path.read_text())
    except ValueError:
        raise OBSError(f"{path} is not readable JSON: run factory.py lab obs-setup <account> again") from None
    if not isinstance(data, dict):
        raise OBSError(f"{path} is not an object: run factory.py lab obs-setup <account> again")
    return data


def write_settings(path: Path, data: dict) -> Path:
    """obs.json written mode 600 in a folder of mode 700 (factory.py lab obs-setup)."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data, indent=1) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def open_app() -> str:
    """Open OBS in the background (no focus stealing). Refused in a test or an offline run."""
    from . import net
    if net.why_not() in ("offline", "a test run"):
        return "not opened: a test or an offline run never opens OBS"
    try:
        r = subprocess.run(["open", "-g", "-a", "OBS"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return f"OBS did not open ({type(e).__name__})"
    return "OBS is opening" if r.returncode == 0 else f"OBS did not open: {(r.stderr or r.stdout).strip()[:200]}"


# ------------------------------------------------------------------------------------------------------ websocket
class _Socket:
    """The RFC 6455 client side: the handshake, framing, ping/pong and close."""

    def __init__(self, host: str, port: int, timeout: float = CONNECT_S):
        self.host, self.port = host, int(port)
        try:
            self.sock = socket.create_connection((host, self.port), timeout=timeout)
        except OSError as e:
            raise NotReachable(f"OBS is not open (nothing answers on {host}:{port}: {e.strerror or e})") from None
        self.buf = b""
        self.closed: Optional[tuple] = None
        try:
            self._handshake(timeout)
        except BaseException:
            self.sock.close()
            raise

    def _handshake(self, timeout: float) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET / HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: {PROTOCOL}\r\n\r\n")
        self.sock.settimeout(timeout)
        self.sock.sendall(req.encode())
        end = time.time() + timeout
        while b"\r\n\r\n" not in self.buf:
            if time.time() > end:
                raise NotReachable("OBS did not answer the websocket handshake")
            try:
                chunk = self._recv()
            except socket.timeout:
                raise NotReachable("OBS did not answer the websocket handshake") from None
            if not chunk:
                raise NotReachable("OBS closed the connection during the websocket handshake")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
            raise NotReachable(f"OBS did not switch to a websocket ({lines[0][:80]}): is the websocket server on "
                               f"(Tools > WebSocket Server Settings)?")
        headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:])}
        want = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != want:
            raise NotReachable("the websocket handshake came back with the wrong Sec-WebSocket-Accept")

    def _recv(self) -> bytes:
        try:
            return self.sock.recv(1 << 16)
        except socket.timeout:
            raise
        except OSError:
            return b""

    def _need(self, n: int, deadline: float) -> bytes:
        while len(self.buf) < n:
            left = deadline - time.time()
            if left <= 0:
                raise socket.timeout("timed out")
            self.sock.settimeout(left)
            chunk = self._recv()
            if not chunk:
                raise NotReachable("OBS closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, payload: bytes, opcode: int = 0x1) -> None:
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 1 << 16:
            head += bytes([0x80 | 126]) + struct.pack("!H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(head + mask + body)

    def message(self, timeout: float) -> Optional[str]:
        """The next whole text message (continuation frames joined); answers a ping; None on close."""
        deadline = time.time() + timeout
        parts: List[bytes] = []
        while True:
            b1, b2 = self._need(2, deadline)
            fin, op = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._need(2, deadline))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._need(8, deadline))[0]
            mask = self._need(4, deadline) if b2 & 0x80 else b""
            data = self._need(n, deadline) if n else b""
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x9:                                 # ping: pong with the same payload
                self.send(data, 0xA)
                continue
            if op == 0xA:
                continue
            if op == 0x8:                                 # close: its code, and close back
                code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else 1005
                self.closed = (code, data[2:].decode("utf-8", "replace"))
                try:
                    self.send(data[:2], 0x8)
                except OSError:
                    pass
                return None
            if op in (0x1, 0x2, 0x0):
                parts.append(data)
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            if not self.closed:
                self.send(struct.pack("!H", 1000), 0x8)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------------------------------------------ OBS
class Obs:
    """One connection to obs-websocket v5. Use it in a `with`: it connects on entry and closes on exit."""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, password: str = "",
                 settings: Optional[dict] = None):
        self.host, self.port = host, int(port)
        self._password = password
        self.settings = dict(settings or {})
        self.settings.pop("password", None)
        self.ws: Optional[_Socket] = None
        self.events: List[dict] = []
        self.subs = SUB_OUTPUTS
        self.hello: dict = {}
        self._n = 0

    @classmethod
    def from_file(cls, path: Path) -> "Obs":
        data = read_settings(path)
        return cls(str(data.get("host") or "127.0.0.1"), int(data.get("port") or DEFAULT_PORT),
                   str(data.get("password") or ""), settings=data)

    def __repr__(self) -> str:                    # never the password
        return f"Obs({self.host}:{self.port})"

    def __enter__(self) -> "Obs":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def video_input(self) -> str:
        return str(self.settings.get("video_input") or "Capture card")

    @property
    def audio_input(self) -> str:
        return str(self.settings.get("audio_input") or "Capture card audio")

    def connect(self, events: int = SUB_OUTPUTS) -> "Obs":
        why = refused_here(self.host, self.port)
        if why:
            raise NotReachable(f"OBS not reached: {why}")
        self.ws = _Socket(self.host, self.port)
        try:
            hello = self._op(0, CONNECT_S)
            self.hello = hello
            ident: Dict[str, object] = {"rpcVersion": 1, "eventSubscriptions": events}
            auth = hello.get("authentication")
            if isinstance(auth, dict):
                if not self._password:
                    raise AuthFailed(REFUSED_PASSWORD + ": obs.json has no password and OBS asks for one")
                secret = base64.b64encode(hashlib.sha256((self._password + str(auth.get("salt"))).encode()).digest())
                ident["authentication"] = base64.b64encode(
                    hashlib.sha256(secret + str(auth.get("challenge")).encode()).digest()).decode()
            self._send(1, ident)
            self._op(2, CONNECT_S)
            self.subs = events
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None

    # the protocol ------------------------------------------------------------------------------------------
    def _send(self, op: int, d: dict) -> None:
        if self.ws is None:
            raise NotReachable("not connected to OBS")
        self.ws.send(json.dumps({"op": op, "d": d}).encode())

    def _next(self, deadline: float) -> dict:
        if self.ws is None:
            raise NotReachable("not connected to OBS")
        try:
            text = self.ws.message(max(0.01, deadline - time.time()))
        except socket.timeout:
            raise OBSError("OBS did not answer in time") from None
        if text is None:
            code, reason = self.ws.closed or (1005, "")
            self.close()
            if code == 4009:
                raise AuthFailed(REFUSED_PASSWORD)
            raise NotReachable(f"OBS closed the connection ({code}{': ' + reason if reason else ''})")
        try:
            msg = json.loads(text)
        except ValueError:
            raise OBSError("OBS sent a message that is not JSON") from None
        return msg if isinstance(msg, dict) else {}

    def _op(self, op: int, timeout: float) -> dict:
        """Wait for a message of this op; events that come meanwhile are kept in self.events."""
        deadline = time.time() + timeout
        while True:
            msg = self._next(deadline)
            if msg.get("op") == op:
                return msg.get("d") or {}
            if msg.get("op") == 5:
                self.events.append(msg.get("d") or {})

    def request(self, rtype: str, data: Optional[dict] = None, timeout: float = REQUEST_S) -> dict:
        """One request; its responseData. OBSError (with OBS's own comment) when it did not succeed."""
        self._n += 1
        rid = f"cf-{os.getpid()}-{self._n}"
        d: Dict[str, object] = {"requestType": rtype, "requestId": rid}
        if data:
            d["requestData"] = data
        self._send(6, d)
        deadline = time.time() + timeout
        while True:
            msg = self._next(deadline)
            if msg.get("op") == 5:
                self.events.append(msg.get("d") or {})
                continue
            body = msg.get("d") or {}
            if msg.get("op") != 7 or body.get("requestId") != rid:
                continue
            st = body.get("requestStatus") or {}
            if not st.get("result"):
                raise OBSError(f"OBS refused {rtype} ({st.get('code')}"
                               + (f": {st.get('comment')}" if st.get("comment") else "") + ")")
            return body.get("responseData") or {}

    def wait_event(self, etype: str, pred: Callable[[dict], bool] = lambda d: True,
                   timeout: float = REQUEST_S) -> dict:
        """The eventData of the first `etype` event that `pred` accepts, kept or still to come."""
        deadline = time.time() + timeout
        while True:
            for i, ev in enumerate(self.events):
                if ev.get("eventType") == etype and pred(ev.get("eventData") or {}):
                    del self.events[: i + 1]
                    return ev.get("eventData") or {}
            if time.time() >= deadline:
                raise OBSError(f"OBS did not send {etype} in {timeout:.0f} s")
            msg = self._next(deadline)
            if msg.get("op") == 5:
                self.events.append(msg.get("d") or {})

    def reidentify(self, events: int) -> None:
        self._send(3, {"eventSubscriptions": events})
        self._op(2, REQUEST_S)
        self.subs = events

    # the helpers -------------------------------------------------------------------------------------------
    def version(self) -> dict:
        return self.request("GetVersion")

    def video_settings(self) -> dict:
        return self.request("GetVideoSettings")

    def record_status(self) -> dict:
        return self.request("GetRecordStatus")

    def record_dir(self) -> str:
        return str(self.request("GetRecordDirectory").get("recordDirectory") or "")

    def set_record_dir(self, path: Path) -> None:
        self.request("SetRecordDirectory", {"recordDirectory": str(path)})

    def start_record(self, timeout: float = STARTED_S) -> dict:
        self.events.clear()
        self.request("StartRecord")
        return self.wait_event("RecordStateChanged", lambda d: d.get("outputState") == "OBS_WEBSOCKET_OUTPUT_STARTED"
                               or (d.get("outputActive") is True and "STOPP" not in str(d.get("outputState"))),
                               timeout)

    def stop_record(self, timeout: float = STOPPED_S) -> str:
        """Stop, wait for STOPPED; the recording's path (the event's, else StopRecord's own answer)."""
        got = self.request("StopRecord")
        ev = self.wait_event("RecordStateChanged", lambda d: d.get("outputState") == "OBS_WEBSOCKET_OUTPUT_STOPPED",
                             timeout)
        return str(ev.get("outputPath") or got.get("outputPath") or "")

    def chapter(self, name: str) -> bool:
        """A chapter marker in a Hybrid MP4 recording; False (never an error) when OBS cannot."""
        try:
            self.request("CreateRecordChapter", {"chapterName": name})
            return True
        except OBSError:
            return False

    def stats(self) -> dict:
        return self.request("GetStats")

    def screenshot_stats(self, source: Optional[str] = None, width: int = 64) -> dict:
        """The capture card's picture, small: mean luma (0-255), mean saturation (0-1), the luma's spread. BMP when
        OBS lists it (no decoding needed), else PNG (zlib)."""
        source = source or self.video_input
        fmts = [str(f).lower() for f in (self.hello.get("supportedImageFormats") or [])]
        if not fmts:
            try:
                fmts = [str(f).lower() for f in self.version().get("supportedImageFormats") or []]
            except OBSError:
                fmts = []
        fmt = "bmp" if (not fmts or "bmp" in fmts) else "png"
        data = self.request("GetSourceScreenshot", {"sourceName": source, "imageFormat": fmt, "imageWidth": width})
        raw = str(data.get("imageData") or "")
        blob = base64.b64decode(raw.split(",", 1)[1] if raw.startswith("data:") else raw)
        px = decode_bmp(blob) if blob[:2] == b"BM" else decode_png(blob)
        return picture_stats(px)

    def audio_level(self, source: Optional[str] = None, seconds: float = 1.5) -> Optional[float]:
        """The loudest peak of `source` over `seconds`, in dB (None: OBS sent no meter for it)."""
        source = source or self.audio_input
        before = self.subs
        self.reidentify(before | SUB_VOLUME)
        peak = None
        try:
            end = time.time() + seconds
            while time.time() < end:
                try:
                    msg = self._next(end)
                except OBSError:
                    break
                if msg.get("op") != 5:
                    continue
                ev = msg.get("d") or {}
                if ev.get("eventType") != "InputVolumeMeters":
                    self.events.append(ev)
                    continue
                for inp in (ev.get("eventData") or {}).get("inputs") or []:
                    if inp.get("inputName") != source:
                        continue
                    for ch in inp.get("inputLevelsMul") or []:
                        vals = [float(v) for v in ch if isinstance(v, (int, float))]
                        if vals:
                            peak = max(peak or 0.0, max(vals))
        finally:
            self.reidentify(before)
        if peak is None:
            return None
        import math
        return -120.0 if peak <= 0 else round(20 * math.log10(peak), 1)


# ------------------------------------------------------------------------------------------------------ pictures
def decode_bmp(blob: bytes) -> List[tuple]:
    """(r, g, b) pixels of an uncompressed 24- or 32-bit BMP."""
    if blob[:2] != b"BM" or len(blob) < 54:
        raise OBSError("the screenshot is not a BMP")
    off = struct.unpack_from("<I", blob, 10)[0]
    w, h = struct.unpack_from("<ii", blob, 18)
    bpp = struct.unpack_from("<H", blob, 28)[0]
    if bpp not in (24, 32):
        raise OBSError(f"the screenshot BMP is {bpp}-bit")
    step = bpp // 8
    stride = (w * step + 3) & ~3
    out = []
    for row in range(abs(h)):
        base = off + row * stride
        for x in range(w):
            i = base + x * step
            if i + 2 < len(blob):
                b, g, r = blob[i], blob[i + 1], blob[i + 2]
                out.append((r, g, b))
    return out


def decode_png(blob: bytes) -> List[tuple]:
    """(r, g, b) pixels of an 8-bit RGB or RGBA PNG (the five scanline filters undone)."""
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise OBSError("the screenshot is not a PNG")
    i, idat, w = 8, b"", 0
    h = ctype = depth = 0
    while i + 8 <= len(blob):
        n, kind = struct.unpack_from("!I4s", blob, i)
        chunk = blob[i + 8:i + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack_from("!IIBB", chunk, 0)
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"IEND":
            break
        i += 12 + n
    if depth != 8 or ctype not in (2, 6):
        raise OBSError(f"the screenshot PNG is not 8-bit RGB or RGBA (depth {depth}, colour type {ctype})")
    bpp = 3 if ctype == 2 else 4
    raw = zlib.decompress(idat)
    stride = w * bpp
    prev = bytearray(stride)
    out = []
    pos = 0
    for _ in range(h):
        ft = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if ft == 1:
                line[x] = (line[x] + a) & 0xFF
            elif ft == 2:
                line[x] = (line[x] + b) & 0xFF
            elif ft == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 0xFF
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        out += [(line[x], line[x + 1], line[x + 2]) for x in range(0, stride, bpp)]
        prev = line
    return out


def picture_stats(px: List[tuple]) -> dict:
    """Mean luma (Rec. 709, 0-255), its spread, and mean saturation (0-1) of a few thousand pixels."""
    if not px:
        return {"luma": 0.0, "spread": 0.0, "sat": 0.0, "pixels": 0}
    lum = [0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in px]
    mean = sum(lum) / len(lum)
    spread = (sum((v - mean) ** 2 for v in lum) / len(lum)) ** 0.5
    sat = sum(((max(p) - min(p)) / max(p)) if max(p) else 0.0 for p in px) / len(px)
    return {"luma": round(mean, 1), "spread": round(spread, 1), "sat": round(sat, 3), "pixels": len(px)}


NO_PICTURE_LUMA = 10.0


def picture_words(st: dict) -> tuple:
    """(kind, words) for the status strip and Record: ok, black (refuse) or washed (HDR on: a warning)."""
    if st.get("luma", 0) < NO_PICTURE_LUMA:
        return "black", "No picture from the capture card: plugged in? console on? PS5 HDCP off?"
    if st.get("spread", 99) < 18 and st.get("sat", 1) < 0.12 and st.get("luma", 0) > 60:
        return "washed", "The picture looks washed out: HDR may be on (turn HDR off on the console)"
    return "ok", "picture OK"


def version_tuple(v: str) -> tuple:
    out = []
    for part in str(v or "").split(".")[:3]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))
