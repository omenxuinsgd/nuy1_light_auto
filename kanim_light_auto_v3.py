"""
Kanim Light Auto — Monitor kamera Windows & kontrol LED strip via Arduino.

Fitur:
- Jalan sebagai aplikasi systray (pystray)
- Auto-start saat Windows nyala (via Registry Run, HKCU)
- Single instance (Windows Named Mutex)
- Log ke file kanim_light_auto.log
- Deteksi semua aplikasi (UWP + desktop/browser) via Registry rekursif
- Menu pengaturan COM port & brightness (disimpan di kanim_light_config.json)
- Global hotkey:
    Alt + 1  →  ALLON  (nyala max)
    Alt + 2  →  ALLOFF (mati)
    Alt + 3  →  DIM:9,<+step> (tambah kecerahan)
    Alt + 4  →  DIM:9,<-step> (kurangi kecerahan)
- Reset koneksi paksa untuk port yang nyangkut (Access Denied)

Perintah:
  python kanim_light_auto.py              → Jalankan aplikasi (systray)
  python kanim_light_auto.py --install    → Pasang auto-start ke Windows
  python kanim_light_auto.py --uninstall  → Hapus auto-start
  python kanim_light_auto.py --status     → Cek status auto-start
  python kanim_light_auto.py --ports      → Daftar COM port
  python kanim_light_auto.py --set-port COM3 → Set COM port
  python kanim_light_auto.py --debug      → Jalankan dengan debug registry
  python kanim_light_auto.py --reset-port → Reset koneksi port sekali (CLI)
  python kanim_light_auto.py --help       → Bantuan
"""
import os
import sys
import json
import time
import ctypes
import subprocess
import threading
import winreg
import tkinter as tk
from tkinter import ttk, messagebox

from datetime import datetime
from PIL import Image, ImageDraw
import pystray
import serial
import serial.tools.list_ports

try:
    import keyboard  # global hotkey
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False

# ================= KONFIGURASI DEFAULT =================
DEFAULT_CONFIG = {
    "com_port": "COM12",
    "baud_rate": 9600,
    "check_interval": 2,
    "led_pin": 9,
    "brightness": 255,
    "brightness_step": 25,
}

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"

AUTOSTART_APP_NAME = "KanimLightAuto"
AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "kanim_light_auto.log")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "kanim_light_config.json")

DEBUG_REGISTRY = False
# ========================================================

APP_NAME_MAP = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Mozilla Firefox",
    "brave.exe": "Brave Browser",
    "opera.exe": "Opera Browser",
    "vivaldi.exe": "Vivaldi Browser",
    "zoom.exe": "Zoom",
    "teams.exe": "Microsoft Teams",
    "msteams.exe": "Microsoft Teams",
    "whatsapp.exe": "WhatsApp Desktop",
    "obs64.exe": "OBS Studio",
    "obs32.exe": "OBS Studio",
    "obsbot_center.exe": "OBSBOT Center",
    "skype.exe": "Skype",
    "discord.exe": "Discord",
    "slack.exe": "Slack",
}

BROWSER_KEYWORDS = ["chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "iexplore"]


# ================== CONFIG MANAGEMENT ==================
def load_config():
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    except Exception as e:
        log(f"⚠️ Gagal membaca config, pakai default: {e}")
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception as e:
        log(f"❌ Gagal menyimpan config: {e}")
        return False


CONFIG = load_config()


# ================== LOGGING ==================
def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_debug(message):
    if DEBUG_REGISTRY:
        log(f"[DEBUG] {message}")


# ================== AUTO-START ==================
def _get_pythonw_path():
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(pythonw):
        return pythonw
    return sys.executable


def _get_launch_command():
    pythonw = _get_pythonw_path()
    script = os.path.abspath(sys.argv[0])
    return f'"{pythonw}" "{script}"'


def autostart_install():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE)
        cmd = _get_launch_command()
        winreg.SetValueEx(key, AUTOSTART_APP_NAME, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        print(f"✅ Auto-start terpasang:\n   {cmd}")
        return True
    except Exception as e:
        print(f"❌ Gagal memasang auto-start: {e}")
        return False


def autostart_uninstall():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, AUTOSTART_APP_NAME)
            print("✅ Auto-start dihapus.")
        except FileNotFoundError:
            print("ℹ️  Auto-start belum terpasang.")
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"❌ Gagal menghapus auto-start: {e}")
        return False


def autostart_is_installed():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_READ)
        try:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_APP_NAME)
            winreg.CloseKey(key)
            return value
        except FileNotFoundError:
            winreg.CloseKey(key)
            return None
    except Exception:
        return None


