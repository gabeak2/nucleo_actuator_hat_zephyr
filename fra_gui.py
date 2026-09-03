#!/usr/bin/env python3
"""
Frequency Response Analyzer (FRA) GUI for Nucleo Actuator Hat.
Provides interactive parameter control, real-time Bode plotting (Gain/Phase vs Frequency),
CSV data export, and a unified command console (replaces needing a separate
terminal/screen session for status/adcraw/fastwrite/set commands).
"""

import csv
import os
import queue
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
        self.geometry("1250x820")
        self.minsize(1000, 700)

        self.ser = None
        self.ser_lock = threading.Lock()  # guards writes; reader thread owns reads

        # Persistent reader thread -- started on connect, stopped on disconnect.
        # Replaces the old per-sweep read loop so the console shows ALL board
        # output (boot log, manual command responses, sweep CSV) all the time,
        # not just while a sweep is running.
        self.reader_thread = None
        self.stop_reader_flag = threading.Event()

        # Sweep state (no longer a separate blocking read loop -- the reader
        # thread dispatches lines to the sweep parser via this flag).
        self.sweep_active = False
        self.sweep_total_pts = 1

        self.frequencies = []
        self.gains = []
        self.phases = []
        self.current_params = {}

        self.output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fra_tests")
        os.makedirs(self.output_dir, exist_ok=True)

        self._setup_style()
        self._build_ui()
        self._refresh_ports()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

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
        self.port_combo = ttk.Combobox(top_frame, width=22, state="readonly")
        self.port_combo.pack(side=tk.LEFT, padx=5)

        refresh_btn = StyledButton(top_frame, text="⟳ Refresh", command=self._refresh_ports, bg="#4a4e69", fg="#ffffff", font=("Helvetica", 10, "bold"), padx=8, pady=4)
        refresh_btn.pack(side=tk.LEFT, padx=5)

        tk.Label(top_frame, text="Baud:", font=("Helvetica", 10, "bold"), fg=self.fg_color, bg=self.panel_bg).pack(side=tk.LEFT, padx=(10, 3))
        self.baud_combo = ttk.Combobox(top_frame, values=["921600", "460800", "230400", "115200"], width=9, state="readonly")
        self.baud_combo.set("921600")
        self.baud_combo.pack(side=tk.LEFT, padx=3)

        self.connect_btn = StyledButton(top_frame, text="Connect", command=self._toggle_connection, bg=self.accent_green, fg="#121214", font=("Helvetica", 10, "bold"), padx=14, pady=4)
        self.connect_btn.pack(side=tk.LEFT, padx=10)

        self.status_lbl = tk.Label(top_frame, text="Disconnected", font=("Helvetica", 10, "italic"), fg="#8d99ae", bg=self.panel_bg)
        self.status_lbl.pack(side=tk.LEFT, padx=10)

        # Main Layout: Left Sidebar for Parameters & Controls, Middle for Console, Right for Bode Plot
        main_paned = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=self.bg_color, bd=0)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Left Control Panel
        left_frame = tk.Frame(main_paned, bg=self.panel_bg, padx=14, pady=14, width=300)
        left_frame.pack_propagate(False)
        main_paned.add(left_frame, minsize=270)

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
            ("Meas Cycles:", "n_meas_entry", "50"),
            ("Settle Cycles:", "n_settle_entry", "10"),
        ]

        self.entries = {}
        for row, (label_text, var_name, default_val) in enumerate(entries_cfg):
            lbl = tk.Label(params_grid, text=label_text, font=("Helvetica", 10), fg=self.fg_color, bg=self.panel_bg)
            lbl.grid(row=row, column=0, sticky="w", pady=3)
            ent = tk.Entry(params_grid, font=("Helvetica", 10), width=10, justify="right", bg="#1e1e24", fg="white", insertbackground="white", relief=tk.FLAT)
            ent.insert(0, default_val)
            ent.grid(row=row, column=1, sticky="e", pady=3, padx=(10, 0))
            self.entries[var_name] = ent

        # Buttons Frame
        btn_frame = tk.Frame(left_frame, bg=self.panel_bg)
        btn_frame.pack(fill=tk.X, pady=8)

        self.start_btn = StyledButton(btn_frame, text="▶ Start Sweep", command=self._start_sweep, bg="#4361ee", fg="#ffffff", font=("Helvetica", 11, "bold"), pady=7)
        self.start_btn.pack(fill=tk.X, pady=3)

        self.stop_btn = StyledButton(btn_frame, text="⏹ Stop / Abort", command=self._stop_sweep, bg=self.accent_red, fg="#ffffff", font=("Helvetica", 10, "bold"), pady=5)
        self.stop_btn.set_state("disabled")
        self.stop_btn.pack(fill=tk.X, pady=3)

        self.export_btn = StyledButton(btn_frame, text="💾 Export CSV...", command=self._export_csv, bg="#4a4e69", fg="#ffffff", font=("Helvetica", 10, "bold"), pady=5)
        self.export_btn.pack(fill=tk.X, pady=3)

        self.clear_btn = StyledButton(btn_frame, text="🗑 Clear Plot", command=self._clear_data, bg="#343a40", fg="#ffffff", font=("Helvetica", 9, "bold"), pady=4)
        self.clear_btn.pack(fill=tk.X, pady=3)

        # Progress / Current Status
        self.prog_lbl = tk.Label(left_frame, text="Ready", font=("Helvetica", 10, "bold"), fg="#8d99ae", bg=self.panel_bg)
        self.prog_lbl.pack(anchor="w", pady=(10, 2))

        self.progress_bar = ttk.Progressbar(left_frame, mode="determinate")
        self.progress_bar.pack(fill=tk.X, pady=3)

        # ---- Middle Console Panel ----
        console_frame = tk.Frame(main_paned, bg=self.panel_bg, padx=14, pady=14, width=380)
        console_frame.pack_propagate(False)
        main_paned.add(console_frame, minsize=320)

        tk.Label(console_frame, text="Console", font=("Helvetica", 13, "bold"), fg=self.accent_color, bg=self.panel_bg).pack(anchor="w", pady=(0, 8))

        # Quick-command buttons for the rest of the "dac test" suite, so this
        # console fully replaces a separate screen/terminal session.
        quick_frame = tk.Frame(console_frame, bg=self.panel_bg)
        quick_frame.pack(fill=tk.X, pady=(0, 8))

        quick_btn_cfg = [
            ("Status (all)", lambda: self._send_line("dac status")),
            ("Status (ch)", lambda: self._send_line(f"dac status {self._ch()}")),
            ("ADC Raw", lambda: self._send_line(f"dac test adcraw {self._ch()}")),
            ("Raw Cycle", lambda: self._send_line(f"dac test rawcycle {self._ch()} 50 10 1000")),
            ("Set 0mA", lambda: self._send_line(f"dac set {self._ch()} 0")),
        ]
        for i, (label, cmd) in enumerate(quick_btn_cfg):
            b = StyledButton(quick_frame, text=label, command=cmd, bg="#4a4e69",
                             fg="#ffffff", font=("Helvetica", 9, "bold"), padx=6, pady=4)
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
        quick_frame.grid_columnconfigure(0, weight=1)
        quick_frame.grid_columnconfigure(1, weight=1)

        # "dac set <ch> <mA>" with an editable value next to it
        set_frame = tk.Frame(console_frame, bg=self.panel_bg)
        set_frame.pack(fill=tk.X, pady=(0, 4))
        tk.Label(set_frame, text="Set (mA):", font=("Helvetica", 9), fg=self.fg_color, bg=self.panel_bg).pack(side=tk.LEFT)
        self.set_ma_entry = tk.Entry(set_frame, font=("Helvetica", 9), width=8, bg="#1e1e24", fg="white", insertbackground="white", relief=tk.FLAT)
        self.set_ma_entry.insert(0, "50.0")
        self.set_ma_entry.pack(side=tk.LEFT, padx=4)
        StyledButton(set_frame, text="Send", command=self._send_set_ma, bg="#4a4e69",
                    fg="#ffffff", font=("Helvetica", 9, "bold"), padx=6, pady=2).pack(side=tk.LEFT, padx=4)

        # "dac test fastwrite <ch> <lo> <hi>"
        fw_frame = tk.Frame(console_frame, bg=self.panel_bg)
        fw_frame.pack(fill=tk.X, pady=(0, 10))
        tk.Label(fw_frame, text="Fastwrite lo/hi:", font=("Helvetica", 9), fg=self.fg_color, bg=self.panel_bg).pack(side=tk.LEFT)
        self.fw_lo_entry = tk.Entry(fw_frame, font=("Helvetica", 9), width=6, bg="#1e1e24", fg="white", insertbackground="white", relief=tk.FLAT)
        self.fw_lo_entry.insert(0, "5000")
        self.fw_lo_entry.pack(side=tk.LEFT, padx=2)
        self.fw_hi_entry = tk.Entry(fw_frame, font=("Helvetica", 9), width=6, bg="#1e1e24", fg="white", insertbackground="white", relief=tk.FLAT)
        self.fw_hi_entry.insert(0, "60000")
        self.fw_hi_entry.pack(side=tk.LEFT, padx=2)
        StyledButton(fw_frame, text="Send", command=self._send_fastwrite, bg="#4a4e69",
                    fg="#ffffff", font=("Helvetica", 9, "bold"), padx=6, pady=2).pack(side=tk.LEFT, padx=4)

        # Console log (shows ALL board output at all times, not just during sweeps)
        self.log_text = tk.Text(console_frame, bg="#121214", fg="#a0aab2", font=("Courier", 9), relief=tk.FLAT, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        log_scroll = tk.Scrollbar(self.log_text, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Free-form command entry -- the direct "screen" replacement. Type any
        # shell command (e.g. "dac test adcraw 0") and press Enter or Send.
        cmd_frame = tk.Frame(console_frame, bg=self.panel_bg)
        cmd_frame.pack(fill=tk.X)
        self.cmd_entry = tk.Entry(cmd_frame, font=("Courier", 10), bg="#1e1e24", fg="white", insertbackground="white", relief=tk.FLAT)
        self.cmd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        self.cmd_entry.bind("<Return>", lambda event: self._send_console_entry())
        self._cmd_history = []
        self._cmd_history_idx = -1
        self.cmd_entry.bind("<Up>", self._history_prev)
        self.cmd_entry.bind("<Down>", self._history_next)
        StyledButton(cmd_frame, text="Send", command=self._send_console_entry, bg=self.accent_color,
                    fg="#121214", font=("Helvetica", 10, "bold"), padx=10, pady=4).pack(side=tk.LEFT, padx=(6, 0))

        # Right Bode Plot Area
        right_frame = tk.Frame(main_paned, bg=self.bg_color)
        main_paned.add(right_frame, minsize=480)

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

        baud_str = self.baud_combo.get() if hasattr(self, "baud_combo") else "921600"
        try:
            baud = int(baud_str)
        except ValueError:
            baud = 921600

        try:
            self.ser = serial.Serial(port, baud, timeout=0.2)
            self.status_lbl.config(text=f"Connected: {port} @ {baud}", fg=self.accent_green)
            self.connect_btn.config(text="Disconnect")
            self.connect_btn.config_colors(bg=self.accent_red, fg="#ffffff")
            self._log(f"Connected to {port} @ {baud} baud\n")

            # Start the single persistent reader thread. It owns ALL reads
            # from this point until disconnect -- both console output and
            # sweep CSV parsing flow through it, so there's never a second
            # thread also trying to read the same port.
            self.stop_reader_flag.clear()
            self.reader_thread = threading.Thread(target=self._reader_worker, daemon=True)
            self.reader_thread.start()
        except Exception as e:
            messagebox.showerror("Connection Error", f"Failed to open port {port}:\n{e}")

    def _disconnect(self):
        self.sweep_active = False
        self.stop_reader_flag.set()
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1.0)
        self.reader_thread = None

        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

        self.status_lbl.config(text="Disconnected", fg="#8d99ae")
        self.connect_btn.config(text="Connect")
        self.connect_btn.config_colors(bg=self.accent_green, fg="#121214")
        self.start_btn.set_state("normal")
        self.stop_btn.set_state("disabled")
        self._log("Disconnected.\n")

    def _on_close(self):
        self._disconnect()
        self.destroy()

    def _log(self, msg):
        self.log_text.insert(tk.END, msg)
        self.log_text.see(tk.END)

    def _ch(self):
        """Current channel from the FRA params panel, used by quick-command buttons."""
        try:
            return int(self.entries["ch_entry"].get())
        except (ValueError, KeyError):
            return 0

    # ---------------- Persistent reader thread ----------------
    def _reader_worker(self):
        """Runs continuously from connect to disconnect. Reads raw bytes,
        splits into lines, and dispatches each line to the console log and
        (when a sweep is active) the CSV/plot parser. This is the single
        owner of ser.read() for the whole connection lifetime."""
        buffer = ""
        while not self.stop_reader_flag.is_set():
            if not self.ser or not self.ser.is_open:
                break
            try:
                chunk = self.ser.read(256).decode("utf-8", errors="ignore")
            except Exception:
                break

            if chunk:
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip("\r\n")
                    if line:
                        self.after(0, self._handle_incoming_line, line)
            else:
                time.sleep(0.01)

    def _handle_incoming_line(self, line):
        """Runs on the Tk main thread (scheduled via self.after)."""
        self._log(line + "\n")

        if self.sweep_active:
            parts = line.split(",")
            if len(parts) == 3:
                try:
                    f = float(parts[0])
                    gain = float(parts[1])
                    phase = float(parts[2])

                    self.frequencies.append(f)
                    self.gains.append(gain)
                    self.phases.append(phase)

                    progress = min(100.0, (len(self.frequencies) / self.sweep_total_pts) * 100.0)
                    self.progress_bar["value"] = progress
                    self.prog_lbl.config(text=f"Meas: {f:.1f} Hz ({gain:.2f} dB, {phase:.1f}°)")

                    self._update_plot()
                except ValueError:
                    pass

            if "sweep complete" in line.lower() or line.strip().lower().endswith("done"):
                self.sweep_active = False
                self._sweep_finished()

    def _write_raw(self, data: bytes):
        if not self.ser or not self.ser.is_open:
            messagebox.showerror("Error", "Please connect to the serial port first.")
            return False
        try:
            with self.ser_lock:
                self.ser.write(data)
            return True
        except Exception as e:
            messagebox.showerror("Serial Error", f"Write failed:\n{e}")
            return False

    def _send_line(self, text):
        """Send a command line (adds CRLF) and echo it in the console."""
        self._log(f"> {text}\n")
        self._write_raw((text + "\r\n").encode("utf-8"))

    # ---------------- Console quick-command handlers ----------------
    def _send_set_ma(self):
        try:
            ma = float(self.set_ma_entry.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Enter a numeric mA value.")
            return
        self._send_line(f"dac set {self._ch()} {ma:.2f}")

    def _send_fastwrite(self):
        try:
            lo = int(self.fw_lo_entry.get())
            hi = int(self.fw_hi_entry.get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Enter integer DAC counts for lo/hi.")
            return
        self._send_line(f"dac test fastwrite {self._ch()} {lo} {hi}")

    def _send_console_entry(self):
        text = self.cmd_entry.get().strip()
        if not text:
            return
        if text == "\x03" or text.lower() in ("ctrl-c", "^c"):
            self._write_raw(b"\x03")
            self._log("> ^C\n")
        else:
            self._send_line(text)
        self._cmd_history.append(text)
        self._cmd_history_idx = len(self._cmd_history)
        self.cmd_entry.delete(0, tk.END)

    def _history_prev(self, event):
        if not self._cmd_history:
            return
        self._cmd_history_idx = max(0, self._cmd_history_idx - 1)
        self.cmd_entry.delete(0, tk.END)
        self.cmd_entry.insert(0, self._cmd_history[self._cmd_history_idx])

    def _history_next(self, event):
        if not self._cmd_history:
            return
        self._cmd_history_idx = min(len(self._cmd_history), self._cmd_history_idx + 1)
        self.cmd_entry.delete(0, tk.END)
        if self._cmd_history_idx < len(self._cmd_history):
            self.cmd_entry.insert(0, self._cmd_history[self._cmd_history_idx])

    # ---------------- FRA sweep control ----------------
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
            n_meas = int(self.entries["n_meas_entry"].get())
            n_settle = int(self.entries["n_settle_entry"].get())
        except ValueError:
            messagebox.showerror("Invalid Input", "Please enter valid numeric parameters.")
            return

        self.current_params = {
            "ch": ch, "dc": dc, "amp": amp, "f_start": f_start, "f_stop": f_stop,
            "ppd": ppd, "n_meas": n_meas, "n_settle": n_settle,
        }

        self._clear_data()
        self.start_btn.set_state("disabled")
        self.stop_btn.set_state("normal")
        self.prog_lbl.config(text="Sweeping...", fg=self.accent_color)
        self.progress_bar["value"] = 0

        import math
        self.sweep_total_pts = max(1, int(math.log10(max(1.0, f_stop / max(0.1, f_start))) * ppd) + 1)

        cmd = f"dac test fra {ch} {dc:.2f} {amp:.2f} {f_start:.1f} {f_stop:.1f} {ppd:.1f} {n_meas} {n_settle}"
        try:
            with self.ser_lock:
                self.ser.reset_input_buffer()
        except Exception:
            pass
        self.sweep_active = True
        self._send_line(cmd)

    def _stop_sweep(self):
        self.sweep_active = False
        self._write_raw(b"\x03\r\n")
        self._log("> ^C (sweep aborted)\n")
        self.prog_lbl.config(text="Aborted.", fg=self.accent_red)
        self.start_btn.set_state("normal")
        self.stop_btn.set_state("disabled")

    def _update_plot(self):
        if not self.frequencies:
            return

        self.ax_mag.set_xscale("linear")
        self.ax_phase.set_xscale("linear")
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
        if self.frequencies:
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
                f.write(f"Meas Cycles:      {self.current_params.get('n_meas', 50)}\n")
                f.write(f"Settle Cycles:    {self.current_params.get('n_settle', 10)}\n")
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
        self.ax_mag.set_xscale("linear")
        self.ax_phase.set_xscale("linear")
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