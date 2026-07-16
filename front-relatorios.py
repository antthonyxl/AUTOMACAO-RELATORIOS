import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
from tkcalendar import Calendar
from datetime import timedelta, datetime, date
import threading
import subprocess
import queue

# ===================== IMPORTS DOS BACKENDS =====================
try:
    from estimativas import estimativas_frente_loja
except Exception:
    from estimativas import estimativas_frente_loja

try:
    from estimativas import estimativas_acougue
except Exception:
    from estimativas import estimativas_acougue


# ===================== CONFIG =====================
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ===================== UTIL DATAS =====================
def next_monday(d: date) -> date:
    days_ahead = (0 - d.weekday()) % 7
    return d if days_ahead == 0 else d + timedelta(days=days_ahead)

def prev_sunday(d: date) -> date:
    days_back = (d.weekday() - 6) % 7
    return d if days_back == 0 else d - timedelta(days=days_back)

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


# ===================== DATEPICKER ESTÁVEL (CTk + Calendar modal) =====================
class CTkDatePicker(ctk.CTkFrame):
    """
    DatePicker estável para CustomTkinter:
    - Entry com dd/mm/yyyy
    - Botão abre modal (CTkToplevel) com tkcalendar.Calendar
    - Callback on_change(date_obj) opcional
    """
    def __init__(self, master, label_text, on_change=None, width=160):
        super().__init__(master)
        self.on_change = on_change
        self._top = None
        self._cal = None

        self.label = ctk.CTkLabel(self, text=label_text)
        self.label.grid(row=0, column=0, padx=(0, 8), pady=6, sticky="e")

        self.var = tk.StringVar(value="")
        self.entry = ctk.CTkEntry(self, textvariable=self.var, width=width, placeholder_text="dd/mm/aaaa")
        self.entry.grid(row=0, column=1, padx=(0, 6), pady=6, sticky="w")

        self.btn = ctk.CTkButton(self, text="📅", width=40, command=self.open_calendar)
        self.btn.grid(row=0, column=2, padx=0, pady=6)

        # valida ao sair do campo (digitado manualmente)
        self.entry.bind("<FocusOut>", self._validate_typed_date)

    def get(self) -> str:
        return self.var.get().strip()

    def set(self, ddmmyyyy: str):
        self.var.set(ddmmyyyy)

    def get_date(self) -> date | None:
        try:
            return datetime.strptime(self.get(), "%d/%m/%Y").date()
        except Exception:
            return None

    def set_date(self, d: date, call_on_change=True):
        self.set(d.strftime("%d/%m/%Y"))
        if call_on_change and self.on_change:
            self.on_change(d)

    def _validate_typed_date(self, event=None):
        s = self.get()
        if not s:
            return
        try:
            d = datetime.strptime(s, "%d/%m/%Y").date()
            if self.on_change:
                self.on_change(d)
        except Exception:
            # inválida → limpa (se preferir, pode manter e avisar)
            self.var.set("")

    def open_calendar(self):
        if self._top and self._top.winfo_exists():
            self._top.lift()
            return

        self._top = ctk.CTkToplevel(self)
        self._top.title("Selecionar data")
        self._top.resizable(False, False)
        self._top.grab_set()  # modal

        # posiciona perto do widget
        try:
            x = self.winfo_rootx() + 20
            y = self.winfo_rooty() + 40
            self._top.geometry(f"+{x}+{y}")
        except Exception:
            pass

        frame = tk.Frame(self._top)
        frame.pack(padx=10, pady=10)

        initial = self.get_date() or date.today()

        self._cal = Calendar(
            frame,
            selectmode="day",
            year=initial.year,
            month=initial.month,
            day=initial.day,
            date_pattern="dd/mm/yyyy",
            locale="pt_BR",
        )
        self._cal.pack()

        btns = ctk.CTkFrame(self._top)
        btns.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkButton(btns, text="OK", command=self._pick).pack(side="left", expand=True, padx=(0, 6))
        ctk.CTkButton(
            btns, text="Cancelar", fg_color="#555", hover_color="#444",
            command=self._top.destroy
        ).pack(side="left", expand=True, padx=(6, 0))

    def _pick(self):
        try:
            s = self._cal.get_date()  # dd/mm/yyyy
            d = datetime.strptime(s, "%d/%m/%Y").date()
            self.set_date(d, call_on_change=True)
        finally:
            if self._top and self._top.winfo_exists():
                self._top.destroy()


