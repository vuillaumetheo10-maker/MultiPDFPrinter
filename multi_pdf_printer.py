import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import sys
import subprocess
import time
import tempfile
import logging
from datetime import datetime


# --- pypdf ---
try:
    from pypdf import PdfReader, PdfWriter, PageObject, Transformation
except ImportError:
    messagebox.showerror("Dépendance manquante", "pip install pypdf")
    raise SystemExit(1)

# ======================================================================
#  Logging fichier
# ======================================================================
LOG_DIR = os.path.join(tempfile.gettempdir(), "livret_printer_logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(
    LOG_DIR, f"print_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
logger = logging.getLogger("LivretPrinter")

# ======================================================================
#  DIMENSIONS DES PAPIERS (en points PDF, 1 pt = 1/72 pouce)
# ======================================================================
PAPER_SIZES = {
    "A4": {
        "label": "A4 (297×210 mm)",
        "portrait": (595.28, 841.89),    # (w, h)
        "landscape": (841.89, 595.28),   # (w, h)
        "sumatra_name": "A4",
    },
    "A3": {
        "label": "A3 (420×297 mm)",
        "portrait": (841.89, 1190.55),
        "landscape": (1190.55, 841.89),
        "sumatra_name": "A3",
    },
}

PRINT_TIMEOUT = 45
DIALOG_TIMEOUT = 120
INTER_JOB_DELAY = 1.5
MAX_RETRIES = 0
RETRY_DELAY = 2.0

VIRTUAL_PRINTERS = [
    "microsoft print to pdf", "microsoft xps document writer",
    "fax", "onenote", "onenote for windows 10",
    "pdf", "adobe pdf", "cute pdf", "bullzip", "doro pdf",
    "foxit", "nova pdf", "primo pdf", "pdf24", "pdfcreator",
    "pdf architect",
]

CONFIG_FILE = os.path.join(
    os.environ.get('APPDATA', os.path.expanduser('~')),
    'livret_printer_config.json'
)

def is_virtual_printer(name: str) -> bool:
    if not name:
        return False
    name_lower = name.lower()
    return any(vp in name_lower for vp in VIRTUAL_PRINTERS)


def get_printer_type(name: str) -> str:
    return "virtual" if is_virtual_printer(name) else "physical"


def get_landscape_dims(paper_key: str):
    """Retourne (largeur, hauteur) paysage pour un format papier donné."""
    return PAPER_SIZES[paper_key]["landscape"]


def validate_pdf(path):
    if not os.path.exists(path):
        return False, "Fichier introuvable"
    if os.path.getsize(path) == 0:
        return False, "Fichier vide"
    try:
        reader = PdfReader(path)
        if len(reader.pages) == 0:
            reader.stream.close()
            return False, "PDF sans pages"
        _ = reader.pages[0].mediabox
        reader.stream.close()
        return True, None
    except Exception as e:
        return False, f"PDF corrompu : {e}"


def safe_remove(filepath, max_attempts=5, delay=0.3):
    if not filepath or not os.path.exists(filepath):
        return
    for attempt in range(max_attempts):
        try:
            os.remove(filepath)
            return
        except PermissionError:
            if attempt < max_attempts - 1:
                time.sleep(delay * (attempt + 1))
            else:
                logger.warning(f"Impossible de supprimer : {filepath}")


def kill_stale_sumatra():
    for _ in range(3):
        try:
            subprocess.run(
                ['taskkill', '/f', '/im', 'SumatraPDF.exe'],
                capture_output=True, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            time.sleep(0.3)
        except Exception:
            pass


def copy_pdf_direct(source, destination):
    import shutil
    shutil.copy2(source, destination)
    return os.path.exists(destination)


# ======================================================================
class SimplePDFPrinter:
        # ==================================================================
    #  CRÉATION DU LIVRET
    # ==================================================================
        # ==================================================================
    #  IMPRESSION VIA SUMATRAPDF
    # ==================================================================
    def print_with_sumatra(self, file_to_print, printer_name,
                           scale_mode="fit", force_landscape=False,
                           disable_auto_rotation=False,
                           paper_key=None, use_dialog=False,
                           timeout=PRINT_TIMEOUT):
        """
        ✅ Correction orientation :
           - force_landscape=True → ajoute 'landscape' (pilote en mode paysage)
           - disable_auto_rotation=True → empêche Sumatra de tourner le contenu
           - Les deux params sont INDÉPENDANTS (deux 'if', pas de elif)
        """
        try:
            if paper_key is None:
                paper_key = self.paper_size.get()

            duplex_mode = self.duplex_mode.get()
            duplex_str = {
                "duplexlong": "duplexlong",
                "duplexshort": "duplexshort"
            }.get(duplex_mode, "simplex")

            paper_name = PAPER_SIZES[paper_key]["sumatra_name"]

            settings_parts = [duplex_str]

            if scale_mode != "noscale":
                settings_parts.append(scale_mode)

            settings_parts.append(f"paper={paper_name}")

            # ✅ Deux 'if' indépendants (pas de elif !)
            if force_landscape:
                settings_parts.append("landscape")
            if disable_auto_rotation:
                settings_parts.append("disable-auto-rotation")

            print_settings = ",".join(settings_parts)

            cmd = [
                self.sumatra_path,
                '-print-to', printer_name,
                '-print-settings', print_settings,
            ]
            if not use_dialog:
                cmd.append('-silent')
            cmd.append(file_to_print)

            self.log(f"🔧 Sumatra : {print_settings}")
            logger.debug(f"Sumatra cmd: {' '.join(cmd)}")

            creationflags = (subprocess.CREATE_NO_WINDOW
                             if not use_dialog else 0)

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=creationflags
            )

            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                logger.warning(f"Sumatra code={result.returncode}")
                if stderr:
                    self.log(f"   ⚠️ {stderr[:150]}")
                return False

            return True

        except subprocess.TimeoutExpired:
            self.log(f"⏱️ Timeout ({timeout}s)")
            kill_stale_sumatra()
            return False
        except FileNotFoundError:
            self.log("❌ SumatraPDF introuvable")
            return False
        except Exception as e:
            self.log(f"❌ Erreur SumatraPDF: {e}")
            logger.exception("print_with_sumatra")
            return False
        
    def create_booklet_python(self, input_pdf):
        """
        Livret paysage au format choisi (A4 ou A3).
        ✅ Centrage corrigé : merge_transformed_page normalise déjà l'origine
           du mediabox, donc on centre directement dans le slot.
        """
        try:
            paper_key = self.paper_size.get()
            TARGET_WIDTH, TARGET_HEIGHT = get_landscape_dims(paper_key)
            HALF_WIDTH = TARGET_WIDTH / 2
            paper_label = PAPER_SIZES[paper_key]["label"]

            reader = PdfReader(input_pdf)
            total_pages = len(reader.pages)

            if total_pages < 2:
                self.log(f"⚠️ {total_pages} page(s) — minimum 2")
                reader.stream.close()
                return None

            blank_pages = (4 - (total_pages % 4)) % 4
            virtual_pages = total_pages + blank_pages

            self.log(f"📐 Livret : {paper_label} paysage "
                      f"({TARGET_WIDTH:.0f}×{TARGET_HEIGHT:.0f} pt)")
            self.log(f"📖 {total_pages} p. + {blank_pages} bl. = {virtual_pages}")

            def compute_transform(src_page, x_offset, slot_w, slot_h):
                mb = src_page.mediabox
                src_w = float(mb.width)
                src_h = float(mb.height)

                rotation = src_page.get('/Rotate', 0)
                if rotation in (90, 270):
                    visual_w, visual_h = src_h, src_w
                else:
                    visual_w, visual_h = src_w, src_h

                scale = min(slot_w / visual_w, slot_h / visual_h)
                scaled_vw = visual_w * scale
                scaled_vh = visual_h * scale

                dx = x_offset + (slot_w - scaled_vw) / 2
                dy = (slot_h - scaled_vh) / 2

                transform = (Transformation()
                             .scale(sx=scale, sy=scale)
                             .translate(tx=dx, ty=dy))
                return transform

            writer = PdfWriter()

            for i in range(virtual_pages // 2):
                page = PageObject.create_blank_page(
                    width=TARGET_WIDTH, height=TARGET_HEIGHT)

                page.mediabox.lower_left = (0, 0)
                page.mediabox.upper_right = (TARGET_WIDTH, TARGET_HEIGHT)

                offsets = [HALF_WIDTH, 0] if i % 2 == 0 else [0, HALF_WIDTH]

                idx1 = i
                if idx1 < total_pages:
                    tf = compute_transform(
                        reader.pages[idx1], offsets[0],
                        HALF_WIDTH, TARGET_HEIGHT)
                    page.merge_transformed_page(reader.pages[idx1], tf)

                idx2 = virtual_pages - 1 - i
                if idx2 < total_pages:
                    tf = compute_transform(
                        reader.pages[idx2], offsets[1],
                        HALF_WIDTH, TARGET_HEIGHT)
                    page.merge_transformed_page(reader.pages[idx2], tf)

                writer.add_page(page)

            first_page = writer.pages[0]
            mb = first_page.mediabox
            if mb.width < mb.height:
                self.log("⚠️ Page en PORTRAIT — correction forcée")
                for pg in writer.pages:
                    pg.mediabox.lower_left = (0, 0)
                    pg.mediabox.upper_right = (TARGET_WIDTH, TARGET_HEIGHT)

            temp_dir = tempfile.gettempdir()
            base = os.path.splitext(os.path.basename(input_pdf))[0]
            output_path = os.path.join(temp_dir, f"livret_{base}.pdf")
            with open(output_path, 'wb') as f:
                writer.write(f)

            reader.stream.close()
            return output_path

        except Exception as e:
            self.log(f"❌ Erreur livret : {e}")
            logger.exception("create_booklet_python")
            return None
        
    def load_sumatra_path(self):
    
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                path = config.get('sumatra_path', '')
                if path and os.path.exists(path):
                    logger.info(f"SumatraPDF chargé depuis config: {path}")
                    return path
        except Exception as e:
            logger.warning(f"Erreur chargement config: {e}")
        return None

    def save_sumatra_path(self, path):
        """Sauvegarde le chemin SumatraPDF dans le fichier de config."""
        try:
            config = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            config['sumatra_path'] = path
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2)
            logger.info(f"SumatraPDF sauvegardé: {path}")
        except Exception as e:
            logger.warning(f"Erreur sauvegarde config: {e}")

    def __init__(self, root):
        self.root = root
        self.root.title("Impression PDF - Version Livret")
        self.root.geometry("850x760")

        self.pdf_files = []
        self.printer_name = tk.StringVar()
        self.cancelled = False

        self.duplex_mode = tk.StringVar(value="simplex")
        self.booklet_mode = tk.BooleanVar(value=False)
        self.paper_size = tk.StringVar(value="A4")        # ✅ NOUVEAU

        self.sumatra_path = self.load_sumatra_path() or self.find_sumatra()

        self.create_widgets()
        self.get_printers()

        logger.info("=== Application démarrée ===")

    # ------------------------------------------------------------------
    def find_sumatra(self):
        paths = [
            r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
            r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
            os.path.join(os.environ.get('ProgramFiles', ''),
                         'SumatraPDF', 'SumatraPDF.exe'),
            os.path.join(os.environ.get('ProgramFiles(x86)', ''),
                         'SumatraPDF', 'SumatraPDF.exe'),
        ]
        for path in paths:
            expanded = os.path.expandvars(path)
            if os.path.exists(expanded):
                return expanded
        try:
            result = subprocess.run(
                ['where', 'sumatrapdf'], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip().split('\n')[0]
        except:
            pass
        return None

    # ------------------------------------------------------------------
    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Boutons ---
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(btn_frame, text="➕ Ajouter des PDF",
                   command=self.add_pdfs).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="🗑 Supprimer",
                   command=self.remove_selected).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="❌ Tout supprimer",
                   command=self.clear_all).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="🔍 Trouver SumatraPDF",
                   command=self.find_sumatra_gui).pack(side=tk.LEFT, padx=(5, 0))

        # --- Liste ---
        list_frame = ttk.LabelFrame(main_frame, text="📄 Documents", padding="5")
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set,
                                  height=5, selectmode=tk.EXTENDED)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.listbox.yview)
        self.count_label = ttk.Label(list_frame, text="Total: 0 fichiers")
        self.count_label.pack(pady=(5, 0))

        # --- Options ---
        options_frame = ttk.LabelFrame(main_frame, text="⚙️ Options", padding="10")
        options_frame.pack(fill=tk.X, pady=(0, 10))

        # Ligne 1 : Imprimante
        row1 = ttk.Frame(options_frame)
        row1.pack(fill=tk.X, pady=2)
        ttk.Label(row1, text="Imprimante:").pack(side=tk.LEFT, padx=(0, 5))
        self.printer_combo = ttk.Combobox(row1, textvariable=self.printer_name,
                                          width=40)
        self.printer_combo.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(row1, text="🔄", width=3,
                   command=self.get_printers).pack(side=tk.LEFT)
        self.printer_type_label = ttk.Label(row1, text="", foreground="gray")
        self.printer_type_label.pack(side=tk.LEFT, padx=(10, 0))
        self.printer_combo.bind('<<ComboboxSelected>>',
                                self._on_printer_changed)

        # Ligne 2 : Duplex
        row2 = ttk.Frame(options_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="Recto-Verso:").pack(side=tk.LEFT, padx=(0, 5))
        for text, value in [
            ("Simplex", "simplex"),
            ("Duplex - Bord long", "duplexlong"),
            ("Duplex - Bord court", "duplexshort")
        ]:
            ttk.Radiobutton(row2, text=text, variable=self.duplex_mode,
                            value=value).pack(side=tk.LEFT, padx=(0, 10))

        # Ligne 3 : Format papier ✅ NOUVEAU
        row_paper = ttk.Frame(options_frame)
        row_paper.pack(fill=tk.X, pady=2)
        ttk.Label(row_paper, text="Format papier:").pack(
            side=tk.LEFT, padx=(0, 5))
        for key, info in PAPER_SIZES.items():
            ttk.Radiobutton(
                row_paper, text=info["label"],
                variable=self.paper_size, value=key
            ).pack(side=tk.LEFT, padx=(0, 15))

        # Ligne 4 : Livret
        row3 = ttk.Frame(options_frame)
        row3.pack(fill=tk.X, pady=5)
        ttk.Checkbutton(
            row3,
            text="📖 Livret paysage (2 pages/face, 4 pages/feuille)",
            variable=self.booklet_mode,
            command=self.on_booklet_toggle
        ).pack(side=tk.LEFT)

        # Ligne 5 : Statut Sumatra
        row4 = ttk.Frame(options_frame)
        row4.pack(fill=tk.X, pady=5)
        if self.sumatra_path:
            self.sumatra_status = ttk.Label(
                row4,
                text=f"✅ SumatraPDF: {os.path.basename(self.sumatra_path)}")
        else:
            self.sumatra_status = ttk.Label(
                row4, text="⚠️ SumatraPDF non trouvé")
        self.sumatra_status.pack(side=tk.LEFT)

        # --- Journal ---
        log_frame = ttk.LabelFrame(main_frame, text="📋 Journal", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        self.log_text = tk.Text(log_frame, height=5, width=70, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL,
                                   command=self.log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=log_scroll.set)

        # --- Barre impression ---
        print_frame = ttk.Frame(main_frame)
        print_frame.pack(fill=tk.X, pady=(5, 0))

        btn_row = ttk.Frame(print_frame)
        btn_row.pack(fill=tk.X)
        self.print_btn = ttk.Button(btn_row, text="🖨️ IMPRIMER",
                                    command=self.print_all)
        self.print_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.cancel_btn = ttk.Button(btn_row, text="⏹ ANNULER",
                                     command=self.cancel_print,
                                     state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=(5, 0))

        self.progress = ttk.Progressbar(print_frame, mode='determinate')
        self.progress.pack(fill=tk.X, pady=(5, 0))
        self.status_label = ttk.Label(print_frame, text="✅ Prêt", anchor=tk.W)
        self.status_label.pack(fill=tk.X)

    # ------------------------------------------------------------------
    def _on_printer_changed(self, event=None):
        name = self.printer_name.get()
        ptype = get_printer_type(name)
        if ptype == "virtual":
            self.printer_type_label.config(
                text="⚠️ IMPRIMANTE PDF", foreground="orange")
        else:
            self.printer_type_label.config(text="")

    def cancel_print(self):
        self.cancelled = True
        self.log("⏹ ANNULATION...")
        kill_stale_sumatra()

    def on_booklet_toggle(self):
        if self.booklet_mode.get():
            self.duplex_mode.set("duplexshort")
            self.log("📖 Mode livret activé - Duplex bord court forcé")
        else:
            self.log("📖 Mode livret désactivé")

    def find_sumatra_gui(self):
        path = filedialog.askopenfilename(
            title="Sélectionner SumatraPDF.exe",
            filetypes=[("Exécutable", "*.exe")]
        )
        if path:
            self.sumatra_path = path
            self.save_sumatra_path(path)           # ✅ SAUVEGARDE
            self.sumatra_status.config(
                text=f"✅ SumatraPDF: {os.path.basename(path)}")
            self.log(f"✅ SumatraPDF sélectionné: {path}")


    def log(self, message):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        logger.info(message)
        self.root.update()

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

    # ------------------------------------------------------------------
    def get_printers(self):
        self.clear_log()
        self.log("🔍 Récupération des imprimantes...")
        printers = []
        try:
            if sys.platform == "win32":
                try:
                    import win32print
                    printers = [p[2] for p in win32print.EnumPrinters(
                        win32print.PRINTER_ENUM_LOCAL |
                        win32print.PRINTER_ENUM_CONNECTIONS
                    )]
                except ImportError:
                    result = subprocess.run(
                        ['wmic', 'printer', 'get', 'name'],
                        capture_output=True, text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW
                    )
                    lines = result.stdout.strip().split('\n')
                    if len(lines) > 1:
                        printers = [p.strip() for p in lines[1:] if p.strip()]
        except Exception as e:
            self.log(f"❌ Erreur: {e}")
        if not printers:
            printers = ["Aucune imprimante"]
        self.printer_combo['values'] = printers
        if printers:
            self.printer_combo.current(0)
            self._on_printer_changed()
        self.log(f"✅ {len(printers)} imprimante(s)")

    # ------------------------------------------------------------------
    def add_pdfs(self):
        files = filedialog.askopenfilenames(
            title="Sélectionner des fichiers PDF",
            filetypes=[("Fichiers PDF", "*.pdf")]
        )
        for f in files:
            if f not in self.pdf_files:
                self.pdf_files.append(f)
                self.listbox.insert(tk.END, os.path.basename(f))
        self.update_counter()

    def remove_selected(self):
        selected = self.listbox.curselection()
        for index in sorted(selected, reverse=True):
            del self.pdf_files[index]
            self.listbox.delete(index)
        self.update_counter()

    def clear_all(self):
        self.pdf_files.clear()
        self.listbox.delete(0, tk.END)
        self.update_counter()

    def update_counter(self):
        self.count_label.config(text=f"Total: {len(self.pdf_files)} fichiers")

    # ==================================================================
    #  IMPRESSION
    # ==================================================================
    def print_all(self):
        if not self.pdf_files:
            messagebox.showwarning("Erreur", "Ajoutez des fichiers PDF.")
            return

        printer = self.printer_name.get()
        if not printer or printer == "Aucune imprimante":
            messagebox.showerror("Erreur", "Sélectionnez une imprimante.")
            return

        printer_type = get_printer_type(printer)

        if printer_type == "virtual":
            if self.booklet_mode.get():
                msg = (
                    f"⚠️ '{printer}' est une imprimante virtuelle.\n\n"
                    f"👉 Voulez-vous SAUVEGARDER les livrets au format PDF ?\n"
                    f"   (Vous pourrez les imprimer ensuite)\n\n"
                    f"Sinon, changez d'imprimante."
                )
                if messagebox.askyesno("Imprimante PDF détectée", msg):
                    self.save_booklets_as_pdf()
                return
            else:
                msg = (
                    f"⚠️ '{printer}' est une imprimante virtuelle.\n\n"
                    f"👉 Option recommandée : SAUVEGARDER directement\n"
                    f"   (copie rapide sans boîte de dialogue)\n\n"
                    f"👉 Alternative : IMPRIMER avec dialogue\n"
                    f"   ({len(self.pdf_files)} confirmation(s) manuelle(s))"
                )
                dialog = SaveOrPrintDialog(self.root, printer,
                                           len(self.pdf_files))
                self.root.wait_window(dialog)
                if dialog.result == "save":
                    self.save_pdfs_direct()
                elif dialog.result == "print":
                    self.print_all_with_dialogs(printer)
                return

        self._run_print_batch(printer, use_dialog=False)

    # ------------------------------------------------------------------
    def _run_print_batch(self, printer, use_dialog=False):
        """Lance le batch d'impression (imprimante physique)."""
        paper_key = self.paper_size.get()

        # Validation
        self.log("\n🔍 VALIDATION DES PDF...")
        invalid_files = []
        valid_files = []
        for pdf_path in self.pdf_files:
            ok_val, err_val = validate_pdf(pdf_path)
            if ok_val:
                valid_files.append(pdf_path)
                self.log(f"   ✅ {os.path.basename(pdf_path)}")
            else:
                invalid_files.append((os.path.basename(pdf_path), err_val))
                self.log(f"   ❌ {os.path.basename(pdf_path)} — {err_val}")

        if invalid_files:
            msg = "⚠️ PDF invalides :\n\n"
            for name, err_val in invalid_files:
                msg += f"• {name}\n  → {err_val}\n"
            msg += "\nContinuer avec les fichiers valides ?"
            if not messagebox.askyesno("PDF invalides", msg):
                return

        if not valid_files:
            messagebox.showerror("Erreur", "Aucun PDF valide.")
            return

        if not self.sumatra_path:
            result = messagebox.askyesno(
                "SumatraPDF requis",
                "SumatraPDF n'est pas trouvé.\nTélécharger ?"
            )
            if result:
                self.download_sumatra()
                if not self.sumatra_path:
                    return
            else:
                return

        kill_stale_sumatra()

        self.cancelled = False
        self.print_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.progress['value'] = 0
        self.progress['maximum'] = len(valid_files)

        success = 0
        errors = []
        temp_files_to_clean = []
        timeout = DIALOG_TIMEOUT if use_dialog else PRINT_TIMEOUT

        self.log(f"\n🖨️ DÉBUT — {len(valid_files)} fichiers")
        self.log(f"📌 Imprimante : {printer} ({get_printer_type(printer)})")
        self.log(f"📌 Format : {paper_key}")
        self.log(f"📌 Mode : {self.duplex_mode.get()}")
        self.log(f"📌 Livret : {'Oui' if self.booklet_mode.get() else 'Non'}")
        self.log(f"📋 Log : {LOG_FILE}")

        for i, current_pdf in enumerate(valid_files):
            if self.cancelled:
                self.log("⏹ Annulé par l'utilisateur")
                break

            filename = os.path.basename(current_pdf)
            self.status_label.config(
                text=f"📄 [{i+1}/{len(valid_files)}] {filename}")
            self.root.update()
            self.log(f"\n📄 [{i+1}/{len(valid_files)}] {filename}")

            kill_stale_sumatra()
            time.sleep(0.3)

            result_ok = False
            for attempt in range(MAX_RETRIES + 1):
                if self.cancelled:
                    break
                if attempt > 0:
                    self.log(f"   🔄 Retry {attempt}/{MAX_RETRIES}...")
                    time.sleep(RETRY_DELAY)
                    kill_stale_sumatra()

                try:
                    booklet_path = None
                    if self.booklet_mode.get():
                        booklet_path = self.create_booklet_python(current_pdf)
                        if booklet_path:
                            temp_files_to_clean.append(booklet_path)
                            result_ok = self.print_with_sumatra(
                                booklet_path, printer,
                                scale_mode="fit",
                                force_landscape=True,
                                disable_auto_rotation=True,   # ✅ Empêche l'auto-rotation paysage→portrait
                                paper_key=paper_key,
                                use_dialog=use_dialog,
                                timeout=timeout)
                        else:
                            result_ok = False
                    else:
                        result_ok = self.print_with_sumatra(
                            current_pdf, printer,
                            paper_key=paper_key,
                            use_dialog=use_dialog,
                            timeout=timeout)

                    if result_ok:
                        break
                except Exception as exc:
                    logger.exception(f"Exception {filename}")
                    self.log(f"   ⚠️ Exception : {exc}")

            if result_ok:
                success += 1
                self.log(f"   ✅ RÉUSSITE")
            else:
                errors.append(filename)
                self.log(f"   ❌ ÉCHEC ({attempt+1} tentative(s))")

            self.progress['value'] = i + 1
            self.root.update()

            if i < len(valid_files) - 1:
                time.sleep(INTER_JOB_DELAY)

        self.log("\n🧹 Nettoyage...")
        for tmp_file in temp_files_to_clean:
            safe_remove(tmp_file)
        kill_stale_sumatra()

        self.print_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        self.progress['value'] = 0
        self.status_label.config(text="✅ Terminé")
        self.cancelled = False

        self.log(f"\n📊 RÉSULTAT : {success}/{len(valid_files)} réussis")
        if errors:
            msg = (f"✅ {success} réussis\n"
                   f"❌ {len(errors)} échecs :\n" +
                   "\n".join(f"• {e}" for e in errors[:10]) +
                   ("\n..." if len(errors) > 10 else ""))
            messagebox.showwarning("⚠️ Impression partielle", msg)
        else:
            messagebox.showinfo("✅ Succès",
                f"Tous les {success} fichiers ont été imprimés !")

    # ------------------------------------------------------------------
    def save_pdfs_direct(self):
        output_dir = filedialog.askdirectory(
            title="Dossier de destination pour les PDF")
        if not output_dir:
            return

        self.print_btn.config(state=tk.DISABLED)
        self.progress['value'] = 0
        self.progress['maximum'] = len(self.pdf_files)
        success = 0

        for i, src_pdf in enumerate(self.pdf_files):
            filename = os.path.basename(src_pdf)
            self.status_label.config(text=f"📄 Copie: {filename}")
            self.root.update()
            self.log(f"\n📄 Copie: {filename}")
            try:
                ok_val, err_val = validate_pdf(src_pdf)
                if not ok_val:
                    self.log(f"   ❌ {err_val}")
                    continue
                dest = os.path.join(output_dir, filename)
                if os.path.exists(dest):
                    base, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(
                        os.path.join(output_dir, f"{base}_{counter}{ext}")):
                        counter += 1
                    dest = os.path.join(output_dir,
                                        f"{base}_{counter}{ext}")
                copy_pdf_direct(src_pdf, dest)
                success += 1
                self.log(f"   ✅ → {os.path.basename(dest)}")
            except Exception as exc:
                self.log(f"   ❌ Erreur : {exc}")

            self.progress['value'] = i + 1
            self.root.update()

        self.print_btn.config(state=tk.NORMAL)
        self.progress['value'] = 0
        self.status_label.config(text="✅ Terminé")
        messagebox.showinfo("✅ Copie terminée",
            f"{success}/{len(self.pdf_files)} copié(s)\ndans {output_dir}")

    def print_all_with_dialogs(self, printer):
        msg = (f"⚠️ {len(self.pdf_files)} boîte(s) de dialogue vont s'ouvrir.\n\n"
               f"Pour chaque fichier :\n"
               f"  1. Confirmez l'enregistrement\n"
               f"  2. Choisissez le dossier\n\n"
               f"Cliquez OK pour continuer.")
        if not messagebox.askokcancel("Confirmation", msg):
            return
        self._run_print_batch(printer, use_dialog=True)

    def save_booklets_as_pdf(self):
        output_dir = filedialog.askdirectory(
            title="Dossier de destination pour les livrets PDF")
        if not output_dir:
            return

        self.cancelled = False
        self.print_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.progress['value'] = 0
        self.progress['maximum'] = len(self.pdf_files)
        success_count = 0
        temp_files = []

        for i, one_pdf in enumerate(self.pdf_files):
            if self.cancelled:
                break

            filename = os.path.basename(one_pdf)
            self.status_label.config(text=f"📄 Traitement: {filename}")
            self.root.update()
            self.log(f"\n📄 Livret: {filename}")

            try:
                ok_val, err_val = validate_pdf(one_pdf)
                if not ok_val:
                    self.log(f"   ❌ {err_val}")
                    continue

                booklet_result = self.create_booklet_python(one_pdf)
                if booklet_result:
                    temp_files.append(booklet_result)
                    base = os.path.splitext(filename)[0]
                    dest = os.path.join(output_dir, f"livret_{base}.pdf")
                    copy_pdf_direct(booklet_result, dest)
                    success_count += 1
                    self.log(f"   ✅ → {os.path.basename(dest)}")
                else:
                    self.log(f"   ❌ Échec création livret")
            except Exception as exc:
                self.log(f"   💥 Erreur : {exc}")

            self.progress['value'] = i + 1
            self.root.update()

        for tmp_file in temp_files:
            safe_remove(tmp_file)

        self.print_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        self.progress['value'] = 0
        self.status_label.config(text="✅ Terminé")
        messagebox.showinfo("✅ Sauvegarde terminée",
            f"{success_count}/{len(self.pdf_files)} livret(s)\ndans {output_dir}")

   # ========== MODIF 1 : create_booklet_python — centrage corrigé ==========

def create_booklet_python(self, input_pdf):
    """
    Livret paysage au format choisi (A4 ou A3).
    ✅ Centrage corrigé : merge_transformed_page normalise déjà l'origine
       du mediabox, donc on ne compense plus avec left/bottom.
    """
    try:
        paper_key = self.paper_size.get()
        TARGET_WIDTH, TARGET_HEIGHT = get_landscape_dims(paper_key)
        HALF_WIDTH = TARGET_WIDTH / 2
        paper_label = PAPER_SIZES[paper_key]["label"]

        reader = PdfReader(input_pdf)
        total_pages = len(reader.pages)

        if total_pages < 2:
            self.log(f"⚠️ {total_pages} page(s) — minimum 2")
            reader.stream.close()
            return None

        blank_pages = (4 - (total_pages % 4)) % 4
        virtual_pages = total_pages + blank_pages

        self.log(f"📐 Livret : {paper_label} paysage "
                  f"({TARGET_WIDTH:.0f}×{TARGET_HEIGHT:.0f} pt)")
        self.log(f"📖 {total_pages} p. + {blank_pages} bl. = {virtual_pages}")

        # ✅ FONCTION CORRIGÉE : centrage propre sans compensation mediabox
        def compute_transform(src_page, x_offset, slot_w, slot_h):
            mb = src_page.mediabox
            src_w = float(mb.width)
            src_h = float(mb.height)

            # Gestion de la rotation de la page source
            rotation = src_page.get('/Rotate', 0)
            if rotation in (90, 270):
                visual_w, visual_h = src_h, src_w
            else:
                visual_w, visual_h = src_w, src_h

            # Facteur d'échelle pour tenir dans le slot
            scale = min(slot_w / visual_w, slot_h / visual_h)
            scaled_vw = visual_w * scale
            scaled_vh = visual_h * scale

            # ✅ Centrage PARFAIT : merge_transformed_page place déjà le
            #    contenu de la page source dans un Form XObject normalisé
            #    (origine à 0,0). On centre donc simplement dans le slot.
            dx = x_offset + (slot_w - scaled_vw) / 2
            dy = (slot_h - scaled_vh) / 2

            # ✅ Transformation : scale puis translate au centre du slot
            transform = (Transformation()
                         .scale(sx=scale, sy=scale)
                         .translate(tx=dx, ty=dy))
            return transform

        writer = PdfWriter()

        for i in range(virtual_pages // 2):
            page = PageObject.create_blank_page(
                width=TARGET_WIDTH, height=TARGET_HEIGHT)

            page.mediabox.lower_left = (0, 0)
            page.mediabox.upper_right = (TARGET_WIDTH, TARGET_HEIGHT)

            # Recto : page virtuelle i à droite, page virtuelle N-1-i à gauche
            # Verso : page virtuelle i à gauche, page virtuelle N-1-i à droite
            offsets = [HALF_WIDTH, 0] if i % 2 == 0 else [0, HALF_WIDTH]

            idx1 = i
            if idx1 < total_pages:
                tf = compute_transform(
                    reader.pages[idx1], offsets[0],
                    HALF_WIDTH, TARGET_HEIGHT)
                page.merge_transformed_page(reader.pages[idx1], tf)

            idx2 = virtual_pages - 1 - i
            if idx2 < total_pages:
                tf = compute_transform(
                    reader.pages[idx2], offsets[1],
                    HALF_WIDTH, TARGET_HEIGHT)
                page.merge_transformed_page(reader.pages[idx2], tf)

            writer.add_page(page)

        # Vérification : s'assurer que toutes les pages sont paysage
        first_page = writer.pages[0]
        mb = first_page.mediabox
        if mb.width < mb.height:
            self.log("⚠️ Page en PORTRAIT — correction forcée")
            for pg in writer.pages:
                pg.mediabox.lower_left = (0, 0)
                pg.mediabox.upper_right = (TARGET_WIDTH, TARGET_HEIGHT)

        temp_dir = tempfile.gettempdir()
        base = os.path.splitext(os.path.basename(input_pdf))[0]
        output_path = os.path.join(temp_dir, f"livret_{base}.pdf")
        with open(output_path, 'wb') as f:
            writer.write(f)

        reader.stream.close()
        return output_path

    except Exception as e:
        self.log(f"❌ Erreur livret : {e}")
        logger.exception("create_booklet_python")
        return None

    # ==================================================================
    #  IMPRESSION VIA SUMATRAPDF
    # ==================================================================
    def print_with_sumatra(self, file_to_print, printer_name,
                       scale_mode="fit", force_landscape=False,
                       disable_auto_rotation=False,   # ✅ NOUVEAU
                       paper_key=None, use_dialog=False,
                       timeout=PRINT_TIMEOUT):
   
        try:
            if paper_key is None:
                paper_key = self.paper_size.get()

            duplex_mode = self.duplex_mode.get()
            duplex_str = {
                "duplexlong": "duplexlong",
                "duplexshort": "duplexshort"
            }.get(duplex_mode, "simplex")

            paper_name = PAPER_SIZES[paper_key]["sumatra_name"]

            settings_parts = [duplex_str]

            if scale_mode != "noscale":
                settings_parts.append(scale_mode)

            settings_parts.append(f"paper={paper_name}")

            # ✅ CORRECTION : disable-auto-rotation pour livret,
            #    landscape pour usage explicite uniquement
            if force_landscape:
                settings_parts.append("landscape")
            if disable_auto_rotation:
                settings_parts.append("disable-auto-rotation")

            print_settings = ",".join(settings_parts)

            cmd = [
                self.sumatra_path,
                '-print-to', printer_name,
                '-print-settings', print_settings,
            ]
            if not use_dialog:
                cmd.append('-silent')
            cmd.append(file_to_print)

            self.log(f"🔧 Sumatra : {print_settings}")
            logger.debug(f"Sumatra cmd: {' '.join(cmd)}")

            creationflags = (subprocess.CREATE_NO_WINDOW
                            if not use_dialog else 0)

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=creationflags
            )

            if result.returncode != 0:
                stderr = (result.stderr or "").strip()
                logger.warning(f"Sumatra code={result.returncode}")
                if stderr:
                    self.log(f"   ⚠️ {stderr[:150]}")
                return False

            return True

        except subprocess.TimeoutExpired:
            self.log(f"⏱️ Timeout ({timeout}s)")
            kill_stale_sumatra()
            return False
        except FileNotFoundError:
            self.log("❌ SumatraPDF introuvable")
            return False
        except Exception as e:
            self.log(f"❌ Erreur SumatraPDF: {e}")
            logger.exception("print_with_sumatra")
            return False

    # ------------------------------------------------------------------
    def download_sumatra(self):
        try:
            import urllib.request
            import zipfile
            self.log("📥 Téléchargement SumatraPDF...")
            url = ("https://www.sumatrapdfreader.org/dl/rel/3.5.2/"
                "SumatraPDF-3.5.2-64.zip")
            zip_path = os.path.join(os.environ['TEMP'], 'sumatra.zip')
            urllib.request.urlretrieve(url, zip_path)
            extract_dir = os.path.join(os.environ['ProgramFiles'],
                                    'SumatraPDF')
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            self.log(f"✅ Extrait dans : {extract_dir}")
            sumatra_exe = os.path.join(extract_dir, 'SumatraPDF.exe')
            if os.path.exists(sumatra_exe):
                self.sumatra_path = sumatra_exe
                self.save_sumatra_path(sumatra_exe)    # ✅ SAUVEGARDE
                self.sumatra_status.config(
                    text=f"✅ SumatraPDF: {os.path.basename(sumatra_exe)}")
                return True
            safe_remove(zip_path)
        except Exception as e:
            self.log(f"❌ Erreur : {e}")
            messagebox.showerror("Erreur",
                "Téléchargement manuel :\n"
                "https://www.sumatrapdfreader.org/download")
        return False


# ======================================================================
#  Boîte de dialogue Sauvegarder / Imprimer / Annuler
# ======================================================================
class SaveOrPrintDialog(tk.Toplevel):
    def __init__(self, parent, printer_name, count):
        super().__init__(parent)
        self.result = None
        self.title("Imprimante PDF détectée")
        self.geometry("520x320")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - 520) // 2
        y = parent.winfo_y() + (parent.winfo_height() - 320) // 2
        self.geometry(f"+{x}+{y}")

        frame = ttk.Frame(self, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame,
                  text=f"⚠️ '{printer_name}' est une imprimante virtuelle.",
                  font=("", 11, "bold"), foreground="orange"
                  ).pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(frame,
                  text=("L'impression via SumatraPDF ouvrira des boîtes de\n"
                        "dialogue 'Enregistrer sous' pour chaque fichier."),
                  wraplength=470).pack(anchor=tk.W, pady=(0, 10))

        ttk.Label(frame,
                  text=f"📂 {count} fichier(s) en attente",
                  font=("", 10, "bold")
                  ).pack(anchor=tk.W, pady=(0, 15))

        ttk.Separator(frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        ttk.Label(frame,
                  text="Que voulez-vous faire ?",
                  font=("", 11, "bold")
                  ).pack(anchor=tk.W, pady=(0, 15))

        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame,
                   text="💾 SAUVEGARDER directement\n(copie rapide, recommandé)",
                   command=lambda: self._choose("save")
                   ).pack(fill=tk.X, pady=(0, 5))

        ttk.Button(btn_frame,
                   text="🖨️ IMPRIMER avec dialogue\n(vous confirmerez chaque fichier)",
                   command=lambda: self._choose("print")
                   ).pack(fill=tk.X, pady=(0, 5))

        ttk.Button(btn_frame,
                   text="❌ ANNULER",
                   command=lambda: self._choose("cancel")
                   ).pack(fill=tk.X)

    def _choose(self, choice):
        self.result = choice
        self.destroy()


def main():
    root = tk.Tk()
    app = SimplePDFPrinter(root)
    root.mainloop()


if __name__ == "__main__":
    main()