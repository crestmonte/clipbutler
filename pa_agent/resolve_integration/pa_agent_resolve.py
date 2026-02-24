"""
ClipButler DaVinci Resolve Integration.
Run this script from DaVinci Resolve's console or as a Fusion script.
Opens a tkinter search window that communicates with the ClipButler service.
"""

import sys
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import requests

CB_API = "http://localhost:8765"


class ClipButlerResolveUI:
    def __init__(self, root):
        self.root = root
        self.root.title("ClipButler")
        self.root.geometry("560x560")
        self.root.configure(bg="#1c1c1e")

        self._build_ui()
        self._check_service()

    def _build_ui(self):
        bg = "#1c1c1e"
        surface = "#2c2c2e"
        accent = "#6c63ff"
        text = "#f2f2f7"
        dim = "#8e8e93"

        # Header
        hdr = tk.Frame(self.root, bg=surface, height=44)
        hdr.pack(fill="x")
        tk.Label(hdr, text="ClipButler", font=("SF Pro Display", 15, "bold"),
                 bg=surface, fg=accent).pack(side="left", padx=12, pady=10)

        self.status_lbl = tk.Label(hdr, text="Connecting…", font=("SF Pro Display", 10),
                                   bg=surface, fg=dim)
        self.status_lbl.pack(side="right", padx=12)

        # Search bar
        search_frame = tk.Frame(self.root, bg=bg, pady=10, padx=12)
        search_frame.pack(fill="x")

        self.q_var = tk.StringVar()
        q_entry = tk.Entry(search_frame, textvariable=self.q_var,
                           bg=surface, fg=text, insertbackground=text,
                           relief="flat", font=("SF Pro Display", 13), bd=6)
        q_entry.pack(side="left", fill="x", expand=True)
        q_entry.bind("<Return>", lambda e: self._search())

        go_btn = tk.Button(search_frame, text="Search", bg=accent, fg="white",
                           relief="flat", font=("SF Pro Display", 12, "bold"),
                           padx=14, command=self._search)
        go_btn.pack(side="left", padx=(8, 0))

        # Filters row
        f_frame = tk.Frame(self.root, bg=bg, padx=12, pady=4)
        f_frame.pack(fill="x")

        tk.Label(f_frame, text="Resolution:", bg=bg, fg=dim, font=("SF Pro Display", 10)).pack(side="left")
        self.res_var = tk.StringVar(value="")
        res_cb = ttk.Combobox(f_frame, textvariable=self.res_var, state="readonly",
                              values=["", "4k", "2k", "1080p", "720p"], width=6)
        res_cb.pack(side="left", padx=(4, 12))

        tk.Label(f_frame, text="Camera:", bg=bg, fg=dim, font=("SF Pro Display", 10)).pack(side="left")
        self.cam_var = tk.StringVar()
        tk.Entry(f_frame, textvariable=self.cam_var, bg=surface, fg=text,
                 insertbackground=text, relief="flat", width=10, bd=4).pack(side="left", padx=(4, 12))

        tk.Label(f_frame, text="FPS:", bg=bg, fg=dim, font=("SF Pro Display", 10)).pack(side="left")
        self.fps_var = tk.StringVar()
        tk.Entry(f_frame, textvariable=self.fps_var, bg=surface, fg=text,
                 insertbackground=text, relief="flat", width=5, bd=4).pack(side="left", padx=4)

        # Results listbox
        list_frame = tk.Frame(self.root, bg=bg)
        list_frame.pack(fill="both", expand=True, padx=12, pady=8)

        self.listbox = tk.Listbox(
            list_frame, bg=surface, fg=text, selectbackground=accent,
            relief="flat", font=("SF Pro Display", 12), bd=0,
            activestyle="none", cursor="hand2",
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<Double-Button-1>", lambda e: self._import_selected())

        scrollbar = tk.Scrollbar(list_frame, command=self.listbox.yview, bg=bg)
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)

        self._results = []

        # Bottom bar
        bot = tk.Frame(self.root, bg=surface, height=44)
        bot.pack(fill="x", side="bottom")

        self.info_lbl = tk.Label(bot, text="Double-click a result to import",
                                 bg=surface, fg=dim, font=("SF Pro Display", 10))
        self.info_lbl.pack(side="left", padx=12)

        import_btn = tk.Button(bot, text="Import Selected", bg=accent, fg="white",
                               relief="flat", font=("SF Pro Display", 11, "bold"),
                               padx=12, pady=6, command=self._import_selected)
        import_btn.pack(side="right", padx=12, pady=6)

    def _check_service(self):
        def check():
            try:
                r = requests.get(f"{CB_API}/api/status", timeout=3)
                data = r.json()
                self.status_lbl.config(text=f"{data.get('indexed', 0)} clips indexed", fg="#34c759")
            except Exception:
                self.status_lbl.config(text="Service offline", fg="#ff3b30")
        threading.Thread(target=check, daemon=True).start()

    def _search(self):
        q = self.q_var.get().strip()
        params = {"n": "30"}
        if q:
            params["q"] = q
        if self.res_var.get():
            params["resolution"] = self.res_var.get()
        if self.cam_var.get().strip():
            params["camera"] = self.cam_var.get().strip()
        if self.fps_var.get().strip():
            params["fps"] = self.fps_var.get().strip()

        self.info_lbl.config(text="Searching…")
        self.listbox.delete(0, "end")

        def fetch():
            try:
                r = requests.get(f"{CB_API}/api/search", params=params, timeout=15)
                data = r.json()
                results = data.get("results", [])
                self.root.after(0, lambda: self._show_results(results))
            except Exception as e:
                self.root.after(0, lambda: self.info_lbl.config(text=f"Error: {e}"))

        threading.Thread(target=fetch, daemon=True).start()

    def _show_results(self, results):
        self._results = results
        self.listbox.delete(0, "end")

        for clip in results:
            fps = clip.get("fps")
            fps_str = f"{fps:.2f}" if fps else "—"
            res = clip.get("resolution") or "—"
            cam = clip.get("camera_model") or ""
            label = f"{clip['filename']}  [{res}  {fps_str}fps{f'  {cam}' if cam else ''}]"
            self.listbox.insert("end", label)

        count = len(results)
        self.info_lbl.config(text=f"{count} result{'s' if count != 1 else ''} — double-click to import")

    def _import_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("ClipButler", "No clip selected")
            return

        clip = self._results[sel[0]]
        filepath = clip.get("filepath", "")
        if not filepath:
            messagebox.showerror("ClipButler", "No filepath for this clip")
            return

        # Try DaVinci Resolve API
        try:
            import DaVinciResolveScript as dvr
            resolve = dvr.scriptapp("Resolve")
            media_storage = resolve.GetMediaStorage()
            media_storage.AddItemListToMediaPool([filepath])
            self.info_lbl.config(text=f"Imported: {clip['filename']}")
        except ImportError:
            # Not running inside Resolve — copy path
            self.root.clipboard_clear()
            self.root.clipboard_append(filepath)
            messagebox.showinfo(
                "ClipButler",
                f"DaVinci Resolve API not found.\n\nPath copied to clipboard:\n{filepath}"
            )
        except Exception as e:
            messagebox.showerror("ClipButler", f"Import failed: {e}")


def main():
    root = tk.Tk()
    app = ClipButlerResolveUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
