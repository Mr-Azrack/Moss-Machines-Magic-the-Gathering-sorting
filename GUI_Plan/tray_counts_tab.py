import tkinter as tk
from tkinter import ttk, messagebox
import csv
from tkinter import filedialog
import datetime

class TrayCountsTab:
    def __init__(self, parent, shared_data):
        self.parent = parent
        self.shared_data = shared_data
        self.setup_ui()
        
    def setup_ui(self):
        # Main container
        main_container = ttk.Frame(self.parent)
        main_container.pack(fill='both', expand=True, padx=10, pady=10)
        
        # Top section - Summary and controls
        top_frame = ttk.Frame(main_container)
        top_frame.pack(fill='x', pady=(0, 10))
        
        # Summary info
        summary_frame = ttk.LabelFrame(top_frame, text="Summary", padding="10")
        summary_frame.pack(side='left', fill='y', padx=(0, 10))
        
        self.total_label = ttk.Label(summary_frame, text="Total Processed: 0", 
                                    font=('Arial', 12, 'bold'))
        self.total_label.pack(anchor='w')
        
        self.unique_label = ttk.Label(summary_frame, text="Unique Cards: 0")
        self.unique_label.pack(anchor='w')
        
        self.total_value_label = ttk.Label(summary_frame, text="Total Value: $0.00")
        self.total_value_label.pack(anchor='w')
        
        # Control buttons
        control_frame = ttk.LabelFrame(top_frame, text="Export Controls", padding="10")
        control_frame.pack(side='right', fill='y')
        
        ttk.Button(control_frame, text="Export CSV", 
                  command=self.export_csv).pack(fill='x', pady=(0, 5))
        ttk.Button(control_frame, text="Export Counts Only", 
                  command=self.export_counts).pack(fill='x', pady=(0, 5))
        ttk.Button(control_frame, text="Reset All Counts", 
                  command=self.reset_counts).pack(fill='x')
        
        # Tray counts display
        self.setup_tray_display(main_container)
        
    def setup_tray_display(self, parent):
        # Create notebook for different views
        self.display_notebook = ttk.Notebook(parent)
        self.display_notebook.pack(fill='both', expand=True)
        
        # Tray counts tab
        tray_frame = ttk.Frame(self.display_notebook)
        self.display_notebook.add(tray_frame, text="Tray Counts")
        self.setup_tray_counts(tray_frame)
        
        # Card details tab
        details_frame = ttk.Frame(self.display_notebook)
        self.display_notebook.add(details_frame, text="Card Details")
        self.setup_card_details(details_frame)
        
    def setup_tray_counts(self, parent):
        # Create scrollable frame for tray counts
        canvas = tk.Canvas(parent)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        
        # Create tray count displays in a grid
        self.tray_labels = {}
        self.tray_progress = {}
        self.tray_value_labels = {}
        
        for i in range(1, 35):  # Bins 1-34
            row = (i - 1) // 6
            col = (i - 1) % 6
            
            bin_frame = ttk.LabelFrame(scrollable_frame, text=f"Bin {i}", padding="5")
            bin_frame.grid(row=row, column=col, padx=5, pady=5, sticky='ew')
            
            # Count label
            count_label = ttk.Label(bin_frame, text="0 cards", 
                                   font=('Arial', 10, 'bold'))
            count_label.pack()
            
            # Progress bar (visual representation)
            progress = ttk.Progressbar(bin_frame, length=100, mode='determinate')
            progress.pack(fill='x', pady=(5, 0))
            
            # Value label
            value_label = ttk.Label(bin_frame, text="$0.00", 
                                   font=('Arial', 8))
            value_label.pack()
            
            self.tray_labels[i] = count_label
            self.tray_progress[i] = progress
            self.tray_value_labels[i] = value_label
            
        # Configure grid weights
        for i in range(6):
            scrollable_frame.columnconfigure(i, weight=1)
            
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
    def setup_card_details(self, parent):
        # Create treeview for card details
        columns = ('Name', 'Set', 'Price', 'Bin', 'Timestamp')
        self.details_tree = ttk.Treeview(parent, columns=columns, show='headings')
        
        # Define column headings and widths
        column_widths = {'Name': 200, 'Set': 80, 'Price': 80, 'Bin': 60, 'Timestamp': 120}
        
        for col in columns:
            self.details_tree.heading(col, text=col)
            self.details_tree.column(col, width=column_widths[col])
            
        # Scrollbars for treeview
        tree_scroll_v = ttk.Scrollbar(parent, orient="vertical", 
                                     command=self.details_tree.yview)
        tree_scroll_h = ttk.Scrollbar(parent, orient="horizontal", 
                                     command=self.details_tree.xview)
        
        self.details_tree.configure(yscrollcommand=tree_scroll_v.set)
        self.details_tree.configure(xscrollcommand=tree_scroll_h.set)
        
        # Pack treeview and scrollbars
        self.details_tree.pack(side="left", fill="both", expand=True)
        tree_scroll_v.pack(side="right", fill="y")
        tree_scroll_h.pack(side="bottom", fill="x")
        
    def update_display(self):
        """Update the display with current counts and data"""
        if not hasattr(self.shared_data, 'card_data'):
            return
            
        # Calculate statistics
        total_cards = len(self.shared_data.card_data)
        unique_cards = len(set(card.get('name', '') for card in self.shared_data.card_data))
        total_value = sum(float(card.get('price', 0)) for card in self.shared_data.card_data)
        
        # Update summary labels
        self.total_label.config(text=f"Total Processed: {total_cards}")
        self.unique_label.config(text=f"Unique Cards: {unique_cards}")
        self.total_value_label.config(text=f"Total Value: ${total_value:.2f}")
        
        # Count cards by bin
        bin_counts = {}
        bin_values = {}
        
        for card in self.shared_data.card_data:
            bin_num = card.get('bin', 0)
            if bin_num not in bin_counts:
                bin_counts[bin_num] = 0
                bin_values[bin_num] = 0.0
            bin_counts[bin_num] += 1
            bin_values[bin_num] += float(card.get('price', 0))
        
        # Update tray displays
        max_count = max(bin_counts.values()) if bin_counts else 1
        
        for i in range(1, 35):
            count = bin_counts.get(i, 0)
            value = bin_values.get(i, 0.0)
            
            # Update labels
            self.tray_labels[i].config(text=f"{count} cards")
            self.tray_value_labels[i].config(text=f"${value:.2f}")
            
            # Update progress bar
            progress_value = (count / max_count) * 100 if max_count > 0 else 0
            self.tray_progress[i].config(value=progress_value)
        
        # Update card details tree
        self.update_card_details()
        
    def update_card_details(self):
        """Update the card details treeview"""
        # Clear existing items
        for item in self.details_tree.get_children():
            self.details_tree.delete(item)
            
        # Add card data
        if hasattr(self.shared_data, 'card_data'):
            for card in self.shared_data.card_data:
                values = (
                    card.get('name', ''),
                    card.get('set', ''),
                    f"${float(card.get('price', 0)):.2f}",
                    card.get('bin', ''),
                    card.get('timestamp', '')
                )
                self.details_tree.insert('', 'end', values=values)
    
    def export_csv(self):
        """Export all card data to CSV"""
        if not hasattr(self.shared_data, 'card_data') or not self.shared_data.card_data:
            messagebox.showwarning("No Data", "No card data to export")
            return
            
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Card Data"
        )
        
        if filename:
            try:
                with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                    fieldnames = ['name', 'set', 'price', 'bin', 'timestamp']
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    
                    writer.writeheader()
                    for card in self.shared_data.card_data:
                        writer.writerow({
                            'name': card.get('name', ''),
                            'set': card.get('set', ''),
                            'price': card.get('price', 0),
                            'bin': card.get('bin', ''),
                            'timestamp': card.get('timestamp', '')
                        })
                
                messagebox.showinfo("Export Complete", f"Data exported to {filename}")
            except Exception as e:
                messagebox.showerror("Export Error", f"Failed to export data: {str(e)}")
    
    def export_counts(self):
        """Export bin counts summary to CSV"""
        if not hasattr(self.shared_data, 'card_data') or not self.shared_data.card_data:
            messagebox.showwarning("No Data", "No card data to export")
            return
            
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            title="Export Bin Counts"
        )
        
        if filename:
            try:
                # Calculate bin counts and values
                bin_counts = {}
                bin_values = {}
                
                for card in self.shared_data.card_data:
                    bin_num = card.get('bin', 0)
                    if bin_num not in bin_counts:
                        bin_counts[bin_num] = 0
                        bin_values[bin_num] = 0.0
                    bin_counts[bin_num] += 1
                    bin_values[bin_num] += float(card.get('price', 0))
                
                with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                    fieldnames = ['bin', 'count', 'total_value']
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    
                    writer.writeheader()
                    for bin_num in sorted(bin_counts.keys()):
                        writer.writerow({
                            'bin': bin_num,
                            'count': bin_counts[bin_num],
                            'total_value': f"{bin_values[bin_num]:.2f}"
                        })
                
                messagebox.showinfo("Export Complete", f"Bin counts exported to {filename}")
            except Exception as e:
                messagebox.showerror("Export Error", f"Failed to export counts: {str(e)}")
    
    def reset_counts(self):
        """Reset all counts and clear data"""
        result = messagebox.askyesno(
            "Confirm Reset", 
            "Are you sure you want to reset all counts? This will clear all card data."
        )
        
        if result:
            if hasattr(self.shared_data, 'card_data'):
                self.shared_data.card_data.clear()
            self.update_display()
            messagebox.showinfo("Reset Complete", "All counts have been reset")
    
    def add_card(self, card_data):
        """Add a new card to the data"""
        if not hasattr(self.shared_data, 'card_data'):
            self.shared_data.card_data = []
        
        # Add timestamp if not present
        if 'timestamp' not in card_data:
            card_data['timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        self.shared_data.card_data.append(card_data)
        self.update_display()
    
    def update(self):
        """Update method for external calls - alias for update_display"""
        self.update_display()


# Example usage and testing
if __name__ == "__main__":
    # Create a simple shared data object for testing
    class SharedData:
        def __init__(self):
            self.card_data = []
    
    # Create main window
    root = tk.Tk()
    root.title("Tray Counts Application")
    root.geometry("1000x700")
    
    # Create shared data
    shared_data = SharedData()
    
    # Create the tray counts tab
    tray_tab = TrayCountsTab(root, shared_data)
    
    # Start the GUI
    root.mainloop()