def ensure_autostart():
    existing = autostart_is_installed()
    if existing:
        log(f"✅ Auto-start sudah terpasang: {existing}")
    else:
        log("🔧 Memasang auto-start...")
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_SET_VALUE)
            cmd = _get_launch_command()
            winreg.SetValueEx(key, AUTOSTART_APP_NAME, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            log(f"✅ Auto-start terpasang: {cmd}")
        except Exception as e:
            log(f"❌ Gagal memasang auto-start: {e}")


# ================== SINGLE INSTANCE ==================
def is_already_running():
    mutex_name = "Global\\KanimLightAutoMutex"
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    last_error = kernel32.GetLastError()
    if last_error == 183:
        return True
    is_already_running._handle = handle
    return False


# ================== PORT RESET / FORCE DISCONNECT ==================
def force_kill_other_python():
    """
    Kill proses Python lain (selain proses ini sendiri) yang mungkin
    masih memegang handle ke COM port.
    """
    killed = 0
    my_pid = os.getpid()
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        lines = []
        for name in ("python.exe", "pythonw.exe"):
            try:
                r = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5, creationflags=flags
                )
                for line in r.stdout.strip().splitlines():
                    if line and "INFO:" not in line:
                        lines.append(line)
            except Exception:
                pass
        for line in lines:
            try:
                parts = line.strip('"').split('","')
                if len(parts) < 2:
                    continue
                pid = int(parts[1])
                if pid == my_pid:
                    continue
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=3, creationflags=flags
                )
                killed += 1
            except Exception:
                continue
    except Exception as e:
        log(f"force_kill_other_python error: {e}")
    if killed:
        log(f"🔪 {killed} proses Python lain di-kill.")
    return killed


def force_reset_port(port_name, baud_rate, max_attempts=5):
    """
    Paksa reset / bebaskan port serial yang nyangkut (Access Denied).
    Return True jika berhasil membuka & menutup port.
    """
    log(f"🔄 Force reset port {port_name} @ {baud_rate}...")

    force_kill_other_python()
    time.sleep(0.5)

    for attempt in range(1, max_attempts + 1):
        try:
            s = serial.Serial()
            s.port = port_name
            s.baudrate = baud_rate
            s.timeout = 0.1
            s.write_timeout = 0.1
            s.dtr = False
            s.rts = False
            s.open()

            for _ in range(3):
                s.dtr = True
                s.rts = True
                time.sleep(0.05)
                s.dtr = False
                s.rts = False
                time.sleep(0.05)

            s.close()
            log(f"✅ Port {port_name} berhasil di-reset (attempt {attempt}).")
            return True
        except PermissionError as e:
            log(f"⚠️ Attempt {attempt}: Access Denied — {e}")
            delay = 0.5 * attempt
            log(f"   Menunggu {delay:.1f}s sebelum retry...")
            time.sleep(delay)
        except Exception as e:
            log(f"⚠️ Attempt {attempt}: {e}")
            time.sleep(0.5)

    log(f"❌ Gagal reset port {port_name} setelah {max_attempts} attempt.")
    return False


def robust_open_port(port_name, baud_rate, timeout=1, max_retries=3):
    """
    Buka port serial dengan retry & force reset otomatis saat Access Denied.
    """
    for attempt in range(1, max_retries + 1):
        try:
            s = serial.Serial(port_name, baud_rate, timeout=timeout)
            return s
        except PermissionError:
            log(f"⚠️ Access Denied pada {port_name} (attempt {attempt}/{max_retries}), "
                f"mencoba force reset...")
            if force_reset_port(port_name, baud_rate):
                try:
                    s = serial.Serial(port_name, baud_rate, timeout=timeout)
                    return s
                except Exception as e2:
                    log(f"   Masih gagal setelah reset: {e2}")
            time.sleep(1)
        except Exception as e:
            log(f"⚠️ Gagal buka {port_name} (attempt {attempt}/{max_retries}): {e}")
            time.sleep(1)
    return None


# ================== HELPERS ==================
def prettify_app_name(raw_name):
    lower_name = raw_name.lower()
    for key, pretty in APP_NAME_MAP.items():
        if key in lower_name:
            return pretty
    parts = raw_name.split("#")
    last_part = parts[-1] if parts else raw_name
    if "_" in last_part and "!" not in last_part:
        last_part = last_part.split("_")[0]
    return last_part


def is_browser(raw_name):
    lower_name = raw_name.lower()
    return any(keyword in lower_name for keyword in BROWSER_KEYWORDS)


def list_serial_ports():
    return [p.device for p in serial.tools.list_ports.comports()]


def connect_arduino(port_name, baud_rate, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            arduino = robust_open_port(port_name, baud_rate, timeout=1, max_retries=1)
            if arduino is None:
                log(f"Percobaan {attempt} gagal, mencoba force reset...")
                if force_reset_port(port_name, baud_rate):
                    arduino = robust_open_port(port_name, baud_rate, timeout=1, max_retries=1)
                if arduino is None:
                    if attempt < max_retries:
                        time.sleep(3)
                    continue

            time.sleep(2)
            try:
                while arduino.in_waiting > 0:
                    arduino.readline()
            except Exception:
                pass
            log(f"Terhubung ke Arduino di {port_name} (Percobaan {attempt}).")
            return arduino
        except serial.SerialException as e:
            log(f"Percobaan {attempt} gagal: {e}")
            if attempt < max_retries:
                time.sleep(3)
    return None


def send_command(arduino, cmd):
    try:
        full_cmd = cmd + "\n"
        arduino.write(full_cmd.encode('ascii'))
        arduino.flush()
        log(f"Kirim: {repr(full_cmd)}")
        time.sleep(0.3)
        while arduino.in_waiting > 0:
            resp = arduino.readline().decode('ascii', errors='ignore').strip()
            if resp:
                log(f"Arduino: {resp}")
    except Exception as e:
        log(f"Gagal kirim ke Arduino: {e}")


# ================== REGISTRY SCANNER ==================
def _read_key_values(base_path):
    start_time = 0
    stop_time = 0
    has_values = False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base_path, 0, winreg.KEY_READ)
        try:
            start_time, _ = winreg.QueryValueEx(key, "LastUsedTimeStart")
            stop_time, _ = winreg.QueryValueEx(key, "LastUsedTimeStop")
            has_values = True
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        pass
    except OSError as e:
        log_debug(f"OSError buka key {base_path}: {e}")
    return start_time, stop_time, has_values


