"""
Kanim Light Auto — Monitor kamera Windows & kontrol LED strip via Arduino.

Fitur:
- Jalan sebagai aplikasi systray (pystray)
- Auto-start saat Windows nyala (via Registry Run, HKCU)
- Single instance (Windows Named Mutex)
- Log ke file kanim_light_auto.log
- Deteksi semua aplikasi (UWP + desktop/browser) via Registry rekursif
- Menu pengaturan COM port dari tray icon (disimpan di kanim_light_config.json)
- Global hotkey: Alt+1 (ALLON), Alt+2 (ALLOFF)

Perintah:
  python kanim_light_auto.py              → Jalankan aplikasi (systray)
  python kanim_light_auto.py --install    → Pasang auto-start ke Windows
  python kanim_light_auto.py --uninstall  → Hapus auto-start
  python kanim_light_auto.py --status     → Cek status auto-start
  python kanim_light_auto.py --ports      → Daftar COM port
  python kanim_light_auto.py --set-port COM3 → Set COM port
  python kanim_light_auto.py --help       → Bantuan
"""
import os
import sys
import json
import time
import ctypes
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
    "com_port": "COM6",
    "baud_rate": 9600,
    "check_interval": 2,
}

REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"

AUTOSTART_APP_NAME = "KanimLightAuto"
AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(SCRIPT_DIR, "kanim_light_auto.log")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "kanim_light_config.json")
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


def force_close_port(port_name, baud_rate):
    try:
        s = serial.Serial(port_name, baud_rate, timeout=0.1)
        s.dtr = False
        s.rts = False
        time.sleep(0.1)
        s.dtr = True
        s.rts = True
        time.sleep(0.1)
        s.dtr = False
        s.rts = False
        s.close()
        return True
    except Exception as e:
        log(f"Info force_close_port: {e}")
        return False