# ===================== ESTADOS (separados por aba) =====================
parar_frente = False
parar_acougue = False

ui_queue_frente = queue.Queue()
ui_queue_acougue = queue.Queue()


# ===================== HELPERS UI (por fila) =====================
def make_ui_helpers(ui_queue):
    def ui_log(msg: str):
        ui_queue.put(("log", msg))

    def ui_status(msg: str):
        ui_queue.put(("status", msg))

    def ui_progress(value_0_1: float):
        ui_queue.put(("progress", _clamp01(value_0_1)))

    def ui_buttons(running: bool):
        ui_queue.put(("buttons", bool(running)))

    return ui_log, ui_status, ui_progress, ui_buttons


ui_log_frente, ui_status_frente, ui_progress_frente, ui_buttons_frente = make_ui_helpers(ui_queue_frente)
ui_log_acougue, ui_status_acougue, ui_progress_acougue, ui_buttons_acougue = make_ui_helpers(ui_queue_acougue)


def process_ui_queue(ui_queue, txt_log, label_status, progressbar, botao_iniciar, botao_parar):
    try:
        while True:
            kind, payload = ui_queue.get_nowait()

            if kind == "log":
                txt_log.configure(state="normal")
                txt_log.insert("end", payload + "\n")
                txt_log.see("end")
                txt_log.configure(state="disabled")

            elif kind == "status":
                label_status.configure(text=payload)

            elif kind == "progress":
                progressbar.set(payload)

            elif kind == "buttons":
                running = bool(payload)
                botao_iniciar.configure(state="disabled" if running else "normal")
                botao_parar.configure(state="normal" if running else "disabled")

    except queue.Empty:
        pass


def tick_ui():
    process_ui_queue(ui_queue_frente, txt_log_frente, label_status_frente, progressbar_frente, botao_iniciar_frente, botao_parar_frente)
    process_ui_queue(ui_queue_acougue, txt_log_acougue, label_status_acougue, progressbar_acougue, botao_iniciar_acougue, botao_parar_acougue)
    janela.after(100, tick_ui)


# ===================== LOJAS (persistentes via backend) =====================
def carregar_lojas():
    # usa a frente como “fonte de verdade” do lojas.json
    return estimativas_frente_loja.carregar_lojas()

def salvar_lojas(lojas):
    estimativas_frente_loja.salvar_lojas(lojas)
    # tenta manter sincronizado se o açougue também tiver salvar_lojas
    try:
        estimativas_acougue.salvar_lojas(lojas)
    except Exception:
        pass

def normalizar_nome_loja(s: str) -> str:
    return (s or "").strip()


# ===================== CHECKBOXES (separados) =====================
check_vars_frente = {}
check_vars_acougue = {}

def rebuild_checkboxes():
    lojas = carregar_lojas()

    marcadas_frente = {k: v.get() for k, v in check_vars_frente.items()}
    marcadas_acougue = {k: v.get() for k, v in check_vars_acougue.items()}

    for w in scrollable_lojas_frente.winfo_children():
        w.destroy()
    for w in scrollable_lojas_acougue.winfo_children():
        w.destroy()

    check_vars_frente.clear()
    check_vars_acougue.clear()

    for loja in lojas:
        var = tk.BooleanVar(value=marcadas_frente.get(loja, False))
        check_vars_frente[loja] = var
        ctk.CTkCheckBox(scrollable_lojas_frente, text=loja, variable=var).pack(anchor="w", padx=10, pady=6)

    for loja in lojas:
        var = tk.BooleanVar(value=marcadas_acougue.get(loja, False))
        check_vars_acougue[loja] = var
        ctk.CTkCheckBox(scrollable_lojas_acougue, text=loja, variable=var).pack(anchor="w", padx=10, pady=6)

    combo_editar.configure(values=lojas)
    if lojas:
        if combo_editar.get() not in lojas:
            combo_editar.set(lojas[0])
    else:
        combo_editar.set("")


