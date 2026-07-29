#!/usr/bin/env python3
"""Front-panel test UI for the Hi3798MV300 mainline port.

Serves one page that drives the two front-panel LEDs and shows what the IR
receiver sees, live. Standard library only -- the box has no pip and 965 MiB
of RAM, so a framework is not worth its footprint.

Two independent views of the same remote press:

  /dev/lirc0        raw pulse/space timings, straight off hix5hd2-ir
  /dev/input/event0 MSC_SCAN scancodes, after the in-kernel decoders

The raw view answers "is the receiver wired up at all"; the scancode view
answers "which protocol is this remote speaking". A remote that produces
pulses but no scancodes means the decoders are off -- see
/sys/class/rc/rc0/protocols.

Runs unprivileged. It needs group `video` for /dev/lirc0 and the LED sysfs
attributes, and group `input` for /dev/input/event0.

    python3 tvbox-panel.py [--port 8080] [--host 0.0.0.0]
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

LIRC_DEVICE = "/dev/lirc0"
INPUT_DEVICE = "/dev/input/event0"
LEDS_ROOT = Path("/sys/class/leds")


def led_names() -> list[str]:
    """Every LED the kernel exposes, discovered rather than hardcoded.

    The names are built from the device tree's colour and function, so
    changing `function` in the DTS renames the sysfs directory. Hardcoding
    them means the panel silently loses an LED the next time the DTS is
    edited.
    """
    if not LEDS_ROOT.is_dir():
        return []
    return sorted(entry.name for entry in LEDS_ROOT.iterdir() if entry.is_dir())

# LIRC mode2 word: top byte is the record type, low 24 bits are microseconds.
LIRC_MODE2_MASK = 0xFF000000
LIRC_VALUE_MASK = 0x00FFFFFF
LIRC_MODE2_SPACE = 0x00000000
LIRC_MODE2_PULSE = 0x01000000
LIRC_MODE2_FREQUENCY = 0x02000000
LIRC_MODE2_TIMEOUT = 0x03000000
LIRC_MODE2_OVERFLOW = 0x04000000

# A gap this long means the remote has stopped talking and the burst is over.
BURST_GAP_US = 15000
# Guard against a stuck receiver flooding memory with one unbounded burst.
MAX_BURST_EDGES = 2048

# linux/input.h
EV_SYN, EV_KEY, EV_MSC = 0x00, 0x01, 0x04
MSC_SCAN = 0x04
INPUT_EVENT_FORMAT = "@llHHi"
INPUT_EVENT_SIZE = struct.calcsize(INPUT_EVENT_FORMAT)


# --------------------------------------------------------------------------
# event fan-out
# --------------------------------------------------------------------------


class EventBus:
    """Broadcasts events to every connected SSE client.

    Each subscriber gets its own bounded queue. A client that stops reading
    loses its oldest events rather than stalling the reader threads.
    """

    def __init__(self, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._lock = threading.Lock()
        self._subscribers: set[queue.Queue] = set()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def publish(self, event: dict) -> None:
        with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except queue.Empty:
                    pass


# --------------------------------------------------------------------------
# IR decoding
# --------------------------------------------------------------------------


def _near(value: int, target: int, tolerance: float = 0.3) -> bool:
    return abs(value - target) <= target * tolerance


def decode_nec(edges: list[dict]) -> dict | None:
    """Decode a NEC burst.

    NEC is worth decoding here even though the kernel already does it: it is
    what most of these boxes ship with, and seeing the address/command split
    next to the raw timings makes it obvious when a frame is malformed rather
    than merely unrecognised.

    Frame: 9 ms pulse, 4.5 ms space, then 32 bits sent LSB-first as
    address, ~address, command, ~command. A 9 ms pulse followed by a 2.25 ms
    space is the auto-repeat.
    """
    if len(edges) < 2:
        return None
    if edges[0]["kind"] != "pulse" or not _near(edges[0]["us"], 9000):
        return None

    if _near(edges[1]["us"], 2250):
        return {"protocol": "nec", "kind": "repeat"}
    if not _near(edges[1]["us"], 4500):
        return None

    bits: list[int] = []
    i = 2
    while i + 1 < len(edges) and len(bits) < 32:
        pulse, space = edges[i], edges[i + 1]
        if pulse["kind"] != "pulse" or space["kind"] != "space":
            break
        if not _near(pulse["us"], 560, 0.4):
            break
        if _near(space["us"], 560, 0.4):
            bits.append(0)
        elif _near(space["us"], 1690, 0.3):
            bits.append(1)
        else:
            break
        i += 2

    if len(bits) != 32:
        return {"protocol": "nec", "kind": "partial", "bits": len(bits)}

    def byte_at(offset: int) -> int:
        value = 0
        for position in range(8):
            value |= bits[offset + position] << position
        return value

    address, address_inv, command, command_inv = (byte_at(n) for n in (0, 8, 16, 24))
    raw = 0
    for position, bit in enumerate(bits):
        raw |= bit << position

    return {
        "protocol": "nec",
        "kind": "frame",
        "address": address,
        "command": command,
        "raw": raw,
        # Standard NEC checks both complements. Extended NEC uses the address
        # byte pair as a 16-bit address instead, so a failed address check is
        # informative rather than fatal.
        "address_ok": address ^ address_inv == 0xFF,
        "command_ok": command ^ command_inv == 0xFF,
        "extended": address ^ address_inv != 0xFF,
    }


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------


class LircReader(threading.Thread):
    """Groups raw mode2 words from /dev/lirc0 into bursts."""

    def __init__(self, bus: EventBus, device: str = LIRC_DEVICE) -> None:
        super().__init__(daemon=True, name="lirc-reader")
        self.bus = bus
        self.device = device
        self.error: str | None = None
        self._sequence = 0

    def run(self) -> None:
        try:
            fd = open(self.device, "rb", buffering=0)
        except OSError as exc:
            self.error = f"{self.device}: {exc}"
            self.bus.publish({"type": "error", "source": "lirc", "message": self.error})
            return

        edges: list[dict] = []
        with fd:
            while True:
                data = fd.read(4)
                if not data or len(data) < 4:
                    continue
                (word,) = struct.unpack("=I", data)
                kind_bits = word & LIRC_MODE2_MASK
                duration = word & LIRC_VALUE_MASK

                if kind_bits == LIRC_MODE2_PULSE:
                    edges.append({"kind": "pulse", "us": duration})
                elif kind_bits == LIRC_MODE2_SPACE:
                    # A long space is the gap after the burst, not part of it.
                    if edges and duration >= BURST_GAP_US:
                        self._flush(edges)
                        edges = []
                    elif edges:
                        edges.append({"kind": "space", "us": duration})
                elif kind_bits in (LIRC_MODE2_TIMEOUT, LIRC_MODE2_OVERFLOW):
                    if edges:
                        self._flush(edges)
                        edges = []
                else:
                    # FREQUENCY and anything else carry no timing information.
                    continue

                if len(edges) >= MAX_BURST_EDGES:
                    self._flush(edges)
                    edges = []

    def _flush(self, edges: list[dict]) -> None:
        # Trailing spaces carry no information and skew the total duration.
        while edges and edges[-1]["kind"] == "space":
            edges.pop()
        if not edges:
            return
        self._sequence += 1
        self.bus.publish(
            {
                "type": "burst",
                "seq": self._sequence,
                "time": time.time(),
                "edges": edges,
                "count": len(edges),
                "duration_us": sum(edge["us"] for edge in edges),
                "decoded": decode_nec(edges),
            }
        )


class InputReader(threading.Thread):
    """Reports the scancodes the in-kernel decoders produce."""

    def __init__(self, bus: EventBus, device: str = INPUT_DEVICE) -> None:
        super().__init__(daemon=True, name="input-reader")
        self.bus = bus
        self.device = device
        self.error: str | None = None
        self._sequence = 0

    def run(self) -> None:
        try:
            fd = open(self.device, "rb", buffering=0)
        except OSError as exc:
            self.error = f"{self.device}: {exc}"
            self.bus.publish({"type": "error", "source": "input", "message": self.error})
            return

        pending_scan: int | None = None
        with fd:
            while True:
                data = fd.read(INPUT_EVENT_SIZE)
                if not data or len(data) < INPUT_EVENT_SIZE:
                    continue
                _sec, _usec, ev_type, code, value = struct.unpack(
                    INPUT_EVENT_FORMAT, data
                )

                if ev_type == EV_MSC and code == MSC_SCAN:
                    pending_scan = value & 0xFFFFFFFF
                elif ev_type == EV_KEY:
                    self._emit(pending_scan, keycode=code, pressed=bool(value))
                    pending_scan = None
                elif ev_type == EV_SYN and pending_scan is not None:
                    # No keymap entry, so the scancode arrives with no EV_KEY
                    # after it. Report it anyway -- that is the whole point of
                    # running with RC_MAP_EMPTY.
                    self._emit(pending_scan, keycode=None, pressed=None)
                    pending_scan = None

    def _emit(self, scancode: int | None, keycode: int | None, pressed: bool | None) -> None:
        if scancode is None:
            return
        self._sequence += 1
        self.bus.publish(
            {
                "type": "scancode",
                "seq": self._sequence,
                "time": time.time(),
                "scancode": scancode,
                "keycode": keycode,
                "pressed": pressed,
            }
        )


# --------------------------------------------------------------------------
# LED access
# --------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    return path.read_text().strip()


def led_state(name: str) -> dict:
    directory = LEDS_ROOT / name
    triggers_raw = _read_text(directory / "trigger")
    active = "none"
    available = []
    for token in triggers_raw.split():
        if token.startswith("[") and token.endswith("]"):
            token = token[1:-1]
            active = token
        available.append(token)
    return {
        "name": name,
        "brightness": int(_read_text(directory / "brightness")),
        "max_brightness": int(_read_text(directory / "max_brightness")),
        "trigger": active,
        "triggers": available,
        "writable": os.access(directory / "brightness", os.W_OK),
    }


def all_leds() -> list[dict]:
    return [led_state(name) for name in led_names()]


def set_led(name: str, brightness: int | None, trigger: str | None) -> dict:
    if name not in led_names():
        raise KeyError(name)
    directory = LEDS_ROOT / name
    # Order matters: a trigger owns the brightness file, so switching trigger
    # first lets an explicit brightness in the same request still take effect.
    if trigger is not None:
        (directory / "trigger").write_text(trigger)
    if brightness is not None:
        (directory / "brightness").write_text(str(brightness))
    return led_state(name)


# --------------------------------------------------------------------------
# GPIO explorer
# --------------------------------------------------------------------------

GPIO_HELPER = "/usr/local/bin/tvbox-gpio-helper.py"

# Lines a kernel driver has claimed. Driving these behind gpiolib's back makes
# the LED class and the register disagree, so the UI greys them out. Use the
# LED controls above for these two.
RESERVED_LINES = {(5, 0), (5, 2)}


class GpioError(RuntimeError):
    pass


def gpio_helper(*args: str) -> str:
    """Run the privileged helper. Raises GpioError with its stderr on failure."""
    import subprocess

    try:
        completed = subprocess.run(
            ["sudo", "-n", GPIO_HELPER, *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError as exc:
        raise GpioError(f"cannot run helper: {exc}") from exc
    except Exception as exc:  # subprocess.TimeoutExpired and friends
        raise GpioError(f"helper timed out: {exc}") from exc
    if completed.returncode != 0:
        raise GpioError(completed.stderr.strip() or "helper failed")
    return completed.stdout


def gpio_dump() -> list[dict]:
    banks = json.loads(gpio_helper("dump"))
    for bank in banks:
        for pin in bank["pins"]:
            pin["reserved"] = (bank["index"], pin["pin"]) in RESERVED_LINES
    return banks


class GpioBaseline:
    """Remembers each bank's GPIODIR as first seen, so the UI can undo itself.

    Hunting for an unknown LED means turning input pins into outputs. Without
    a recorded starting point there is no way back short of a reboot.
    """

    def __init__(self) -> None:
        self.masks: dict[int, int] = {}
        self.lock = threading.Lock()

    def capture(self) -> None:
        with self.lock:
            if self.masks:
                return
            try:
                for bank in json.loads(gpio_helper("dump")):
                    self.masks[bank["index"]] = bank["dir"]
            except GpioError:
                pass

    def restore(self) -> None:
        with self.lock:
            for index, mask in self.masks.items():
                gpio_helper("restore", str(index), f"{mask:02x}")


GPIO_BASELINE = GpioBaseline()


def gpio_pulse(bank: int, pin: int, seconds: float) -> None:
    """Drive one pin high for a moment, then put it back the way it was.

    Restoring runs even if the caller disconnects mid-pulse -- an output left
    driven on an unidentified pin is exactly what this tool is trying to avoid
    leaving behind.
    """
    original = json.loads(gpio_helper("dump"))[bank]["dir"]
    try:
        gpio_helper("dir", str(bank), str(pin), "out")
        gpio_helper("set", str(bank), str(pin), "1")
        time.sleep(seconds)
    finally:
        gpio_helper("set", str(bank), str(pin), "0")
        gpio_helper("restore", str(bank), f"{original:02x}")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hi3798MV300 panel</title>
<style>
  :root {
    --bg: #0e1116; --panel: #161b22; --line: #262d38; --text: #d7dee8;
    --dim: #8b98a8; --red: #ff5f56; --blue: #5aa9ff; --ok: #46d19a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 14px/1.5 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  }
  header {
    padding: 14px 20px; border-bottom: 1px solid var(--line);
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
  }
  h1 { font-size: 15px; margin: 0; font-weight: 600; letter-spacing: .02em; }
  .status { font-size: 12px; color: var(--dim); }
  .status.live { color: var(--ok); }
  .status.down { color: var(--red); }
  main { padding: 20px; display: grid; gap: 20px; grid-template-columns: 1fr; max-width: 1100px; }
  @media (min-width: 900px) { main { grid-template-columns: 320px 1fr; } }
  section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
  h2 {
    font-size: 12px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--dim); margin: 0; padding: 12px 16px; border-bottom: 1px solid var(--line);
  }
  .body { padding: 16px; }
  .led { display: flex; align-items: center; gap: 12px; padding: 12px 0; border-bottom: 1px solid var(--line); }
  .led:last-child { border-bottom: 0; }
  .dot { width: 18px; height: 18px; border-radius: 50%; border: 1px solid var(--line); flex: 0 0 auto; }
  .dot.red.on { background: var(--red); box-shadow: 0 0 12px var(--red); }
  .dot.blue.on { background: var(--blue); box-shadow: 0 0 12px var(--blue); }
  .dot.other.on { background: var(--ok); box-shadow: 0 0 12px var(--ok); }
  .led-info { flex: 1; min-width: 0; }
  .led-name { font-weight: 600; }
  .led-meta { font-size: 12px; color: var(--dim); }
  button, select {
    background: #1d2530; color: var(--text); border: 1px solid var(--line);
    border-radius: 5px; padding: 5px 10px; font: inherit; font-size: 12px; cursor: pointer;
  }
  button:hover, select:hover { border-color: #3a4553; }
  select { width: 100%; margin-top: 8px; }
  .feed { max-height: 62vh; overflow-y: auto; }
  .burst { padding: 12px 16px; border-bottom: 1px solid var(--line); }
  .burst-head { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; font-size: 12px; }
  .seq { color: var(--dim); }
  .tag { padding: 1px 7px; border-radius: 999px; font-size: 11px; border: 1px solid var(--line); }
  .tag.nec { color: var(--ok); border-color: var(--ok); }
  .tag.repeat { color: var(--blue); border-color: var(--blue); }
  .tag.raw { color: var(--dim); }
  .tag.scan { color: #e3b341; border-color: #e3b341; }
  .wave { display: flex; align-items: flex-end; height: 30px; margin-top: 9px; gap: 1px; overflow-x: auto; }
  .wave i { display: block; height: 100%; flex: 0 0 auto; }
  .wave i.p { background: var(--blue); }
  .wave i.s { background: #202836; }
  .timings { margin-top: 7px; font-size: 11px; color: var(--dim); word-break: break-all; }
  .empty { padding: 22px 16px; color: var(--dim); font-size: 13px; }
  .err { color: var(--red); padding: 10px 16px; font-size: 12px; }

  .span-all { grid-column: 1 / -1; }
  .toolbar {
    display: flex; gap: 14px; align-items: center; flex-wrap: wrap;
    padding: 12px 16px; border-bottom: 1px solid var(--line); font-size: 12px;
  }
  .toolbar label { display: flex; gap: 5px; align-items: center; cursor: pointer; color: var(--dim); }
  .toolbar input { accent-color: var(--blue); }
  .warn {
    padding: 10px 16px; font-size: 12px; color: #e3b341;
    border-bottom: 1px solid var(--line); background: #1c1a12;
  }
  .bank { display: flex; align-items: center; gap: 10px; padding: 7px 16px; border-bottom: 1px solid var(--line); }
  .bank:last-child { border-bottom: 0; }
  .bank-name { width: 62px; flex: 0 0 auto; font-weight: 600; }
  .bank-base { width: 96px; flex: 0 0 auto; color: var(--dim); font-size: 11px; }
  .pins { display: flex; gap: 5px; flex-wrap: wrap; }
  .pin {
    width: 42px; height: 34px; border-radius: 5px; border: 1px solid var(--line);
    background: #171d26; color: var(--dim); font: inherit; font-size: 11px;
    cursor: pointer; display: flex; flex-direction: column;
    align-items: center; justify-content: center; line-height: 1.15; padding: 0;
  }
  .pin b { font-size: 12px; color: var(--text); font-weight: 600; }
  .pin.out { border-color: #3d5573; }
  .pin.out.hi { background: var(--blue); border-color: var(--blue); }
  .pin.out.hi b, .pin.out.hi span { color: #06101d; }
  .pin.hw { border-style: dashed; border-color: #e3b341; }
  .pin.reserved { opacity: .45; cursor: not-allowed; border-style: dotted; }
  .pin.busy { outline: 2px solid var(--ok); outline-offset: 1px; }
  .legend { padding: 10px 16px; font-size: 11px; color: var(--dim); display: flex; gap: 16px; flex-wrap: wrap; }
</style>
</head>
<body>
<header>
  <h1>Hi3798MV300 front panel</h1>
  <span id="status" class="status">connecting…</span>
  <span id="kernel" class="status"></span>
</header>
<main>
  <section>
    <h2>LEDs</h2>
    <div class="body" id="leds"><div class="empty">loading…</div></div>
  </section>
  <section>
    <h2>IR receiver <span id="ircount" class="status"></span></h2>
    <div id="irerr"></div>
    <div class="feed" id="feed"><div class="empty">Press a button on a remote.</div></div>
  </section>
  <section class="span-all">
    <h2>GPIO — 10 PL061 banks <span id="gpiostatus" class="status"></span></h2>
    <div class="warn">
      These pins are unlabelled. Driving the wrong one can reset the box, cut
      power to the eMMC or drop the Wi-Fi. Pulse is the safe way to hunt: it
      restores the pin's original direction afterwards, even if you close the
      page. Nothing here survives a reboot.
    </div>
    <div class="toolbar">
      <span>click a pin to:</span>
      <label><input type="radio" name="mode" value="pulse" checked> pulse high 2s</label>
      <label><input type="radio" name="mode" value="level"> toggle level</label>
      <label><input type="radio" name="mode" value="dir"> toggle direction</label>
      <button id="restore">restore all directions</button>
      <button id="refresh">refresh</button>
    </div>
    <div id="banks"><div class="empty">loading…</div></div>
    <div class="legend">
      <span>filled = output high</span>
      <span>outlined = output low</span>
      <span>grey = input</span>
      <span>dashed = AFSEL, hardware owns the pin</span>
      <span>dotted = claimed by a kernel driver</span>
    </div>
  </section>
</main>
<script>
const MAX_ROWS = 60;
let received = 0;

function h(tag, cls, text) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text;
  return el;
}

async function loadLeds() {
  const leds = await (await fetch('api/leds')).json();
  const host = document.getElementById('leds');
  host.textContent = '';
  if (!leds.length) {
    host.appendChild(h('div', 'empty', 'No LEDs found under /sys/class/leds.'));
    return;
  }
  for (const led of leds) {
    const row = h('div', 'led');
    // gpio-leds names its entries "<colour>:<function>".
    const prefix = led.name.split(':')[0];
    const colour = prefix === 'red' ? 'red' : prefix === 'blue' ? 'blue' : 'other';
    const dot = h('span', 'dot ' + colour + (led.brightness ? ' on' : ''));
    const info = h('div', 'led-info');
    info.appendChild(h('div', 'led-name', led.name));
    info.appendChild(h('div', 'led-meta',
      'brightness ' + led.brightness + '/' + led.max_brightness +
      (led.writable ? '' : ' · read-only')));
    const btn = h('button', null, led.brightness ? 'turn off' : 'turn on');
    btn.disabled = !led.writable;
    btn.onclick = () => post(led.name, { brightness: led.brightness ? 0 : led.max_brightness });
    row.append(dot, info, btn);

    const sel = h('select');
    for (const t of led.triggers) {
      const opt = h('option', null, t);
      opt.value = t;
      if (t === led.trigger) opt.selected = true;
      sel.appendChild(opt);
    }
    sel.disabled = !led.writable;
    sel.onchange = () => post(led.name, { trigger: sel.value });

    const wrap = h('div');
    wrap.style.borderBottom = '1px solid var(--line)';
    wrap.append(row, sel);
    wrap.lastChild.style.marginBottom = '12px';
    host.appendChild(wrap);
  }
}

async function post(name, payload) {
  await fetch('api/leds/' + encodeURIComponent(name), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  loadLeds();
}

function renderBurst(ev) {
  const row = h('div', 'burst');
  const head = h('div', 'burst-head');
  head.appendChild(h('span', 'seq', '#' + ev.seq));

  const d = ev.decoded;
  if (d && d.kind === 'frame') {
    head.appendChild(h('span', 'tag nec', 'NEC'));
    const hex = n => '0x' + n.toString(16).padStart(2, '0');
    head.appendChild(h('span', null,
      'addr ' + hex(d.address) + '  cmd ' + hex(d.command) +
      '  raw 0x' + d.raw.toString(16).padStart(8, '0')));
    if (!d.command_ok) head.appendChild(h('span', 'tag raw', 'cmd checksum bad'));
    if (d.extended) head.appendChild(h('span', 'tag raw', 'extended'));
  } else if (d && d.kind === 'repeat') {
    head.appendChild(h('span', 'tag repeat', 'NEC repeat'));
  } else if (d && d.kind === 'partial') {
    head.appendChild(h('span', 'tag raw', 'NEC partial (' + d.bits + ' bits)'));
  } else {
    head.appendChild(h('span', 'tag raw', 'raw'));
  }
  head.appendChild(h('span', 'seq', ev.count + ' edges · ' + ev.duration_us + ' µs'));
  row.appendChild(head);

  const wave = h('div', 'wave');
  for (const edge of ev.edges.slice(0, 200)) {
    const bar = h('i', edge.kind === 'pulse' ? 'p' : 's');
    bar.style.width = Math.max(1, Math.min(60, Math.round(edge.us / 60))) + 'px';
    if (edge.kind === 'space') bar.style.height = '35%';
    bar.title = edge.kind + ' ' + edge.us + ' µs';
    wave.appendChild(bar);
  }
  row.appendChild(wave);
  row.appendChild(h('div', 'timings',
    ev.edges.slice(0, 40).map(e => (e.kind === 'pulse' ? '+' : '-') + e.us).join(' ') +
    (ev.edges.length > 40 ? ' …' : '')));
  return row;
}

function renderScan(ev) {
  const row = h('div', 'burst');
  const head = h('div', 'burst-head');
  head.appendChild(h('span', 'seq', '#' + ev.seq));
  head.appendChild(h('span', 'tag scan', 'SCANCODE'));
  head.appendChild(h('span', null, '0x' + ev.scancode.toString(16)));
  if (ev.keycode !== null) {
    head.appendChild(h('span', 'seq',
      'keycode ' + ev.keycode + (ev.pressed ? ' down' : ' up')));
  } else {
    head.appendChild(h('span', 'seq', 'no keymap entry'));
  }
  row.appendChild(head);
  return row;
}

function push(node) {
  const feed = document.getElementById('feed');
  const placeholder = feed.querySelector('.empty');
  if (placeholder) placeholder.remove();
  feed.prepend(node);
  while (feed.childElementCount > MAX_ROWS) feed.lastElementChild.remove();
  document.getElementById('ircount').textContent = received + ' events';
}

function connect() {
  const status = document.getElementById('status');
  const src = new EventSource('api/ir/stream');
  src.onopen = () => { status.textContent = 'live'; status.className = 'status live'; };
  src.onerror = () => { status.textContent = 'disconnected — retrying'; status.className = 'status down'; };
  src.onmessage = e => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'burst') { received++; push(renderBurst(ev)); }
    else if (ev.type === 'scancode') { received++; push(renderScan(ev)); }
    else if (ev.type === 'error') {
      document.getElementById('irerr').appendChild(
        h('div', 'err', ev.source + ': ' + ev.message));
    }
  };
}

function clickMode() {
  return document.querySelector('input[name=mode]:checked').value;
}

function renderBanks(banks) {
  const host = document.getElementById('banks');
  host.textContent = '';
  for (const bank of banks) {
    const row = h('div', 'bank');
    row.appendChild(h('span', 'bank-name', bank.name));
    row.appendChild(h('span', 'bank-base', bank.base));
    const pins = h('div', 'pins');
    for (const pin of bank.pins) {
      let cls = 'pin';
      if (pin.output) cls += ' out' + (pin.level ? ' hi' : '');
      if (pin.hw) cls += ' hw';
      if (pin.reserved) cls += ' reserved';
      const cell = h('button', cls);
      cell.appendChild(h('b', null, String(pin.pin)));
      cell.appendChild(h('span', null, pin.output ? (pin.level ? 'out 1' : 'out 0') : 'in'));
      cell.title = bank.name + ' line ' + pin.pin +
        (pin.reserved ? ' — claimed by a kernel driver' : '') +
        (pin.hw ? ' — AFSEL set' : '');
      cell.disabled = pin.reserved;
      cell.onclick = () => actOnPin(cell, bank, pin);
      pins.appendChild(cell);
    }
    row.appendChild(pins);
    host.appendChild(row);
  }
}

async function actOnPin(cell, bank, pin) {
  const mode = clickMode();
  let payload;
  if (mode === 'pulse') payload = { pulse: 2 };
  else if (mode === 'level') payload = { direction: 'out', level: pin.level ? 0 : 1 };
  else payload = { direction: pin.output ? 'in' : 'out' };

  cell.classList.add('busy');
  setGpioStatus(mode + ' ' + bank.name + '.' + pin.pin + '…');
  try {
    const res = await fetch('api/gpio/' + bank.index + '/' + pin.pin, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await res.json();
    if (!res.ok) { setGpioStatus('error: ' + (body.error || res.status), true); return; }
    renderBanks(body);
    setGpioStatus(mode + ' ' + bank.name + '.' + pin.pin + ' done');
  } catch (e) {
    setGpioStatus('error: ' + e.message, true);
  } finally {
    cell.classList.remove('busy');
  }
}

function setGpioStatus(text, bad) {
  const el = document.getElementById('gpiostatus');
  el.textContent = text;
  el.className = 'status' + (bad ? ' down' : '');
}

async function loadGpio() {
  try {
    const res = await fetch('api/gpio');
    const body = await res.json();
    if (!res.ok) { setGpioStatus('error: ' + (body.error || res.status), true); return; }
    renderBanks(body);
    setGpioStatus('');
  } catch (e) {
    setGpioStatus('error: ' + e.message, true);
  }
}

document.getElementById('refresh').onclick = loadGpio;
document.getElementById('restore').onclick = async () => {
  setGpioStatus('restoring…');
  const res = await fetch('api/gpio/restore', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}',
  });
  const body = await res.json();
  if (res.ok) { renderBanks(body); setGpioStatus('directions restored'); }
  else setGpioStatus('error: ' + (body.error || res.status), true);
};

fetch('api/info').then(r => r.json()).then(i => {
  document.getElementById('kernel').textContent = i.kernel + ' · ' + i.host;
});
loadLeds();
loadGpio();
connect();
setInterval(loadLeds, 5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "tvbox-panel"
    bus: EventBus
    readers: dict

    # Polling GETs would bury anything interesting; keep errors and writes.
    QUIET_PATHS = ("/api/ir/stream", "/api/leds", "/api/gpio", "/api/info", "/")

    def log_message(self, fmt: str, *args) -> None:
        if self.command == "GET" and self.path.split("?")[0] in self.QUIET_PATHS:
            return
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/info":
            self._send_json(
                {
                    "host": os.uname().nodename,
                    "kernel": os.uname().release + " " + os.uname().version.split()[0],
                    "lirc_error": self.readers["lirc"].error,
                    "input_error": self.readers["input"].error,
                }
            )
        elif path == "/api/leds":
            self._send_json(all_leds())
        elif path == "/api/gpio":
            try:
                self._send_json(gpio_dump())
            except GpioError as exc:
                self._send_json({"error": str(exc)}, 500)
        elif path == "/api/ir/stream":
            self._stream()
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("content-length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send_json({"error": "bad json"}, 400)
            return

        if path.startswith("/api/gpio/"):
            self._handle_gpio(path[len("/api/gpio/") :], payload)
            return
        if not path.startswith("/api/leds/"):
            self._send_json({"error": "not found"}, 404)
            return
        # LED names contain a colon, which the browser percent-encodes.
        name = unquote(path[len("/api/leds/") :])
        try:
            state = set_led(
                name,
                payload.get("brightness"),
                payload.get("trigger"),
            )
        except KeyError:
            self._send_json({"error": "unknown led"}, 404)
        except OSError as exc:
            self._send_json({"error": str(exc)}, 500)
        else:
            self._send_json(state)

    def _handle_gpio(self, target: str, payload: dict) -> None:
        if target == "restore":
            try:
                GPIO_BASELINE.restore()
                self._send_json(gpio_dump())
            except GpioError as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        parts = target.split("/")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            self._send_json({"error": "expected /api/gpio/<bank>/<pin>"}, 400)
            return
        bank, pin = int(parts[0]), int(parts[1])
        if (bank, pin) in RESERVED_LINES:
            self._send_json({"error": "line is claimed by a kernel driver"}, 409)
            return

        try:
            if "pulse" in payload:
                seconds = min(float(payload["pulse"]), 10.0)
                gpio_pulse(bank, pin, seconds)
            else:
                if "direction" in payload:
                    gpio_helper("dir", str(bank), str(pin), str(payload["direction"]))
                if "level" in payload:
                    gpio_helper("set", str(bank), str(pin), str(int(payload["level"])))
            self._send_json(gpio_dump())
        except GpioError as exc:
            self._send_json({"error": str(exc)}, 500)
        except (TypeError, ValueError) as exc:
            self._send_json({"error": f"bad payload: {exc}"}, 400)

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "keep-alive")
        self.end_headers()

        q = self.bus.subscribe()
        try:
            for reader in ("lirc", "input"):
                error = self.readers[reader].error
                if error:
                    self._send_event({"type": "error", "source": reader, "message": error})
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    # Keep proxies and idle sockets from timing out.
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self._send_event(event)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self.bus.unsubscribe(q)

    def _send_event(self, event: dict) -> None:
        self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
        self.wfile.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--lirc", default=LIRC_DEVICE)
    parser.add_argument("--input", dest="input_device", default=INPUT_DEVICE)
    args = parser.parse_args()

    bus = EventBus()
    lirc = LircReader(bus, args.lirc)
    keys = InputReader(bus, args.input_device)
    lirc.start()
    keys.start()

    Handler.bus = bus
    Handler.readers = {"lirc": lirc, "input": keys}

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"tvbox-panel on http://{args.host}:{args.port}/", flush=True)
    for led in all_leds():
        print(f"  led {led['name']:<14} writable={led['writable']}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
