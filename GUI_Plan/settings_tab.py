import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial.tools.list_ports
import serial
import threading
import time
import os
import subprocess
import sys
import tempfile
import shutil
import urllib.request
import zipfile
import json

from PIL import Image, ImageTk

class SettingsTab:
    def __init__(self, parent, main_app):
        self.parent = parent
        self.main_app = main_app
        self.frame = ttk.Frame(parent)

        self.selected_port = tk.StringVar()
        self.selected_baud = tk.StringVar(value="9600")
        self.timeout_var = tk.StringVar(value="1")
        self.auto_reconnect = tk.BooleanVar(value=False)
        self.theme_var = tk.StringVar(value="Regular")
        self.resolution_var = tk.StringVar(value="85% Screen")

        self.upload_img = None

        self.setup_ui()
        self.refresh_ports()
        
        # Load settings and auto-connect if enabled
        self.load_settings()
        if self.auto_reconnect.get():
            self.main_app.root.after(1000, self.connect_arduino)  # Delay to allow UI to fully initialize

    def refresh_ports(self):
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports and not self.selected_port.get():
            self.selected_port.set(ports[0])

    def ensure_cli_installed(self):
        cli_path = shutil.which("arduino-cli")
        if cli_path:
            return True
        messagebox.showinfo("Installing Arduino CLI", "Arduino CLI not found. Installing...")
        version = "0.35.3"
        url = f"https://downloads.arduino.cc/arduino-cli/arduino-cli_{version}_Windows_64bit.zip"
        local_zip = os.path.join(tempfile.gettempdir(), "arduino-cli.zip")
        install_dir = os.path.join(os.getenv("USERPROFILE"), "AppData", "Local", "Programs", "arduino-cli")
        try:
            os.makedirs(install_dir, exist_ok=True)
            urllib.request.urlretrieve(url, local_zip)
            with zipfile.ZipFile(local_zip, 'r') as zip_ref:
                zip_ref.extractall(install_dir)
            os.environ["PATH"] += os.pathsep + install_dir
            messagebox.showinfo("Success", f"Arduino CLI installed to {install_dir}")
            return True
        except Exception as e:
            messagebox.showerror("Installation Failed", f"Failed to install Arduino CLI:\n{e}")
            return False

    def get_resolution_options(self):
        """Get available resolution options based on screen size"""
        screen_width = self.main_app.root.winfo_screenwidth()
        screen_height = self.main_app.root.winfo_screenheight()
        
        options = [
            "50% Screen",
            "60% Screen", 
            "70% Screen",
            "80% Screen",
            "85% Screen",
            "90% Screen",
            "95% Screen",
            "100% Screen (Fullscreen)",
            "Custom - 800x480",
            "Custom - 1024x768",
            "Custom - 1280x800",
            "Custom - 1280x1024",
            "Custom - 1366x768",
            "Custom - 1440x900",
            "Custom - 1600x900",
            "Custom - 1680x1050",
            "Custom - 1920x1080",
            "Custom - 1920x1200",
            "Custom - 2560x1440"
        ]
        
        # Filter out custom resolutions that are larger than screen
        filtered_options = []
        for option in options:
            if option.startswith("Custom -"):
                # Extract width and height from custom option
                try:
                    res_part = option.split("Custom - ")[1]
                    width, height = map(int, res_part.split("x"))
                    if width <= screen_width and height <= screen_height:
                        filtered_options.append(option)
                except:
                    continue
            else:
                filtered_options.append(option)
        
        return filtered_options

    def apply_resolution(self, resolution_setting):
        """Apply the selected resolution setting"""
        try:
            screen_width = self.main_app.root.winfo_screenwidth()
            screen_height = self.main_app.root.winfo_screenheight()
            
            if resolution_setting.endswith("% Screen"):
                # Percentage-based resolution
                percentage = int(resolution_setting.split("%")[0]) / 100
                win_width = int(screen_width * percentage)
                win_height = int(screen_height * percentage)
                
                if percentage == 1.0:  # 100% Screen (Fullscreen)
                    self.main_app.root.state('zoomed')  # Windows fullscreen
                    return
                else:
                    # Exit fullscreen if currently in fullscreen
                    self.main_app.root.state('normal')
                    
            elif resolution_setting.startswith("Custom -"):
                # Custom resolution
                res_part = resolution_setting.split("Custom - ")[1]
                win_width, win_height = map(int, res_part.split("x"))
                
                # Ensure custom resolution doesn't exceed screen size
                win_width = min(win_width, screen_width)
                win_height = min(win_height, screen_height)
                
                # Exit fullscreen if currently in fullscreen
                self.main_app.root.state('normal')
                
            else:
                # Default to 85% if unknown format
                win_width = int(screen_width * 0.85)
                win_height = int(screen_height * 0.85)
                self.main_app.root.state('normal')
            
            # Calculate position to center the window
            pos_x = (screen_width - win_width) // 2
            pos_y = (screen_height - win_height) // 2
            
            # Apply the new geometry
            self.main_app.root.geometry(f"{win_width}x{win_height}+{pos_x}+{pos_y}")
            
            self.update_info(f"Resolution changed to: {resolution_setting}")
            
        except Exception as e:
            self.update_info(f"Error applying resolution: {e}")

    def setup_ui(self):
        # Serial Settings Frame
        label_frame = ttk.LabelFrame(self.frame, text="Serial Settings")
        label_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(label_frame, text="COM Port:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.port_combo = ttk.Combobox(label_frame, textvariable=self.selected_port, state="readonly", width=15)
        self.port_combo.grid(row=0, column=1, padx=5, pady=5)
        refresh_btn = ttk.Button(label_frame, text="Refresh Ports", command=self.refresh_ports)
        refresh_btn.grid(row=0, column=2, padx=5, pady=5)

        ttk.Label(label_frame, text="Baud Rate:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        baud_rates = ["9600", "14400", "19200", "38400", "57600", "115200"]
        self.baud_combo = ttk.Combobox(label_frame, textvariable=self.selected_baud, values=baud_rates, state="readonly", width=15)
        self.baud_combo.grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(label_frame, text="Timeout (s):").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        self.timeout_entry = ttk.Entry(label_frame, textvariable=self.timeout_var, width=17)
        self.timeout_entry.grid(row=2, column=1, padx=5, pady=5)

        self.auto_reconnect_chk = ttk.Checkbutton(label_frame, text="Auto Reconnect", variable=self.auto_reconnect)
        self.auto_reconnect_chk.grid(row=3, column=0, columnspan=2, padx=5, pady=5, sticky="w")

        # Arduino Control Buttons
        arduino_btn_frame = ttk.Frame(self.frame)
        arduino_btn_frame.pack(fill="x", padx=10, pady=(0, 10))

        btn_width = 14
        connect_btn = tk.Button(arduino_btn_frame, text="Connect", command=self.connect_arduino, width=btn_width)
        connect_btn.pack(side=tk.LEFT, padx=5)
        disconnect_btn = tk.Button(arduino_btn_frame, text="Disconnect", command=self.disconnect_arduino, width=btn_width)
        disconnect_btn.pack(side=tk.LEFT, padx=5)
        test_btn = tk.Button(arduino_btn_frame, text="Test Connection", command=self.test_connection, width=btn_width)
        test_btn.pack(side=tk.LEFT, padx=5)

        # Theme and Resolution Settings Frame
        appearance_frame = ttk.LabelFrame(self.frame, text="Appearance Settings")
        appearance_frame.pack(fill="x", padx=10, pady=5)

        # Theme selection
        ttk.Label(appearance_frame, text="Theme:").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        theme_menu = ttk.Combobox(appearance_frame, textvariable=self.theme_var, values=["Regular", "Dark"], state="readonly", width=15)
        theme_menu.grid(row=0, column=1, padx=5, pady=5)
        theme_menu.bind("<<ComboboxSelected>>", lambda e: self.apply_theme(self.theme_var.get()))

        # Resolution selection
        ttk.Label(appearance_frame, text="Window Size:").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        resolution_options = self.get_resolution_options()
        self.resolution_menu = ttk.Combobox(appearance_frame, textvariable=self.resolution_var, 
                                          values=resolution_options, state="readonly", width=20)
        self.resolution_menu.grid(row=1, column=1, padx=5, pady=5)
        self.resolution_menu.bind("<<ComboboxSelected>>", lambda e: self.apply_resolution(self.resolution_var.get()))

        # Apply Resolution Button
        apply_res_btn = ttk.Button(appearance_frame, text="Apply Size", 
                                  command=lambda: self.apply_resolution(self.resolution_var.get()))
        apply_res_btn.grid(row=1, column=2, padx=5, pady=5)

        # Info/Output Frame
        info_frame = ttk.LabelFrame(self.frame, text="Info / Output")
        info_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.info_text = tk.Text(info_frame, height=10, state="disabled")
        self.info_text.pack(fill="both", expand=True, padx=5, pady=5)

        # Top Right Buttons Frame
        top_right_frame = ttk.Frame(self.frame)
        top_right_frame.place(relx=1.0, y=5, anchor="ne", width=320)

        try:
            pil_image = Image.open("upload_sketch.png")
            pil_image = pil_image.resize((100, 100), Image.LANCZOS)
            self.upload_img = ImageTk.PhotoImage(pil_image)
        except Exception as e:
            print(f"Failed to load image: {e}")
            self.upload_img = None

        btn_padx = 5
        save_btn = tk.Button(top_right_frame, text="Save Settings", command=self.save_settings, bg="lightgreen", width=12, height=6)
        save_btn.pack(side=tk.LEFT, padx=btn_padx)
        load_btn = tk.Button(top_right_frame, text="Load Settings", command=self.load_settings, bg="lightblue", width=12, height=6)
        load_btn.pack(side=tk.LEFT, padx=btn_padx)

        if self.upload_img:
            arduino_loader_btn = tk.Button(top_right_frame, image=self.upload_img, command=self.on_arduino_loader_clicked,
                                           bg="lightyellow", width=100, height=100)
        else:
            arduino_loader_btn = tk.Button(top_right_frame, text="Arduino Loader", command=self.on_arduino_loader_clicked,
                                           bg="lightyellow", width=12, height=6)
        arduino_loader_btn.pack(side=tk.LEFT, padx=btn_padx)

        self.frame.grid_columnconfigure(0, weight=1)
        self.update_info("Application started")

    def apply_theme(self, theme):
        style = ttk.Style()
        if theme == "Dark":
            self.parent.tk_setPalette(background="#2e2e2e", foreground="white")
            style.configure(".", background="#2e2e2e", foreground="white")
            style.configure("TLabel", background="#2e2e2e", foreground="white")
            style.configure("TButton", background="#444", foreground="white")
            style.configure("TEntry", fieldbackground="#444", foreground="white")
            style.configure("TCombobox", fieldbackground="#444", foreground="white")
            self.info_text.config(bg="#1e1e1e", fg="white", insertbackground="white")
        else:
            self.parent.tk_setPalette(background="SystemButtonFace", foreground="black")
            style.configure(".", background="SystemButtonFace", foreground="black")
            self.info_text.config(bg="white", fg="black", insertbackground="black")

    def connect_arduino(self):
        """Connect to Arduino using the main app's connection system"""
        port = self.selected_port.get()
        baud = self.selected_baud.get()
        
        if not port:
            self.update_info("No COM port selected.")
            return
        
        if self.main_app.is_connected:
            self.update_info("Already connected.")
            return
        
        # Use the main app's connection method
        success = self.main_app.connect_arduino(port, int(baud))
        
        if success:
            self.update_info(f"Connected to {port} at {baud} baud.")
            # Update shared data
            self.main_app.shared_data['serial_port'] = port
            self.main_app.shared_data['baud_rate'] = int(baud)
        else:
            self.update_info(f"Failed to connect to {port}")

    def disconnect_arduino(self):
        """Disconnect from Arduino using the main app's connection system"""
        if self.main_app.is_connected:
            self.main_app.disconnect_arduino()
            self.update_info("Disconnected from Arduino.")
        else:
            self.update_info("No active connection.")

    def test_connection(self):
        port = self.selected_port.get()
        baud = self.selected_baud.get()
        timeout = float(self.timeout_var.get()) if self.timeout_var.get() else 1.0
        if not port:
            self.update_info("No COM port selected.")
            return
        self.update_info(f"Testing {port} @ {baud} baud...")
        try:
            with serial.Serial(port=port, baudrate=int(baud), timeout=timeout):
                time.sleep(0.5)
            self.update_info("Test successful.")
        except Exception as e:
            self.update_info(f"Test failed: {e}")

    def on_arduino_loader_clicked(self):
        def target():
            self.update_info("Checking Arduino CLI...")
            if self.ensure_cli_installed():
                self.run_arduino_loader()
            else:
                self.update_info("CLI install failed.")
        threading.Thread(target=target, daemon=True).start()

    def run_arduino_loader(self):
        try:
            python_exe = sys.executable
            script_path = os.path.join(os.getcwd(), "arduino_loader.py")
            if not os.path.isfile(script_path):
                self.update_info("arduino_loader.py not found.")
                return
            self.update_info("Launching loader...")
            proc = subprocess.Popen(
                [python_exe, script_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            for line in proc.stdout:
                self.update_info(line.strip())
            proc.wait()
            if proc.returncode == 0:
                self.update_info("Loader finished.")
            else:
                err = proc.stderr.read()
                self.update_info(f"Loader error: {err}")
        except Exception as e:
            self.update_info(f"Loader failed: {e}")

    def save_settings(self):
        config = {
            "port": self.selected_port.get(),
            "baud": self.selected_baud.get(),
            "timeout": self.timeout_var.get(),
            "auto_reconnect": self.auto_reconnect.get(),
            "theme": self.theme_var.get(),
            "resolution": self.resolution_var.get()
        }
        try:
            with open("settings.json", "w") as f:
                json.dump(config, f, indent=2)
            self.update_info("Settings saved.")
        except Exception as e:
            self.update_info(f"Save failed: {e}")

    def load_settings(self):
        try:
            with open("settings.json", "r") as f:
                config = json.load(f)
            self.selected_port.set(config.get("port", ""))
            self.selected_baud.set(config.get("baud", "9600"))
            self.timeout_var.set(config.get("timeout", "1"))
            self.auto_reconnect.set(config.get("auto_reconnect", False))
            self.theme_var.set(config.get("theme", "Regular"))
            self.resolution_var.set(config.get("resolution", "85% Screen"))
            
            # Apply loaded theme and resolution
            self.apply_theme(self.theme_var.get())
            # Apply resolution with a small delay to ensure UI is ready
            self.main_app.root.after(100, lambda: self.apply_resolution(self.resolution_var.get()))
            
            self.update_info("Settings loaded.")
        except Exception as e:
            self.update_info(f"Load failed: {e}")

    def update_info(self, text):
        self.info_text.config(state="normal")
        self.info_text.insert("end", f"{text}\n")
        self.info_text.see("end")
        self.info_text.config(state="disabled")

    def update(self):
        """Update method for compatibility with main GUI loop"""
        pass