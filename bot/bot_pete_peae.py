# -*- coding: utf-8 -*-
"""
BOT PETE/PEAE — captura de descrições de OB no SIAFE.

Provisório: enquanto a SEFA não libera acesso direto ao PostgreSQL do SIAFE, a
descrição de cada OB é obtida por Selenium (login automático + navegação no
frameset + consulta OB a OB). Quando o banco for liberado, esta etapa vira uma
query SQL e este arquivo sai — o resto do sistema não muda (ver store.fonte_descricao).

Reaproveita integralmente a navegação validada do bot de ressarcimento de Diárias.

Uso:
    python bot_pete_peae.py <input_obs.json> <status.json>
Variáveis de ambiente:
    SIAFE_USER, SIAFE_PASS  (obrigatórias)
    SIAFE_PROXY             (opcional)
"""
import os
import sys
import json
import time
import tempfile
import traceback
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service


if getattr(sys, "frozen", False):
    PASTA_ATUAL = os.path.dirname(sys.executable)
else:
    PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))

CAMINHO_DRIVER = os.path.join(PASTA_ATUAL, "chromedriver.exe")
LINK_SIAFE = "http://177.74.1.218/SIAFE/faces/fbFramesetTemplate.xhtml"


class Status:
    """Escreve o estado atual no status.json de forma atômica (lido pelo app)."""

    def __init__(self, caminho):
        self.caminho = caminho
        self.dados = {
            "state": "starting",
            "message": "Iniciando...",
            "total": 0, "processed": 0,
            "resultados": [], "logs": [],
        }
        self.log("Bot iniciado.")

    def set(self, **kwargs):
        self.dados.update(kwargs)
        self.salvar()

    def log(self, msg):
        carimbo = datetime.now().strftime("%H:%M:%S")
        self.dados["logs"].append(f"{carimbo}  {msg}")
        if len(self.dados["logs"]) > 300:
            self.dados["logs"] = self.dados["logs"][-300:]
        self.salvar()

    def add_resultado(self, ob, descricao, status):
        self.dados["resultados"].append(
            {"ob": ob, "descricao": descricao, "status": status})
        self.dados["processed"] = len(self.dados["resultados"])
        self.salvar()

    def salvar(self):
        pasta = os.path.dirname(self.caminho) or "."
        fd, tmp = tempfile.mkstemp(dir=pasta, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.dados, f, ensure_ascii=False)
            os.replace(tmp, self.caminho)
        except Exception:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise


# ── helpers de frame (SIAFE usa <frameset>/<frame>, não só <iframe>) ──────────
def _achar_em_qualquer_frame(driver, by, value, timeout=10, max_prof=4):
    fim = time.time() + timeout
    while time.time() < fim:
        driver.switch_to.default_content()
        el = _buscar_recursivo(driver, by, value, max_prof)
        if el:
            return el
        time.sleep(0.4)
    driver.switch_to.default_content()
    return None


def _buscar_recursivo(driver, by, value, prof):
    el = _tenta_achar(driver, by, value)
    if el:
        return el
    if prof <= 0:
        return None
    frames = driver.find_elements(By.CSS_SELECTOR, "frame, iframe")
    for idx in range(len(frames)):
        frames = driver.find_elements(By.CSS_SELECTOR, "frame, iframe")
        if idx >= len(frames):
            break
        try:
            driver.switch_to.frame(frames[idx])
        except Exception:
            continue
        achado = _buscar_recursivo(driver, by, value, prof - 1)
        if achado:
            return achado
        try:
            driver.switch_to.parent_frame()
        except Exception:
            driver.switch_to.default_content()
    return None


def _tenta_achar(driver, by, value):
    try:
        for el in driver.find_elements(by, value):
            if el.is_displayed():
                return el
    except Exception:
        pass
    return None


def _xpath_literal(s):
    if '"' not in s:
        return '"' + s + '"'
    if "'" not in s:
        return "'" + s + "'"
    partes = s.split('"')
    return "concat(" + ", '\"', ".join('"' + p + '"' for p in partes) + ")"


