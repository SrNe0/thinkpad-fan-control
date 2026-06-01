#!/usr/bin/env python3
"""ThinkFan Control — Tray monitor + fan control for ThinkPad"""

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess, glob, threading, time, math, os
from collections import deque

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.ticker as ticker

# ── constants ─────────────────────────────────────────────────────────────────
HELPER      = "/usr/local/bin/thinkfan-set-level"
FAN_PROC    = "/proc/acpi/ibm/fan"
LEVELS      = ["0","1","2","3","4","5","6","7","auto","full-speed"]
HISTORY     = 150
CONFIG_DIR  = os.path.expanduser("~/.config/thinkfan-gui")
PROFILE_FILE = os.path.join(CONFIG_DIR, "current_profile")
TMP_CONF    = "/tmp/thinkfan-profile.conf"

BG      = "#1e1e2e"
BG2     = "#313244"
BG3     = "#45475a"
FG      = "#cdd6f4"
C_CPU   = "#f38ba8"
C_GPU   = "#89b4fa"
C_RPM   = "#a6e3a1"
C_GREEN = "#a6e3a1"
C_ORG   = "#fab387"
C_RED   = "#f38ba8"
C_GRID  = "#45475a"

# ── fan curve profiles ────────────────────────────────────────────────────────
_HEADER = """sensors:
  - hwmon: /sys/class/hwmon
    name: thinkpad
    indices: [1, 2]

fans:
  - tpacpi: /proc/acpi/ibm/fan

"""

PROFILE_CONFIGS = {
    "silencioso": _HEADER + """\
#                          CPU  GPU
levels:
  - speed: 0
    upper_limit:          [57,  54]

  - speed: 1
    lower_limit:          [55,  52]
    upper_limit:          [63,  60]

  - speed: 2
    lower_limit:          [61,  58]
    upper_limit:          [69,  66]

  - speed: 3
    lower_limit:          [67,  64]
    upper_limit:          [75,  72]

  - speed: 4
    lower_limit:          [73,  70]
    upper_limit:          [81,  78]

  - speed: 5
    lower_limit:          [79,  76]
    upper_limit:          [87,  84]

  - speed: 6
    lower_limit:          [85,  82]
    upper_limit:          [92,  89]

  - speed: 7
    lower_limit:          [90,  87]
""",
    "normal": _HEADER + """\
#                          CPU  GPU
levels:
  - speed: 0
    upper_limit:          [50,  47]

  - speed: 1
    lower_limit:          [48,  45]
    upper_limit:          [55,  52]

  - speed: 2
    lower_limit:          [53,  50]
    upper_limit:          [60,  57]

  - speed: 3
    lower_limit:          [58,  55]
    upper_limit:          [65,  62]

  - speed: 4
    lower_limit:          [63,  60]
    upper_limit:          [70,  67]

  - speed: 5
    lower_limit:          [68,  65]
    upper_limit:          [75,  72]

  - speed: 6
    lower_limit:          [73,  70]
    upper_limit:          [80,  77]

  - speed: 7
    lower_limit:          [78,  75]
""",
    "rendimiento": _HEADER + """\
#                          CPU  GPU
levels:
  - speed: 0
    upper_limit:          [43,  40]

  - speed: 1
    lower_limit:          [41,  38]
    upper_limit:          [48,  45]

  - speed: 2
    lower_limit:          [46,  43]
    upper_limit:          [53,  50]

  - speed: 3
    lower_limit:          [51,  48]
    upper_limit:          [58,  55]

  - speed: 4
    lower_limit:          [56,  53]
    upper_limit:          [63,  60]

  - speed: 5
    lower_limit:          [61,  58]
    upper_limit:          [68,  65]

  - speed: 6
    lower_limit:          [66,  63]
    upper_limit:          [73,  70]

  - speed: 7
    lower_limit:          [71,  68]
""",
    "turbo": _HEADER + """\
#                          CPU  GPU
levels:
  - speed: 0
    upper_limit:          [38,  35]

  - speed: 1
    lower_limit:          [36,  33]
    upper_limit:          [43,  40]

  - speed: 2
    lower_limit:          [41,  38]
    upper_limit:          [48,  45]

  - speed: 3
    lower_limit:          [46,  43]
    upper_limit:          [53,  50]

  - speed: 4
    lower_limit:          [51,  48]
    upper_limit:          [58,  55]

  - speed: 5
    lower_limit:          [56,  53]
    upper_limit:          [63,  60]

  - speed: 6
    lower_limit:          [61,  58]
    upper_limit:          [68,  65]

  - speed: 7
    lower_limit:          [66,  63]
""",
}

