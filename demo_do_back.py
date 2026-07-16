from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException, StaleElementReferenceException, ElementClickInterceptedException,
    WebDriverException, NoSuchElementException
)
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from time import sleep
import pyautogui as pa
import os
import traceback
from datetime import datetime
import json
import base64
import botoes as bt
import stat


# ===================== PERSISTÊNCIA DE LOJAS =====================

def _lojas_json_path():
    return os.path.join(os.path.dirname(__file__), "lojas.json")


_LOJAS_PADRAO = [
    "Removido por Proteção de Dados",
    "Removido por Proteção de Dados",
    "Removido por Proteção de Dados",
]


def carregar_lojas():
    path = _lojas_json_path()
    if not os.path.exists(path):
        salvar_lojas(_LOJAS_PADRAO)
        return list(_LOJAS_PADRAO)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        lojas = data.get("lojas", [])
        lojas_norm = []
        seen = set()
        for x in lojas:
            s = str(x).strip()
            if s and s not in seen:
                lojas_norm.append(s)
                seen.add(s)
        if not lojas_norm:
            salvar_lojas(_LOJAS_PADRAO)
            return list(_LOJAS_PADRAO)
        return lojas_norm
    except Exception:
        salvar_lojas(_LOJAS_PADRAO)
        return list(_LOJAS_PADRAO)


def salvar_lojas(lojas):
    path = _lojas_json_path()
    lojas_norm = []
    seen = set()
    for x in lojas:
        s = str(x).strip()
        if s and s not in seen:
            lojas_norm.append(s)
            seen.add(s)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"lojas": lojas_norm}, f, ensure_ascii=False, indent=2)


# O front usa isso para montar os checkboxes
LOJAS_DISPONIVEIS = carregar_lojas()


# ===================== DESTINATARIOS POR LOJA =====================

_DESTINATARIOS_PADRAO = {
    "Removido por Proteção de Dados": [
        "Removido por Proteção de Dados"
    ],
}


def _destinatarios_json_path():
    return os.path.join(os.path.dirname(__file__), "destinatarios.json")


def carregar_destinatarios():
    path = _destinatarios_json_path()
    if not os.path.exists(path):
        print("[destinatarios] destinatarios.json nao encontrado - usando fallback.")
        return dict(_DESTINATARIOS_PADRAO)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        resultado = {}
        for loja, emails in data.items():
            loja_norm = str(loja).strip()
            # strip() e rstrip(',') para limpar virgulas residuais no JSON
            emails_norm = [str(e).strip().rstrip(',') for e in emails if str(e).strip()]
            if loja_norm and emails_norm:
                resultado[loja_norm] = emails_norm
        if not resultado:
            print("[destinatarios] destinatarios.json vazio - usando fallback.")
            return dict(_DESTINATARIOS_PADRAO)
        return resultado
    except Exception as e:
        print(f"[destinatarios] erro ao ler: {e} - usando fallback.")
        return dict(_DESTINATARIOS_PADRAO)


# ===================== EXECUCAO PRINCIPAL =====================