def selecionar_todas_frente():
    for var in check_vars_frente.values():
        var.set(True)

def desmarcar_todas_frente():
    for var in check_vars_frente.values():
        var.set(False)

def selecionar_todas_acougue():
    for var in check_vars_acougue.values():
        var.set(True)

def desmarcar_todas_acougue():
    for var in check_vars_acougue.values():
        var.set(False)


# ===================== ABA LOJAS: CRUD =====================
def adicionar_loja():
    nome = normalizar_nome_loja(entry_nova_loja.get())
    if not nome:
        messagebox.showerror("Erro", "Digite o nome da loja para adicionar.")
        return

    lojas = carregar_lojas()
    if nome in lojas:
        messagebox.showerror("Erro", "Essa loja já existe.")
        return

    lojas.append(nome)
    salvar_lojas(lojas)

    entry_nova_loja.delete(0, "end")
    ui_log_frente(f"✅ Loja adicionada: {nome}")
    ui_log_acougue(f"✅ Loja adicionada: {nome}")
    rebuild_checkboxes()

def editar_loja():
    antiga = normalizar_nome_loja(combo_editar.get())
    nova = normalizar_nome_loja(entry_editar_loja.get())

    if not antiga:
        messagebox.showerror("Erro", "Selecione a loja a editar.")
        return
    if not nova:
        messagebox.showerror("Erro", "Digite o novo nome da loja.")
        return

    lojas = carregar_lojas()
    if antiga not in lojas:
        messagebox.showerror("Erro", "A loja selecionada não existe mais.")
        rebuild_checkboxes()
        return

    if nova in lojas and nova != antiga:
        messagebox.showerror("Erro", "Já existe uma loja com esse nome.")
        return

    lojas[lojas.index(antiga)] = nova
    salvar_lojas(lojas)

    entry_editar_loja.delete(0, "end")
    ui_log_frente(f"✏️ Loja renomeada: {antiga} → {nova}")
    ui_log_acougue(f"✏️ Loja renomeada: {antiga} → {nova}")
    rebuild_checkboxes()

def remover_loja():
    alvo = normalizar_nome_loja(combo_editar.get())
    if not alvo:
        messagebox.showerror("Erro", "Selecione a loja a remover.")
        return

    if not messagebox.askyesno("Confirmar", f"Remover a loja '{alvo}'?"):
        return

    lojas = carregar_lojas()
    if alvo in lojas:
        lojas.remove(alvo)
        salvar_lojas(lojas)
        ui_log_frente(f"🗑️ Loja removida: {alvo}")
        ui_log_acougue(f"🗑️ Loja removida: {alvo}")
        rebuild_checkboxes()
    else:
        messagebox.showerror("Erro", "A loja selecionada não existe mais.")
        rebuild_checkboxes()


# ===================== PARAR (separado por aba) =====================
def parar_execucao_frente():
    global parar_frente
    parar_frente = True
    ui_log_frente("🛑 Parada solicitada (Frente de Loja)!")

    try:
        subprocess.call('taskkill /f /im chromedriver.exe', shell=True)
        subprocess.call('taskkill /f /im chrome.exe', shell=True)
        ui_log_frente("🧹 Chrome e Chromedriver finalizados.")
    except Exception as e:
        ui_log_frente(f"Erro ao tentar parar os navegadores: {e}")

def parar_execucao_acougue():
    global parar_acougue
    parar_acougue = True
    ui_log_acougue("🛑 Parada solicitada (Açougue)!")

    try:
        subprocess.call('taskkill /f /im chromedriver.exe', shell=True)
        subprocess.call('taskkill /f /im chrome.exe', shell=True)
        ui_log_acougue("🧹 Chrome e Chromedriver finalizados.")
    except Exception as e:
        ui_log_acougue(f"Erro ao tentar parar os navegadores: {e}")


