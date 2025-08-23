import tkinter as tk
from tkinter import ttk
from datetime import datetime

class ControlsTab:
    def __init__(self, parent, main_app):
        self.parent = parent
        self.main_app = main_app
        
        # Track if tab is active/initialized (must be set before setup_ui)
        self.is_active = True
        
        # Motor states - get from shared_data instead of local variables
        self.shared_data = main_app.shared_data
        self.motor_states = {"x": False, "y": False, "z": False}
        
        # Create widgets directly in the parent frame instead of a separate frame
        self.setup_ui()
        
    def setup_ui(self):
        # Main motor controls
        main_frame = ttk.LabelFrame(self.parent, text="Motor Controls", padding="10")
        main_frame.grid(row=1, column=0, columnspan=3, sticky="ew", padx=5, pady=5)
        
        # All motors control
        self.all_motors_btn = tk.Button(main_frame, text="Turn All Motors ON", 
                                       command=self.toggle_all_motors,
                                       bg="red", fg="white", font=("Arial", 12, "bold"))
        self.all_motors_btn.grid(row=0, column=0, columnspan=6, pady=10, sticky="ew")
        
        # Individual motor controls with incremental movement
        motor_groups = ["X", "Y", "Z"]
        self.motor_buttons = {}
        self.increment_entries = {}
        
        for i, group in enumerate(motor_groups):
            row = i + 1
            
            # Motor group label
            ttk.Label(main_frame, text=f"{group} Axis:", font=("Arial", 10, "bold")).grid(
                row=row, column=0, sticky="w", padx=5)
            
            # On/Off toggle button
            btn_key = f"{group.lower()}_toggle"
            self.motor_buttons[btn_key] = tk.Button(main_frame, text=f"{group} OFF", 
                                                   command=lambda g=group.lower(): self.toggle_motor_group(g),
                                                   bg="red", fg="white", width=8)
            self.motor_buttons[btn_key].grid(row=row, column=1, padx=5, pady=2)
            
            # Increment value entry
            ttk.Label(main_frame, text="Step:").grid(row=row, column=2, sticky="w", padx=5)
            entry_key = f"{group.lower()}_increment"
            self.increment_entries[entry_key] = tk.Entry(main_frame, width=8)
            self.increment_entries[entry_key].insert(0, "1.0")  # Default increment
            self.increment_entries[entry_key].grid(row=row, column=3, padx=5, pady=2)
            
            # Movement buttons
            minus_btn = tk.Button(main_frame, text=f"{group}-", 
                                 command=lambda g=group.lower(): self.move_motor_negative(g),
                                 bg="orange", fg="white", width=5)
            minus_btn.grid(row=row, column=4, padx=2, pady=2)
            
            plus_btn = tk.Button(main_frame, text=f"{group}+", 
                                command=lambda g=group.lower(): self.move_motor_positive(g),
                                bg="orange", fg="white", width=5)
            plus_btn.grid(row=row, column=5, padx=2, pady=2)
        
        # Configure column weights for proper resizing
        for col in range(6):
            main_frame.grid_columnconfigure(col, weight=1)
        
        # Auxiliary controls
        aux_frame = ttk.LabelFrame(self.parent, text="Auxiliary Controls", padding="10")
        aux_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=5, pady=5)
        
        # Vacuum control - toggle on/off
        self.vacuum_btn = tk.Button(aux_frame, text="Vacuum OFF", 
                                   command=self.toggle_vacuum,
                                   bg="red", fg="white", width=15, height=2)
        self.vacuum_btn.grid(row=0, column=0, padx=10, pady=5)
        
        # Lights control - toggle on/off
        self.lights_btn = tk.Button(aux_frame, text="Lights OFF", 
                                   command=self.toggle_lights,
                                   bg="red", fg="white", width=15, height=2)
        self.lights_btn.grid(row=0, column=1, padx=10, pady=5)
        
        # Action log
        log_frame = ttk.LabelFrame(self.parent, text="Action Log", padding="10")
        log_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", padx=5, pady=5)
        
        # Configure row/column weights for resizing
        self.parent.grid_rowconfigure(3, weight=1)
        self.parent.grid_columnconfigure(0, weight=1)
        
        # Log text widget with scrollbar
        self.log_text = tk.Text(log_frame, height=10, width=70)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        
        self.log_text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Clear log button
        clear_btn = tk.Button(log_frame, text="Clear Log", command=self.clear_log)
        clear_btn.pack(pady=5)
        
        # Initialize log
        self.log_action("System initialized")
    
    def log_action(self, action):
        """Add action to log with timestamp"""
        if not self.is_active:
            return
            
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {action}\n"
        self.log_text.insert(tk.END, log_entry)
        self.log_text.see(tk.END)
    
    def clear_log(self):
        """Clear the action log"""
        if not self.is_active:
            return
            
        self.log_text.delete(1.0, tk.END)
        self.log_action("Log cleared")
    
    def toggle_all_motors(self):
        """Toggle all motors on/off"""
        if not self.is_active:
            return
            
        if not self.main_app.is_connected:
            self.log_action("ERROR: Not connected to Arduino")
            return
            
        # Toggle based on shared_data state
        new_state = not self.shared_data['motors_on']
        self.shared_data['motors_on'] = new_state
        
        if new_state:
            self.all_motors_btn.config(text="Turn All Motors OFF", bg="green")
            self.main_app.send_command("MOTORS_ON")
            self.log_action("All motors turned ON")
        else:
            self.all_motors_btn.config(text="Turn All Motors ON", bg="red")
            self.main_app.send_command("MOTORS_OFF")
            self.log_action("All motors turned OFF")
    
    def toggle_motor_group(self, group):
        """Toggle specific motor group on/off"""
        if not self.is_active:
            return
            
        if not self.main_app.is_connected:
            self.log_action("ERROR: Not connected to Arduino")
            return
            
        self.motor_states[group] = not self.motor_states[group]
        btn_key = f"{group}_toggle"
        
        if self.motor_states[group]:
            self.motor_buttons[btn_key].config(text=f"{group.upper()} ON", bg="green")
            self.main_app.send_command(f"MOTOR_{group.upper()}_ON")
            self.log_action(f"{group.upper()} axis motor turned ON")
        else:
            self.motor_buttons[btn_key].config(text=f"{group.upper()} OFF", bg="red")
            self.main_app.send_command(f"MOTOR_{group.upper()}_OFF")
            self.log_action(f"{group.upper()} axis motor turned OFF")
    
    def move_motor_positive(self, group):
        """Move motor in positive direction by increment"""
        if not self.is_active:
            return
            
        if not self.main_app.is_connected:
            self.log_action("ERROR: Not connected to Arduino")
            return
            
        if not self.motor_states[group]:
            self.log_action(f"ERROR: {group.upper()} motor is not enabled")
            return
            
        try:
            entry_key = f"{group}_increment"
            increment = float(self.increment_entries[entry_key].get())
            command = f"MOVE_{group.upper()}_POS_{increment}"
            self.main_app.send_command(command)
            self.log_action(f"{group.upper()} axis moved +{increment} units")
        except ValueError:
            self.log_action(f"ERROR: Invalid increment value for {group.upper()} axis")
    
    def move_motor_negative(self, group):
        """Move motor in negative direction by increment"""
        if not self.is_active:
            return
            
        if not self.main_app.is_connected:
            self.log_action("ERROR: Not connected to Arduino")
            return
            
        if not self.motor_states[group]:
            self.log_action(f"ERROR: {group.upper()} motor is not enabled")
            return
            
        try:
            entry_key = f"{group}_increment"
            increment = float(self.increment_entries[entry_key].get())
            command = f"MOVE_{group.upper()}_NEG_{increment}"
            self.main_app.send_command(command)
            self.log_action(f"{group.upper()} axis moved -{increment} units")
        except ValueError:
            self.log_action(f"ERROR: Invalid increment value for {group.upper()} axis")
    
    def toggle_vacuum(self):
        """Toggle vacuum on/off"""
        if not self.is_active:
            return
            
        if not self.main_app.is_connected:
            self.log_action("ERROR: Not connected to Arduino")
            return
            
        # Update shared_data instead of local variable
        new_state = not self.shared_data['vacuum_on']
        self.shared_data['vacuum_on'] = new_state
        
        if new_state:
            self.vacuum_btn.config(text="Vacuum ON", bg="green")
            self.main_app.send_command("VACUUM_ON")
            self.log_action("Vacuum turned ON")
        else:
            self.vacuum_btn.config(text="Vacuum OFF", bg="red")
            self.main_app.send_command("VACUUM_OFF")
            self.log_action("Vacuum turned OFF")
    
    def toggle_lights(self):
        """Toggle lights on/off"""
        if not self.is_active:
            return
            
        if not self.main_app.is_connected:
            self.log_action("ERROR: Not connected to Arduino")
            return
            
        # Update shared_data instead of local variable
        new_state = not self.shared_data['lights_on']
        self.shared_data['lights_on'] = new_state
        
        if new_state:
            self.lights_btn.config(text="Lights ON", bg="green")
            self.main_app.send_command("LIGHTS_ON")
            self.log_action("Lights turned ON")
        else:
            self.lights_btn.config(text="Lights OFF", bg="red")
            self.main_app.send_command("LIGHTS_OFF")
            self.log_action("Lights turned OFF")
    
    def update(self):
        """Update method for compatibility - sync UI with shared_data"""
        if not self.is_active:
            return
            
        # Update button states based on shared_data
        if self.shared_data['motors_on']:
            self.all_motors_btn.config(text="Turn All Motors OFF", bg="green")
        else:
            self.all_motors_btn.config(text="Turn All Motors ON", bg="red")
            
        if self.shared_data['vacuum_on']:
            self.vacuum_btn.config(text="Vacuum ON", bg="green")
        else:
            self.vacuum_btn.config(text="Vacuum OFF", bg="red")
            
        if self.shared_data['lights_on']:
            self.lights_btn.config(text="Lights ON", bg="green")
        else:
            self.lights_btn.config(text="Lights OFF", bg="red")
    
    def safe_shutdown(self):
        """Safely shutdown all systems before closing"""
        if not self.is_active:
            return
            
        self.log_action("Initiating safe shutdown...")
        
        # Turn off all systems safely
        if self.shared_data['motors_on']:
            self.toggle_all_motors()
        
        for group in ["x", "y", "z"]:
            if self.motor_states[group]:
                self.toggle_motor_group(group)
        
        if self.shared_data['vacuum_on']:
            self.toggle_vacuum()
            
        if self.shared_data['lights_on']:
            self.toggle_lights()
        
        self.log_action("Safe shutdown completed")
    
    def close(self):
        """Close and cleanup the tab"""
        if not self.is_active:
            return
            
        self.log_action("Closing Controls Tab...")
        
        # Perform safe shutdown
        self.safe_shutdown()
        
        # Mark as inactive
        self.is_active = False
        
        # Clear references
        self.motor_buttons.clear()
        self.increment_entries.clear()
        self.main_app = None
        
        # Destroy all widgets in parent
        for widget in self.parent.winfo_children():
            widget.destroy()
            
        print("ControlsTab closed and cleaned up")
    
    def __del__(self):
        """Destructor to ensure cleanup"""
        if hasattr(self, 'is_active') and self.is_active:
            self.close()