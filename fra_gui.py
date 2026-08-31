#!/usr/bin/env python3
"""
Frequency Response Analyzer (FRA) GUI for Nucleo Actuator Hat.
Provides interactive parameter control, real-time Bode plotting (Gain/Phase vs Frequency),
and CSV data export.
"""

import csv
import os
import sys
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

import serial
import serial.tools.list_ports


class StyledButton(tk.Label):
    """Custom label-based button that respects colors and fonts on macOS Aqua Tkinter."""
    def __init__(self, parent, text, command=None, bg="#4361ee", fg="#ffffff", 
                 hover_bg=None, disabled_bg="#222430", disabled_fg="#55586d",
                 font=("Helvetica", 10, "bold"), padx=10, pady=6, **kwargs):
        self.normal_bg = bg
        self.normal_fg = fg
        self.hover_bg = hover_bg or self._lighten_color(bg)
        self.disabled_bg = disabled_bg
        self.disabled_fg = disabled_fg
        self.command = command
        self._state = "normal"

        super().__init__(
            parent, text=text, bg=bg, fg=fg, font=font,
            padx=padx, pady=pady, cursor="hand2", relief=tk.FLAT, **kwargs
        )
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _lighten_color(self, hex_color):
        try:
            hex_color = hex_color.lstrip('#')
            r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
            r = min(255, int(r * 1.25 + 25))
            g = min(255, int(g * 1.25 + 25))
            b = min(255, int(b * 1.25 + 25))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_color

    def _on_enter(self, event):
        if self._state == "normal":
            self.config(bg=self.hover_bg)

    def _on_leave(self, event):
        if self._state == "normal":
            self.config(bg=self.normal_bg)

    def _on_click(self, event):
        if self._state == "normal" and self.command:
            self.command()

    def set_state(self, state):
        self._state = state
        if state == "disabled":
            self.config(bg=self.disabled_bg, fg=self.disabled_fg, cursor="arrow")
        else:
            self.config(bg=self.normal_bg, fg=self.normal_fg, cursor="hand2")

    def config_colors(self, bg=None, fg=None):
        if bg:
            self.normal_bg = bg
            self.hover_bg = self._lighten_color(bg)
        if fg:
            self.normal_fg = fg
        if self._state == "normal":
            self.config(bg=self.normal_bg, fg=self.normal_fg)


class FRAGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Actuator FRA - Frequency Response Analyzer")
        self.geometry("1100x820")
        self.minsize(900, 700)

        self.ser = None
        self.sweep_thread = None
        self.stop_sweep_flag = threading.Event()

        self.frequencies = []
        self.gains = []
        self.phases = []
        self.current_params = {}

        self.output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fra_tests")
        os.makedirs(self.output_dir, exist_ok=True)

        self._setup_style()
        self._build_ui()
        self._refresh_ports()

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Colors
        self.bg_color = "#1e1e24"
        self.panel_bg = "#2b2d42"
        self.fg_color = "#edf2f4"
        self.accent_color = "#4cc9f0"
        self.accent_red = "#ef233c"
        self.accent_green = "#2ec4b6"

        self.configure(bg=self.bg_color)

    def _build_ui(self):
        # Top Header / Port Bar
        top_frame = tk.Frame(self, bg=self.panel_bg, pady=8, padx=12)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=(10, 5))

        tk.Label(top_frame, text="Serial Port:", font=("Helvetica", 11, "bold"), fg=self.fg_color, bg=self.panel_bg).pack(side=tk.LEFT, padx=(0, 5))
        self.port_combo = ttk.Combobox(top_frame, width=28, state="readonly")
        self.port_combo.pack(side=tk.LEFT, padx=5)

        refresh_btn = StyledButton(top_frame, text="⟳ Refresh", command=self._refresh_ports, bg="#4a4e69", fg="#ffffff", font=("Helvetica", 10, "bold"), padx=8, pady=4)
        refresh_btn.pack(side=tk.LEFT, padx=5)

        self.connect_btn = StyledButton(top_frame, text="Connect", command=self._toggle_connection, bg=self.accent_green, fg="#121214", font=("Helvetica", 10, "bold"), padx=14, pady=4)
        self.connect_btn.pack(side=tk.LEFT, padx=10)

        self.status_lbl = tk.Label(top_frame, text="Disconnected", font=("Helvetica", 10, "italic"), fg="#8d99ae", bg=self.panel_bg)
        self.status_lbl.pack(side=tk.LEFT, padx=10)

        # Main Layout: Left Sidebar for Parameters & Controls, Right for Bode Plot
        main_paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=self.bg_color, bd=0)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Left Control Panel
        left_frame = tk.Frame(main_paned, bg=self.panel_bg, padx=14, pady=14, width=320)
        left_frame.pack_propagate(False)
        main_paned.add(left_frame, minsize=290)

        tk.Label(left_frame, text="FRA Parameters", font=("Helvetica", 13, "bold"), fg=self.accent_color, bg=self.panel_bg).pack(anchor="w", pady=(0, 12))

        # Parameters Form
        params_grid = tk.Frame(left_frame, bg=self.panel_bg)
        params_grid.pack(fill=tk.X, pady=(0, 15))

        entries_cfg = [
            ("Channel (0-7):", "ch_entry", "0"),
            ("DC Offset (mA):", "dc_entry", "100.0"),
            ("AC Amplitude (mA):", "amp_entry", "10.0"),
            ("Start Freq (Hz):", "f_start_entry", "100.0"),
            ("Stop Freq (Hz):", "f_stop_entry", "10000.0"),
            ("Points / Decade:", "ppd_entry", "10.0"),
        ]

        self.entries = {}
        for row, (label_text, var_name, default_val) in enumerate(entries_cfg):
            lbl = tk.Label(params_grid, text=label_text, font=("Helvetica", 10), fg=self.fg_color, bg=self.panel_bg)
            lbl.grid(row=row, column=0, sticky="w", pady=4)
            ent = tk.Entry(params_grid, font=("Helvetica", 10), width=12, justify="right", bg="#1e1e24", fg="white", insertbackground="white", relief=tk.FLAT)
            ent.insert(0, default_val)
            ent.grid(row=row, column=1, sticky="e", pady=4, padx=(10, 0))
            self.entries[var_name] = ent

        # Buttons Frame
        btn_frame = tk.Frame(left_frame, bg=self.panel_bg)
        btn_frame.pack(fill=tk.X, pady=10)

        self.start_btn = StyledButton(btn_frame, text="▶ Start Sweep", command=self._start_sweep, bg="#4361ee", fg="#ffffff", font=("Helvetica", 11, "bold"), pady=8)
        self.start_btn.pack(fill=tk.X, pady=4)

        self.stop_btn = StyledButton(btn_frame, text="⏹ Stop / Abort", command=self._stop_sweep, bg=self.accent_red, fg="#ffffff", font=("Helvetica", 10, "bold"), pady=6)
        self.stop_btn.set_state("disabled")
        self.stop_btn.pack(fill=tk.X, pady=4)

        self.export_btn = StyledButton(btn_frame, text="💾 Export CSV...", command=self._export_csv, bg="#4a4e69", fg="#ffffff", font=("Helvetica", 10, "bold"), pady=6)
        self.export_btn.pack(fill=tk.X, pady=4)

        self.clear_btn = StyledButton(btn_frame, text="🗑 Clear Plot", command=self._clear_data, bg="#343a40", fg="#ffffff", font=("Helvetica", 9, "bold"), pady=5)
        self.clear_btn.pack(fill=tk.X, pady=4)

        # Progress / Current Status
        self.prog_lbl = tk.Label(left_frame, text="Ready", font=("Helvetica", 10, "bold"), fg="#8d99ae", bg=self.panel_bg)
        self.prog_lbl.pack(anchor="w", pady=(15, 2))

        self.progress_bar = ttk.Progressbar(left_frame, mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=4)

        # Terminal / Log Box
        tk.Label(left_frame, text="Console Output:", font=("Helvetica", 9, "bold"), fg=self.fg_color, bg=self.panel_bg).pack(anchor="w", pady=(15, 2))
        self.log_text = tk.Text(left_frame, height=9, bg="#121214", fg="#a0aab2", font=("Courier", 9), relief=tk.FLAT, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(2, 0))

        # Right Bode Plot Area
        right_frame = tk.Frame(main_paned, bg=self.bg_color)
        main_paned.add(right_frame, minsize=550)

        self.fig = Figure(figsize=(8, 6), facecolor="#1e1e24")
        self.ax_mag = self.fig.add_subplot(2, 1, 1)
        self.ax_phase = self.fig.add_subplot(2, 1, 2, sharex=self.ax_mag)

        self._style_axes()

        self.canvas = FigureCanvasTkAgg(self.fig, master=right_frame)
        self.canvas.draw()
        
        toolbar_frame = tk.Frame(right_frame, bg=self.bg_color)
        toolbar_frame.pack(side=tk.TOP, fill=tk.X)
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        self.toolbar.update()
        
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    def _style_axes(self):
        for ax in [self.ax_mag, self.ax_phase]:
            ax.set_facecolor("#121214")
            ax.grid(True, which="both", color="#343a40", linestyle="--", linewidth=0.6)
            ax.tick_params(colors="#edf2f4", labelsize=9)
            for spine in ax.spines.values():
                spine.set_color("#4a4e69")

        self.ax_mag.set_ylabel("Gain (dB)", color="#4cc9f0", fontsize=10, fontweight="bold")
        self.ax_mag.set_title("Bode Plot: Frequency Response Analysis", color="#edf2f4", fontsize=12, fontweight="bold", pad=8)

        self.ax_phase.set_xlabel("Frequency (Hz)", color="#edf2f4", fontsize=10, fontweight="bold")
        self.ax_phase.set_ylabel("Phase (deg)", color="#f72585", fontsize=10, fontweight="bold")

        if self.frequencies:
            f_min = min(self.frequencies)
            f_max = max(self.frequencies)
            if f_min > 0 and f_max > f_min:
                self.ax_mag.set_xlim(f_min * 0.8, f_max * 1.2)
            elif f_min > 0:
                self.ax_mag.set_xlim(f_min * 0.8, f_min * 10.0)
            else:
                self.ax_mag.set_xlim(10.0, 100000.0)
        else:
            self.ax_mag.set_xlim(10.0, 100000.0)

        self.ax_mag.set_xscale("log")
        self.ax_phase.set_xscale("log")

        self.fig.tight_layout()

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo['values'] = ports
        if ports:
            for p in ports:
                if "usbmodem" in p.lower():
                    self.port_combo.set(p)
                    break
            else:
                self.port_combo.set(ports[0])

    def _toggle_connection(self):
        if self.ser and self.ser.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self.port_combo.get()
        if not port:
            messagebox.showerror("Error", "Please select a serial port.")
            return

        try:
            self.ser = serial.Serial(port, 115200, timeout=0.2)
            self.status_lbl.config(text=f"Connected: {port}", fg=self.accent_green)
            self.connect_btn.config(text="Disconnect")
            self.connect_btn.config_colors(bg=self.accent_red, fg="#ffffff")
            self._log(f"Connected to {port} @ 115200 baud\n")
        except Exception as e:
            messagebox.showerror("Connection Error", f"Failed to open port {port}:\n{e}")

    def _disconnect(self):
        if self.sweep_thread and self.sweep_thread.is_alive():
            self._stop_sweep()

        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

        self.status_lbl.config(text="Disconnected", fg="#8d99ae")
        self.connect_btn.config(text="Connect")
        self.connect_btn.config_colors(bg=self.accent_green, fg="#121214")
        self._log("Disconnected.\n")

    def _log(self, msg):
        self.log_text.insert(tk.END, msg)
        self.log_text.see(tk.END)

    def _start_sweep(self):
        if not self.ser or not self.ser.is_open:
            messagebox.showerror("Error", "Please connect to the serial port first.")
            return

        try:
            ch = int(self.entries["ch_entry"].get())
            dc = float(self.entries["dc_entry"].get())
            amp = float(self.entries["amp_entry"].get())
            f_start = float(self.entries["f_start_entry"].get())
            f_stop = float(self.entries["f_stop_entry"].get())
            ppd = float(self.entries["ppd_entry"].get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter valid numeric parameters.")
            return

        self.current_params = {
            "ch": ch,
            "dc": dc,
            "amp": amp,
            "f_start": f_start,
            "f_stop": f_stop,
            "ppd": ppd,
        }

        self._clear_data()
        self.stop_sweep_flag.clear()
        self.start_btn.set_state("disabled")
        self.stop_btn.set_state("normal")
        self.prog_lbl.config(text="Sweeping...", fg=self.accent_color)
        self.progress_bar["value"] = 0

        self.sweep_thread = threading.Thread(
            target=self._sweep_worker,
            args=(ch, dc, amp, f_start, f_stop, ppd),
            daemon=True
        )
        self.sweep_thread.start()

    def _stop_sweep(self):
        self.stop_sweep_flag.set()
        if self.ser and self.ser.is_open:
            try:
                self.ser.write(b"\x03\r\n")  # Ctrl+C
            except Exception:
                pass
        self.prog_lbl.config(text="Aborted.", fg=self.accent_red)
        self.start_btn.set_state("normal")
        self.stop_btn.set_state("disabled")

    def _sweep_worker(self, ch, dc, amp, f_start, f_stop, ppd):
        cmd = f"dac test fra {ch} {dc:.2f} {amp:.2f} {f_start:.1f} {f_stop:.1f} {ppd:.1f}\r\n"
        self._log(f"> {cmd}")

        try:
            self.ser.reset_input_buffer()
            self.ser.write(cmd.encode("utf-8"))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Serial Error", f"Write failed: {e}"))
            self.after(0, self._sweep_finished)
            return

        # Estimate expected number of frequency points for progress bar
        import math
        total_pts = max(1, int(math.log10(max(1.0, f_stop / max(0.1, f_start))) * ppd) + 1)

        buffer = ""
        while not self.stop_sweep_flag.is_set():
            try:
                chunk = self.ser.read(64).decode("utf-8", errors="ignore")
            except Exception:
                break

            if chunk:
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self.after(0, self._process_line, line, total_pts)

            if "uart:~$" in buffer or "Done" in buffer or "complete" in buffer.lower():
                break

            time.sleep(0.01)

        self.after(0, self._sweep_finished)

    def _process_line(self, line, total_pts):
        self._log(line + "\n")
        # Check if line matches CSV format: freq_hz,gain_db,phase_deg
        parts = line.split(",")
        if len(parts) == 3:
            try:
                f = float(parts[0])
                gain = float(parts[1])
                phase = float(parts[2])

                self.frequencies.append(f)
                self.gains.append(gain)
                self.phases.append(phase)

                # Update progress
                progress = min(100.0, (len(self.frequencies) / total_pts) * 100.0)
                self.progress_bar["value"] = progress
                self.prog_lbl.config(text=f"Meas: {f:.1f} Hz ({gain:.2f} dB, {phase:.1f}°)")

                self._update_plot()
            except ValueError:
                pass

    def _update_plot(self):
        if not self.frequencies:
            return

        self.ax_mag.cla()
        self.ax_phase.cla()
        self._style_axes()

        self.ax_mag.plot(self.frequencies, self.gains, color="#4cc9f0", marker="o", markersize=4, linewidth=1.8, label="Magnitude (dB)")
        self.ax_phase.plot(self.frequencies, self.phases, color="#f72585", marker="s", markersize=4, linewidth=1.8, label="Phase (°)")

        self.ax_mag.legend(loc="upper right", facecolor="#2b2d42", edgecolor="none", labelcolor="white", fontsize=8)
        self.ax_phase.legend(loc="upper right", facecolor="#2b2d42", edgecolor="none", labelcolor="white", fontsize=8)

        self.canvas.draw_idle()

    def _sweep_finished(self):
        self.start_btn.set_state("normal")
        self.stop_btn.set_state("disabled")
        if not self.stop_sweep_flag.is_set() and self.frequencies:
            self._auto_save_results()
        self._update_plot()

    def _auto_save_results(self):
        if not self.frequencies:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"sweep_{timestamp}"
        run_dir = os.path.join(self.output_dir, folder_name)
        os.makedirs(run_dir, exist_ok=True)

        base_name = f"sweep_{timestamp}"
        base_path = os.path.join(run_dir, base_name)

        csv_path = f"{base_path}.csv"
        png_path = f"{base_path}.png"
        txt_path = f"{base_path}.txt"

        # 1. Save CSV Data
        try:
            with open(csv_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["freq_hz", "gain_db", "phase_deg"])
                for freq, gain, phase in zip(self.frequencies, self.gains, self.phases):
                    writer.writerow([f"{freq:.3f}", f"{gain:.3f}", f"{phase:.3f}"])
        except Exception as e:
            self._log(f"Error auto-saving CSV: {e}\n")

        # 2. Save PNG Bode Plot
        try:
            self.fig.savefig(png_path, dpi=200, facecolor=self.fig.get_facecolor(), bbox_inches="tight")
        except Exception as e:
            self._log(f"Error auto-saving PNG: {e}\n")

        # 3. Save Settings & Summary TXT
        try:
            with open(txt_path, "w") as f:
                f.write("========================================\n")
                f.write("FRA Sweep Test Settings & Summary\n")
                f.write("========================================\n")
                f.write(f"Timestamp:        {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Channel:          {self.current_params.get('ch', 'N/A')}\n")
                f.write(f"DC Offset:        {self.current_params.get('dc', 0.0):.2f} mA\n")
                f.write(f"AC Amplitude:     {self.current_params.get('amp', 0.0):.2f} mA\n")
                f.write(f"Start Frequency:  {self.current_params.get('f_start', 0.0):.1f} Hz\n")
                f.write(f"Stop Frequency:   {self.current_params.get('f_stop', 0.0):.1f} Hz\n")
                f.write(f"Points / Decade:  {self.current_params.get('ppd', 0.0):.1f}\n")
                f.write(f"Total Points:     {len(self.frequencies)}\n")
                if self.frequencies:
                    f.write(f"Freq Range Meas:  {min(self.frequencies):.2f} Hz to {max(self.frequencies):.2f} Hz\n")
                    f.write(f"Gain Range Meas:  {min(self.gains):.2f} dB to {max(self.gains):.2f} dB\n")
                f.write("----------------------------------------\n")
                f.write(f"Data CSV:         {os.path.basename(csv_path)}\n")
                f.write(f"Plot PNG:         {os.path.basename(png_path)}\n")
                f.write("========================================\n")
        except Exception as e:
            self._log(f"Error auto-saving TXT: {e}\n")

        self._log(f"\n[Auto-Saved] {folder_name}/ with .csv, .png, .txt to fra_tests/\n")
        self.prog_lbl.config(text=f"Auto-saved: {folder_name}", fg=self.accent_green)
        self.progress_bar["value"] = 100

    def _clear_data(self):
        self.frequencies.clear()
        self.gains.clear()
        self.phases.clear()
        self.ax_mag.cla()
        self.ax_phase.cla()
        self._style_axes()
        self.canvas.draw_idle()
        self.progress_bar["value"] = 0
        self.prog_lbl.config(text="Ready", fg="#8d99ae")

    def _export_csv(self):
        if not self.frequencies:
            messagebox.showwarning("No Data", "No sweep data available to export.")
            return

        filename = filedialog.asksaveasfilename(
            initialdir=self.output_dir,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile=f"fra_sweep_ch{self.current_params.get('ch', 0)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        if not filename:
            return

        try:
            with open(filename, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["freq_hz", "gain_db", "phase_deg"])
                for freq, gain, phase in zip(self.frequencies, self.gains, self.phases):
                    writer.writerow([f"{freq:.3f}", f"{gain:.3f}", f"{phase:.3f}"])
            messagebox.showinfo("Export Successful", f"Saved {len(self.frequencies)} data points to:\n{filename}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to save CSV:\n{e}")


if __name__ == "__main__":
    app = FRAGui()
    app.mainloop()