# ===================== REGRAS DE SEGUNDA/DOMINGO (Frente) =====================
def on_change_data_inicial_frente(d: date):
    # força segunda
    if d.weekday() != 0:
        d2 = next_monday(d)
        dp_ini_frente.set_date(d2, call_on_change=False)
        d = d2

    # garante final >= inicial
    df = dp_fim_frente.get_date()
    if df and df < d:
        dp_fim_frente.set_date(d + timedelta(days=6), call_on_change=False)

    # força final domingo
    df2 = dp_fim_frente.get_date()
    if df2 and df2.weekday() != 6:
        dp_fim_frente.set_date(prev_sunday(df2), call_on_change=False)

def on_change_data_final_frente(d: date):
    # força domingo
    if d.weekday() != 6:
        d2 = prev_sunday(d)
        dp_fim_frente.set_date(d2, call_on_change=False)
        d = d2

    di = dp_ini_frente.get_date()
    if di and d < di:
        dp_ini_frente.set_date(d - timedelta(days=6), call_on_change=False)

    # força inicial segunda
    di2 = dp_ini_frente.get_date()
    if di2 and di2.weekday() != 0:
        dp_ini_frente.set_date(next_monday(di2), call_on_change=False)


# ===================== REGRAS DE SEGUNDA/DOMINGO (Açougue) =====================
def on_change_data_inicial_acougue(d: date):
    if d.weekday() != 0:
        d2 = next_monday(d)
        dp_ini_acougue.set_date(d2, call_on_change=False)
        d = d2

    df = dp_fim_acougue.get_date()
    if df and df < d:
        dp_fim_acougue.set_date(d + timedelta(days=6), call_on_change=False)

    df2 = dp_fim_acougue.get_date()
    if df2 and df2.weekday() != 6:
        dp_fim_acougue.set_date(prev_sunday(df2), call_on_change=False)

def on_change_data_final_acougue(d: date):
    if d.weekday() != 6:
        d2 = prev_sunday(d)
        dp_fim_acougue.set_date(d2, call_on_change=False)
        d = d2

    di = dp_ini_acougue.get_date()
    if di and d < di:
        dp_ini_acougue.set_date(d - timedelta(days=6), call_on_change=False)

    di2 = dp_ini_acougue.get_date()
    if di2 and di2.weekday() != 0:
        dp_ini_acougue.set_date(next_monday(di2), call_on_change=False)


# ===================== EXECUÇÃO: FRENTE DE LOJA =====================
def iniciar_frente_thread():
    threading.Thread(target=iniciar_frente, daemon=True).start()

def iniciar_frente():
    global parar_frente
    parar_frente = False

    data_inicial = dp_ini_frente.get()
    data_final = dp_fim_frente.get()
    tempo_de_espera = int(combo_tempo_frente.get())

    lojas_selecionadas = [loja for loja, var in check_vars_frente.items() if var.get()]

    if not data_inicial:
        messagebox.showerror("Erro", "Por favor, preencha a data inicial (Frente de Loja).")
        return
    if not lojas_selecionadas:
        messagebox.showerror("Erro", "Selecione pelo menos uma loja (Frente de Loja).")
        return

    ui_buttons_frente(True)
    ui_status_frente("Iniciando Frente de Loja...")
    ui_progress_frente(0.0)
    ui_log_frente(f"[{datetime.now().strftime('%H:%M:%S')}] Frente: {len(lojas_selecionadas)} loja(s).")

    def on_log(msg: str):
        ui_log_frente(msg)

    def on_progress(done, tot, loja_atual):
        if tot <= 0:
            return
        ui_status_frente(f"Frente: {loja_atual} ({min(done+1, tot)}/{tot})")
        ui_progress_frente(done / tot)

    def should_stop():
        return parar_frente

    try:
        estimativas_frente_loja.executar(
            data_inicial=data_inicial,
            data_final=data_final,
            tempo_de_espera=tempo_de_espera,
            lojas=lojas_selecionadas,
            on_log=on_log,
            on_progress=on_progress,
            should_stop=should_stop
        )
    except Exception as e:
        ui_log_frente(f"❌ Erro geral (Frente): {e}")

    ui_progress_frente(1.0)
    ui_status_frente("Frente finalizado.")
    ui_log_frente("🏁 Frente finalizado.")
    ui_buttons_frente(False)