def scan_registry_recursive(root_path):
    active_apps = {}
    scanned_keys = 0

    def scan_key(base_path, depth=0):
        nonlocal scanned_keys
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base_path, 0, winreg.KEY_READ)
        except FileNotFoundError:
            return
        except OSError as e:
            log_debug(f"OSError buka {base_path}: {e}")
            return

        start_time, stop_time, has_values = _read_key_values(base_path)
        if has_values:
            scanned_keys += 1
            app_name = base_path.split("\\")[-1]
            is_active = (stop_time == 0 and start_time > 0)
            log_debug(
                f"{'  ' * depth}[{'ACTIVE' if is_active else 'idle  '}] "
                f"{app_name} | start={start_time} stop={stop_time}"
            )
            if is_active:
                active_apps[app_name] = is_browser(app_name)

        i = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(key, i)
            except OSError:
                break

            full_subkey_path = f"{base_path}\\{subkey_name}"
            scan_key(full_subkey_path, depth + 1)
            i += 1

        winreg.CloseKey(key)

    scan_key(root_path)

    if DEBUG_REGISTRY:
        log_debug(f"Selesai scan: {scanned_keys} key dengan values, "
                  f"{len(active_apps)} aktif")
    return active_apps


def format_app_list(active_apps):
    formatted = []
    for raw_name, browser in active_apps.items():
        pretty = prettify_app_name(raw_name)
        if browser:
            formatted.append(f"{pretty} [BROWSER]")
        else:
            formatted.append(pretty)
    return ", ".join(formatted)


