import tkinter as tk
from tkinter import ttk

class StatusTab:
    def __init__(self, parent, shared_data):
        self.parent = parent
        self.shared_data = shared_data
        self.setup_ui()
        
    def setup_ui(self):
        # Main container
        main_container = ttk.Frame(self.parent)
        main_container.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Top section with status indicators
        top_frame = ttk.Frame(main_container)
        top_frame.pack(fill='x', pady=(0, 10))
        
        # System Status Area
        self.setup_system_status(top_frame)
        
        # Sensor Readings Area
        self.setup_sensor_readings(top_frame)
        
        # System Log Area
        self.setup_system_log(main_container)
        
    def setup_system_status(self, parent):
        status_frame = ttk.LabelFrame(parent, text="System Status", padding="10")
        status_frame.pack(side='left', fill='both', expand=True, padx=(0, 5))
        
        # Create status indicators
        self.status_indicators = {}
        
        status_items = [
            ("Arduino Connected", "arduino_connected"),
            ("TOF Connected", "tof_connected"),
            ("Motors ON", "motors_on"),
            ("Vacuum ON", "vacuum_on"),
            ("Lights ON", "lights_on")
        ]
        
        for i, (label, key) in enumerate(status_items):
            frame = ttk.Frame(status_frame)
            frame.pack(fill='x', pady=2)
            
            ttk.Label(frame, text=label + ":").pack(side='left')
            
            indicator = tk.Label(frame, text="●", font=('Arial', 12), 
                               fg='red', width=3)
            indicator.pack(side='right')
            
            self.status_indicators[key] = indicator
            
    def setup_sensor_readings(self, parent):
        sensor_frame = ttk.LabelFrame(parent, text="Sensor Readings", padding="10")
        sensor_frame.pack(side='right', fill='both', expand=True, padx=(5, 0))
        
        # Create sensor reading labels
        self.sensor_labels = {}
        
        sensor_items = [
            ("Range (mm)", "range_mm"),
            ("X Min", "xmin"),
            ("X Max", "xmax"),
            ("Y Min", "ymin"),
            ("Y Max", "ymax"),
            ("Z Min", "zmin"),
            ("Z Max", "zmax")
        ]
        
        for i, (label, key) in enumerate(sensor_items):
            frame = ttk.Frame(sensor_frame)
            frame.pack(fill='x', pady=2)
            
            ttk.Label(frame, text=label + ":").pack(side='left')
            
            value_label = ttk.Label(frame, text="0", font=('Arial', 10, 'bold'))
            value_label.pack(side='right')
            
            self.sensor_labels[key] = value_label
            
    def setup_system_log(self, parent):
        log_frame = ttk.LabelFrame(parent, text="System Log", padding="10")
        log_frame.pack(fill='both', expand=True)
        
        # Create text widget with scrollbar
        log_container = ttk.Frame(log_frame)
        log_container.pack(fill='both', expand=True)
        
        self.log_text = tk.Text(log_container, height=15, font=('Courier', 9))
        scrollbar = ttk.Scrollbar(log_container, orient='vertical', 
                                 command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        
        self.log_text.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        
        # Control buttons
        button_frame = ttk.Frame(log_frame)
        button_frame.pack(fill='x', pady=(5, 0))
        
        ttk.Button(button_frame, text="Clear Log", 
                  command=self.clear_log).pack(side='left')
        ttk.Button(button_frame, text="Save Log", 
                  command=self.save_log).pack(side='left', padx=(5, 0))
        ttk.Button(button_frame, text="Auto-scroll", 
                  command=self.toggle_autoscroll).pack(side='right')
        
        self.autoscroll = True
        
    def update_status_indicators(self):
        """Update status indicator colors"""
        for key, indicator in self.status_indicators.items():
            if self.shared_data[key]:
                indicator.config(fg='green')
            else:
                indicator.config(fg='red')
                
    def update_sensor_readings(self):
        """Update sensor reading values"""
        readings = self.shared_data['sensor_readings']
        for key, label in self.sensor_labels.items():
            value = readings.get(key, 0)
            if key == 'range_mm':
                label.config(text=f"{value:.1f}")
            else:
                label.config(text=f"{value:.2f}")
                
    def update_system_log(self):
        """Update system log display"""
        # Get current log entries
        log_entries = self.shared_data['system_log']
        
        # Get current text content
        current_text = self.log_text.get(1.0, tk.END)
        
        # Check if we need to add new entries
        if log_entries:
            new_text = '\n'.join(log_entries)
            if new_text != current_text.strip():
                self.log_text.delete(1.0, tk.END)
                self.log_text.insert(tk.END, new_text)
                
                # Auto-scroll to bottom if enabled
                if self.autoscroll:
                    self.log_text.see(tk.END)
                    
    def clear_log(self):
        """Clear the system log"""
        self.shared_data['system_log'].clear()
        self.log_text.delete(1.0, tk.END)
        
    def save_log(self):
        """Save log to file"""
        from tkinter import filedialog
        import datetime
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialname=f"system_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        )
        
        if filename:
            try:
                with open(filename, 'w') as f:
                    f.write('\n'.join(self.shared_data['system_log']))
                self.add_log(f"Log saved to {filename}")
            except Exception as e:
                self.add_log(f"Error saving log: {e}")
                
    def toggle_autoscroll(self):
        """Toggle auto-scroll feature"""
        self.autoscroll = not self.autoscroll
        status = "enabled" if self.autoscroll else "disabled"
        self.add_log(f"Auto-scroll {status}")
        
    def add_log(self, message):
        """Add entry to system log"""
        import datetime
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}"
        self.shared_data['system_log'].append(log_entry)
        if len(self.shared_data['system_log']) > 1000:
            self.shared_data['system_log'] = self.shared_data['system_log'][-1000:]
            
    def update(self):
        """Called periodically to update the tab"""
        self.update_status_indicators()
        self.update_sensor_readings()
        self.update_system_log()