def connect_arduino(port_name, baud_rate, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            force_close_port(port_name, baud_rate)
            time.sleep(0.5)
            arduino = serial.Serial(port_name, baud_rate, timeout=1)
            time.sleep(2)
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


def scan_registry_recursive(root_path):
    active_apps = {}

    def scan_key(base_path):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base_path, 0, winreg.KEY_READ)
        except FileNotFoundError:
            return

        i = 0
        while True:
            try:
                subkey_name = winreg.EnumKey(key, i)
            except OSError:
                break

            full_subkey_path = f"{base_path}\\{subkey_name}"

            try:
                subkey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, full_subkey_path, 0, winreg.KEY_READ)
                has_values = False
                try:
                    stop_time, _ = winreg.QueryValueEx(subkey, "LastUsedTimeStop")
                    start_time, _ = winreg.QueryValueEx(subkey, "LastUsedTimeStart")
                    has_values = True
                    if stop_time == 0 and start_time > 0:
                        active_apps[subkey_name] = is_browser(subkey_name)
                except FileNotFoundError:
                    pass
                finally:
                    winreg.CloseKey(subkey)

                if not has_values:
                    scan_key(full_subkey_path)
            except OSError:
                pass

            i += 1

        winreg.CloseKey(key)

    scan_key(root_path)
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

        self.win = tk.Toplevel(parent)
        self.win.title("⚙️ Pengaturan Port Arduino")
        self.win.geometry("420x280")
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)

        frame = ttk.Frame(self.win, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame, text="Konfigurasi Koneksi Arduino",
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w", pady=(0, 15))

        # COM Port
        port_frame = ttk.Frame(frame)
        port_frame.pack(fill="x", pady=5)
        ttk.Label(port_frame, text="COM Port:", width=12).pack(side="left")

        self.port_var = tk.StringVar(value=current_cfg.get("com_port", "COM12"))
        available_ports = list_serial_ports()

        self.port_combo = ttk.Combobox(
            port_frame, textvariable=self.port_var,
            values=available_ports, width=20
        )
        self.port_combo.pack(side="left", padx=(0, 5))

        ttk.Button(
            port_frame, text="🔄 Refresh", width=10,
            command=self._refresh_ports
        ).pack(side="left")

        self.info_label = ttk.Label(
            frame,
            text=f"Terdeteksi {len(available_ports)} port: "
                 f"{', '.join(available_ports) if available_ports else '(tidak ada)'}",
            font=("Segoe UI", 8), foreground="gray"
        )
        self.info_label.pack(anchor="w", pady=(0, 10))

        # Baud Rate
        baud_frame = ttk.Frame(frame)
        baud_frame.pack(fill="x", pady=5)
        ttk.Label(baud_frame, text="Baud Rate:", width=12).pack(side="left")

        self.baud_var = tk.StringVar(value=str(current_cfg.get("baud_rate", 9600)))
        ttk.Combobox(
            baud_frame, textvariable=self.baud_var,
            values=["9600", "19200", "38400", "57600", "115200"],
            width=20
        ).pack(side="left")

        # Test
        test_frame = ttk.Frame(frame)
        test_frame.pack(fill="x", pady=15)
        ttk.Button(
            test_frame, text="🔌 Test Koneksi",
            command=self._test_connection
        ).pack(side="left")

        self.test_status = ttk.Label(test_frame, text="", font=("Segoe UI", 9))
        self.test_status.pack(side="left", padx=10)

        # Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=(15, 0))
        ttk.Button(btn_frame, text="💾 Simpan", command=self._save).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Batal", command=self._cancel).pack(side="right")

        self.win.bind("<Return>", lambda e: self._save())
        self.win.bind("<Escape>", lambda e: self._cancel())

        self.win.update_idletasks()
        w, h = self.win.winfo_width(), self.win.winfo_height()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.win.geometry(f"+{x}+{y}")

    def _refresh_ports(self):
        ports = list_serial_ports()
        self.port_combo["values"] = ports
        self.info_label.config(
            text=f"Terdeteksi {len(ports)} port: "
                 f"{', '.join(ports) if ports else '(tidak ada)'}"
        )

    def _test_connection(self):
        port = self.port_var.get().strip()
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            self.test_status.config(text="❌ Baud rate tidak valid", foreground="red")
            return

        self.test_status.config(text="⏳ Testing...", foreground="gray")
        self.win.update_idletasks()

        try:
            s = serial.Serial(port, baud, timeout=1)
            time.sleep(0.5)
            s.write(b"ALLOFF\n")
            s.flush()
            time.sleep(0.3)
            resp = ""
            while s.in_waiting > 0:
                resp += s.readline().decode("ascii", errors="ignore")
            s.close()
            if resp.strip():
                self.test_status.config(
                    text=f"✅ OK (respons: {resp.strip()[:30]})",
                    foreground="green"
                )
            else:
                self.test_status.config(
                    text="⚠️ Terbuka, tapi tidak ada respons",
                    foreground="orange"
                )
        except Exception as e:
            self.test_status.config(text=f"❌ Gagal: {str(e)[:40]}", foreground="red")

    def _save(self):
        port = self.port_var.get().strip()
        if not port:
            messagebox.showerror("Error", "COM port tidak boleh kosong.", parent=self.win)
            return
        try:
            baud = int(self.baud_var.get())
        except ValueError:
            messagebox.showerror("Error", "Baud rate tidak valid.", parent=self.win)
            return

        self.result = {
            "com_port": port,
            "baud_rate": baud,
            "check_interval": self.cfg.get("check_interval", 2),
        }
        self.win.destroy()

    def _cancel(self):
        self.result = None
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
        # Lock untuk mencegah race condition saat hotkey dipakai bersamaan
        self.serial_lock = threading.Lock()

    def start(self):
        self.running = True
        self.arduino = connect_arduino(self.com_port, self.baud_rate, max_retries=3)
        if self.arduino is None:
            log(f"GAGAL terhubung ke {self.com_port}.")
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while self.running:
            try:
                if self.arduino is None or not self.arduino.is_open:
                    log(f"Mencoba konek ulang ke {self.com_port}...")
                    with self.serial_lock:
                        self.arduino = connect_arduino(self.com_port, self.baud_rate, max_retries=1)

                active_apps = scan_registry_recursive(REG_PATH)
                currently_active = len(active_apps) > 0

                if currently_active and not self.camera_in_use:
                    self.camera_in_use = True
                    app_list = format_app_list(active_apps)
                    log(f"🟢 START Kamera: {app_list}")
                    with self.serial_lock:
                        if self.arduino:
                            send_command(self.arduino, "ALLON")
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

            except Exception as e:
                log(f"Error di loop: {e}")

            time.sleep(self.check_interval)

    def send_now(self, cmd):
        """Kirim perintah langsung tanpa menunggu loop (untuk hotkey)."""
        with self.serial_lock:
            if self.arduino and self.arduino.is_open:
                send_command(self.arduino, cmd)
                return True
            else:
                log(f"⚠️ Hotkey '{cmd}' ditekan, tapi Arduino tidak terhubung.")
                return False

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=3)
        if self.arduino:
            try:
                with self.serial_lock:
                    send_command(self.arduino, "ALLOFF")
                    self.arduino.close()
            except Exception:
                pass