PROFILES = {
    #  key           label           color    description
    "silencioso":  ("Silencioso",   "#6c7086", "Fan apagado hasta 57°C — máximo silencio"),
    "normal":      ("Normal",       "#89b4fa", "Balance ruido/temperatura"),
    "rendimiento": ("Rendimiento",  C_ORG,     "Mantiene zona verde (CPU < 55°C)"),
    "turbo":       ("Turbo",        C_RED,     "Enfriamiento máximo, más ruidoso"),
}

# ── profile thresholds (CPU upper °C per level 0-7) ───────────────────────────
PROFILE_THRESHOLDS = {
    "silencioso":  [57, 63, 69, 75, 81, 87, 92, 999],
    "normal":      [50, 55, 60, 65, 70, 75, 80, 999],
    "rendimiento": [43, 48, 53, 58, 63, 68, 73, 999],
    "turbo":       [38, 43, 48, 53, 58, 63, 68, 999],
}

def compute_target(cpu, profile_key):
    thresholds = PROFILE_THRESHOLDS.get(profile_key, PROFILE_THRESHOLDS["rendimiento"])
    for level, upper in enumerate(thresholds):
        if cpu <= upper:
            return level
    return 7

# ── persistence ───────────────────────────────────────────────────────────────
def load_saved_profile():
    try:
        p = open(PROFILE_FILE).read().strip()
        return p if p in PROFILES else "rendimiento"
    except OSError:
        return "rendimiento"

def save_profile(name):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(PROFILE_FILE, "w") as f:
        f.write(name)

# ── low-level helpers ─────────────────────────────────────────────────────────
def find_hwmon(name):
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(f"{d}/name") as f:
                if f.read().strip() == name:
                    return d
        except OSError:
            pass
    return None

def read_temp(path):
    try:
        return int(open(path).read().strip()) // 1000
    except OSError:
        return None

def read_fan():
    out = {}
    try:
        for line in open(FAN_PROC):
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out

