import tkinter as tk
from tkinter import ttk
import sys
import os
import serial
import threading
import time
import json
from datetime import datetime
import queue

# Import tab modules
from main_tab import MainTab
from status_tab import StatusTab
from tray_counts_tab import TrayCountsTab
from controls_tab import ControlsTab
from settings_tab import SettingsTab

def apply_saved_theme(root):
    try:
        with open("settings.json", "r") as f:
            config = json.load(f)
        theme = config.get("theme", "Regular")

        style = ttk.Style()
        if theme == "Dark":
            root.tk_setPalette(background="#2e2e2e", foreground="white")
            style.theme_use("default")
            style.configure(".", background="#2e2e2e", foreground="white")
            style.configure("TLabel", background="#2e2e2e", foreground="white")
            style.configure("TButton", background="#444", foreground="white")
            style.configure("TEntry", fieldbackground="#444", foreground="white")
            style.configure("TCombobox", fieldbackground="#444", foreground="white")
        else:
            root.tk_setPalette(background="SystemButtonFace", foreground="black")
            style.theme_use("default")
            style.configure(".", background="SystemButtonFace", foreground="black")
    except Exception as e:
        print(f"Theme load failed: {e}")

def apply_saved_resolution(root):
    """Apply saved resolution settings to the root window"""
    try:
        with open("settings.json", "r") as f:
            config = json.load(f)
        resolution = config.get("resolution", "85% Screen")
        
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        
        if resolution.endswith("% Screen"):
            # Percentage-based resolution
            percentage = int(resolution.split("%")[0]) / 100
            win_width = int(screen_width * percentage)
            win_height = int(screen_height * percentage)
            
            if percentage == 1.0:  # 100% Screen (Fullscreen)
                root.state('zoomed')  # Windows fullscreen
                return
            else:
                root.state('normal')
                
        elif resolution.startswith("Custom -"):
            # Custom resolution
            res_part = resolution.split("Custom - ")[1]
            win_width, win_height = map(int, res_part.split("x"))
            
            # Ensure custom resolution doesn't exceed screen size
            win_width = min(win_width, screen_width)
            win_height = min(win_height, screen_height)
            root.state('normal')
            
        else:
            # Default to 85% if unknown format
            win_width = int(screen_width * 0.85)
            win_height = int(screen_height * 0.85)
            root.state('normal')
        
        # Calculate position to center the window
        pos_x = (screen_width - win_width) // 2
        pos_y = (screen_height - win_height) // 2
        
        # Apply the geometry
        root.geometry(f"{win_width}x{win_height}+{pos_x}+{pos_y}")
        
    except Exception as e:
        print(f"Resolution load failed: {e}")
        # Fallback to default 85% screen size
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        win_width = int(screen_width * 0.85)
        win_height = int(screen_height * 0.85)
        pos_x = (screen_width - win_width) // 2
        pos_y = (screen_height - win_height) // 2
        root.geometry(f"{win_width}x{win_height}+{pos_x}+{pos_y}")

class SorterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Card Sorter Control System")

        # Apply saved resolution first
        apply_saved_resolution(self.root)
        
        # Set minimum size
        self.root.minsize(800, 480)
        
        # Configure root window grid weight for resizing
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.serial_connection = None
        self.is_connected = False
        self.serial_thread = None
        self.serial_thread_running = False
        self.response_queue = queue.Queue()
        self.command_queue = queue.Queue()
        self.tof_last_check = 0
        self.tof_check_interval = 2.0  # Check TOF every 2 seconds

        self.shared_data = {
            'arduino_connected': False,
            'tof_connected': False,
            'motors_on': False,
            'vacuum_on': False,
            'lights_on': False,
            'sensor_readings': {
                'range_mm': 0,
                'xmin': 0, 'xmax': 0,
                'ymin': 0, 'ymax': 0,
                'zmin': 0, 'zmax': 0
            },
            'system_log': [],
            'tray_counts': {i: 0 for i in range(1, 35)},
            'processed_cards': [],
            'total_processed': 0,
            'sorting_active': False,
            'current_card_data': None,
            'serial_port': 'COM3',
            'baud_rate': 9600
        }

        # Create notebook with proper expansion settings
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill='both', expand=True, padx=10, pady=10)

        self.init_tabs()
        
        # Bind window resize event to update tab frames
        self.root.bind('<Configure>', self.on_window_resize)
        
        # Check for auto-reconnect after tabs are initialized
        self.check_auto_reconnect()
        
        # Start the GUI update loop
        self.update_gui()

    def init_tabs(self):
        """Initialize all tabs with proper resizing configuration"""
        # Main Tab
        self.main_frame = ttk.Frame(self.notebook)
        self.main_frame.rowconfigure(0, weight=1)
        self.main_frame.columnconfigure(0, weight=1)
        self.notebook.add(self.main_frame, text="Main")
        self.main_tab = MainTab(self.main_frame, self.shared_data)

        # Status Tab
        self.status_frame = ttk.Frame(self.notebook)
        self.status_frame.rowconfigure(0, weight=1)
        self.status_frame.columnconfigure(0, weight=1)
        self.notebook.add(self.status_frame, text="Status")
        self.status_tab = StatusTab(self.status_frame, self.shared_data)

        # Tray Counts Tab
        self.tray_frame = ttk.Frame(self.notebook)
        self.tray_frame.rowconfigure(0, weight=1)
        self.tray_frame.columnconfigure(0, weight=1)
        self.notebook.add(self.tray_frame, text="Tray/Bin Counts")
        self.tray_tab = TrayCountsTab(self.tray_frame, self.shared_data)

        # Controls Tab
        self.controls_frame = ttk.Frame(self.notebook)
        self.controls_frame.rowconfigure(0, weight=1)
        self.controls_frame.columnconfigure(0, weight=1)
        self.notebook.add(self.controls_frame, text="Controls")
        self.controls_tab = ControlsTab(self.controls_frame, self)

        # Settings Tab
        self.settings_tab = SettingsTab(self.notebook, self)
        # Configure settings tab frame for resizing
        if hasattr(self.settings_tab, 'frame'):
            self.settings_tab.frame.rowconfigure(0, weight=1)
            self.settings_tab.frame.columnconfigure(0, weight=1)
        self.notebook.add(self.settings_tab.frame, text="Settings")
        
        # Notify tabs about initial sizing
        self.notify_tabs_resize()

    def on_window_resize(self, event):
        """Handle window resize events"""
        # Only handle resize events for the root window
        if event.widget == self.root:
            # Delay the resize notification to avoid excessive calls
            if hasattr(self, '_resize_after_id'):
                self.root.after_cancel(self._resize_after_id)
            self._resize_after_id = self.root.after(100, self.notify_tabs_resize)

    def notify_tabs_resize(self):
        """Notify all tabs that the window has been resized"""
        try:
            # Get current window dimensions
            window_width = self.root.winfo_width()
            window_height = self.root.winfo_height()
            
            # Notify each tab if it has a resize method
            tabs_with_resize = [
                self.main_tab, self.status_tab, self.tray_tab, 
                self.controls_tab, self.settings_tab
            ]
            
            for tab in tabs_with_resize:
                if hasattr(tab, 'on_window_resize'):
                    try:
                        tab.on_window_resize(window_width, window_height)
                    except Exception as e:
                        self.add_log_entry(f"Error notifying tab resize: {e}")
                        
            # Force update of all widgets
            self.root.update_idletasks()
            
        except Exception as e:
            self.add_log_entry(f"Error in notify_tabs_resize: {e}")

    def apply_new_resolution(self, resolution_setting):
        """Apply a new resolution setting and update all tabs"""
        try:
            # Save the new resolution setting
            config = {}
            try:
                with open("settings.json", "r") as f:
                    config = json.load(f)
            except FileNotFoundError:
                pass
            
            config["resolution"] = resolution_setting
            
            with open("settings.json", "w") as f:
                json.dump(config, f, indent=2)
            
            # Apply the new resolution
            apply_saved_resolution(self.root)
            
            # Wait a moment for the window to resize, then notify tabs
            self.root.after(200, self.notify_tabs_resize)
            
            self.add_log_entry(f"Applied resolution setting: {resolution_setting}")
            
        except Exception as e:
            self.add_log_entry(f"Error applying resolution: {e}")

    def get_current_resolution_setting(self):
        """Get the current window size as a resolution setting string"""
        try:
            geometry = self.root.geometry()
            # Parse geometry string like "1440x900+240+90"
            size_part = geometry.split('+')[0]
            width, height = map(int, size_part.split('x'))
            
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            
            # Check if it matches a percentage
            for percentage in [50, 60, 70, 80, 85, 90, 95]:
                expected_width = int(screen_width * percentage / 100)
                expected_height = int(screen_height * percentage / 100)
                
                # Allow for small differences due to rounding
                if abs(width - expected_width) <= 2 and abs(height - expected_height) <= 2:
                    return f"{percentage}% Screen"
            
            # Check if it's fullscreen
            if self.root.state() == 'zoomed':
                return "100% Screen (Fullscreen)"
            
            # Check common custom resolutions
            common_resolutions = [
                "1024x768", "1280x800", "1280x1024", "1366x768",
                "1440x900", "1600x900", "1680x1050", "1920x1080",
                "1920x1200", "2560x1440"
            ]
            
            current_res = f"{width}x{height}"
            if current_res in common_resolutions:
                return f"Custom - {current_res}"
            
            # Default to custom resolution
            return f"Custom - {current_res}"
            
        except Exception as e:
            print(f"Error getting current resolution: {e}")
            return "85% Screen"

    def check_auto_reconnect(self):
        """Check if auto-reconnect is enabled and connect if so"""
        try:
            with open("settings.json", "r") as f:
                config = json.load(f)
            
            if config.get("auto_reconnect", False):
                port = config.get("port", "")
                baud = config.get("baud", "9600")
                
                if port:
                    self.add_log_entry(f"Auto-reconnect enabled, connecting to {port}...")
                    # Delay the connection to allow UI to fully initialize
                    self.root.after(1000, lambda: self.connect_arduino(port, int(baud)))
                else:
                    self.add_log_entry("Auto-reconnect enabled but no port specified")
        except FileNotFoundError:
            self.add_log_entry("No settings file found, skipping auto-reconnect")
        except Exception as e:
            self.add_log_entry(f"Error checking auto-reconnect: {e}")

    def serial_thread_worker(self):
        """Serial communication thread worker"""
        self.add_log_entry("Serial communication thread started")
        
        while self.serial_thread_running:
            try:
                # Send any pending commands
                try:
                    while not self.command_queue.empty():
                        command = self.command_queue.get_nowait()
                        if self.serial_connection and self.serial_connection.is_open:
                            self.serial_connection.write(f"{command}\n".encode())
                            self.add_log_entry(f"Sent command: {command}")
                except queue.Empty:
                    pass
                except Exception as e:
                    self.add_log_entry(f"Error sending command: {e}")
                
                # Read incoming data
                if self.serial_connection and self.serial_connection.is_open:
                    if self.serial_connection.in_waiting > 0:
                        try:
                            response = self.serial_connection.readline().decode().strip()
                            if response:
                                self.response_queue.put(response)
                        except Exception as e:
                            self.add_log_entry(f"Error reading serial data: {e}")
                
                time.sleep(0.01)  # Small delay to prevent excessive CPU usage
                
            except Exception as e:
                self.add_log_entry(f"Serial thread error: {e}")
                time.sleep(0.1)
        
        self.add_log_entry("Serial communication thread stopped")

    def process_serial_responses(self):
        """Process responses from Arduino"""
        try:
            while not self.response_queue.empty():
                response = self.response_queue.get_nowait()
                self.handle_arduino_response(response)
        except queue.Empty:
            pass
        except Exception as e:
            self.add_log_entry(f"Error processing serial responses: {e}")

    def handle_arduino_response(self, response):
        """Handle responses from Arduino"""
        try:
            if response.startswith("TOF_OK"):
                self.shared_data['tof_connected'] = True
                # Parse TOF data if available
                parts = response.split(":")
                if len(parts) > 1:
                    try:
                        range_mm = float(parts[1])
                        self.shared_data['sensor_readings']['range_mm'] = range_mm
                    except ValueError:
                        pass
                        
            elif response.startswith("TOF_ERROR") or response.startswith("TOF_FAIL"):
                self.shared_data['tof_connected'] = False
                self.add_log_entry(f"TOF sensor error: {response}")
                
            elif response.startswith("MOTORS_ON_OK"):
                self.shared_data['motors_on'] = True
                self.add_log_entry("Motors activated successfully")
                
            elif response.startswith("MOTORS_OFF_OK"):
                self.shared_data['motors_on'] = False
                self.add_log_entry("Motors deactivated successfully")
                
            elif response.startswith("VACUUM_ON_OK"):
                self.shared_data['vacuum_on'] = True
                self.add_log_entry("Vacuum activated successfully")
                
            elif response.startswith("VACUUM_OFF_OK"):
                self.shared_data['vacuum_on'] = False
                self.add_log_entry("Vacuum deactivated successfully")
                
            elif response.startswith("LIGHTS_ON_OK"):
                self.shared_data['lights_on'] = True
                self.add_log_entry("Lights activated successfully")
                
            elif response.startswith("LIGHTS_OFF_OK"):
                self.shared_data['lights_on'] = False
                self.add_log_entry("Lights deactivated successfully")
                
            elif response.startswith("SENSOR_DATA"):
                # Parse sensor data: SENSOR_DATA:range:xmin:xmax:ymin:ymax:zmin:zmax
                parts = response.split(":")
                if len(parts) >= 8:
                    try:
                        self.shared_data['sensor_readings']['range_mm'] = float(parts[1])
                        self.shared_data['sensor_readings']['xmin'] = float(parts[2])
                        self.shared_data['sensor_readings']['xmax'] = float(parts[3])
                        self.shared_data['sensor_readings']['ymin'] = float(parts[4])
                        self.shared_data['sensor_readings']['ymax'] = float(parts[5])
                        self.shared_data['sensor_readings']['zmin'] = float(parts[6])
                        self.shared_data['sensor_readings']['zmax'] = float(parts[7])
                    except ValueError as e:
                        self.add_log_entry(f"Error parsing sensor data: {e}")
                        
            elif response.startswith("ERROR"):
                self.add_log_entry(f"Arduino error: {response}")
                
            else:
                # Log any other responses
                self.add_log_entry(f"Arduino response: {response}")
                
        except Exception as e:
            self.add_log_entry(f"Error handling Arduino response '{response}': {e}")

    def send_command(self, command):
        """Send command to Arduino via queue"""
        if self.is_connected and self.serial_connection:
            try:
                self.command_queue.put(command)
                return True
            except Exception as e:
                self.add_log_entry(f"Error queuing command {command}: {e}")
                return False
        else:
            self.add_log_entry(f"Cannot send command {command}: Not connected to Arduino")
            return False

    def check_tof_sensor(self):
        """Check if TOF sensor is connected and responding"""
        current_time = time.time()
        
        # Only check TOF sensor periodically to avoid flooding
        if current_time - self.tof_last_check < self.tof_check_interval:
            return self.shared_data['tof_connected']
        
        self.tof_last_check = current_time
        
        if not self.is_connected:
            self.shared_data['tof_connected'] = False
            return False
            
        try:
            # Send command to check TOF sensor
            self.send_command("CHECK_TOF")
            return self.shared_data['tof_connected']
        except Exception as e:
            self.add_log_entry(f"Error checking TOF sensor: {e}")
            self.shared_data['tof_connected'] = False
            return False

    def connect_arduino(self, port, baud_rate):
        """Connect to Arduino"""
        if self.is_connected:
            self.add_log_entry("Already connected to Arduino")
            return True
            
        try:
            self.serial_connection = serial.Serial(port, baud_rate, timeout=1)
            time.sleep(2)  # Allow time for Arduino to initialize
            
            self.is_connected = True
            self.shared_data['arduino_connected'] = True
            self.shared_data['serial_port'] = port
            self.shared_data['baud_rate'] = baud_rate
            
            # Start serial communication thread
            self.serial_thread_running = True
            self.serial_thread = threading.Thread(target=self.serial_thread_worker, daemon=True)
            self.serial_thread.start()
            
            self.add_log_entry(f"Connected to Arduino on {port} at {baud_rate} baud")
            
            # Initial TOF sensor check
            self.root.after(1000, self.check_tof_sensor)
            
            return True
            
        except Exception as e:
            self.add_log_entry(f"Failed to connect to Arduino: {e}")
            self.is_connected = False
            self.shared_data['arduino_connected'] = False
            self.shared_data['tof_connected'] = False
            if self.serial_connection:
                try:
                    self.serial_connection.close()
                except:
                    pass
                self.serial_connection = None
            return False

    def disconnect_arduino(self):
        """Disconnect from Arduino"""
        if self.serial_connection:
            try:
                # Stop serial thread
                self.serial_thread_running = False
                if self.serial_thread and self.serial_thread.is_alive():
                    self.serial_thread.join(timeout=2)
                
                # Close serial connection
                self.serial_connection.close()
                self.serial_connection = None
                
                # Update status
                self.is_connected = False
                self.shared_data['arduino_connected'] = False
                self.shared_data['tof_connected'] = False
                self.shared_data['motors_on'] = False
                self.shared_data['vacuum_on'] = False
                self.shared_data['lights_on'] = False
                
                self.add_log_entry("Disconnected from Arduino")
                
            except Exception as e:
                self.add_log_entry(f"Error disconnecting from Arduino: {e}")
        else:
            self.add_log_entry("No active Arduino connection to disconnect")

    def update_shared_data(self):
        """Update shared data with current status"""
        self.shared_data['arduino_connected'] = self.is_connected
        
        # Check TOF sensor status periodically
        if self.is_connected:
            self.check_tof_sensor()
        else:
            self.shared_data['tof_connected'] = False

    def update_gui(self):
        """Update GUI components"""
        try:
            # Process any serial responses
            self.process_serial_responses()
            
            # Update shared data
            self.update_shared_data()
            
            # Update all tabs
            if hasattr(self.main_tab, 'update'):
                self.main_tab.update()
            if hasattr(self.status_tab, 'update'):
                self.status_tab.update()
            if hasattr(self.tray_tab, 'update'):
                self.tray_tab.update()
            if hasattr(self.controls_tab, 'update'):
                self.controls_tab.update()
            if hasattr(self.settings_tab, 'update'):
                self.settings_tab.update()
                
        except Exception as e:
            print(f"GUI update error: {e}")
            
        # Schedule next update
        self.root.after(100, self.update_gui)

    def add_log_entry(self, message):
        """Add entry to system log"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.shared_data['system_log'].append(log_entry)
        
        # Keep log size manageable
        if len(self.shared_data['system_log']) > 1000:
            self.shared_data['system_log'] = self.shared_data['system_log'][-1000:]
        
        print(log_entry)

    def on_closing(self):
        """Handle application closing"""
        self.add_log_entry("Application shutting down...")
        
        # Stop serial communication
        if self.is_connected:
            self.disconnect_arduino()
        
        # Close the application
        self.root.destroy()

def main():
    root = tk.Tk()
    apply_saved_theme(root)
    app = SorterGUI(root)
    
    # Handle window closing
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    
    root.mainloop()

if __name__ == "__main__":
    main()