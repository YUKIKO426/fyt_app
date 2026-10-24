# gui_app.py — RGB neon gaming interface

import asyncio
import threading
import colorsys
import random
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk
import pystray

from audio_engine import engine, settings, level_meter


PRESETS = {
    "FIGHT": {"volume": 30, "sub_bass_db": 20, "bass_db": 16,
              "presence_db": 12, "air_db": 6,  "loudness": 85, "gate_db": -40,
              "color": "#ff2d55"},
    "CLEAR": {"volume": 20, "sub_bass_db": 6,  "bass_db": 6,
              "presence_db": 16, "air_db": 10, "loudness": 60, "gate_db": -42,
              "color": "#00e5ff"},
    "NUKE":  {"volume": 55, "sub_bass_db": 24, "bass_db": 22,
              "presence_db": 14, "air_db": 8,  "loudness": 95, "gate_db": -50,
              "color": "#ff6b00"},
    "CHILL": {"volume": 15, "sub_bass_db": 4,  "bass_db": 4,
              "presence_db": 6,  "air_db": 4,  "loudness": 40, "gate_db": -40,
              "color": "#a855f7"},
}


def _make_tray_icon(color="#a855f7"):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=color)
    d.polygon([(24, 18), (24, 46), (46, 32)], fill="white")
    return img


class RGBFrame(ctk.CTkFrame):
    """Frame with animated RGB border."""
    def __init__(self, master, hue_speed=0.5, **kwargs):
        kwargs.setdefault("fg_color", "#0a0a0f")
        kwargs.setdefault("border_width", 2)
        kwargs.setdefault("corner_radius", 12)
        super().__init__(master, **kwargs)
        self._hue        = random.random()
        self._hue_speed  = hue_speed
        self._running    = True
        self._pulse()

    def _pulse(self):
        if not self._running:
            return
        self._hue = (self._hue + 0.005 * self._hue_speed) % 1.0
        r, g, b = colorsys.hsv_to_rgb(self._hue, 0.9, 1.0)
        color = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
        try:
            self.configure(border_color=color)
        except Exception:
            return
        self.after(40, self._pulse)

    def destroy(self):
        self._running = False
        super().destroy()


class VUMeter(ctk.CTkCanvas):
    """Animated audio level bar with RGB gradient."""
    def __init__(self, master, width=460, height=16, **kwargs):
        kwargs.setdefault("bg", "#0a0a0f")
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, width=width, height=height, **kwargs)
        self._w = width
        self._h = height
        self._bar = self.create_rectangle(0, 0, 0, height, fill="#a855f7", outline="")
        self._running = True
        self._smooth = 0.0

    def _update(self):
        if not self._running:
            return
        target = level_meter["peak"]
        self._smooth += (target - self._smooth) * 0.3

        w = int(self._w * self._smooth)
        hue = 0.75 - self._smooth * 0.4
        r, g, b = colorsys.hsv_to_rgb(hue, 0.9, 1.0)
        color = f"#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}"
        self.coords(self._bar, 0, 0, w, self._h)
        self.itemconfig(self._bar, fill=color)
        self.after(33, self._update)

    def start(self):
        self._running = True
        self._update()

    def destroy(self):
        self._running = False
        super().destroy()