def _clicar_menu_por_texto(driver, texto, timeout=15):
    xpath = ("//div[contains(@class,'itemHeader__name') and "
             "normalize-space(text())=" + _xpath_literal(texto) + "]")
    el = _achar_em_qualquer_frame(driver, By.XPATH, xpath, timeout)
    if not el:
        raise RuntimeError("Item de menu não encontrado: " + texto)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.2)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)
    time.sleep(1.0)


def fazer_login(driver, status, usuario, senha):
    status.set(state="logging_in", message="Fazendo login no SIAFE...")
    campo_user = _achar_em_qualquer_frame(driver, By.ID, "fbUser", timeout=30)
    if not campo_user:
        raise RuntimeError("Campo de usuário (fbUser) não encontrado.")
    campo_user.clear(); campo_user.send_keys(usuario)
    campo_pass = _achar_em_qualquer_frame(driver, By.ID, "fbPass", timeout=10)
    if not campo_pass:
        raise RuntimeError("Campo de senha (fbPass) não encontrado.")
    campo_pass.clear(); campo_pass.send_keys(senha)
    botao = _achar_em_qualquer_frame(driver, By.CSS_SELECTOR, "input.btLogin", timeout=10)
    if not botao:
        raise RuntimeError("Botão de login (btLogin) não encontrado.")
    try:
        botao.click()
    except Exception:
        driver.execute_script("arguments[0].click();", botao)
    status.log("Login enviado. Aguardando o ambiente carregar...")
    time.sleep(4.0)
    status.log("Login concluído.")


def _trocar_para_janela_mais_recente(driver, status):
    try:
        handles = driver.window_handles
        if len(handles) > 1:
            driver.switch_to.window(handles[-1])
            status.log("Trocado para a janela mais recente.")
    except Exception as e:
        status.log("Erro ao trocar janelas: " + str(e))


XPATH_ITENS_MENU = "//div[contains(@class,'itemHeader__name')]"


def _menu_aberto(driver):
    return _achar_em_qualquer_frame(driver, By.XPATH, XPATH_ITENS_MENU, timeout=2) is not None


def _abrir_menu(driver, status):
    for tentativa in range(1, 5):
        if _menu_aberto(driver):
            status.log("Menu lateral aberto.")
            return True
        toggle = _achar_em_qualquer_frame(driver, By.CSS_SELECTOR, "div.action--toggle", timeout=3)
        if not toggle:
            toggle = _achar_em_qualquer_frame(driver, By.XPATH, "//div[starts-with(@title,'Menu')]", timeout=2)
        if toggle:
            try:
                toggle.click()
            except Exception:
                driver.execute_script("arguments[0].click();", toggle)
            status.log(f"Botão de menu clicado (tentativa {tentativa}).")
            time.sleep(1.5)
            continue
        try:
            driver.switch_to.default_content()
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.CONTROL, Keys.SHIFT, "m")
            time.sleep(1.5)
        except Exception:
            time.sleep(1.0)
    return _menu_aberto(driver)


def navegar_ate_consulta_ob(driver, status):
    status.set(state="navigating", message="Navegando até a consulta de OB...")
    _trocar_para_janela_mais_recente(driver, status)
    if not _abrir_menu(driver, status):
        raise RuntimeError("Não foi possível abrir o menu lateral.")
    _clicar_menu_por_texto(driver, "Gestão Financeira")
    _clicar_menu_por_texto(driver, "Pagamentos")
    _clicar_menu_por_texto(driver, "OB - Ordem Bancária")
    status.log("Tela de consulta de OB aberta.")
    time.sleep(2.0)