# ================== HOTKEY MANAGER ==================
class HotkeyManager:
    """
    Mengelola global hotkey:
      Alt+1 → ALLON
      Alt+2 → ALLOFF
    """
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
            self.registered = True
            log("✅ Hotkey terdaftar: Alt+1 (ON), Alt+2 (OFF)")
            return True
        except Exception as e:
            log(f"❌ Gagal mendaftarkan hotkey: {e}")
            log("   Coba jalankan aplikasi sebagai Administrator.")
            return False

    def unregister(self):
        if self.registered and HAS_KEYBOARD:
            try:
                keyboard.remove_hotkey("alt+1")
                keyboard.remove_hotkey("alt+2")
                log("Hotkey di-unregister.")
            except Exception:
                pass
            self.registered = False

    def _hotkey_on(self):
        log("⌨️  Hotkey Alt+1 ditekan → ALLON")
        ok = self.monitor.send_now("ALLON")
        if self.on_notify:
            self.on_notify("ON" if ok else "GAGAL", "on" if ok else "error")

    def _hotkey_off(self):
        log("⌨️  Hotkey Alt+2 ditekan → ALLOFF")
        ok = self.monitor.send_now("ALLOFF")
        if self.on_notify:
            self.on_notify("OFF" if ok else "GAGAL", "off" if ok else "error")


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
        hotkey_label = "Hotkey: Alt+1=ON, Alt+2=OFF" if HAS_KEYBOARD else "Hotkey: (nonaktif)"
        return pystray.Menu(
            pystray.MenuItem(f"Status: {status_text}", self._noop, enabled=False),
            pystray.MenuItem(f"Port: {CONFIG['com_port']} @ {CONFIG['baud_rate']}", self._noop, enabled=False),
            pystray.MenuItem(hotkey_label, self._noop, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("💡 Nyalakan LED (Alt+1)", self._menu_on),
            pystray.MenuItem("🌑 Matikan LED (Alt+2)", self._menu_off),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("⚙️ Pengaturan Port...", self._open_settings),
            pystray.MenuItem("🔌 Test Koneksi", self._test_connection_now),
            pystray.MenuItem("🔄 Restart Monitoring", self._restart),
            pystray.MenuItem("📄 Buka Log", self._open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Keluar", self._quit),
        )

    def _noop(self, icon, item):
        pass

    def _on_start(self, app_list):
        self.icon.icon = make_icon("green")
        self.icon.title = f"Kanim Light Auto ({CONFIG['com_port']}) - AKTIF: {app_list}"
        self.icon.menu = self._build_menu(f"AKTIF ({app_list})")

    def _on_stop(self):
        self.icon.icon = make_icon("red")
        self.icon.title = f"Kanim Light Auto ({CONFIG['com_port']}) - Menunggu kamera"
        self.icon.menu = self._build_menu("Menunggu kamera")

    def _on_hotkey_notify(self, status, kind):
        """Tampilkan notifikasi kecil di title tray (tidak bisa popup karena thread)."""
        original_title = self.icon.title
        suffix = f" [Alt+1: {status}]" if kind == "on" else f" [Alt+2: {status}]" if kind == "off" else f" [{status}]"
        self.icon.title = original_title.split(" [Alt+")[0] + suffix
        # Reset title setelah 2 detik
        def reset():
            time.sleep(2)
            try:
                self.icon.title = self.icon.title.split(" [Alt+")[0].split(" [GAGAL")[0]
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

    def _open_log(self, icon, item):
        if os.path.exists(LOG_FILE):
            os.startfile(LOG_FILE)
        else:
            log("Log belum ada.")

    def _test_connection_now(self, icon, item):
        port = CONFIG["com_port"]
        baud = CONFIG["baud_rate"]
        try:
            s = serial.Serial(port, baud, timeout=1)
            time.sleep(0.5)
            s.write(b"ALLOFF\n")
            s.flush()
            time.sleep(0.3)
            resp = ""
            while s.in_waiting > 0:
                resp += s.readline().decode("ascii", errors="ignore")
            s.close()
            msg = f"✅ Port {port} OK\nRespons: {resp.strip() or '(kosong)'}"
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
                    f"baud={CONFIG['baud_rate']}")

                self.monitor.stop()
                self.monitor = CameraMonitor(on_start=self._on_start, on_stop=self._on_stop)
                self.monitor.start()

                # Update hotkey agar pakai monitor baru
                self.hotkeys.monitor = self.monitor

                self.icon.title = f"Kanim Light Auto ({CONFIG['com_port']}) - Menunggu kamera"
                self.icon.menu = self._build_menu(
                    "AKTIF" if self.monitor.camera_in_use else "Menunggu kamera"
                )

                def notif():
                    r = tk.Tk()
                    r.withdraw()
                    r.attributes("-topmost", True)
                    messagebox.showinfo(
                        "Pengaturan Tersimpan",
                        f"Port diubah ke {CONFIG['com_port']} @ {CONFIG['baud_rate']}.\n"
                        f"Monitoring di-restart otomatis.",
                        parent=r
                    )
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
        self.icon.menu = self._build_menu("Menunggu kamera")

    def _quit(self, icon, item):
        log("Keluar dari aplikasi.")
        self.hotkeys.unregister()
        self.monitor.stop()
        icon.stop()

    def run(self):
        log("=== APLIKASI DIMULAI ===")
        log(f"Config: port={CONFIG['com_port']}, baud={CONFIG['baud_rate']}, "
            f"interval={CONFIG['check_interval']}s")
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
    print("  python kanim_light_auto.py --help       Tampilkan bantuan ini")
    print()
    print("Hotkey global (saat aplikasi berjalan):")
    print("  Alt + 1  →  Nyalakan LED (ALLON)")
    print("  Alt + 2  →  Matikan LED (ALLOFF)")


if __name__ == "__main__":
    args = sys.argv[1:]

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

    if "--help" in args or "-h" in args:
        print_usage()
        sys.exit(0)

    if is_already_running():
        log("Instance sudah berjalan. Keluar.")
        sys.exit(0)

    TrayApp().run()