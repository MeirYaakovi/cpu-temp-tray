import collections
import ctypes
import json
import logging
import os
import sys
import threading
import time
import traceback
import urllib.request
from PIL import Image, ImageDraw, ImageFont
import pystray

# Base directory: works for both script and PyInstaller exe
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(BASE_DIR, "crash.log")
logging.basicConfig(filename=LOG_FILE, level=logging.ERROR,
                    format="%(asctime)s %(message)s")

HISTORY_HOURS = 12
HISTORY_MAXLEN = HISTORY_HOURS * 3600 // 5  # worst case: 5 s interval
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
_history = collections.deque(maxlen=HISTORY_MAXLEN)  # [(timestamp, temp, fan_rpm), ...]
_last_fan_rpm = 0
_fan_detected = None   # None=unknown, True=has sensors, False=no sensors
FAN_ACTIVE_RPM = 500

VERSION = "1.1.0"
LHM_URL = "http://localhost:8085/data.json"
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "sensor": "Core Max",
    "green_max": 70,
    "orange_max": 90,
    "interval": 15,
    "alert_suppressed": False,
    "icon_size": 64,
    "font_size": 52,
}

_settings = DEFAULT_SETTINGS.copy()
_was_red = False
INTERVAL_OPTIONS = [5, 10, 15, 30, 60, 120]


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            _settings.update(json.load(f))
    except Exception:
        pass


def save_settings():
    with open(SETTINGS_FILE, "w") as f:
        json.dump(_settings, f, indent=2)


def load_history():
    cutoff = time.time() - HISTORY_HOURS * 3600
    try:
        with open(HISTORY_FILE) as f:
            for entry in json.load(f):
                ts, temp = entry[0], entry[1]
                fan_rpm = entry[2] if len(entry) > 2 else 0
                if ts >= cutoff:
                    _history.append((ts, temp, fan_rpm))
    except Exception:
        pass


def save_history():
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(list(_history), f)
    except Exception:
        pass


# ── LHM auto-start ────────────────────────────────────────────────────────────

def _lhm_url_ok():
    try:
        urllib.request.urlopen(LHM_URL, timeout=1)
        return True
    except Exception:
        return False


def _autostart_lhm():
    if _lhm_url_ok():
        return
    lhm_exe = os.path.join(BASE_DIR, "lhm_bundle", "LibreHardwareMonitor.exe")
    if not os.path.exists(lhm_exe):
        return
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", lhm_exe, None, os.path.dirname(lhm_exe), 0
        )
    except Exception:
        logging.error(traceback.format_exc())


# ── temperature fetching ───────────────────────────────────────────────────────

def _collect_temps(node, out):
    name = node.get("Text", "")
    value = node.get("Value", "")
    if "°C" in value and name and "Distance" not in name:
        try:
            out[name] = float(value.split()[0])
        except Exception:
            pass
    for child in node.get("Children", []):
        _collect_temps(child, out)


def _collect_fans(node, out):
    name = node.get("Text", "")
    value = node.get("Value", "")
    if "RPM" in value and name:
        try:
            out[name] = float(value.split()[0].replace(",", ""))
        except Exception:
            pass
    for child in node.get("Children", []):
        _collect_fans(child, out)


def fetch_all_temps():
    global _last_fan_rpm, _fan_detected
    try:
        with urllib.request.urlopen(LHM_URL, timeout=2) as r:
            data = json.loads(r.read())
        temps, fans = {}, {}
        _collect_temps(data, temps)
        _collect_fans(data, fans)
        _last_fan_rpm = max(fans.values()) if fans else 0
        if _fan_detected is None:
            _fan_detected = bool(fans)
        return temps
    except Exception:
        return {}


def get_cpu_temp(all_temps):
    sensor = _settings["sensor"]
    for name, val in all_temps.items():
        if sensor in name:
            return val
    return None


def _format_tooltip(temp, all_temps):
    if not all_temps:
        return "CPU: N/A (LHM not running)"
    sensor = _settings["sensor"]
    lines = [f"{sensor}: {int(round(temp))}°C" if temp is not None else "CPU: N/A"]
    for name, val in all_temps.items():
        if name != sensor:
            lines.append(f"{name}: {int(round(val))}°C")
    return "\n".join(lines)


# ── icon drawing ───────────────────────────────────────────────────────────────