# ===================== EXECUÇÃO: AÇOUGUE =====================
def iniciar_acougue_thread():
    threading.Thread(target=iniciar_acougue, daemon=True).start()

def iniciar_acougue():
    global parar_acougue
    parar_acougue = False

    data_inicial = dp_ini_acougue.get()
    data_final = dp_fim_acougue.get()
    tempo_de_espera = int(combo_tempo_acougue.get())

    lojas_selecionadas = [loja for loja, var in check_vars_acougue.items() if var.get()]

    if not data_inicial:
        messagebox.showerror("Erro", "Por favor, preencha a data inicial (Açougue).")
        return
    if not lojas_selecionadas:
        messagebox.showerror("Erro", "Selecione pelo menos uma loja (Açougue).")
        return

    ui_buttons_acougue(True)
    ui_status_acougue("Iniciando Açougue...")
    ui_progress_acougue(0.0)
    ui_log_acougue(f"[{datetime.now().strftime('%H:%M:%S')}] Açougue: {len(lojas_selecionadas)} loja(s).")

    def on_log(msg: str):
        ui_log_acougue(msg)

    def on_progress(done, tot, loja_atual):
        if tot <= 0:
            return
        ui_status_acougue(f"Açougue: {loja_atual} ({min(done+1, tot)}/{tot})")
        ui_progress_acougue(done / tot)

    def should_stop():
        return parar_acougue

    try:
        estimativas_acougue.executar(
            data_inicial=data_inicial,
            data_final=data_final,
            tempo_de_espera=tempo_de_espera,
            lojas=lojas_selecionadas,
            on_log=on_log,
            on_progress=on_progress,
            should_stop=should_stop
        )
    except Exception as e:
        ui_log_acougue(f"❌ Erro geral (Açougue): {e}")

    ui_progress_acougue(1.0)
    ui_status_acougue("Açougue finalizado.")
    ui_log_acougue("🏁 Açougue finalizado.")
    ui_buttons_acougue(False)


# ===================== UI PRINCIPAL (ABAS) =====================
janela = ctk.CTk()
janela.title("Relatórios - Impress.AI 3.2")
janela.geometry("1050x880")
janela.resizable(True, True)

ctk.CTkLabel(janela, text="Impress.AI 3.2 by Anthony Matheus", font=("Arial", 18, "bold")).pack(pady=(10, 6))

tabs = ctk.CTkTabview(janela)
tabs.pack(fill="both", expand=True, padx=12, pady=12)

tab_frente = tabs.add("Frente de Loja")
tab_acougue = tabs.add("Açougue")
tab_prevencao = tabs.add("Prevenção de Perdas")
tab_lojas = tabs.add("Lojas")


# ===================== ABA: FRENTE DE LOJA =====================
frame_dados_frente = ctk.CTkFrame(tab_frente)
frame_dados_frente.pack(pady=10, padx=10, fill="x")

dp_ini_frente = CTkDatePicker(frame_dados_frente, " Data Inicial (Segundas): ", on_change=on_change_data_inicial_frente)
dp_ini_frente.grid(row=0, column=0, padx=10, pady=6, sticky="w")

dp_fim_frente = CTkDatePicker(frame_dados_frente, " Data Final (Domingos): ", on_change=on_change_data_final_frente)
dp_fim_frente.grid(row=1, column=0, padx=10, pady=6, sticky="w")

ctk.CTkLabel(frame_dados_frente, text="Tempo de Espera (padrão = 8s):").grid(row=2, column=0, padx=10, pady=6, sticky="w")
combo_tempo_frente = ctk.CTkComboBox(frame_dados_frente, values=["6","8","10","15","30","45","60","120","240"], width=90)
combo_tempo_frente.set("8")
combo_tempo_frente.grid(row=2, column=0, padx=230, pady=6, sticky="w")

frame_lojas_frente = ctk.CTkFrame(tab_frente)
frame_lojas_frente.pack(pady=10, padx=10, fill="both", expand=True)