def capturar_descricao(driver, wait, ob):
    """Pesquisa uma OB e devolve (descricao, status)."""
    try:
        driver.switch_to.default_content()
        try:
            driver.switch_to.frame("MainBody")
        except Exception:
            pass
        try:
            wait.until(EC.element_to_be_clickable((By.ID, "SearchButton"))).click()
            time.sleep(0.5)
        except Exception:
            pass
        try:
            campo_ob = wait.until(EC.visibility_of_element_located(
                (By.ID, "paymentExtractPayment:applicationId")))
            campo_ob.clear(); campo_ob.send_keys(ob)
        except Exception:
            return "", "erro"

        # limpa o campo de descrição ANTES de disparar a busca: sem isso, o
        # campo continua com o valor da OB pesquisada anteriormente até a
        # resposta da busca atual chegar, e como a descrição é um texto
        # genérico (ex. "6ª PARCELA DO PEAE/2026", igual para qualquer
        # município), um AJAX lento faz a leitura pegar o valor "grudado" da
        # OB anterior sem erro nenhum — atribuindo a descrição errada à OB
        # atual. Zerando aqui, só um valor realmente novo pode aparecer.
        try:
            campo_desc = driver.find_element(By.ID, "paymentExtractPayment:description")
            driver.execute_script("arguments[0].value = '';", campo_desc)
        except Exception:
            pass

        try:
            driver.find_element(By.ID, "FindButton").click()
        except Exception:
            return "", "erro"

        try:
            WebDriverWait(driver, 10).until(
                lambda d: (d.find_element(By.ID, "paymentExtractPayment:description")
                           .get_attribute("value") or "").strip() != "")
            el = driver.find_element(By.ID, "paymentExtractPayment:description")
            desc = (el.get_attribute("value") or "").strip()
            return (desc, "ok") if desc else ("", "vazio")
        except Exception:
            return "", "vazio"
    except Exception:
        return "", "erro"


def main():
    input_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PASTA_ATUAL, "obs.json")
    status_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(PASTA_ATUAL, "status.json")
    status = Status(status_path)

    proxy = os.environ.get("SIAFE_PROXY", "").strip()
    if proxy:
        os.environ["http_proxy"] = proxy
        os.environ["https_proxy"] = proxy
        os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

    usuario = os.environ.get("SIAFE_USER", "")
    senha = os.environ.get("SIAFE_PASS", "")
    if not usuario or not senha:
        status.set(state="error", message="Credenciais do SIAFE não informadas.")
        return

    try:
        with open(input_path, encoding="utf-8") as f:
            obs = [str(o).strip() for o in json.load(f) if str(o).strip()]
    except Exception as e:
        status.set(state="error", message="Erro ao ler lista de OBs: " + str(e))
        return
    if not obs:
        status.set(state="error", message="Nenhuma OB para processar.")
        return
    status.set(total=len(obs))

    status.log("Abrindo o navegador Chrome...")
    try:
        if os.path.exists(CAMINHO_DRIVER):
            # driver manual, se o usuário colocou um chromedriver.exe em bot/
            driver = webdriver.Chrome(service=Service(executable_path=CAMINHO_DRIVER))
        else:
            # Selenium Manager baixa/seleciona o chromedriver compatível automaticamente
            status.log("chromedriver.exe não encontrado — usando o Selenium Manager.")
            driver = webdriver.Chrome()
    except Exception as e:
        status.log("Traceback:\n" + traceback.format_exc())
        status.set(state="error", message="Erro ao abrir o Chrome: " + str(e))
        return

    try:
        driver.maximize_window()
        status.log("Acessando o SIAFE...")
        driver.get(LINK_SIAFE)
        fazer_login(driver, status, usuario, senha)
        navegar_ate_consulta_ob(driver, status)

        status.set(state="processing", message="Capturando descrições...")
        wait = WebDriverWait(driver, 10)
        for i, ob in enumerate(obs, 1):
            descricao, st = capturar_descricao(driver, wait, ob)
            icone = {"ok": "OK", "vazio": "VAZIO", "erro": "ERRO"}.get(st, st)
            status.add_resultado(ob, descricao, st)
            status.log(f"[{i}/{len(obs)}] OB {ob} -> {icone}")

        status.set(state="done", message="Concluído.")
        status.log("Processo finalizado com sucesso.")
    except Exception as e:
        status.log("ERRO: " + str(e))
        status.log("Traceback:\n" + traceback.format_exc())
        status.set(state="error", message="Falha na execução: " + str(e))
        try:
            time.sleep(15)
        except Exception:
            pass
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