class AppWindow(ctk.CTk):
    def __init__(self, loop, vc, on_quit):
        super().__init__()
        self.loop     = loop
        self.vc       = vc
        self.on_quit  = on_quit
        self.tray     = None
        self.sliders  = {}
        self._active_preset = None

        self.title("FYT // DOMINANT")
        self.geometry("620x780")
        self.resizable(False, False)
        self.configure(fg_color="#05050a")

        ctk.set_appearance_mode("dark")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self._poll_status()

    def _build_ui(self):
        # ── header ──
        header_frame = RGBFrame(self, hue_speed=1.0)
        header_frame.pack(fill="x", padx=20, pady=(20, 12))

        title = ctk.CTkLabel(
            header_frame,
            text="⚡ FYT // DOMINANT",
            font=("Orbitron", 26, "bold"),
            text_color="#ffffff",
        )
        title.pack(pady=(14, 2))

        subtitle = ctk.CTkLabel(
            header_frame,
            text="NEON AUDIO ENGINE  •  MSI TUNED",
            font=("Consolas", 10),
            text_color="#6b7280",
        )
        subtitle.pack(pady=(0, 14))

        # ── connect section ──
        conn = RGBFrame(self, hue_speed=0.7)
        conn.pack(fill="x", padx=20, pady=8)

        conn_inner = ctk.CTkFrame(conn, fg_color="transparent")
        conn_inner.pack(fill="x", padx=12, pady=12)

        self.chat_entry = ctk.CTkEntry(
            conn_inner,
            placeholder_text="chat_id  (-100xxxxxxxxxx)",
            font=("Consolas", 12),
            fg_color="#0f0f18",
            border_color="#1f1f2e",
            border_width=1,
            height=40,
        )
        self.chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.conn_btn = ctk.CTkButton(
            conn_inner,
            text="▶ CONNECT",
            width=130,
            height=40,
            font=("Orbitron", 12, "bold"),
            fg_color="#a855f7",
            hover_color="#c084fc",
            command=self._toggle_connect,
        )
        self.conn_btn.pack(side="right")

        self.status_lbl = ctk.CTkLabel(
            self,
            text="● OFFLINE",
            font=("Consolas", 11, "bold"),
            text_color="#ef4444",
        )
        self.status_lbl.pack(pady=(8, 4))

        # ── VU meter ──
        vu_frame = RGBFrame(self, hue_speed=0.5)
        vu_frame.pack(fill="x", padx=20, pady=8)
        vu_inner = ctk.CTkFrame(vu_frame, fg_color="transparent")
        vu_inner.pack(fill="x", padx=12, pady=12)

        ctk.CTkLabel(
            vu_inner,
            text="LIVE LEVEL",
            font=("Orbitron", 9, "bold"),
            text_color="#6b7280",
        ).pack(anchor="w", pady=(0, 4))

        self.vu_meter = VUMeter(vu_inner, width=540, height=14)
        self.vu_meter.pack(fill="x")
        self.vu_meter.start()

        # ── sliders ──
        controls = RGBFrame(self, hue_speed=0.4)
        controls.pack(fill="x", padx=20, pady=8)

        ctk.CTkLabel(
            controls,
            text="◢ AUDIO SHAPING ◣",
            font=("Orbitron", 10, "bold"),
            text_color="#a855f7",
        ).pack(anchor="w", padx=14, pady=(12, 4))

        slider_defs = [
            ("volume",      "VOLUME",    1.0,  60.0, "x"),
            ("sub_bass_db", "SUB BASS",  0.0,  30.0, " dB"),
            ("bass_db",     "BASS",      0.0,  30.0, " dB"),
            ("presence_db", "PRESENCE",  0.0,  25.0, " dB"),
            ("air_db",      "AIR",       0.0,  20.0, " dB"),
            ("loudness",    "LOUDNESS",  0.0, 100.0, "%"),
            ("gate_db",     "GATE",    -70.0, -20.0, " dB"),
        ]

        for key, label, lo, hi, unit in slider_defs:
            self._add_slider(controls, key, label, lo, hi, unit)

        # ── presets ──
        preset_frame = RGBFrame(self, hue_speed=0.6)
        preset_frame.pack(fill="x", padx=20, pady=8)

        ctk.CTkLabel(
            preset_frame,
            text="◢ PRESETS ◣",
            font=("Orbitron", 10, "bold"),
            text_color="#a855f7",
        ).pack(anchor="w", padx=14, pady=(12, 6))

        row = ctk.CTkFrame(preset_frame, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(0, 14))

        for name, cfg in PRESETS.items():
            btn = ctk.CTkButton(
                row,
                text=name,
                width=120,
                height=44,
                font=("Orbitron", 11, "bold"),
                fg_color="#0f0f18",
                hover_color=cfg["color"],
                border_color=cfg["color"],
                border_width=2,
                text_color=cfg["color"],
                command=lambda n=name: self._apply_preset(n),
            )
            btn.pack(side="left", padx=4, expand=True, fill="x")

        # ── log ──
        log_frame = RGBFrame(self, hue_speed=0.3)
        log_frame.pack(fill="both", expand=True, padx=20, pady=(8, 20))

        ctk.CTkLabel(
            log_frame,
            text="◢ SYSTEM LOG ◣",
            font=("Orbitron", 10, "bold"),
            text_color="#6b7280",
        ).pack(anchor="w", padx=14, pady=(12, 4))

        self.log_box = ctk.CTkTextbox(
            log_frame,
            height=110,
            font=("Consolas", 10),
            fg_color="#05050a",
            border_width=0,
            text_color="#a855f7",
        )
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _add_slider(self, parent, key, label, lo, hi, unit):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=14, pady=5)

        top = ctk.CTkFrame(frame, fg_color="transparent")
        top.pack(fill="x")

        ctk.CTkLabel(
            top,
            text=label,
            font=("Orbitron", 9, "bold"),
            text_color="#6b7280",
            anchor="w",
            width=110,
        ).pack(side="left")

        value_lbl = ctk.CTkLabel(
            top,
            text="",
            font=("Consolas", 11, "bold"),
            text_color="#a855f7",
            anchor="e",
        )
        value_lbl.pack(side="right")

        slider = ctk.CTkSlider(
            frame,
            from_=lo,
            to=hi,
            height=14,
            button_color="#a855f7",
            button_hover_color="#c084fc",
            progress_color="#a855f7",
            fg_color="#1a1a2e",
            command=lambda v, k=key, l=value_lbl, u=unit: self._on_slider(k, v, l, u),
        )
        slider.set(settings[key])
        slider.pack(fill="x", pady=(3, 0))

        self.sliders[key] = (slider, value_lbl, unit)
        value_lbl.configure(text=f"{settings[key]:.0f}{unit}")

    def _on_slider(self, key, value, label, unit):
        settings[key] = float(value)
        label.configure(text=f"{float(value):.0f}{unit}")

    def _apply_preset(self, name):
        cfg = PRESETS[name]
        for k, v in cfg.items():
            if k == "color":
                continue
            settings[k] = float(v)
            if k in self.sliders:
                slider, lbl, unit = self.sliders[k]
                slider.set(v)
                lbl.configure(text=f"{v:.0f}{unit}")
        self._active_preset = name
        self.log(f"◢ preset → {name}  [{cfg['color']}]")

    def _toggle_connect(self):
        if self.vc.joined_chat is None:
            self._do_connect()
        else:
            self._do_disconnect()

    def _do_connect(self):
        raw = self.chat_entry.get().strip()
        if not raw:
            self.log("!! chat_id required")
            return
        try:
            chat_id = int(raw)
        except ValueError:
            self.log("!! invalid chat_id")
            return

        if not settings["running"]:
            engine.start()
            self.log("◢ audio engine started")

        self.log(f"◢ joining {chat_id}...")
        asyncio.run_coroutine_threadsafe(self._async_join(chat_id), self.loop)

    async def _async_join(self, chat_id):
        try:
            await self.vc.join(chat_id)
            self.after(0, lambda: self.log(f"◢ connected → {chat_id}"))
        except Exception as e:
            self.after(0, lambda: self.log(f"!! join failed: {e}"))

    def _do_disconnect(self):
        asyncio.run_coroutine_threadsafe(self._async_leave(), self.loop)

    async def _async_leave(self):
        try:
            await self.vc.leave()
            self.after(0, lambda: self.log("◢ disconnected"))
        except Exception as e:
            self.after(0, lambda: self.log(f"!! leave failed: {e}"))

    def _poll_status(self):
        if self.vc.joined_chat is not None:
            self.status_lbl.configure(
                text=f"● LIVE  —  {self.vc.joined_chat}",
                text_color="#22c55e",
            )
            self.conn_btn.configure(
                text="■ DISCONNECT",
                fg_color="#ef4444",
                hover_color="#f87171",
            )
        else:
            self.status_lbl.configure(text="● OFFLINE", text_color="#ef4444")
            self.conn_btn.configure(
                text="▶ CONNECT",
                fg_color="#a855f7",
                hover_color="#c084fc",
            )
        self.after(500, self._poll_status)

    def log(self, msg):
        self.log_box.insert("end", f"{msg}\n")
        self.log_box.see("end")

    def _hide_to_tray(self):
        self.withdraw()
        if self.tray is None:
            self._start_tray()

    def _start_tray(self):
        icon_img = _make_tray_icon()
        menu = pystray.Menu(
            pystray.MenuItem("Show",     lambda: self.after(0, self._show_window)),
            pystray.MenuItem("Disconnect", lambda: self.after(0, self._do_disconnect)),
            pystray.MenuItem("Quit",     lambda: self.after(0, self._real_quit)),
        )
        self.tray = pystray.Icon("fyt", icon_img, "FYT // DOMINANT", menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _show_window(self):
        self.deiconify()
        self.lift()
        if self.tray:
            self.tray.stop()
            self.tray = None

    def _real_quit(self):
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        engine.stop()
        self.on_quit()
        self.destroy()