ctk.CTkLabel(frame_lojas_frente, text="Selecione as lojas (Frente de Loja)", font=("Arial", 14, "bold"))\
    .pack(anchor="w", padx=10, pady=(10, 6))

scrollable_lojas_frente = ctk.CTkScrollableFrame(frame_lojas_frente)
scrollable_lojas_frente.pack(fill="both", expand=True, padx=10, pady=(0, 10))

frame_botoes_frente = ctk.CTkFrame(tab_frente)
frame_botoes_frente.pack(pady=6)
ctk.CTkButton(frame_botoes_frente, text="Selecionar Todas", command=selecionar_todas_frente).grid(row=0, column=0, padx=8)
ctk.CTkButton(frame_botoes_frente, text="Desmarcar Todas", command=desmarcar_todas_frente).grid(row=0, column=1, padx=8)

botao_iniciar_frente = ctk.CTkButton(tab_frente, text="Iniciar", command=iniciar_frente_thread, width=240, height=42)
botao_iniciar_frente.pack(pady=(8, 6))

botao_parar_frente = ctk.CTkButton(tab_frente, text="Parar", command=parar_execucao_frente, width=240, height=42,
                                   fg_color="#8B2B2B", hover_color="#6f2222", state="disabled")
botao_parar_frente.pack(pady=(0, 10))

label_status_frente = ctk.CTkLabel(tab_frente, text="Aguardando...", font=("Arial", 13, "bold"))
label_status_frente.pack(pady=(0, 6))

progressbar_frente = ctk.CTkProgressBar(tab_frente, width=560)
progressbar_frente.set(0.0)
progressbar_frente.pack(pady=(0, 10))

frame_log_frente = ctk.CTkFrame(tab_frente)
frame_log_frente.pack(fill="both", padx=10, pady=(0, 10))
ctk.CTkLabel(frame_log_frente, text="Log da execução", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(10, 6))
txt_log_frente = ctk.CTkTextbox(frame_log_frente, height=160)
txt_log_frente.pack(fill="both", expand=True, padx=10, pady=(0, 10))
txt_log_frente.insert("end", "Pronto (Frente de Loja).\n")
txt_log_frente.configure(state="disabled")


# ===================== ABA: AÇOUGUE =====================
frame_dados_acougue = ctk.CTkFrame(tab_acougue)
frame_dados_acougue.pack(pady=10, padx=10, fill="x")

dp_ini_acougue = CTkDatePicker(frame_dados_acougue, "Data Inicial (Segundas):", on_change=on_change_data_inicial_acougue)
dp_ini_acougue.grid(row=0, column=0, padx=10, pady=6, sticky="w")

dp_fim_acougue = CTkDatePicker(frame_dados_acougue, "Data Final (Domingos):", on_change=on_change_data_final_acougue)
dp_fim_acougue.grid(row=1, column=0, padx=10, pady=6, sticky="w")

ctk.CTkLabel(frame_dados_acougue, text="Tempo de Espera (padrão = 8s):").grid(row=2, column=0, padx=10, pady=6, sticky="w")
combo_tempo_acougue = ctk.CTkComboBox(frame_dados_acougue, values=["6","8","10","15","30","45","60","120","240"], width=90)
combo_tempo_acougue.set("8")
combo_tempo_acougue.grid(row=2, column=0, padx=230, pady=6, sticky="w")

frame_lojas_acougue = ctk.CTkFrame(tab_acougue)
frame_lojas_acougue.pack(pady=10, padx=10, fill="both", expand=True)

ctk.CTkLabel(frame_lojas_acougue, text="Selecione as lojas (Açougue)", font=("Arial", 14, "bold"))\
    .pack(anchor="w", padx=10, pady=(10, 6))

scrollable_lojas_acougue = ctk.CTkScrollableFrame(frame_lojas_acougue)
scrollable_lojas_acougue.pack(fill="both", expand=True, padx=10, pady=(0, 10))