def _load_font(size):
    for name in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make_icon(temp):
    size = _settings.get("icon_size", 64)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if temp is None:
        bg, label = (100, 100, 100, 220), "?"
    elif temp < _settings["green_max"]:
        bg, label = (20, 170, 20, 255), str(int(round(temp)))
    elif temp < _settings["orange_max"]:
        bg, label = (210, 130, 0, 255), str(int(round(temp)))
    else:
        bg, label = (200, 20, 20, 255), str(int(round(temp)))

    # Fill the full icon space — no padding, no gap
    draw.rectangle([0, 0, size, size], fill=bg)

    base = _settings.get("font_size", 52)
    font_size = base if len(label) <= 2 else int(base * 0.77)
    font = _load_font(font_size)
    bbox = draw.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
        label, fill="white", font=font,
    )
    return img


def make_spinner_icon(frame):
    size = _settings.get("icon_size", 64)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, size, size], fill=(70, 70, 70, 220))
    m = max(4, size // 8)
    w = max(2, size // 14)
    start = frame * 90
    draw.arc([m, m, size - m, size - m], start=start, end=start + 270,
             fill=(255, 255, 255, 230), width=w)
    return img


# ── update helper ──────────────────────────────────────────────────────────────

def _apply_update(icon):
    global _was_red
    try:
        _apply_update_inner(icon)
    except Exception:
        logging.error(traceback.format_exc())


def _apply_update_inner(icon):
    global _was_red
    all_temps = fetch_all_temps()
    temp = get_cpu_temp(all_temps)
    icon.icon = make_icon(temp)
    icon.title = _format_tooltip(temp, all_temps)

    if temp is not None:
        _history.append((time.time(), temp, _last_fan_rpm))
        save_history()
        is_red = temp >= _settings["orange_max"]
        if is_red and not _was_red and not _settings.get("alert_suppressed", False):
            threading.Thread(target=_show_alert, args=(temp,), daemon=True).start()
        _was_red = is_red
    else:
        _was_red = False


def _animated_refresh(icon):
    for frame in range(8):
        icon.icon = make_spinner_icon(frame)
        time.sleep(0.08)
    _apply_update(icon)


# ── alert popup ────────────────────────────────────────────────────────────────

def _show_alert(temp):
    import tkinter as tk

    root = tk.Tk()
    root.title("CPU Temperature Warning")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    tk.Label(root, text=f"⚠  CPU is at {int(round(temp))}°C", font=("Arial", 13, "bold"),
             fg="red").grid(row=0, column=0, columnspan=2, pady=(16, 4))
    tk.Label(root, text=f"Above red threshold ({_settings['orange_max']}°C).",
             font=("Arial", 10)).grid(row=1, column=0, columnspan=2, padx=16, pady=4)

    suppress_var = tk.BooleanVar(master=root, value=False)
    tk.Checkbutton(root, text="Don't show this again",
                   variable=suppress_var).grid(row=2, column=0, columnspan=2, pady=(4, 2))

    def ok():
        if suppress_var.get():
            _settings["alert_suppressed"] = True
            save_settings()
        root.destroy()

    tk.Button(root, text="OK", width=10, command=ok).grid(
        row=3, column=0, columnspan=2, pady=(6, 16))
    root.mainloop()


# ── about window ───────────────────────────────────────────────────────────────

def open_about(icon, _item):
    threading.Thread(target=_show_about, daemon=True).start()


def _show_about():
    import tkinter as tk

    root = tk.Tk()
    root.title("About")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    pad = {"padx": 20, "pady": 4}

    tk.Label(root, text="CPU Temp Tray", font=("Arial", 15, "bold")).pack(pady=(20, 2))
    tk.Label(root, text=f"v{VERSION}", font=("Arial", 10), fg="gray").pack()
    tk.Label(root, text="Windows system tray CPU temperature monitor",
             font=("Arial", 10)).pack(**pad)

    tk.Frame(root, height=1, bg="#cccccc").pack(fill="x", padx=20, pady=8)

    tk.Label(root, text="Powered by LibreHardwareMonitor (MPL 2.0)",
             font=("Arial", 9), fg="gray").pack()
    tk.Label(root, text="Uses pystray (LGPL v3) · Pillow (HPND) · wmi (MIT)",
             font=("Arial", 9), fg="gray").pack(pady=(2, 8))

    link = tk.Label(root, text="github.com/MeirYaakovi/cpu-temp-tray",
                    font=("Arial", 9), fg="#0066cc", cursor="hand2")
    link.pack(pady=(0, 16))
    link.bind("<Button-1>", lambda e: __import__("webbrowser").open(
        "https://github.com/MeirYaakovi/cpu-temp-tray"))

    tk.Button(root, text="Close", width=10, command=root.destroy).pack(pady=(0, 16))
    root.mainloop()


# ── settings window ────────────────────────────────────────────────────────────

def open_settings(icon, _item):
    threading.Thread(target=lambda: _show_settings(icon), daemon=True).start()


def _show_settings(icon):
    import tkinter as tk
    from tkinter import ttk, messagebox

    root = tk.Tk()
    root.title("CPU Temp Tray — Settings")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    pad = {"padx": 12, "pady": 6}
    sensors = ["Core Max", "Core Average", "CPU Package",
               "CPU Core #1", "CPU Core #2", "CPU Core #3", "CPU Core #4"]

    tk.Label(root, text="Sensor:").grid(row=0, column=0, sticky="e", **pad)
    sensor_var = tk.StringVar(master=root, value=_settings["sensor"])
    ttk.Combobox(root, textvariable=sensor_var, values=sensors,
                 state="readonly", width=18).grid(row=0, column=1, sticky="w", **pad)

    tk.Label(root, text="Green up to (°C):").grid(row=1, column=0, sticky="e", **pad)
    green_var = tk.StringVar(master=root, value=str(_settings["green_max"]))
    tk.Entry(root, textvariable=green_var, width=6).grid(row=1, column=1, sticky="w", **pad)

    tk.Label(root, text="Orange up to (°C):").grid(row=2, column=0, sticky="e", **pad)
    orange_var = tk.StringVar(master=root, value=str(_settings["orange_max"]))
    tk.Entry(root, textvariable=orange_var, width=6).grid(row=2, column=1, sticky="w", **pad)

    tk.Label(root, text="Refresh every (sec):").grid(row=3, column=0, sticky="e", **pad)
    interval_var = tk.StringVar(master=root, value=str(_settings["interval"]))
    tk.Entry(root, textvariable=interval_var, width=6).grid(row=3, column=1, sticky="w", **pad)

    tk.Label(root, text="Icon size (px):").grid(row=4, column=0, sticky="e", **pad)
    icon_size_var = tk.IntVar(master=root, value=_settings.get("icon_size", 64))
    tk.Scale(root, from_=24, to=128, orient="horizontal", variable=icon_size_var,
             length=140).grid(row=4, column=1, sticky="w", **pad)

    tk.Label(root, text="Font size (px):").grid(row=5, column=0, sticky="e", **pad)
    font_size_var = tk.IntVar(master=root, value=_settings.get("font_size", 52))
    tk.Scale(root, from_=10, to=100, orient="horizontal", variable=font_size_var,
             length=140).grid(row=5, column=1, sticky="w", **pad)

    alert_var = tk.BooleanVar(master=root, value=not _settings.get("alert_suppressed", False))
    tk.Checkbutton(root, text="Show alert when temp goes red",
                   variable=alert_var).grid(row=6, column=0, columnspan=2, pady=(4, 0))

    def read_fields():
        green = int(green_var.get())
        orange = int(orange_var.get())
        interval = int(interval_var.get())
        if not (0 < green < orange < 150):
            raise ValueError
        if not (5 <= interval <= 3600):
            raise ValueError
        return green, orange, interval

    def apply():
        try:
            green, orange, interval = read_fields()
        except ValueError:
            messagebox.showerror("Invalid input",
                "• Green must be less than Orange\n"
                "• Both must be between 0–150°C\n"
                "• Interval must be 5–3600 sec")
            return
        _settings["sensor"] = sensor_var.get()
        _settings["green_max"] = green
        _settings["orange_max"] = orange
        _settings["interval"] = interval
        _settings["icon_size"] = icon_size_var.get()
        _settings["font_size"] = font_size_var.get()
        _settings["alert_suppressed"] = not alert_var.get()
        save_settings()
        _apply_update(icon)

    def save():
        apply()
        root.destroy()

    frame = tk.Frame(root)
    frame.grid(row=7, column=0, columnspan=2, pady=12)
    tk.Button(frame, text="Save", width=9, command=save).pack(side="left", padx=4)
    tk.Button(frame, text="Apply", width=9, command=apply).pack(side="left", padx=4)
    tk.Button(frame, text="Cancel", width=9, command=root.destroy).pack(side="left", padx=4)

    root.mainloop()


# ── history graph ─────────────────────────────────────────────────────────────

_history_root = None


def open_history(icon, _item):
    global _history_root
    if _history_root is not None:
        try:
            _history_root.deiconify()
            _history_root.lift()
            _history_root.focus_force()
            return
        except Exception:
            _history_root = None
    threading.Thread(target=_show_history, daemon=True).start()


def _show_history():
    global _history_root
    try:
        _show_history_inner()
    except Exception:
        logging.error(traceback.format_exc())
    finally:
        _history_root = None


def _show_history_inner():
    global _history_root
    import tkinter as tk

    W, H = 700, 320
    PAD_L, PAD_R, PAD_T, PAD_B = 56, 20, 20, 48

    root = tk.Tk()
    root.title("CPU Temperature History")
    root.resizable(True, True)
    root.attributes("-topmost", True)
    root.configure(bg="#1e1e1e")

    # view state: seconds of data to show, and rightmost timestamp shown
    all_data = [(e[0], e[1], e[2] if len(e) > 2 else 0) for e in _history]
    initial_span = (all_data[-1][0] - all_data[0][0]) if len(all_data) >= 2 else HISTORY_HOURS * 3600
    view_dur = [max(initial_span, 60)]
    view_end = [all_data[-1][0] if all_data else time.time()]

    # ── toolbar ────────────────────────────────────────────────────────────────
    toolbar = tk.Frame(root, bg="#2a2a2a", height=30)
    toolbar.pack(fill="x", padx=0, pady=0)
    toolbar.pack_propagate(False)

    btn = dict(bg="#3a3a3a", fg="#cccccc", relief="flat", font=("Segoe UI", 9),
               padx=7, pady=2, activebackground="#555", activeforeground="white", cursor="hand2")

    def zoom_in():
        view_dur[0] = max(60, view_dur[0] // 2)
        draw()

    def zoom_out():
        d = [(e[0], e[1], e[2] if len(e) > 2 else 0) for e in _history]
        total = (d[-1][0] - d[0][0]) if len(d) >= 2 else HISTORY_HOURS * 3600
        view_dur[0] = min(view_dur[0] * 2, max(total, 60))
        draw()

    def scroll_left():
        d = [(e[0], e[1], e[2] if len(e) > 2 else 0) for e in _history]
        min_end = d[0][0] + view_dur[0] if d else view_dur[0]
        view_end[0] = max(view_end[0] - view_dur[0] * 0.25, min_end)
        draw()

    def scroll_right():
        d = [(e[0], e[1], e[2] if len(e) > 2 else 0) for e in _history]
        max_end = d[-1][0] if d else time.time()
        view_end[0] = min(view_end[0] + view_dur[0] * 0.25, max_end)
        draw()

    def reset_view():
        d = [(e[0], e[1], e[2] if len(e) > 2 else 0) for e in _history]
        span = (d[-1][0] - d[0][0]) if len(d) >= 2 else HISTORY_HOURS * 3600
        view_dur[0] = max(span, 60)
        view_end[0] = d[-1][0] if d else time.time()
        draw()

    tk.Button(toolbar, text="−", command=zoom_out, **btn).pack(side="left", padx=(6, 1), pady=3)
    tk.Button(toolbar, text="+", command=zoom_in, **btn).pack(side="left", padx=1, pady=3)
    tk.Label(toolbar, text="zoom", bg="#2a2a2a", fg="#555", font=("Segoe UI", 8)).pack(side="left", padx=(3, 10))
    tk.Button(toolbar, text="◀", command=scroll_left, **btn).pack(side="left", padx=1, pady=3)
    tk.Button(toolbar, text="▶", command=scroll_right, **btn).pack(side="left", padx=1, pady=3)
    tk.Label(toolbar, text="scroll", bg="#2a2a2a", fg="#555", font=("Segoe UI", 8)).pack(side="left", padx=(3, 10))
    tk.Button(toolbar, text="↺", command=reset_view, **btn).pack(side="left", padx=1, pady=3)

    range_lbl = tk.Label(toolbar, text="", bg="#2a2a2a", fg="#888", font=("Segoe UI", 8))
    range_lbl.pack(side="right", padx=10)

    # ── canvas ─────────────────────────────────────────────────────────────────
    canvas = tk.Canvas(root, width=W, height=H, bg="#1e1e1e", highlightthickness=0)
    canvas.pack(fill="both", expand=True, padx=8, pady=(4, 8))

    def on_wheel(e):
        if e.delta > 0:
            scroll_right()
        else:
            scroll_left()

    canvas.bind("<MouseWheel>", on_wheel)
    root.bind("+", lambda e: zoom_in())
    root.bind("=", lambda e: zoom_in())
    root.bind("-", lambda e: zoom_out())
    root.bind("<Left>",  lambda e: scroll_left())
    root.bind("<Right>", lambda e: scroll_right())
    root.bind("0", lambda e: reset_view())

    def draw():
        canvas.delete("all")
        cw = canvas.winfo_width() or W
        ch = canvas.winfo_height() or H
        pl, pr, pt, pb = PAD_L, PAD_R, PAD_T, PAD_B
        gw = cw - pl - pr
        gh = ch - pt - pb

        all_d = [(e[0], e[1], e[2] if len(e) > 2 else 0) for e in _history]
        green_max  = _settings["green_max"]
        orange_max = _settings["orange_max"]

        # clamp view_end to actual data range
        if all_d:
            view_end[0] = min(view_end[0], all_d[-1][0])
        t_end   = view_end[0]
        t_start = t_end - view_dur[0]
        data = [e for e in all_d if t_start <= e[0] <= t_end]


        # update range label
        fmt = "%H:%M" if view_dur[0] >= 3600 else "%H:%M:%S"
        dur_str = (f"{int(view_dur[0]//3600)}h {int((view_dur[0]%3600)//60)}m"
                   if view_dur[0] >= 3600 else f"{int(view_dur[0]//60)}m {int(view_dur[0]%60)}s")
        range_lbl.config(text=f"{time.strftime(fmt, time.localtime(t_start))} – "
                              f"{time.strftime(fmt, time.localtime(t_end))}  ({dur_str})")

        y_max_val = orange_max + 20
        if data:
            y_max_val = max(y_max_val, max(t for _, t, _ in data) + 10)
        y_max_val = (int(y_max_val) // 10 + 1) * 10
        y_min_val = 0

        def to_x(ts):
            span = t_end - t_start or 1
            return pl + int((ts - t_start) / span * gw)

        def to_y(val):
            span = y_max_val - y_min_val or 1
            return pt + gh - int((val - y_min_val) / span * gh)

        # zone bands
        for lo, hi, colour in [(0, green_max, "#1a3a1a"),
                                (green_max, orange_max, "#3a2e10"),
                                (orange_max, y_max_val, "#3a1212")]:
            y1 = to_y(min(hi, y_max_val))
            y2 = to_y(max(lo, y_min_val))
            if y1 < y2:
                canvas.create_rectangle(pl, y1, pl + gw, y2, fill=colour, outline="")

        # grid + Y labels
        for v in range(y_min_val, y_max_val + 1, 10):
            y = to_y(v)
            canvas.create_line(pl, y, pl + gw, y, fill="#333333", dash=(4, 4))
            canvas.create_text(pl - 6, y, text=f"{v}°", anchor="e",
                               fill="#aaaaaa", font=("Segoe UI", 8))

        # threshold lines
        canvas.create_line(pl, to_y(green_max),  pl + gw, to_y(green_max),
                           fill="#2d8a2d", dash=(6, 3), width=1)
        canvas.create_line(pl, to_y(orange_max), pl + gw, to_y(orange_max),
                           fill="#cc7700", dash=(6, 3), width=1)

        # axes
        canvas.create_line(pl, pt, pl, pt + gh, fill="#555555", width=1)
        canvas.create_line(pl, pt + gh, pl + gw, pt + gh, fill="#555555", width=1)

        # fan strip — shown only when hardware exposes RPM
        fan_sy, fan_ey = pt + gh + 3, pt + gh + 11
        if _fan_detected:
            canvas.create_rectangle(pl, fan_sy, pl + gw, fan_ey, fill="#222222", outline="")
            canvas.create_text(pl - 6, (fan_sy + fan_ey) // 2, text="Fan",
                               anchor="e", fill="#666666", font=("Segoe UI", 7))

        if not data:
            canvas.create_text(cw // 2, ch // 2, text="No data in this time range",
                               fill="#777777", font=("Segoe UI", 11))
        else:
            pts = [(to_x(ts), to_y(t), t) for ts, t, _ in data]
            for i in range(len(pts) - 1):
                x1, y1, t1v = pts[i]
                x2, y2, t2v = pts[i + 1]
                avg = (t1v + t2v) / 2
                c = "#22cc22" if avg < green_max else "#ffaa00" if avg < orange_max else "#ff3333"
                canvas.create_line(x1, y1, x2, y2, fill=c, width=2, capstyle="round")

            if len(pts) == 1:
                x, y, tv = pts[0]
                c = "#22cc22" if tv < green_max else "#ffaa00" if tv < orange_max else "#ff3333"
                canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=c, outline="")

            if _fan_detected:
                for i in range(len(data) - 1):
                    ts1, _, rpm1 = data[i]
                    ts2, _, rpm2 = data[i + 1]
                    if (rpm1 + rpm2) / 2 >= FAN_ACTIVE_RPM:
                        canvas.create_rectangle(to_x(ts1), fan_sy, to_x(ts2), fan_ey,
                                                fill="#00aacc", outline="")

            # X-axis labels (up to 6)
            n = min(6, len(data))
            fmt_x = "%H:%M" if view_dur[0] >= 3600 else "%H:%M:%S"
            for idx in [int(i * (len(data) - 1) / max(n - 1, 1)) for i in range(n)]:
                ts, _, _ = data[idx]
                canvas.create_text(to_x(ts), pt + gh + 18, anchor="n",
                                   text=time.strftime(fmt_x, time.localtime(ts)),
                                   fill="#888888", font=("Segoe UI", 7))

            _, last_temp, last_rpm = data[-1]
            fan_str = f"  Fan: {int(last_rpm)} RPM" if _fan_detected else ""
            canvas.create_text(cw - pr, pt - 4, anchor="ne",
                               text=f"Now: {int(round(last_temp))}°C{fan_str}",
                               fill="#dddddd", font=("Segoe UI", 9, "bold"))

        canvas.create_text(pl + gw // 2, pt // 2, text="CPU Temperature — last 12 hours",
                           fill="#cccccc", font=("Segoe UI", 10))

    def refresh_loop():
        if not root.winfo_exists():
            return
        # auto-advance view_end when user is at the live edge
        d = [(e[0], e[1], e[2] if len(e) > 2 else 0) for e in _history]
        if d and (d[-1][0] - view_end[0]) < 30:
            view_end[0] = d[-1][0]
        try:
            draw()
        except Exception:
            logging.error(traceback.format_exc())
        root.after(2000, refresh_loop)

    _history_root = root
    root.after(50, refresh_loop)
    root.mainloop()


# ── tray menu ──────────────────────────────────────────────────────────────────

def refresh_now(icon, _item):
    threading.Thread(target=lambda: _animated_refresh(icon), daemon=True).start()


def _set_interval(secs):
    def handler(icon, _item):
        _settings["interval"] = secs
        save_settings()
    return handler


def _interval_checked(secs):
    return lambda _item: _settings.get("interval") == secs


def _refresh_loop(icon, stop_event):
    while not stop_event.wait(_settings.get("interval", 15)):
        _apply_update(icon)


def on_quit(icon, _item):
    icon._stop_event.set()
    icon.stop()


def main():
    logging.error(f"Starting cpu-temp-tray v{VERSION}")
    load_settings()
    load_history()
    threading.Thread(target=_autostart_lhm, daemon=True).start()
    all_temps = fetch_all_temps()
    temp = get_cpu_temp(all_temps)

    interval_menu = pystray.Menu(*[
        pystray.MenuItem(
            f"{s} sec", _set_interval(s), checked=_interval_checked(s), radio=True
        )
        for s in INTERVAL_OPTIONS
    ])

    icon = pystray.Icon(
        "cpu_temp",
        make_icon(temp),
        title=_format_tooltip(temp, all_temps),
        menu=pystray.Menu(
            pystray.MenuItem("Refresh", refresh_now, default=True, visible=False),
            pystray.MenuItem("Refresh interval", interval_menu),
            pystray.MenuItem("History", open_history),
            pystray.MenuItem("Settings", open_settings),
            pystray.MenuItem("About", open_about),
            pystray.MenuItem("Quit", on_quit),
        ),
    )
    stop_event = threading.Event()
    icon._stop_event = stop_event
    threading.Thread(target=_refresh_loop, args=(icon, stop_event), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