def thinkfan_active():
    r = subprocess.run(["systemctl", "is-active", "thinkfan"],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active"

def run_helper(*args):
    try:
        r = subprocess.run(["sudo", HELPER] + list(args),
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return r.stderr.strip() or "Error desconocido"
    except Exception as e:
        return str(e)
    return None

def write_tmp_config(profile_name):
    try:
        with open(TMP_CONF, "w") as f:
            f.write(PROFILE_CONFIGS[profile_name])
        return True
    except OSError as e:
        return str(e)

def temp_color(t):
    if t is None: return "#888888"
    if t < 55:    return C_GREEN
    if t < 70:    return C_ORG
    return C_RED

def make_tray_icon(cpu=None, profile="rendimiento"):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if cpu is None or cpu < 55:
        fill = (76, 175, 80, 230)
    elif cpu < 70:
        fill = (255, 152, 0, 230)
    else:
        fill = (244, 67, 54, 230)
    d.ellipse([2, 2, 62, 62], fill=fill)
    cx, cy = 32, 32
    for deg in (0, 120, 240):
        a = math.radians(deg)
        p0 = (cx + 7*math.cos(a),     cy + 7*math.sin(a))
        p1 = (cx + 24*math.cos(a+.9), cy + 24*math.sin(a+.9))
        p2 = (cx + 24*math.cos(a-.9), cy + 24*math.sin(a-.9))
        d.polygon([p0, p1, p2], fill=(255, 255, 255, 190))
    d.ellipse([27, 27, 37, 37], fill=(255, 255, 255, 220))
    return img

# ── shared state ──────────────────────────────────────────────────────────────
class State:
    def __init__(self):
        self.cpu = self.gpu = self.rpm = None
        self.level = "—"
        self.active = False
        self.h_cpu = deque(maxlen=HISTORY)
        self.h_gpu = deque(maxlen=HISTORY)
        self.h_rpm = deque(maxlen=HISTORY)
        self._lock = threading.Lock()

    def update(self, cpu, gpu, rpm, level, active):
        with self._lock:
            self.cpu, self.gpu, self.rpm = cpu, gpu, rpm
            self.level, self.active = level, active
            self.h_cpu.append(cpu or 0)
            self.h_gpu.append(gpu or 0)
            self.h_rpm.append(rpm or 0)

    def snapshot(self):
        with self._lock:
            return (self.cpu, self.gpu, self.rpm, self.level, self.active,
                    list(self.h_cpu), list(self.h_gpu), list(self.h_rpm))

# ── data collector ────────────────────────────────────────────────────────────
class Collector(threading.Thread):
    def __init__(self, state, hwmon):
        super().__init__(daemon=True)
        self.state, self.hwmon = state, hwmon

    def run(self):
        while True:
            fan = read_fan()
            cpu = read_temp(f"{self.hwmon}/temp1_input") if self.hwmon else None
            gpu = read_temp(f"{self.hwmon}/temp2_input") if self.hwmon else None
            try:
                rpm = int(fan.get("speed", 0))
            except ValueError:
                rpm = 0
            self.state.update(cpu, gpu, rpm, fan.get("level", "—"), thinkfan_active())
            time.sleep(2)

# ── smooth fan controller ─────────────────────────────────────────────────────
class SmoothController(threading.Thread):
    """Steps fan level one notch at a time toward the profile target."""
    STEP_UP   = 2.0   # seconds between upward steps (fast to cool)
    STEP_DOWN = 4.0   # seconds between downward steps (slow to spin down)
    POLL      = 2.0   # seconds between checks when already at target

    def __init__(self, state, profile_getter):
        super().__init__(daemon=True)
        self._stopper = threading.Event()
        self.state = state
        self.profile_getter = profile_getter
        self.current_level = 0

    def stop(self):
        self._stopper.set()

    def run(self):
        self._stopper.clear()
        try:
            self.current_level = int(read_fan().get("level", "0"))
        except ValueError:
            self.current_level = 0

        while not self._stopper.is_set():
            cpu, *_ = self.state.snapshot()
            if cpu is not None:
                target = compute_target(cpu, self.profile_getter())
                if self.current_level < target:
                    self.current_level += 1
                    run_helper("level", str(self.current_level))
                    self._stopper.wait(self.STEP_UP)
                elif self.current_level > target:
                    self.current_level -= 1
                    run_helper("level", str(self.current_level))
                    self._stopper.wait(self.STEP_DOWN)
                else:
                    self._stopper.wait(self.POLL)
            else:
                self._stopper.wait(self.POLL)

# ── main window ───────────────────────────────────────────────────────────────
class MainWindow(tk.Tk):
    def __init__(self, state, on_quit):
        super().__init__()
        self.state   = state
        self.on_quit = on_quit
        self.title("ThinkFan Control")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW",
                      self._hide if HAS_TRAY else on_quit)
        self._smooth = None
        self._apply_theme()
        self._build()
        self._tick()
        self.after(300, self._to_auto)

    def _hide(self): self.withdraw()
    def show(self):
        self.deiconify(); self.lift(); self.focus_force()

    def _apply_theme(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".",                 background=BG,  foreground=FG)
        s.configure("TLabelframe",       background=BG,  foreground=FG,
                                         bordercolor=BG3)
        s.configure("TLabelframe.Label", background=BG,  foreground=FG)
        s.configure("TRadiobutton",      background=BG,  foreground=FG)
        s.configure("TButton",           background=BG2, foreground=FG,
                                         borderwidth=0,  relief="flat")
        s.map("TButton",     background=[("active", BG3), ("disabled", BG)])
        s.map("TRadiobutton", background=[("active", BG)])

    def _build(self):
        P = dict(padx=14, pady=5)

        # ── graph ─────────────────────────────────────────────────────────────
        gf = tk.Frame(self, bg=BG)
        gf.pack(fill="x", padx=14, pady=(14, 4))

        fig = Figure(figsize=(5.2, 3.2), dpi=96, facecolor=BG)
        fig.subplots_adjust(left=0.12, right=0.97, top=0.93, bottom=0.04, hspace=0.35)
        self.ax_t = fig.add_subplot(211)
        self.ax_r = fig.add_subplot(212)

        for ax, title in ((self.ax_t, "Temperatura  (últimos 5 min)"),
                          (self.ax_r, "Velocidad ventilador")):
            ax.set_facecolor(BG2)
            ax.set_title(title, color=FG, fontsize=7.5, pad=3)
            ax.tick_params(colors=FG, labelsize=7, length=2)
            ax.grid(color=C_GRID, linewidth=0.4, linestyle="--", alpha=0.6)
            ax.set_xticklabels([])
            for sp in ax.spines.values(): sp.set_color(BG3)

        self.ax_t.set_ylabel("°C",  color=FG, fontsize=8)
        self.ax_t.set_ylim(25, 105)
        self.ax_t.yaxis.set_major_locator(ticker.MultipleLocator(10))
        self.ax_r.set_ylabel("RPM", color=FG, fontsize=8)
        self.ax_r.set_ylim(0, 6000)
        self.ax_r.yaxis.set_major_locator(ticker.MultipleLocator(1000))

        self.l_cpu, = self.ax_t.plot([], [], color=C_CPU, lw=1.5, label="CPU")
        self.l_gpu, = self.ax_t.plot([], [], color=C_GPU, lw=1.5, label="GPU")
        self.l_rpm, = self.ax_r.plot([], [], color=C_RPM, lw=1.5)
        self.ax_t.legend(loc="upper left", fontsize=7,
                         facecolor=BG2, labelcolor=FG, framealpha=0.9, edgecolor=BG3)

        self.canvas = FigureCanvasTkAgg(fig, master=gf)
        w = self.canvas.get_tk_widget()
        w.configure(bg=BG, highlightthickness=0)
        w.pack()

        # ── current readings ───────────────────────────────────────────────────
        vrow = tk.Frame(self, bg=BG)
        vrow.pack(fill="x", padx=14, pady=2)
        self.lbl_cpu = tk.Label(vrow, font=("monospace", 13, "bold"), bg=BG)
        self.lbl_cpu.pack(side="left", padx=8)
        self.lbl_gpu = tk.Label(vrow, font=("monospace", 13, "bold"), bg=BG)
        self.lbl_gpu.pack(side="left", padx=8)
        self.lbl_rpm = tk.Label(vrow, font=("monospace", 11), bg=BG, fg=FG)
        self.lbl_rpm.pack(side="left", padx=8)
        self.lbl_lvl = tk.Label(vrow, font=("monospace", 11), bg=BG, fg=FG)
        self.lbl_lvl.pack(side="left", padx=8)
        self.lbl_svc = tk.Label(vrow, font=("", 10), bg=BG)
        self.lbl_svc.pack(side="right", padx=10)

        # ── profiles ──────────────────────────────────────────────────────────
        pf = ttk.LabelFrame(self, text="Perfil de ventilación")
        pf.pack(fill="x", **P)

        self.profile_var = tk.StringVar(value=load_saved_profile())
        self._profile_dots = {}

        grid = tk.Frame(pf, bg=BG)
        grid.pack(fill="x", padx=8, pady=6)

        for col, (key, (label, color, desc)) in enumerate(PROFILES.items()):
            cell = tk.Frame(grid, bg=BG)
            cell.grid(row=0, column=col, padx=6)

            dot = tk.Label(cell, text="●", fg=color, bg=BG, font=("", 11))
            dot.pack(side="left")
            self._profile_dots[key] = dot

            rb = tk.Radiobutton(
                cell, text=label,
                variable=self.profile_var, value=key,
                command=lambda k=key: self._select_profile(k),
                bg=BG, fg=FG, selectcolor=BG2,
                activebackground=BG, activeforeground=FG,
                font=("", 10), relief="flat", bd=0,
            )
            rb.pack(side="left")

        self.lbl_profile_desc = tk.Label(pf, text="", font=("", 8),
                                          fg="#6c7086", bg=BG)
        self.lbl_profile_desc.pack(anchor="w", padx=10, pady=(0, 4))
        self._update_profile_desc(self.profile_var.get())

        # ── mode ──────────────────────────────────────────────────────────────
        mf = ttk.LabelFrame(self, text="Modo de control")
        mf.pack(fill="x", **P)
        self.mode = tk.StringVar(value="auto")
        ttk.Radiobutton(mf, text="Automático  (thinkfan gestiona la curva)",
                        variable=self.mode, value="auto",
                        command=self._to_auto).pack(anchor="w", padx=10, pady=3)
        ttk.Radiobutton(mf, text="Manual  (control directo del nivel)",
                        variable=self.mode, value="manual",
                        command=self._to_manual).pack(anchor="w", padx=10, pady=3)

        # ── level buttons ─────────────────────────────────────────────────────
        lf = ttk.LabelFrame(self, text="Nivel manual")
        lf.pack(fill="x", **P)
        r1 = tk.Frame(lf, bg=BG); r1.pack(pady=4)
        r2 = tk.Frame(lf, bg=BG); r2.pack(pady=(0, 6))
        self.btns = []
        for lvl in LEVELS[:8]:
            b = ttk.Button(r1, text=lvl, width=4,
                           command=lambda l=lvl: self._set_level(l))
            b.pack(side="left", padx=2)
            self.btns.append(b)
        for lvl in LEVELS[8:]:
            b = ttk.Button(r2, text=lvl, width=11,
                           command=lambda l=lvl: self._set_level(l))
            b.pack(side="left", padx=4)
            self.btns.append(b)
        self._btns("disabled")

        # ── status bar ────────────────────────────────────────────────────────
        hint = "Al cerrar se minimiza a la bandeja" if HAS_TRAY else ""
        self.status = tk.Label(self, text=hint, font=("", 8),
                               fg="#6c7086", bg=BG, anchor="w")
        self.status.pack(fill="x", padx=14, pady=(2, 8))

    # ── profile logic ─────────────────────────────────────────────────────────
    def _update_profile_desc(self, key):
        _, _, desc = PROFILES[key]
        self.lbl_profile_desc.config(text=desc)

    def _select_profile(self, key):
        save_profile(key)
        self._update_profile_desc(key)
        if write_tmp_config(key) is True:
            threading.Thread(target=lambda: run_helper("write-config"),
                             daemon=True).start()
        label, _, _ = PROFILES[key]
        self.status.config(text=f"Perfil  {label}")

    # ── mode actions ──────────────────────────────────────────────────────────
    def _btns(self, s):
        for b in self.btns: b.configure(state=s)

    def _run_async_cmd(self, *args):
        def task():
            err = run_helper(*args)
            msg = f"Error: {err}" if err else (
                "Al cerrar se minimiza a la bandeja" if HAS_TRAY else "")
            self.after(0, lambda: self.status.config(text=msg))
        threading.Thread(target=task, daemon=True).start()

    def _stop_smooth(self):
        if self._smooth and self._smooth.is_alive():
            self._smooth.stop()
        self._smooth = None

    def _to_auto(self):
        self._btns("disabled")
        self._stop_smooth()
        def task():
            run_helper("stop-service")
            self._smooth = SmoothController(self.state, lambda: self.profile_var.get())
            self._smooth.start()
        threading.Thread(target=task, daemon=True).start()

    def _to_manual(self):
        self._stop_smooth()
        self._btns("normal")
        self.status.config(text="Modo manual — elige un nivel")
        self._run_async_cmd("stop-service")

    def _set_level(self, lvl):
        self.status.config(text=f"Nivel {lvl}…")
        self._run_async_cmd("level", lvl)

    # ── refresh loop ──────────────────────────────────────────────────────────
    def _tick(self):
        cpu, gpu, rpm, lvl, active, h_cpu, h_gpu, h_rpm = self.state.snapshot()
        smooth_ok = self._smooth is not None and self._smooth.is_alive()
        if smooth_ok:
            lvl = str(self._smooth.current_level)

        cpu_txt = f"{cpu}°C" if cpu is not None else "--°C"
        gpu_txt = f"{gpu}°C" if gpu is not None else "--°C"
        self.lbl_cpu.config(text=f"CPU  {cpu_txt:>6}", fg=temp_color(cpu))
        self.lbl_gpu.config(text=f"GPU  {gpu_txt:>6}", fg=temp_color(gpu))
        self.lbl_rpm.config(text=f"{rpm or '—':>4} RPM")
        self.lbl_lvl.config(text=f"Nivel  {lvl}")
        self.lbl_svc.config(
            text="● auto suave" if smooth_ok else ("● thinkfan" if active else "● inactivo"),
            fg=C_GREEN if (smooth_ok or active) else C_RED)

        expected = "auto" if smooth_ok else "manual"
        if self.mode.get() != expected:
            self.mode.set(expected)
            self._btns("disabled" if smooth_ok else "normal")

        n = len(h_cpu)
        if n > 1:
            xs = list(range(n))
            self.l_cpu.set_data(xs, h_cpu)
            self.l_gpu.set_data(xs, h_gpu)
            self.l_rpm.set_data(xs, h_rpm)
            self.ax_t.set_xlim(0, n - 1)
            self.ax_r.set_xlim(0, n - 1)
            self.canvas.draw_idle()

        self.after(2000, self._tick)

# ── system tray ───────────────────────────────────────────────────────────────
class Tray:
    def __init__(self, window, state, on_quit):
        self.window, self.state, self.on_quit = window, state, on_quit
        menu = pystray.Menu(
            pystray.MenuItem("Mostrar ventana", self._show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir", self._quit),
        )
        self.icon = pystray.Icon("thinkfan", make_tray_icon(),
                                 "ThinkFan Control", menu)

    def _show(self, *_): self.window.after(0, self.window.show)
    def _quit(self, *_):
        self.icon.stop()
        self.window.after(0, self.on_quit)

    def start(self):
        def updater():
            while True:
                cpu, *_ = self.state.snapshot()
                profile = load_saved_profile()
                label, _, _ = PROFILES[profile]
                self.icon.icon  = make_tray_icon(cpu, profile)
                self.icon.title = f"ThinkFan  |  {label}  |  CPU {cpu or '--'}°C"
                time.sleep(5)
        threading.Thread(target=updater, daemon=True).start()
        self.icon.run_detached()

# ── entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if HAS_TRAY:
        try:
            import gi
            gi.require_version("GLib", "2.0")
            from gi.repository import GLib
            threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
        except Exception:
            pass

    hwmon = find_hwmon("thinkpad")
    state = State()
    Collector(state, hwmon).start()

    window = MainWindow(state, on_quit=lambda: window.destroy())

    if HAS_TRAY:
        window.withdraw()
        Tray(window, state, on_quit=lambda: window.destroy()).start()

    window.mainloop()