# ================== ICON GENERATOR ==================
def make_icon(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = (0, 200, 0, 255) if color == "green" else (200, 0, 0, 255)
    d.ellipse((8, 8, 56, 56), fill=fill, outline=(255, 255, 255, 255), width=3)
    return img


# ================== PORT SETTINGS DIALOG ==================
class PortSettingsDialog:
    def __init__(self, parent, current_cfg):
        self.result = None
        self.cfg = current_cfg
        self._closing = False  # flag agar thread tidak update UI setelah window tutup

        self.win = tk.Toplevel(parent)
        self.win.title("⚙️ Pengaturan Port & Brightness")
        self.win.geometry("460x600")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        btn_container = ttk.Frame(self.win)
        btn_container.pack(side="bottom", fill="x")
        ttk.Separator(btn_container, orient="horizontal").pack(side="top", fill="x")

        btn_frame = ttk.Frame(btn_container, padding=(20, 12, 20, 15))
        btn_frame.pack(side="top", fill="x")
        ttk.Button(btn_frame, text="💾 Simpan", command=self._save, width=12
                   ).pack(side="right", padx=(8, 0))
        ttk.Button(btn_frame, text="✖ Batal", command=self._cancel, width=12
                   ).pack(side="right")

        main = ttk.Frame(self.win, padding=20)
        main.pack(side="top", fill="both", expand=True)

        ttk.Label(main, text="Konfigurasi Koneksi Arduino",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 12))

        # COM Port
        port_frame = ttk.Frame(main)
        port_frame.pack(fill="x", pady=4)
        ttk.Label(port_frame, text="COM Port:", width=14).pack(side="left")
        self.port_var = tk.StringVar(value=current_cfg.get("com_port", "COM12"))
        available_ports = list_serial_ports()
        self.port_combo = ttk.Combobox(port_frame, textvariable=self.port_var,
                                       values=available_ports, width=18)
        self.port_combo.pack(side="left", padx=(0, 5))
        ttk.Button(port_frame, text="🔄 Refresh", width=10,
                   command=self._refresh_ports).pack(side="left")

        self.info_label = ttk.Label(
            main,
            text=f"Terdeteksi {len(available_ports)} port: "
                 f"{', '.join(available_ports) if available_ports else '(tidak ada)'}",
            font=("Segoe UI", 8), foreground="gray")
        self.info_label.pack(anchor="w", pady=(0, 8))

        # Baud Rate
        baud_frame = ttk.Frame(main)
        baud_frame.pack(fill="x", pady=4)
        ttk.Label(baud_frame, text="Baud Rate:", width=14).pack(side="left")
        self.baud_var = tk.StringVar(value=str(current_cfg.get("baud_rate", 9600)))
        ttk.Combobox(baud_frame, textvariable=self.baud_var,
                     values=["9600", "19200", "38400", "57600", "115200"],
                     width=18).pack(side="left")

        # LED Pin
        pin_frame = ttk.Frame(main)
        pin_frame.pack(fill="x", pady=4)
        ttk.Label(pin_frame, text="LED Pin (PWM):", width=14).pack(side="left")
        self.pin_var = tk.StringVar(value=str(current_cfg.get("led_pin", 9)))
        ttk.Combobox(pin_frame, textvariable=self.pin_var,
                     values=["3", "5", "6", "9", "10", "11"],
                     width=18).pack(side="left")

        ttk.Separator(main, orient="horizontal").pack(fill="x", pady=10)

        ttk.Label(main, text="Kecerahan (Brightness)",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        bright_frame = ttk.Frame(main)
        bright_frame.pack(fill="x", pady=4)
        ttk.Label(bright_frame, text="Nilai (0-255):", width=14).pack(side="left")
        self.bright_var = tk.IntVar(value=current_cfg.get("brightness", 255))
        self.bright_scale = ttk.Scale(bright_frame, from_=0, to=255,
                                      orient="horizontal", length=160,
                                      variable=self.bright_var,
                                      command=self._on_bright_change)
        self.bright_scale.pack(side="left", padx=(0, 5))
        self.bright_label = ttk.Label(bright_frame, text=str(self.bright_var.get()), width=4)
        self.bright_label.pack(side="left")

        # Step
        step_frame = ttk.Frame(main)
        step_frame.pack(fill="x", pady=4)
        ttk.Label(step_frame, text="Step (Alt+3/4):", width=14).pack(side="left")
        self.step_var = tk.StringVar(value=str(current_cfg.get("brightness_step", 25)))
        ttk.Combobox(step_frame, textvariable=self.step_var,
                     values=["5", "10", "15", "25", "50", "85"],
                     width=18).pack(side="left")

        # Test & Reset
        action_frame = ttk.Frame(main)
        action_frame.pack(fill="x", pady=(12, 0))
        ttk.Button(action_frame, text="🔌 Test Koneksi",
                   command=self._test_connection).pack(side="left", padx=(0, 5))
        ttk.Button(action_frame, text="🔄 Reset Port",
                   command=self._force_reset).pack(side="left")

        self.test_status = ttk.Label(main, text="", font=("Segoe UI", 9),
                                     wraplength=400)
        self.test_status.pack(anchor="w", pady=(8, 0))

        self.win.bind("<Return>", lambda e: self._save())
        self.win.bind("<Escape>", lambda e: self._cancel())

        self.win.update_idletasks()
        w, h = 460, 600
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    # ---------- thread-safe UI update ----------
    def _set_status(self, text, color="black"):
        """
        Update label status secara thread-safe.
        Dipanggil dari thread manapun — dijadwalkan ke main thread Tk.
        """
        if self._closing:
            return
        try:
            self.win.after(0, lambda: self._do_set_status(text, color))
        except Exception:
            pass

    def _do_set_status(self, text, color):
        if self._closing:
            return
        try:
            self.test_status.config(text=text, foreground=color)
        except Exception:
            pass

    def _on_bright_change(self, val):
        try:
            self.bright_label.config(text=str(int(float(val))))
        except Exception:
            pass

    def _refresh_ports(self):
        ports = list_serial_ports()
        self.port_combo["values"] = ports
        self.info_label.config(
            text=f"Terdeteksi {len(ports)} port: "
                 f"{', '.join(ports) if ports else '(tidak ada)'}")

    def _force_reset(self):
        port = self.port_var.get().strip()
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            self._set_status("❌ Baud rate tidak valid", "red")
            return

        self._set_status(f"⏳ Force reset {port} (bisa 5-10 detik)...", "gray")

        def do_reset():
            ok = force_reset_port(port, baud, max_attempts=5)
            if ok:
                self._set_status(
                    f"✅ Port {port} berhasil di-reset. Coba Test Koneksi lagi.",
                    "green")
            else:
                self._set_status(
                    f"❌ Gagal reset {port}. Pastikan Arduino IDE / "
                    f"Serial Monitor tertutup, lalu coba lagi.",
                    "red")

        threading.Thread(target=do_reset, daemon=True).start()

    def _test_connection(self):
        port = self.port_var.get().strip()
        try:
            baud = int(self.baud_var.get())
            pin = int(self.pin_var.get())
            bright = int(self.bright_var.get())
        except ValueError:
            self._set_status("❌ Nilai tidak valid", "red")
            return

        self._set_status("⏳ Testing...", "gray")

        def do_test():
            resp = ""
            try:
                s = robust_open_port(port, baud, timeout=1, max_retries=2)
                if s is None:
                    self._set_status(
                        f"❌ Gagal buka {port}. Coba klik '🔄 Reset Port' dulu.",
                        "red")
                    log(f"Test koneksi dialog {port}: GAGAL buka port")
                    return
                time.sleep(0.5)
                test_cmd = f"DIM:{pin},{bright}\n"
                s.write(test_cmd.encode('ascii'))
                s.flush()
                time.sleep(0.3)
                while s.in_waiting > 0:
                    resp += s.readline().decode("ascii", errors="ignore")
                s.close()
                if resp.strip():
                    self._set_status(
                        f"✅ OK (respons: {resp.strip()[:60]})",
                        "green")
                    log(f"Test koneksi dialog {port}: OK")
                else:
                    self._set_status(
                        "⚠️ Port terbuka, tapi tidak ada respons dari Arduino.",
                        "orange")
                    log(f"Test koneksi dialog {port}: terbuka tapi tanpa respons")
            except Exception as e:
                self._set_status(f"❌ Gagal: {str(e)[:80]}", "red")
                log(f"Test koneksi dialog {port}: GAGAL - {e}")

        threading.Thread(target=do_test, daemon=True).start()

    def _save(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "COM port tidak boleh kosong.", parent=self.win)
            return
        try:
            baud = int(self.baud_var.get())
            pin = int(self.pin_var.get())
            bright = int(self.bright_var.get())
            step = int(self.step_var.get())
        except ValueError:
            messagebox.showerror("Error", "Ada nilai yang tidak valid.", parent=self.win)
            return

        self.result = {
            "com_port": port,
            "baud_rate": baud,
            "check_interval": self.cfg.get("check_interval", 2),
            "led_pin": pin,
            "brightness": bright,
            "brightness_step": step,
        }
        self._closing = True
        self.win.destroy()

    def _cancel(self):
        self.result = None
        self._closing = True
        self.win.destroy()

    def show(self):
        self.win.wait_window()
        return self.result


# ================== MONITORING THREAD ==================
class CameraMonitor:
    def __init__(self, on_start=None, on_stop=None):
        self.arduino = None
        self.camera_in_use = False
        self.running = False
        self.thread = None
        self.on_start = on_start
        self.on_stop = on_stop
        self.com_port = CONFIG["com_port"]
        self.baud_rate = CONFIG["baud_rate"]
        self.check_interval = CONFIG["check_interval"]
        self.led_pin = CONFIG.get("led_pin", 9)
        self.serial_lock = threading.Lock()
        self._loop_count = 0
        self._last_heartbeat = time.time()
        self._last_reconnect_attempt = 0

    def start(self):
        self.running = True
        self.arduino = connect_arduino(self.com_port, self.baud_rate, max_retries=3)
        if self.arduino is None:
            log(f"GAGAL terhubung ke {self.com_port}. "
                f"Coba Reset Port dari menu tray.")
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        log("📹 Loop monitoring kamera dimulai.")

    def _close_arduino_safe(self):
        try:
            if self.arduino:
                try:
                    self.arduino.close()
                except Exception:
                    pass
        finally:
            self.arduino = None

    def restart_connection(self):
        log("🔄 Reset koneksi diminta...")
        with self.serial_lock:
            self._close_arduino_safe()
            time.sleep(0.3)
            ok = force_reset_port(self.com_port, self.baud_rate, max_attempts=5)
            if not ok:
                log("❌ Force reset gagal, coba buka langsung...")
            self.arduino = connect_arduino(self.com_port, self.baud_rate, max_retries=2)
            if self.arduino:
                log("✅ Koneksi Arduino berhasil di-reset & dibuka kembali.")
                return True
            else:
                log("❌ Gagal membuka kembali port setelah reset.")
                return False

    def _loop(self):
        while self.running:
            try:
                if self.arduino is None or not self.arduino.is_open:
                    now = time.time()
                    if now - self._last_reconnect_attempt >= 10:
                        self._last_reconnect_attempt = now
                        log(f"🔌 Arduino terputus, mencoba konek ulang ke {self.com_port}...")
                        with self.serial_lock:
                            self.arduino = connect_arduino(
                                self.com_port, self.baud_rate, max_retries=1)
                        if self.arduino:
                            log("✅ Arduino terhubung kembali.")

                active_apps = scan_registry_recursive(REG_PATH)
                currently_active = len(active_apps) > 0

                if currently_active and not self.camera_in_use:
                    self.camera_in_use = True
                    app_list = format_app_list(active_apps)
                    log(f"🟢 START Kamera: {app_list}")
                    with self.serial_lock:
                        if self.arduino:
                            self._send_dim_locked()
                    if self.on_start:
                        self.on_start(app_list)

                elif not currently_active and self.camera_in_use:
                    self.camera_in_use = False
                    log("🔴 STOP Semua kamera tidak digunakan.")
                    with self.serial_lock:
                        if self.arduino:
                            send_command(self.arduino, "ALLOFF")
                    if self.on_stop:
                        self.on_stop()

                self._loop_count += 1
                now = time.time()
                if now - self._last_heartbeat >= 60:
                    log(f"💓 Heartbeat: loop #{self._loop_count}, "
                        f"apps_aktif={len(active_apps)}, "
                        f"arduino={'OK' if (self.arduino and self.arduino.is_open) else 'DISCONNECTED'}")
                    self._last_heartbeat = now

            except Exception as e:
                log(f"❌ Error di loop: {e}")
                import traceback
                log(f"Traceback: {traceback.format_exc()}")

            time.sleep(self.check_interval)

    def _send_dim_locked(self):
        pin = CONFIG.get("led_pin", 9)
        bright = CONFIG.get("brightness", 255)
        cmd = f"DIM:{pin},{bright}"
        send_command(self.arduino, cmd)

    def send_now(self, cmd):
        with self.serial_lock:
            if self.arduino and self.arduino.is_open:
                send_command(self.arduino, cmd)
                return True
            else:
                log(f"⚠️ Perintah '{cmd}' gagal, Arduino tidak terhubung.")
                return False

    def send_brightness(self, delta):
        step = CONFIG.get("brightness_step", 25)
        current = CONFIG.get("brightness", 255)
        new_val = max(0, min(255, current + (step if delta > 0 else -step)))
        CONFIG["brightness"] = new_val
        save_config(CONFIG)

        pin = CONFIG.get("led_pin", 9)
        cmd = f"DIM:{pin},{new_val}"
        ok = self.send_now(cmd)
        return new_val, ok

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
        with self.serial_lock:
            try:
                if self.arduino:
                    send_command(self.arduino, "ALLOFF")
                    self.arduino.close()
            except Exception:
                pass
            self.arduino = None


# ================== HOTKEY MANAGER ==================
class HotkeyManager:
    def __init__(self, monitor, on_notify=None):
        self.monitor = monitor
        self.on_notify = on_notify
        self.registered = False

    def register(self):
        if not HAS_KEYBOARD:
            log("⚠️ Library 'keyboard' tidak terinstall. Hotkey dinonaktifkan.")
            log("   Install dengan: pip install keyboard")
            return False

        try:
            keyboard.add_hotkey("alt+1", self._hotkey_on)
            keyboard.add_hotkey("alt+2", self._hotkey_off)
            keyboard.add_hotkey("alt+3", self._hotkey_up)
            keyboard.add_hotkey("alt+4", self._hotkey_down)
            self.registered = True
            log("✅ Hotkey terdaftar: Alt+1=ON, Alt+2=OFF, Alt+3=+DIM, Alt+4=-DIM")
            return True
        except Exception as e:
            log(f"❌ Gagal mendaftarkan hotkey: {e}")
            log("   Coba jalankan aplikasi sebagai Administrator.")
            return False

    def unregister(self):
        if self.registered and HAS_KEYBOARD:
            try:
                for k in ("alt+1", "alt+2", "alt+3", "alt+4"):
                    keyboard.remove_hotkey(k)
                log("Hotkey di-unregister.")
            except Exception:
                pass
            self.registered = False

    def _notify(self, status, kind):
        if self.on_notify:
            self.on_notify(status, kind)

    def _hotkey_on(self):
        log("⌨️  Alt+1 → ALLON")
        ok = self.monitor.send_now("ALLON")
        self._notify("ON" if ok else "GAGAL", "on" if ok else "error")

    def _hotkey_off(self):
        log("⌨️  Alt+2 → ALLOFF")
        ok = self.monitor.send_now("ALLOFF")
        self._notify("OFF" if ok else "GAGAL", "off" if ok else "error")

    def _hotkey_up(self):
        log("⌨️  Alt+3 → Brightness +")
        new_val, ok = self.monitor.send_brightness(+1)
        self._notify(f"DIM {new_val}" if ok else "GAGAL", "up" if ok else "error")

    def _hotkey_down(self):
        log("⌨️  Alt+4 → Brightness -")
        new_val, ok = self.monitor.send_brightness(-1)
        self._notify(f"DIM {new_val}" if ok else "GAGAL", "down" if ok else "error")


# ================== PYSTRAY APP ==================
class TrayApp:
    def __init__(self):
        self.monitor = CameraMonitor(on_start=self._on_start, on_stop=self._on_stop)
        self.hotkeys = HotkeyManager(self.monitor, on_notify=self._on_hotkey_notify)
        self.icon = pystray.Icon(
            name="KanimLightAuto",
            icon=make_icon("red"),
            title=f"Kanim Light Auto ({CONFIG['com_port']}) - Menunggu kamera",
            menu=self._build_menu("Menunggu kamera"),
        )

    def _build_menu(self, status_text):
        hotkey_label = "Hotkey: 1=ON 2=OFF 3=+ 4=-" if HAS_KEYBOARD else "Hotkey: (nonaktif)"
        bright = CONFIG.get("brightness", 255)
        conn_status = "✅" if (self.monitor.arduino and self.monitor.arduino.is_open) else "❌"
        return pystray.Menu(
            pystray.MenuItem(f"Status: {status_text}", self._noop, enabled=False),
            pystray.MenuItem(f"Port: {CONFIG['com_port']} @ {CONFIG['baud_rate']} {conn_status}",
                             self._noop, enabled=False),
            pystray.MenuItem(f"Brightness: {bright}/255 (Pin {CONFIG.get('led_pin', 9)})",
                             self._noop, enabled=False),
            pystray.MenuItem(hotkey_label, self._noop, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("💡 Nyalakan LED (Alt+1)", self._menu_on),
            pystray.MenuItem("🌑 Matikan LED (Alt+2)", self._menu_off),
            pystray.MenuItem("🔆 Tambah Kecerahan (Alt+3)", self._menu_up),
            pystray.MenuItem("🔅 Kurangi Kecerahan (Alt+4)", self._menu_down),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🔄 Reset Koneksi (Force)", self._reset_connection),
            pystray.MenuItem("⚙️ Pengaturan Port & Brightness...", self._open_settings),
            pystray.MenuItem("🔌 Test Koneksi", self._test_connection_now),
            pystray.MenuItem("🔄 Restart Monitoring", self._restart),
            pystray.MenuItem("📄 Buka Log", self._open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Keluar", self._quit),
        )

    def _noop(self, icon, item):
        pass

    def _refresh_menu(self):
        status = "AKTIF" if self.monitor.camera_in_use else "Menunggu kamera"
        self.icon.menu = self._build_menu(status)

    def _on_start(self, app_list):
        self.icon.icon = make_icon("green")
        self.icon.title = f"Kanim Light Auto ({CONFIG['com_port']}) - AKTIF: {app_list}"
        self.icon.menu = self._build_menu(f"AKTIF ({app_list})")

    def _on_stop(self):
        self.icon.icon = make_icon("red")
        self.icon.title = f"Kanim Light Auto ({CONFIG['com_port']}) - Menunggu kamera"
        self.icon.menu = self._build_menu("Menunggu kamera")

    def _on_hotkey_notify(self, status, kind):
        base = self.icon.title.split(" [")[0]
        suffix = f" [{status}]"
        self.icon.title = base + suffix
        self._refresh_menu()

        def reset():
            time.sleep(2)
            try:
                self.icon.title = self.icon.title.split(" [")[0]
            except Exception:
                pass
        threading.Thread(target=reset, daemon=True).start()

    def _menu_on(self, icon, item):
        log("Menu: Nyalakan LED")
        ok = self.monitor.send_now("ALLON")
        self._on_hotkey_notify("ON" if ok else "GAGAL", "on" if ok else "error")

    def _menu_off(self, icon, item):
        log("Menu: Matikan LED")
        ok = self.monitor.send_now("ALLOFF")
        self._on_hotkey_notify("OFF" if ok else "GAGAL", "off" if ok else "error")

    def _menu_up(self, icon, item):
        log("Menu: Tambah Kecerahan")
        new_val, ok = self.monitor.send_brightness(+1)
        self._on_hotkey_notify(f"DIM {new_val}" if ok else "GAGAL", "up" if ok else "error")

    def _menu_down(self, icon, item):
        log("Menu: Kurangi Kecerahan")
        new_val, ok = self.monitor.send_brightness(-1)
        self._on_hotkey_notify(f"DIM {new_val}" if ok else "GAGAL", "down" if ok else "error")

    def _open_log(self, icon, item):
        if os.path.exists(LOG_FILE):
            os.startfile(LOG_FILE)
        else:
            log("Log belum ada.")

    def _reset_connection(self, icon, item):
        log("🔄 Reset Koneksi diminta dari menu tray...")

        def show_popup(msg):
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            messagebox.showinfo("Reset Koneksi", msg, parent=root)
            root.destroy()

        def do_reset():
            ok = self.monitor.restart_connection()
            self._refresh_menu()
            if ok:
                msg = (f"✅ Koneksi ke {CONFIG['com_port']} berhasil di-reset.\n"
                       f"Arduino siap menerima perintah.")
            else:
                msg = (f"❌ Gagal reset koneksi ke {CONFIG['com_port']}.\n\n"
                       f"Kemungkinan penyebab:\n"
                       f"• Arduino IDE / Serial Monitor masih terbuka\n"
                       f"• Kabel USB longgar\n"
                       f"• Port dikuasai proses lain\n\n"
                       f"Coba tutup aplikasi lain, cabut-pasang kabel USB, "
                       f"lalu klik Reset Koneksi lagi.")
            threading.Thread(target=show_popup, args=(msg,), daemon=True).start()

        threading.Thread(target=do_reset, daemon=True).start()

    def _test_connection_now(self, icon, item):
        port = CONFIG["com_port"]
        baud = CONFIG["baud_rate"]
        pin = CONFIG.get("led_pin", 9)
        bright = CONFIG.get("brightness", 255)

        def do_test():
            try:
                s = robust_open_port(port, baud, timeout=1, max_retries=3)
                if s is None:
                    msg = (f"❌ Port {port} GAGAL dibuka.\n\n"
                           f"Coba klik '🔄 Reset Koneksi (Force)' dari menu tray.")
                    log(f"Test koneksi {port}: GAGAL (tidak bisa buka port)")
                else:
                    time.sleep(0.5)
                    s.write(f"DIM:{pin},{bright}\n".encode('ascii'))
                    s.flush()
                    time.sleep(0.3)
                    resp = ""
                    while s.in_waiting > 0:
                        resp += s.readline().decode("ascii", errors="ignore")
                    s.close()
                    msg = (f"✅ Port {port} OK\n"
                           f"Perintah: DIM:{pin},{bright}\n"
                           f"Respons: {resp.strip() or '(kosong)'}")
                    log(f"Test koneksi {port}: OK")
            except Exception as e:
                msg = f"❌ Port {port} GAGAL\nError: {e}"
                log(f"Test koneksi {port}: GAGAL - {e}")

            def show_popup():
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                messagebox.showinfo("Test Koneksi", msg, parent=root)
                root.destroy()

            threading.Thread(target=show_popup, daemon=True).start()

        threading.Thread(target=do_test, daemon=True).start()

    def _open_settings(self, icon, item):
        def show_dialog():
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)

            dlg = PortSettingsDialog(root, CONFIG)
            new_cfg = dlg.show()
            root.destroy()

            if new_cfg:
                CONFIG.update(new_cfg)
                save_config(CONFIG)
                log(f"⚙️ Konfigurasi diubah: port={CONFIG['com_port']}, "
                    f"baud={CONFIG['baud_rate']}, pin={CONFIG['led_pin']}, "
                    f"brightness={CONFIG['brightness']}, step={CONFIG['brightness_step']}")

                self.monitor.stop()
                self.monitor = CameraMonitor(on_start=self._on_start, on_stop=self._on_stop)
                self.monitor.start()
                self.hotkeys.monitor = self.monitor

                self.icon.title = f"Kanim Light Auto ({CONFIG['com_port']}) - Menunggu kamera"
                self._refresh_menu()

                def notif():
                    r = tk.Tk()
                    r.withdraw()
                    r.attributes("-topmost", True)
                    messagebox.showinfo(
                        "Pengaturan Tersimpan",
                        f"Port: {CONFIG['com_port']} @ {CONFIG['baud_rate']}\n"
                        f"Pin: {CONFIG['led_pin']}\n"
                        f"Brightness: {CONFIG['brightness']}/255\n"
                        f"Step: {CONFIG['brightness_step']}",
                        parent=r)
                    r.destroy()
                threading.Thread(target=notif, daemon=True).start()

        threading.Thread(target=show_dialog, daemon=True).start()

    def _restart(self, icon, item):
        log("Restart monitoring diminta user.")
        self.monitor.stop()
        self.monitor = CameraMonitor(on_start=self._on_start, on_stop=self._on_stop)
        self.monitor.start()
        self.hotkeys.monitor = self.monitor
        self.icon.title = f"Kanim Light Auto ({CONFIG['com_port']}) - Menunggu kamera"
        self._refresh_menu()

    def _quit(self, icon, item):
        log("Keluar dari aplikasi.")
        self.hotkeys.unregister()
        self.monitor.stop()
        icon.stop()

    def run(self):
        log("=== APLIKASI DIMULAI ===")
        log(f"Config: port={CONFIG['com_port']}, baud={CONFIG['baud_rate']}, "
            f"interval={CONFIG['check_interval']}s, pin={CONFIG.get('led_pin', 9)}, "
            f"brightness={CONFIG.get('brightness', 255)}, "
            f"step={CONFIG.get('brightness_step', 25)}")
        if DEBUG_REGISTRY:
            log("🔬 DEBUG_REGISTRY aktif — semua key akan di-log")
        ensure_autostart()
        self.monitor.start()
        self.hotkeys.register()
        self.icon.run()


# ================== ENTRY POINT ==================
def print_usage():
    print("Kanim Light Auto")
    print()
    print("Penggunaan:")
    print("  python kanim_light_auto.py              Jalankan aplikasi (systray)")
    print("  python kanim_light_auto.py --install    Pasang auto-start ke Windows")
    print("  python kanim_light_auto.py --uninstall  Hapus auto-start")
    print("  python kanim_light_auto.py --status     Cek status auto-start")
    print("  python kanim_light_auto.py --ports      Tampilkan daftar COM port")
    print("  python kanim_light_auto.py --set-port COM3   Set COM port")
    print("  python kanim_light_auto.py --reset-port Reset port sekali (CLI)")
    print("  python kanim_light_auto.py --debug      Jalankan dengan debug registry")
    print("  python kanim_light_auto.py --help       Tampilkan bantuan ini")
    print()
    print("Hotkey global (saat aplikasi berjalan):")
    print("  Alt + 1  →  Nyalakan LED (ALLON)")
    print("  Alt + 2  →  Matikan LED (ALLOFF)")
    print("  Alt + 3  →  Tambah kecerahan (DIM:+step)")
    print("  Alt + 4  →  Kurangi kecerahan (DIM:-step)")


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--debug" in args:
        DEBUG_REGISTRY = True
        print("🔬 Mode DEBUG_REGISTRY aktif.")

    if "--install" in args:
        autostart_install()
        sys.exit(0)

    if "--uninstall" in args:
        autostart_uninstall()
        sys.exit(0)

    if "--status" in args:
        val = autostart_is_installed()
        if val:
            print(f"✅ Auto-start TERPASANG:\n   {val}")
        else:
            print("❌ Auto-start BELUM terpasang.")
        sys.exit(0)

    if "--ports" in args:
        ports = list_serial_ports()
        print("COM Port yang tersedia:")
        for p in ports:
            print(f"  - {p}")
        if not ports:
            print("  (tidak ada)")
        print(f"\nPort aktif di config: {CONFIG['com_port']}")
        sys.exit(0)

    if "--set-port" in args:
        try:
            idx = args.index("--set-port")
            new_port = args[idx + 1]
        except (ValueError, IndexError):
            print("❌ Format: python kanim_light_auto.py --set-port COM3")
            sys.exit(1)
        CONFIG["com_port"] = new_port
        save_config(CONFIG)
        print(f"✅ COM port diubah ke {new_port}")
        sys.exit(0)

    if "--reset-port" in args:
        port = CONFIG["com_port"]
        baud = CONFIG["baud_rate"]
        print(f"🔄 Reset port {port} @ {baud}...")
        ok = force_reset_port(port, baud, max_attempts=5)
        if ok:
            print(f"✅ Port {port} berhasil di-reset.")
        else:
            print(f"❌ Gagal reset {port}. Cek apakah Arduino IDE / Serial "
                  f"Monitor masih terbuka, atau kabel USB longgar.")
        sys.exit(0 if ok else 1)

    if "--help" in args or "-h" in args:
        print_usage()
        sys.exit(0)

    if is_already_running():
        log("Instance sudah berjalan. Keluar.")
        sys.exit(0)

    TrayApp().run()