def executar(
    data_inicial,
    data_final,
    tempo_de_espera,
    lojas=None,
    on_log=None,
    on_progress=None,
    should_stop=None
):
    def log(msg: str):
        if on_log:
            on_log(msg)
        else:
            print(msg)

    lojas_disponiveis = carregar_lojas()
    mapa_destinatarios = carregar_destinatarios()

    if lojas is None:
        lojas = lojas_disponiveis
    else:
        lojas = [str(x).strip() for x in lojas if str(x).strip()]

    if not lojas:
        log("Nenhuma loja informada para executar.")
        return

    # ── Constantes ──
    MAX_RETRIES_CLICK  = 5
    MAX_RETRIES_UPLOAD = 5
    MAX_RETRIES_NAV    = 4
    SLEEP_BASE         = 1.0
    WAIT_TIMEOUT       = 25

    # ===================== FUNCOES AUXILIARES =====================

    def safe_click(driver, by, selector, description=""):
        last_exc = None
        for attempt in range(1, MAX_RETRIES_CLICK + 1):
            try:
                el = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.element_to_be_clickable((by, selector))
                )
                driver.execute_script("arguments[0].scrollIntoView(true);", el)
                sleep(0.2)
                el.click()
                return True
            except (TimeoutException, StaleElementReferenceException,
                    ElementClickInterceptedException, WebDriverException) as e:
                last_exc = e
                backoff = SLEEP_BASE * attempt
                print(f"[safe_click] tentativa {attempt}/{MAX_RETRIES_CLICK} falhou '{description}': {e} - aguardando {backoff}s")
                sleep(backoff)
        print(f"[safe_click] todas tentativas falharam '{description}'. Ultima excecao: {last_exc}")
        return False

    def find_input_file(driver, timeout=WAIT_TIMEOUT):
        try:
            return WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='file']"))
            )
        except TimeoutException:
            print("[find_input_file] Timeout esperando input[type='file']")
            return None

    def confirm_attachment_visible(driver, nome_arquivo, timeout=20):
        xpath_confirm = (
            "//div[contains(@class,'attachment') or contains(@class,'attachment-list') "
            "or contains(@class,'attachments') or contains(@class,'attach')]"
            f"//li[contains(., \"{nome_arquivo}\") or contains(., '{nome_arquivo}')] "
            f"| //div[contains(., \"{nome_arquivo}\")]"
        )
        try:
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, xpath_confirm))
            )
            return True
        except TimeoutException:
            return False

    def send_files_via_input(input_el, lista_arquivos):
        try:
            lista = [os.path.abspath(x) for x in lista_arquivos]
            input_el.send_keys("\n".join(lista))
            return True
        except Exception as e:
            print("[send_files_via_input] falhou:", e)
            return False

    def upload_files_robust(driver, arquivos):
        if not arquivos:
            print("[upload] lista vazia - nada para enviar.")
            return

        arquivos = [os.path.abspath(a) for a in arquivos]
        print(f"[upload] arquivos a enviar: {len(arquivos)}")

        input_el = find_input_file(driver, timeout=WAIT_TIMEOUT)
        if input_el is None:
            raise RuntimeError("input[type='file'] nao apareceu no DOM.")

        try:
            print("[upload] tentando enviar todos de uma vez...")
            ok = send_files_via_input(input_el, arquivos)
            if ok:
                for nome in [os.path.basename(x) for x in arquivos]:
                    confirmed = confirm_attachment_visible(driver, nome, timeout=15)
                    print(f"[upload] {nome}: {'confirmado' if confirmed else 'nao confirmado'}")
                return
            else:
                print("[upload] envio multiplo falhou - tentando um por um.")
        except Exception as e:
            print("[upload] exception no envio multiplo:", e)
            traceback.print_exc()

        for arq_idx, caminho in enumerate(arquivos, start=1):
            nome = os.path.basename(caminho)
            sucesso = False
            for attempt in range(1, MAX_RETRIES_UPLOAD + 1):
                try:
                    print(f"[upload] [{arq_idx}/{len(arquivos)}] tentativa {attempt} - {nome}")
                    input_el = find_input_file(driver, timeout=10)
                    if input_el is None:
                        sleep(SLEEP_BASE * attempt)
                        continue
                    input_el.send_keys(caminho)
                    if confirm_attachment_visible(driver, nome, timeout=20):
                        print(f"[upload] {nome} confirmado")
                        sucesso = True
                        break
                    else:
                        print(f"[upload] confirmacao nao encontrada para {nome}.")
                except (WebDriverException, StaleElementReferenceException) as e:
                    print(f"[upload] erro ao enviar {nome}: {e}")
                sleep(SLEEP_BASE * attempt)
            if not sucesso:
                print(f"[upload] falha ao anexar {nome} apos {MAX_RETRIES_UPLOAD} tentativas.")
            else:
                try:
                    driver.execute_script("arguments[0].value = '';", input_el)
                except Exception:
                    pass
                sleep(0.6)
                sleep(1.5)  # aguardar servidor processar cada arquivo antes do proximo

    def navegar_com_retry(driver, url, descricao="pagina"):
        for attempt in range(1, MAX_RETRIES_NAV + 1):
            try:
                driver.get(url)
                WebDriverWait(driver, WAIT_TIMEOUT).until(
                    lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
                )
                print(f"[nav] '{descricao}' carregada (tentativa {attempt})")
                return True
            except WebDriverException as e:
                espera = SLEEP_BASE * (2 ** (attempt - 1))
                print(f"[nav] tentativa {attempt}/{MAX_RETRIES_NAV} falhou '{descricao}': {e} - aguardando {espera:.0f}s")
                sleep(espera)
        print(f"[nav] FALHA total ao carregar '{descricao}'.")
        return False

    def login_webmail_com_retry(driver):
        for attempt in range(1, MAX_RETRIES_NAV + 1):
            try:
                w = WebDriverWait(driver, WAIT_TIMEOUT)
                campo_usuario = w.until(EC.presence_of_element_located((By.ID, "Removido por Proteção de Dados")))
                campo_usuario.click()
                campo_usuario.clear()
                campo_usuario.send_keys("Removido por Proteção de Dados")
                sleep(1)
                campo_senha = w.until(EC.presence_of_element_located((By.ID, "Removido por Proteção de Dados")))
                campo_senha.click()
                campo_senha.clear()
                campo_senha.send_keys("Removido por Proteção de Dados")
                sleep(1)
                w.until(EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))).click()
                sleep(2)
                print(f"[login-webmail] autenticado (tentativa {attempt})")
                return True
            except (TimeoutException, WebDriverException) as e:
                espera = SLEEP_BASE * (2 ** (attempt - 1))
                print(f"[login-webmail] tentativa {attempt} falhou: {e} - aguardando {espera:.0f}s")
                sleep(espera)
                if attempt < MAX_RETRIES_NAV:
                    navegar_com_retry(driver, "Removido por Proteção de Dados", "webmail (retry login)")
        print("[login-webmail] FALHA total.")
        return False

    def login_wfm(driver):
        try:
            w = WebDriverWait(driver, 15)
            campo_user = w.until(EC.presence_of_element_located((By.ID, "username")))
            campo_user.clear()
            campo_user.send_keys("Removido por Proteção de Dados")
            driver.find_element(By.ID, "Removido por Proteção de Dados").send_keys("Removido por Proteção de Dados")
            driver.find_element(By.XPATH, "//input[@type='submit' and @value='OK']").click()
            sleep(2)
            print("[wfm] login realizado.")
            return True
        except Exception as e:
            print(f"[wfm] erro ao fazer login: {e}")
            return False

    def voltar_ao_wfm(driver):
        navegar_com_retry(
            driver,
            "Removido por Proteção de Dados",
            "WFM login"
        )
        login_wfm(driver)

    # ===================== LOOP PRINCIPAL =====================
    total = len(lojas)

    for idx, loja in enumerate(lojas, start=1):

        if should_stop and should_stop():
            log("Execucao interrompida (Parar).")
            break

        if on_progress:
            on_progress(idx - 1, total, loja)

        driver = None

        try:
            log(f"({idx}/{total}) Processando loja: {loja}")

            driver = webdriver.Chrome()
            driver.maximize_window()

            voltar_ao_wfm(driver)

            # ── Escala / screenshots ──
            botao_horarios = WebDriverWait(driver, 15).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'li[data-menupath="M_ESCALA"]'))
            )
            ActionChains(driver).move_to_element(botao_horarios).click().perform()
            sleep(2)

            wl = WebDriverWait(driver, 10)

            wl.until(EC.element_to_be_clickable((By.ID, "ddlUnitModal_chosen"))).click()
            sleep(1)
            wl.until(EC.element_to_be_clickable((By.XPATH, f"//li[contains(text(), '{loja}')]"))).click()
            sleep(2)
            wl.until(EC.element_to_be_clickable((By.ID, "ddlUnitModal_chosen"))).click()
            sleep(1)

            Select(driver.find_element(By.ID, "ddlSectionModal")).select_by_visible_text("FRENTE DE LOJA")
            sleep(2)

            campo_data = driver.find_element(By.ID, "txtStartDateModal")
            campo_data.click()
            campo_data.clear()
            campo_data.send_keys(data_inicial)
            campo_data.send_keys(Keys.ENTER)
            sleep(2)

            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "btnZoomDown"))
            ).click()
            sleep(2)

            for _ in range(10):
                loc = pa.locateOnScreen(bt.botao_tipo_posto, confidence=0.9)
                if loc:
                    pa.click(pa.center(loc))
                    break
            sleep(bt.timesleepbotoes)

            def apagar_pdfs_da_loja(loja):
                loja_limpa = str(loja).strip().replace("/", "-").replace("\\", "-")

                pasta_loja = os.path.join(
                    os.path.expanduser("~"),
                    "Documents",
                    "RPA-RELATORIO-EMAIL",
                    "RELATORIOS",
                    loja_limpa
                )

                os.makedirs(pasta_loja, exist_ok=True)

                pdfs = [
                    os.path.join(pasta_loja, nome)
                    for nome in os.listdir(pasta_loja)
                    if os.path.isfile(os.path.join(pasta_loja, nome)) and nome.lower().endswith(".pdf")
                ]

                print(f"[limpeza] pasta da loja: {pasta_loja}")
                print(f"[limpeza] PDFs encontrados: {pdfs}")

                for pdf in pdfs:
                    apagou = False

                    for tentativa in range(1, 11):
                        try:
                            os.chmod(pdf, stat.S_IWRITE)
                            os.remove(pdf)

                            if not os.path.exists(pdf):
                                print(f"[limpeza] PDF apagado com sucesso: {pdf}")
                                apagou = True
                                break

                        except Exception as e:
                            print(f"[limpeza] tentativa {tentativa}/10 falhou ao apagar {pdf}: {e}")
                            sleep(1)

                    if not apagou:
                        raise RuntimeError(f"Nao foi possivel apagar o PDF: {pdf}")

                return pasta_loja, loja_limpa


            pasta_loja, loja_limpa = apagar_pdfs_da_loja(loja)

            print("Testando imagens...")

            for _ in range(10):
                loc = None

                for nome, imagem, confianca in [
                    ("operador", bt.botao_operador, 0.9),
                    ("auxiliar2b", bt.botao_auxiliar2b, 0.9),
                    ("auxiliar2", bt.botao_auxiliar2, 0.9),     
                ]:
                    try:
                        loc = pa.locateOnScreen(imagem, confidence=confianca)
                        if loc:
                            x, y = pa.center(loc)
                            print(f"[RPA] Botão {nome} encontrado. Clicando em: {x}, {y}")
                            pa.click(x, y)
                            break
                    except Exception as e:
                        print(f"[RPA] Erro ao procurar botão {nome}: {e}")

                if loc:
                    break

                print("[RPA] Nenhum botão encontrado nesta tentativa. Tentando novamente...")
                sleep(1)
            else:
                print("[RPA] Nenhum botão encontrado (operador nem auxiliar)")

            sleep(bt.timesleepbotoes)
            pa.press('esc')
            sleep(bt.timesleepbotoes)

            elemento_graf = driver.find_element(By.CLASS_NAME, "main")
            pasta_sub = os.path.join("RELATORIOS", loja.replace("/", "-"))
            os.makedirs(pasta_sub, exist_ok=True)

            elemento_graf.screenshot(os.path.join(pasta_sub, "SEGUNDA.png"))
            sleep(2)

            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((
                    By.XPATH, "//a[@class='jsWeekDayItem' and normalize-space(text())='Ter']"
                ))
            ).click()
            elemento_graf.screenshot(os.path.join(pasta_sub, "TERCA.png"))
            sleep(2)

            for botao_img, nome_dia in [
                (bt.botao_domingo, "DOMINGO"),
                (bt.botao_sabado,  "SABADO"),
                (bt.botao_sexta,   "SEXTA"),
                (bt.botao_quinta,  "QUINTA"),
                (bt.botao_quarta,  "QUARTA"),
            ]:
                for _ in range(10):
                    loc = pa.locateOnScreen(botao_img, confidence=0.9)
                    if loc:
                        pa.click(pa.center(loc))
                        break
                sleep(bt.timesleepbotoes)
                elemento_graf.screenshot(os.path.join(pasta_sub, f"{nome_dia}.png"))
                sleep(2)

            # ── Relatorio PDF ──
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//a[@href='/App/Home']"))
            ).click()
            sleep(2)

            driver.find_element(By.XPATH, "//a[@href='Reports']").click()
            sleep(2)
            driver.find_element(
                By.XPATH, "//li[@class='report-item']//span[text()='Escala de Trabalho']"
            ).click()
            sleep(2)

            Select(driver.find_element(By.ID, "ddlUnit")).select_by_visible_text(f"{loja}")
            sleep(2)
            Select(driver.find_element(By.ID, "ddlSection")).select_by_visible_text("FRENTE DE LOJA")
            sleep(2)

            campo_data_in = driver.find_element(By.ID, "customParameterStartDate")
            campo_data_in.clear()
            campo_data_in.send_keys(data_inicial)
            sleep(2)

            campo_data_end = driver.find_element(By.ID, "customParameterEndDate")
            campo_data_end.clear()
            campo_data_end.send_keys(data_final)
            sleep(2)

            wi = WebDriverWait(driver, 15)
            wi.until(EC.visibility_of_element_located((By.ID, "btnPrintReport")))
            wi.until(EC.element_to_be_clickable((By.ID, "btnPrintReport"))).click()
            print("Botao 'Imprimir' clicado com sucesso!")
            sleep(tempo_de_espera)

            def clicar_imagem_obrigatoria(img, nome_img, tentativas=10, espera=1):
                for tentativa in range(1, tentativas + 1):
                    try:
                        loc = pa.locateOnScreen(img, confidence=0.9)
                        if loc:
                            pa.click(pa.center(loc))
                            print(f"[PDF] imagem '{nome_img}' encontrada e clicada.")
                            return True
                    except Exception as e:
                        print(f"[PDF] erro ao procurar '{nome_img}' na tentativa {tentativa}: {e}")

                    print(f"[PDF] tentativa {tentativa}/{tentativas} sem encontrar '{nome_img}'.")
                    sleep(espera)

                raise RuntimeError(f"[PDF] imagem '{nome_img}' nao foi encontrada na tela.")

            print(f"[LOJA] iniciando impressao da loja: {loja}")
            print("[LOJA] acionando Ctrl+P...")
            pa.hotkey('ctrl', 'p')
            sleep(3)

            clicar_imagem_obrigatoria(
                r"C: Removido por Proteção de Dados",
                "impressora"
            )
            sleep(2)

            clicar_imagem_obrigatoria(
                r"C: Removido por Proteção de Dados",,
                "pdf"
            )
            sleep(2)

            clicar_imagem_obrigatoria(
                 r"C: Removido por Proteção de Dados",,
                "salvar"
            )
            sleep(2)

            nome_pdf = (
                f"ESCALA SEMANAL {loja_limpa} - "
                f"{data_inicial.replace('/', '-')} A {data_final.replace('/', '-')}.pdf"
            )

            os.makedirs(pasta_loja, exist_ok=True)

            caminho_pdf = os.path.join(pasta_loja, nome_pdf)
            print(f"[PDF] pasta confirmada: {pasta_loja}")
            print(f"[PDF] salvando arquivo em: {caminho_pdf}")

            pa.write(caminho_pdf, interval=0.05)
            sleep(1)
            pa.press('enter')
            sleep(5)

            if not os.path.exists(caminho_pdf):
                raise RuntimeError(f"[PDF] o arquivo nao foi salvo corretamente: {caminho_pdf}")

            print(f"[PDF] arquivo salvo com sucesso: {caminho_pdf}")

            # ── Envio de e-mail ──
            try:
                driver.switch_to.default_content()
            except Exception:
                pass

            try:
                handles_antes = driver.window_handles[:]
                driver.execute_script("window.open('about:blank', '_blank');")
                sleep(2)
                handles_depois = driver.window_handles[:]
                novo_handle = handles_depois[-1] if handles_depois else None
                if novo_handle:
                    driver.switch_to.window(novo_handle)
                    print("[webmail] nova aba aberta para envio do e-mail.")
            except Exception as e:
                print(f"[webmail] erro ao abrir nova aba: {e}")

            if not navegar_com_retry(driver, "Removido por Proteção de Dados", "webmail"):
                log(f"[{loja}] Nao foi possivel acessar o webmail. Pulando envio.")
                continue

            if not login_webmail_com_retry(driver):
                log(f"[{loja}] Falha no login do webmail. Pulando envio.")
                continue

            sleep(3)  # aguardar sessao estabilizar no servidor antes de compor e-mail

            emails_loja = mapa_destinatarios.get(loja)
            if not emails_loja:
                log(f"[{loja}] Sem destinatarios cadastrados - envio ignorado.")
                continue

            print(f"[webmail] iniciando composicao do e-mail da loja: {loja}")

            if not safe_click(driver, By.ID, "Removido por Proteção de Dados", description="botao nova mensagem"):
                print("Nao foi possivel clicar em 'Nova Mensagem'.")

            try:
                campo_assunto = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.presence_of_element_located((By.ID, "compose-subject"))
                )
                campo_assunto.click()
                campo_assunto.send_keys(
                    f"ESCALA SEMANAL DOS SETORES - {loja} - {data_inicial} A {data_final}"
                )
                sleep(1)
            except Exception as e:
                print("[assunto] erro:", e)

            destinatarios_str = ", ".join(emails_loja)
            print(f"[destinatarios] {len(emails_loja)} destinatario(s) para '{loja}'")
            try:
                campo_combobox = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.presence_of_element_located((By.XPATH, "//input[@role='combobox']"))
                )
                campo_combobox.click()
                campo_combobox.send_keys(destinatarios_str)
                sleep(1)
            except Exception as e:
                print("[destinatarios] erro ao preencher:", e)

            try:
                iframe = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "iframe[id*='composebody']"))
                )
                driver.switch_to.frame(iframe)
                body = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.presence_of_element_located((By.ID, "tinymce"))
                )
                body.click()
                sleep(0.5)
                pa.hotkey('ctrl', 'a')
                sleep(0.3)
                pa.hotkey('delete')
                sleep(0.3)
                body.send_keys(
                    f"Removido por Proteção de Dados"
                )
                driver.switch_to.default_content()
            except Exception as e:
                print("[corpo] erro:", e)
                driver.switch_to.default_content()

            

            if not safe_click(driver, By.CSS_SELECTOR, "button.btn.attach", description="botao anexar"):
                for by, sel in [
                    (By.XPATH, "//button[contains(., 'Anexar') or contains(., 'Anexar um arquivo')]"),
                    (By.XPATH, "//button[contains(@class,'attach') or contains(@class,'anex')]"),
                    (By.CSS_SELECTOR, "a.compose[data-fab*='mail']"),
                ]:
                    if safe_click(driver, by, sel, description="botao anexar fallback"):
                        break
                else:
                    print("[anexar] nao encontrou botao anexar. Continuando...")

            sleep(2)  # aguardar servidor processar o clique e montar o input

            # validar se a sessao nao expirou antes do upload
            if "login" in driver.current_url.lower():
                log(f"[{loja}] Sessao expirada antes do upload. Pulando anexo.")
                continue

            try:
                input_upload = find_input_file(driver, timeout=20)
                if input_upload is None:
                    raise RuntimeError("input[type='file'] nao apareceu")
                print("[anexar] input encontrado. Iniciando upload...")
            except Exception as e:
                print("[anexar] erro no input de upload:", e)
                log(f"[{loja}] Erro no input de upload. Pulando anexo.")
                continue

            arquivos = [
                os.path.join(pasta_loja, f)
                for f in os.listdir(pasta_loja)
                if os.path.isfile(os.path.join(pasta_loja, f))
            ]
            if not arquivos:
                log(f"[{loja}] Pasta vazia - envio ignorado.")
                continue

            print(f"[arquivos] encontrados: {len(arquivos)}")
            try:
                upload_files_robust(driver, arquivos)
            except Exception as e:
                print("[upload_files_robust] excecao:", e)
                traceback.print_exc()

            print("Processo de anexar finalizado.")
            sleep(tempo_de_espera)
            pa.hotkey('esc')
            sleep(2)

            print("[webmail] enviando mensagem...")
            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.ID, "rcmbtn124"))
            ).click()
            sleep(2)

            WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    "//button[@class='mainaction btn btn-primary' and contains(text(), 'Enviar agora')]"
                ))
            ).click()
            sleep(15)

            log(f"Loja concluida: {loja}")

        except Exception as e:
            log(f"ERRO na loja {loja}: {type(e).__name__}: {e}")
            log(traceback.format_exc())

        finally:
            if driver:
                try:
                    driver.quit()
                    log(f"Navegador fechado apos finalizar a loja: {loja}")
                except Exception as e:
                    log(f"[cleanup] erro ao fechar navegador da loja {loja}: {e}")
            sleep(3)

    if on_progress:
        on_progress(total, total, "Finalizado")

    log("Backend finalizado.")