frame_botoes_acougue = ctk.CTkFrame(tab_acougue)
frame_botoes_acougue.pack(pady=6)
ctk.CTkButton(frame_botoes_acougue, text="Selecionar Todas", command=selecionar_todas_acougue).grid(row=0, column=0, padx=8)
ctk.CTkButton(frame_botoes_acougue, text="Desmarcar Todas", command=desmarcar_todas_acougue).grid(row=0, column=1, padx=8)

botao_iniciar_acougue = ctk.CTkButton(tab_acougue, text="Iniciar", command=iniciar_acougue_thread, width=240, height=42)
botao_iniciar_acougue.pack(pady=(8, 6))

botao_parar_acougue = ctk.CTkButton(tab_acougue, text="Parar", command=parar_execucao_acougue, width=240, height=42,
                                    fg_color="#8B2B2B", hover_color="#6f2222", state="disabled")
botao_parar_acougue.pack(pady=(0, 10))

label_status_acougue = ctk.CTkLabel(tab_acougue, text="Aguardando...", font=("Arial", 13, "bold"))
label_status_acougue.pack(pady=(0, 6))

progressbar_acougue = ctk.CTkProgressBar(tab_acougue, width=560)
progressbar_acougue.set(0.0)
progressbar_acougue.pack(pady=(0, 10))

frame_log_acougue = ctk.CTkFrame(tab_acougue)
frame_log_acougue.pack(fill="both", padx=10, pady=(0, 10))
ctk.CTkLabel(frame_log_acougue, text="Log da execução", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(10, 6))
txt_log_acougue = ctk.CTkTextbox(frame_log_acougue, height=160)
txt_log_acougue.pack(fill="both", expand=True, padx=10, pady=(0, 10))
txt_log_acougue.insert("end", "Pronto (Açougue).\n")
txt_log_acougue.configure(state="disabled")


# ===================== ABA: LOJAS =====================
frame_gerenciar = ctk.CTkFrame(tab_lojas)
frame_gerenciar.pack(pady=10, padx=10, fill="x")

ctk.CTkLabel(frame_gerenciar, text="Gerenciador de lojas (persistente em lojas.json)",
             font=("Arial", 14, "bold")).grid(row=0, column=0, columnspan=3, padx=10, pady=(10, 6), sticky="w")

ctk.CTkLabel(frame_gerenciar, text="Nova loja:").grid(row=1, column=0, padx=10, pady=8, sticky="e")
entry_nova_loja = ctk.CTkEntry(frame_gerenciar, width=420, placeholder_text="Ex.: RECIFE - BOA VIAGEM")
entry_nova_loja.grid(row=1, column=1, padx=10, pady=8, sticky="w")
ctk.CTkButton(frame_gerenciar, text="Adicionar", command=adicionar_loja, width=120).grid(row=1, column=2, padx=10, pady=8)

ctk.CTkLabel(frame_gerenciar, text="Editar/Remover:").grid(row=2, column=0, padx=10, pady=8, sticky="e")
combo_editar = ctk.CTkComboBox(frame_gerenciar, values=[], width=420)
combo_editar.grid(row=2, column=1, padx=10, pady=8, sticky="w")

entry_editar_loja = ctk.CTkEntry(frame_gerenciar, width=420, placeholder_text="Novo nome da loja")
entry_editar_loja.grid(row=3, column=1, padx=10, pady=8, sticky="w")

ctk.CTkButton(frame_gerenciar, text="Renomear", command=editar_loja, width=120).grid(row=2, column=2, padx=10, pady=8)
ctk.CTkButton(frame_gerenciar, text="Remover", command=remover_loja, width=120,
              fg_color="#8B2B2B", hover_color="#6f2222").grid(row=3, column=2, padx=10, pady=8)

frame_dica = ctk.CTkFrame(tab_lojas)
frame_dica.pack(pady=(0, 10), padx=10, fill="x")
ctk.CTkLabel(frame_dica,
             text="As lojas aqui valem para Frente de Loja e Açougue (execução separada por aba).",
             font=("Arial", 12)).pack(anchor="w", padx=10, pady=10)


# ===================== INIT =====================
rebuild_checkboxes()
janela.after(100, tick_ui)
janela.mainloop()
