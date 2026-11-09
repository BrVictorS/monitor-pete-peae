# -*- coding: utf-8 -*-
"""
Monitor PETE/PEAE — casca Streamlit (local).

Única peça acoplada ao Streamlit. Toda a regra vive em core/ e store/.
Fluxo: upload do relatório de OB do dia → bot traz descrições → revisão/
normalização → anexa ao razão → relatórios (WhatsApp + 2 PDFs).
"""
import os
import sys
import json
import time
import subprocess
from datetime import datetime
from pathlib import Path

import html as _html

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from core.leitura import ler_relatorio_ob, obs_distintas
from core.classificacao import classificar, eh_pete_peae
from core.razao import (definir_tipos, montar_grade, grade_consolidada,
                        corrigir_classificacao, anular_lancamento)
from core.whatsapp import gerar_texto
from core.pdf import gerar_pdf, brl
from core.municipios import DicionarioMunicipios
from store.db import Razao, data_br, data_iso
from store.seed import importar_seed
from store import gsheets

# ── caminhos ──────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent
DATA_DIR    = BASE_DIR / "data"
BOT_PATH    = BASE_DIR / "bot" / "bot_pete_peae.py"
CHROMEDRIVER = BOT_PATH.parent / "chromedriver.exe"
OBS_PATH    = DATA_DIR / "obs.json"
STATUS_PATH = DATA_DIR / "status.json"
DATA_DIR.mkdir(exist_ok=True)

def _selenium_ok():
    try:
        import selenium  # noqa: F401
        return True
    except Exception:
        return False

# O bot precisa do script + selenium (o chromedriver é resolvido pelo Selenium
# Manager automaticamente; só é obrigatório ter o Chrome instalado na máquina).
BOT_DISPONIVEL = BOT_PATH.exists() and _selenium_ok()

# Recarregar o seed apaga o razão inteiro, inclusive as correções manuais feitas
# depois da carga inicial. Desativado; só religar para montar um razão do zero.
SEED_HABILITADO = False

st.set_page_config(page_title="Monitor PETE/PEAE — SEDUC", layout="centered",
                   initial_sidebar_state="collapsed")

# ── recursos compartilhados ───────────────────────────────────────────────────
# Sem cache: abre a conexão SQLite a cada rerun (instantâneo, local). Evita o
# problema de o @st.cache_resource segurar um objeto antigo após mudanças no código.
razao = Razao()
dic = DicionarioMunicipios()

# ── estado ────────────────────────────────────────────────────────────────────
if "etapa" not in st.session_state:
    st.session_state.etapa = "upload"           # upload|credenciais|aguardando|revisao
if "registros_novos" not in st.session_state:
    st.session_state.registros_novos = []        # registros de OB ainda não no razão
if "obs_novas" not in st.session_state:
    st.session_state.obs_novas = []
if "descricoes" not in st.session_state:
    st.session_state.descricoes = {}             # ob -> descricao capturada
if "msg" not in st.session_state:
    st.session_state.msg = None

# ── tema SEDUC-PA ─────────────────────────────────────────────────────────────
T = {"bg": "#F4F6F8", "card": "#FFFFFF", "text": "#161C24", "text_soft": "#3B4452",
     "text_muted": "#6B7686", "accent": "#0071CE", "accent_hover": "#005AA6",
     "accent_active": "#004B8C", "accent_bg": "#E3F0FB", "accent_text": "#005AA6",
     "danger": "#EB2939", "border": "#DDE2E9", "upload_bg": "#F1F7FD",
     "upload_bdr": "#7FB6E9", "metric_bg": "#FAFBFC", "tab_inactive": "#6B7686"}
SH_SM = "0 1px 3px rgba(22,28,36,.08), 0 1px 2px rgba(22,28,36,.04)"
SH_MD = "0 4px 12px rgba(22,28,36,.08), 0 1px 3px rgba(22,28,36,.05)"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Mulish:wght@400;500;600;700;800&family=Roboto+Mono:wght@400;500;600&display=swap');
html, body, [class*="css"], .stApp {{
    font-family: 'Mulish','Segoe UI',system-ui,sans-serif !important;
    background-color: {T['bg']} !important; color: {T['text']} !important;
    -webkit-font-smoothing: antialiased; }}
#MainMenu, footer, header, .stDeployButton {{ visibility: hidden; display: none; }}
.app-header {{ background:{T['card']}; border-radius:12px 12px 0 0; padding:.9rem 1.4rem;
    box-shadow:{SH_SM}; display:flex; align-items:center; gap:1.1rem;
    border:1px solid {T['border']}; border-bottom:none; }}
.app-header h1 {{ font-size:1.02rem; font-weight:800; margin:0; letter-spacing:-.02em; }}
.app-header p {{ font-size:.74rem; color:{T['text_muted']}; margin:2px 0 0 0; font-weight:500; }}
.app-header-badge {{ margin-left:auto; background:{T['accent_bg']}; color:{T['accent_text']};
    font-size:.62rem; font-weight:800; padding:.3rem .7rem; border-radius:999px;
    letter-spacing:.08em; text-transform:uppercase; }}
.app-stripe {{ height:3px; width:100%; border-radius:0 0 3px 3px; margin-bottom:1.4rem;
    background:linear-gradient(90deg,{T['danger']} 0%,{T['danger']} 22%,
    {T['card']} 22%,{T['card']} 26%,{T['accent']} 26%,{T['accent']} 100%); }}
.stTabs [data-baseweb="tab-list"] {{ background:{T['card']}; border-radius:12px 12px 0 0;
    padding:0 1rem; border:1px solid {T['border']}; gap:0; }}
.stTabs [data-baseweb="tab"] {{ font-size:.82rem; font-weight:500; color:{T['tab_inactive']};
    padding:.8rem 1.2rem; border-bottom:2px solid transparent; margin-bottom:-1px; }}
.stTabs [aria-selected="true"] {{ color:{T['accent']} !important;
    border-bottom:2px solid {T['accent']} !important; font-weight:600; }}
.stTabs [data-baseweb="tab-panel"] {{ background:{T['card']}; border-radius:0 0 12px 12px;
    padding:1.5rem; box-shadow:{SH_SM}; border:1px solid {T['border']}; border-top:none; }}
.base-info {{ background:{T['accent_bg']}; border-left:3px solid {T['accent']};
    border-radius:0 8px 8px 0; padding:.6rem 1rem; font-size:.8rem;
    color:{T['accent_text']}; font-weight:500; margin-bottom:1.4rem; }}
.section-title {{ font-size:.7rem; font-weight:800; color:{T['accent_text']};
    text-transform:uppercase; letter-spacing:.08em; margin-bottom:.45rem; }}
.stButton > button[kind="primary"] {{ background:{T['accent']} !important; color:#fff !important;
    border:none !important; border-radius:8px !important; font-weight:700 !important;
    padding:.65rem 1.5rem !important; box-shadow:{SH_SM}; }}
.stButton > button[kind="primary"]:hover {{ background:{T['accent_hover']} !important; box-shadow:{SH_MD}; }}
[data-testid="stFileUploader"] section {{ border:2px dashed {T['upload_bdr']} !important;
    border-radius:12px !important; background:{T['upload_bg']} !important; padding:1.6rem 1.4rem !important; }}
[data-testid="stFileUploader"] section small,
[data-testid="stFileUploader"] section span,
[data-testid="stFileUploader"] section p,
[data-testid="stFileUploader"] section div {{ color:{T['text_soft']} !important; }}
[data-testid="stFileUploader"] section button {{ background:{T['card']} !important;
    border:1.5px solid {T['accent']} !important; border-radius:8px !important;
    color:{T['accent']} !important; font-weight:600 !important; padding:.4rem 1.2rem !important; }}
[data-testid="stFileUploader"] section button:hover {{ background:{T['accent']} !important; color:#fff !important; }}
[data-testid="stFileUploader"] [data-testid="stFileUploaderDeleteBtn"] button {{ color:{T['danger']} !important; }}
/* alertas legíveis (texto escuro) */
[data-testid="stAlert"], [data-testid="stAlert"] * {{ color:{T['text']} !important; }}
[data-testid="stAlert"] {{ border-radius:10px !important; font-size:.82rem !important; }}
.stCaption, .stCaption p {{ color:{T['text_soft']} !important; }}
/* métricas — cobre testids antigos (metric-container) e novos (stMetric) do 1.58 */
[data-testid="metric-container"], [data-testid="stMetric"] {{ background:{T['metric_bg']} !important;
    border:1px solid {T['border']} !important; border-radius:12px !important; padding:1rem 1.2rem !important; }}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * {{
    color:{T['text']} !important; font-weight:700 !important; }}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] *,
[data-testid="metric-container"] label p {{ color:{T['text_muted']} !important;
    font-weight:700 !important; font-size:.7rem !important; text-transform:uppercase; letter-spacing:.6px; }}
[data-testid="stMetricDelta"], [data-testid="stMetricDelta"] * {{ color:{T['text_muted']} !important; }}
/* botões de download — Azul Pará (secundário = contorno; primário = sólido) */
[data-testid="stDownloadButton"] button {{ background:{T['card']} !important; color:{T['accent']} !important;
    border:1.5px solid {T['accent']} !important; border-radius:8px !important; font-weight:700 !important; }}
[data-testid="stDownloadButton"] button:hover {{ background:{T['accent_bg']} !important; color:{T['accent_hover']} !important; }}
[data-testid="stDownloadButton"] button[kind="primary"] {{ background:{T['accent']} !important;
    color:#fff !important; border:none !important; box-shadow:{SH_SM}; }}
[data-testid="stDownloadButton"] button[kind="primary"]:hover {{ background:{T['accent_hover']} !important; }}
[data-testid="stDownloadButton"] button p, [data-testid="stDownloadButton"] button div {{ color:inherit !important; }}
/* selectbox / dropdown legível (texto escuro sobre claro) */
[data-baseweb="select"] > div {{ background:{T['card']} !important; border-color:{T['border']} !important; }}
[data-baseweb="select"] div {{ color:{T['text']} !important; }}
/* rótulos de widgets (ex.: "Data do pagamento") */
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label,
.stSelectbox label, .stTextInput label, .stTextArea label, .stFileUploader label {{
    color:{T['text_soft']} !important; font-weight:600 !important; font-size:.82rem !important; }}
/* expander — barra clara com texto escuro */
[data-testid="stExpander"] details {{ background:{T['card']} !important;
    border:1px solid {T['border']} !important; border-radius:10px !important; }}
[data-testid="stExpander"] summary {{ background:{T['metric_bg']} !important; border-radius:10px !important; }}
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p,
[data-testid="stExpander"] summary span, [data-testid="stExpander"] summary svg {{
    color:{T['text']} !important; fill:{T['text']} !important; font-weight:600 !important; }}
.bot-console {{ background:radial-gradient(120% 80% at 50% 0%,#0a1410 0%,#06090d 70%);
    border:1px solid #163a25; border-radius:8px; padding:.9rem 1rem 1.2rem; max-height:340px;
    overflow-y:auto; font-family:'Roboto Mono',monospace; font-size:.78rem; line-height:1.6;
    white-space:pre-wrap; word-break:break-word; }}
.stTabs [data-baseweb="tab-panel"] .bot-console, .stTabs [data-baseweb="tab-panel"] .bot-console div {{
    color:#43e07a !important; }}
.stTabs [data-baseweb="tab-panel"] .bot-console .log-ts {{ color:#3fd0e8 !important; }}
.stTabs [data-baseweb="tab-panel"] .bot-console .log-ok {{ color:#5cff9d !important; }}
.stTabs [data-baseweb="tab-panel"] .bot-console .log-erro {{ color:#ff5f6e !important; }}
.stTabs [data-baseweb="tab-panel"] .bot-console .log-vazio {{ color:#ffc14d !important; }}
.cursor {{ animation: termblink 1.1s steps(1) infinite; }}
@keyframes termblink {{ 50% {{ opacity:0; }} }}
</style>""", unsafe_allow_html=True)

# ── helpers ───────────────────────────────────────────────────────────────────
def siafe_alcancavel(timeout=4):
    """True se o SIAFE responde (precisa da OpenVPN conectada)."""
    import socket
    for host in ("177.74.1.218", "www.siafe.pa.gov.br", "siafe.pa.gov.br"):
        try:
            socket.create_connection((host, 80), timeout=timeout).close()
            return True
        except Exception:
            continue
    return False

def ler_status_bot():
    if not STATUS_PATH.exists():
        return None
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None

def resetar_processamento():
    st.session_state.etapa = "upload"
    st.session_state.registros_novos = []
    st.session_state.obs_novas = []
    st.session_state.descricoes = {}
    st.session_state.descricoes_parciais = {}
    for p in (OBS_PATH, STATUS_PATH):
        try:
            p.unlink()
        except Exception:
            pass

def render_whatsapp(texto: str):
    """Caixa com o texto do WhatsApp + botão que copia direto para o clipboard."""
    conteudo = _html.escape(texto)
    componente = f"""
<div style="font-family:'Mulish',sans-serif;">
  <textarea id="zap" readonly style="width:100%; height:230px; box-sizing:border-box;
    background:#FFFFFF; color:#161C24; border:1px solid #DDE2E9; border-radius:10px;
    padding:14px; font-family:'Roboto Mono',monospace; font-size:13px; line-height:1.55;
    resize:none; outline:none;">{conteudo}</textarea>
  <button id="btn" onclick="copiar()" style="margin-top:10px; width:100%; padding:12px;
    background:#075E54; color:#fff; border:none; border-radius:8px; font-weight:700;
    font-size:14px; cursor:pointer; transition:background .2s;">📋 Copiar texto</button>
</div>
<script>
function copiar() {{
  var ta = document.getElementById('zap');
  var btn = document.getElementById('btn');
  ta.select(); ta.setSelectionRange(0, 999999);
  try {{
    navigator.clipboard.writeText(ta.value).then(ok, fail);
  }} catch (e) {{ fail(); }}
  function ok() {{ btn.innerText = '✅ Copiado!'; btn.style.background = '#128C7E';
    setTimeout(function(){{ btn.innerText='📋 Copiar texto'; btn.style.background='#075E54'; }}, 1800); }}
  function fail() {{
    try {{ document.execCommand('copy'); ok(); }}
    catch (e) {{ btn.innerText = 'Selecione e Ctrl+C'; }} }}
}}
</script>"""
    components.html(componente, height=300)


def render_console(logs):
    linhas = []
    for ln in logs:
        esc = _html.escape(str(ln)); up = esc.upper()
        if len(esc) >= 8 and esc[2] == ":" and esc[5] == ":":
            esc = f'<span class="log-ts">{esc[:8]}</span>{esc[8:]}'
        cls = ("log-ok" if "-> OK" in up else "log-erro" if ("ERRO" in up or "TRACEBACK" in up or "FALHA" in up)
               else "log-vazio" if "VAZIO" in up else "")
        linhas.append(f'<div class="{cls}">{esc}</div>')
    if not linhas:
        linhas.append('<div>Aguardando o bot…</div>')
    linhas.append('<div class="cursor">█</div>')
    st.markdown('<div class="bot-console">' + "".join(linhas) + "</div>", unsafe_allow_html=True)

# ── cabeçalho ─────────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="app-header">
  <div><h1>Monitor PETE/PEAE</h1><p>Acompanhamento diário de parcelas · SAPF/SEDUC-PA</p></div>
  <span class="app-header-badge">DPPC</span>
</div><div class="app-stripe"></div>""", unsafe_allow_html=True)

aba_proc, aba_rel, aba_dash, aba_mun, aba_razao, aba_cfg = st.tabs(
    ["Processamento do dia", "Relatórios", "Dashboard", "Por município",
     "Razão", "Configurações"])

# ══════════════════════════════════════════════════════════════════════════════
# ABA 1 — PROCESSAMENTO DO DIA
# ══════════════════════════════════════════════════════════════════════════════
with aba_proc:
    if not BOT_DISPONIVEL:
        st.warning("⚠️ Bot SIAFE indisponível (selenium não instalado ou Chrome ausente). "
                   "Upload e revisão manual funcionam; a captura automática só roda local.")

    etapa = st.session_state.etapa
    nomes = {"upload": "1 · Upload", "credenciais": "2 · SIAFE", "aguardando": "3 · Bot", "revisao": "4 · Revisão"}
    st.markdown(f'<p class="section-title">Processamento do dia — etapa {nomes.get(etapa, etapa)}</p>',
                unsafe_allow_html=True)
    if etapa != "upload":
        if st.button("↺ Recomeçar", key="reset", type="secondary"):
            resetar_processamento(); st.rerun()

    # ETAPA 1 — UPLOAD
    if etapa == "upload":
        st.caption("Envie o Relatório de Ordem Bancária do dia (SIAFE, evento 700414). XLSX ou CSV.")
        arq = st.file_uploader("relatório de OB", type=["xlsx", "xls", "csv"],
                               label_visibility="collapsed", key="up_ob")
        if arq is not None:
            try:
                registros = ler_relatorio_ob(arq, arq.name)
            except Exception as e:
                st.error(f"Erro ao ler o arquivo: {e}"); registros = []
            existentes = razao.obs_existentes()
            novos = [r for r in registros if r["ob"] not in existentes]
            ja = len(registros) - len(novos)
            if not registros:
                st.warning("Nenhuma OB válida encontrada no arquivo.")
            else:
                st.success(f"{len(registros)} OBs no arquivo · **{len(novos)} novas** · "
                           f"{ja} já no razão (ignoradas).")
                if novos:
                    st.dataframe(pd.DataFrame([{
                        "OB": r["ob"], "CREDOR": r["credor"][:45],
                        "DATA": r["data"], "VALOR": brl(r["valor"])} for r in novos]),
                        use_container_width=True, hide_index=True, height=240)
                    if st.button("Avançar →", type="primary", use_container_width=True):
                        st.session_state.registros_novos = novos
                        st.session_state.obs_novas = obs_distintas(novos)
                        st.session_state.etapa = "credenciais"
                        st.rerun()
                else:
                    st.info("Todas as OBs deste arquivo já estão no razão. Nada a processar.")

    # ETAPA 2 — CREDENCIAIS
    elif etapa == "credenciais":
        n = len(st.session_state.obs_novas)
        st.markdown(f'<div class="base-info">{n} OBs novas serão consultadas no SIAFE.</div>',
                    unsafe_allow_html=True)

        # recuperação: se já há captura salva (ex.: depois de um F5/queda), retoma SEM bot
        _salvo = ler_status_bot()
        _cap = [r for r in (_salvo or {}).get("resultados", []) if r.get("descricao")]
        if _cap:
            st.success(f"💾 Encontrei **{len(_cap)} descrições já capturadas** em disco "
                       f"(da última busca). Você pode retomar sem rodar o bot de novo.")
            if st.button("↩ Retomar com as descrições já capturadas (sem bot)",
                         type="primary", use_container_width=True):
                st.session_state.descricoes = {r["ob"]: r.get("descricao", "")
                                               for r in _salvo["resultados"]}
                st.session_state.descricoes_parciais = {}
                st.session_state.etapa = "revisao"
                st.rerun()
            st.markdown("---")

        st.caption("Ou rode o bot novamente. Credenciais do SIAFE — usadas só nesta "
                   "execução, **nunca gravadas em disco**.")

        # checa a VPN/SIAFE só quando o usuário pedir (evita travar a cada rerun)
        if st.button("Testar conexão com o SIAFE (VPN)"):
            st.session_state.siafe_ok = siafe_alcancavel()
        if st.session_state.get("siafe_ok") is True:
            st.success("SIAFE alcançável — VPN OK. Pode iniciar o bot.")
        elif st.session_state.get("siafe_ok") is False:
            st.error("SIAFE inalcançável. **Conecte a OpenVPN** antes de iniciar o bot.")

        u = st.text_input("Usuário SIAFE", key="siafe_user")
        p = st.text_input("Senha SIAFE", type="password", key="siafe_pass")
        if not BOT_DISPONIVEL:
            st.info("Bot indisponível — você pode pular para a revisão e preencher as descrições manualmente.")
            if st.button("Pular para revisão (sem bot) →", use_container_width=True):
                st.session_state.descricoes = {}
                st.session_state.etapa = "revisao"; st.rerun()
        if st.button("Iniciar bot", type="primary", use_container_width=True,
                     disabled=not (u and p and BOT_DISPONIVEL)):
            try:
                OBS_PATH.write_text(json.dumps(st.session_state.obs_novas, ensure_ascii=False), encoding="utf-8")
                if STATUS_PATH.exists():
                    STATUS_PATH.unlink()
                st.session_state.descricoes_parciais = {}   # execução nova, do zero
                # guarda credenciais em chave estável (sobrevive à troca de etapa)
                st.session_state._siafe_user = u
                st.session_state._siafe_pass = p
                env = dict(os.environ); env["SIAFE_USER"] = u; env["SIAFE_PASS"] = p
                subprocess.Popen([sys.executable, str(BOT_PATH), str(OBS_PATH), str(STATUS_PATH)],
                                 env=env, cwd=str(BOT_PATH.parent))
                st.session_state.etapa = "aguardando"; st.rerun()
            except Exception as e:
                st.error(f"Erro ao iniciar o bot: {e}")

    # ETAPA 3 — AGUARDANDO
    elif etapa == "aguardando":
        status = ler_status_bot()
        mapa = {"starting": "Iniciando…", "logging_in": "Login no SIAFE…",
                "navigating": "Navegando até a consulta de OB…", "processing": "Consultando OBs…",
                "done": "Concluído!", "error": "Erro."}
        estado = (status or {}).get("state", "")

        # mescla o que já foi capturado antes (retomadas) com o status atual,
        # para nunca perder progresso se o Chrome fechar no meio.
        atuais = dict(st.session_state.get("descricoes_parciais", {}))
        for it in (status or {}).get("resultados", []):
            if it.get("descricao"):
                atuais[it["ob"]] = it["descricao"]
            else:
                atuais.setdefault(it["ob"], "")
        capturadas = sum(1 for ob in st.session_state.obs_novas if atuais.get(ob))
        faltantes = [ob for ob in st.session_state.obs_novas if not atuais.get(ob)]
        em_proc = estado in ("starting", "logging_in", "navigating", "processing")

        if status:
            st.markdown(f'<div class="base-info">Status: <strong>{mapa.get(estado, estado)}</strong>'
                        f' &nbsp;·&nbsp; {capturadas}/{len(st.session_state.obs_novas)} '
                        f'com descrição</div>', unsafe_allow_html=True)
            tot, proc = status.get("total", 0), status.get("processed", 0)
            if tot:
                st.progress(min(proc / tot, 1.0), text=f"{proc} / {tot} OBs")
            if estado == "error":
                st.error(status.get("message", "Falha no bot."))
        else:
            st.info("Aguardando o bot iniciar… o Chrome abrirá em instantes.")
        st.markdown('<p class="section-title">Console</p>', unsafe_allow_html=True)
        render_console((status or {}).get("logs", []))

        # se o bot parou (concluído/erro/Chrome fechado) e faltam OBs, oferece retomar
        if faltantes and not em_proc:
            st.warning(f"⚠️ {len(faltantes)} OB(s) ficaram sem descrição "
                       f"(de {len(st.session_state.obs_novas)}). As {capturadas} já "
                       f"capturadas estão salvas — você pode **retomar só as faltantes** "
                       f"(não refaz o que já deu certo) ou carregar e preencher à mão.")
            cred_ok = bool(st.session_state.get("_siafe_user") and st.session_state.get("_siafe_pass"))
            if st.button(f"↻ Retomar as {len(faltantes)} faltantes", type="primary",
                         use_container_width=True, disabled=not (cred_ok and BOT_DISPONIVEL)):
                try:
                    st.session_state.descricoes_parciais = atuais   # preserva o capturado
                    OBS_PATH.write_text(json.dumps(faltantes, ensure_ascii=False), encoding="utf-8")
                    if STATUS_PATH.exists():
                        STATUS_PATH.unlink()
                    env = dict(os.environ)
                    env["SIAFE_USER"] = st.session_state.get("_siafe_user", "")
                    env["SIAFE_PASS"] = st.session_state.get("_siafe_pass", "")
                    subprocess.Popen([sys.executable, str(BOT_PATH), str(OBS_PATH), str(STATUS_PATH)],
                                     env=env, cwd=str(BOT_PATH.parent))
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao retomar: {e}")
            if not cred_ok:
                st.caption("Para retomar, volte e informe as credenciais (Recomeçar).")

        c1, c2 = st.columns(2)
        if c1.button("Atualizar", use_container_width=True):
            st.rerun()
        # carregar funciona com PARCIAL: o que faltar vira preenchimento manual na revisão
        rotulo = ("Carregar resultados →" if not faltantes
                  else f"Carregar {capturadas} e preencher {len(faltantes)} à mão →")
        if c2.button(rotulo, type="primary", use_container_width=True,
                     disabled=capturadas == 0):
            st.session_state.descricoes = atuais
            st.session_state.descricoes_parciais = {}
            st.session_state.etapa = "revisao"; st.rerun()

        # auto-atualiza só enquanto o bot está realmente rodando
        if em_proc:
            st.caption("Atualizando automaticamente a cada 2s…")
            time.sleep(2); st.rerun()

    # ETAPA 4 — REVISÃO
    elif etapa == "revisao":
        st.caption("Revise/normalize as descrições para o padrão "
                   "`Nª PARCELA DO PETE/2026` (ou PEAE). A classificação é recalculada ao confirmar.")
        registros = st.session_state.registros_novos
        descricoes = st.session_state.descricoes

        # uma linha por OB nova
        linhas = []
        info_por_ob = {r["ob"]: r for r in registros}
        for ob in st.session_state.obs_novas:
            r = info_por_ob[ob]
            desc = descricoes.get(ob, "")
            prev = classificar(r, desc, dic)
            linhas.append({
                "OB": ob, "MUNICÍPIO": prev["municipio"], "CREDOR": r["credor"][:40],
                "VALOR": brl(r["valor"]), "DESCRIÇÃO": desc,
                "PROG": prev["programa"] or "—",
                "PARC": prev["parcela"] if prev["parcela"] is not None else "—",
            })
        df = pd.DataFrame(linhas)
        st.markdown('<div class="base-info">Edite a coluna <strong>DESCRIÇÃO</strong> quando '
                    'estiver fora do padrão. PROG/PARC são prévias (recalculadas ao confirmar).</div>',
                    unsafe_allow_html=True)
        editado = st.data_editor(df, use_container_width=True, hide_index=True, height=420,
                                 key="editor", column_config={
            "OB": st.column_config.TextColumn("OB", disabled=True),
            "MUNICÍPIO": st.column_config.TextColumn("Município", disabled=True),
            "CREDOR": st.column_config.TextColumn("Credor", disabled=True),
            "VALOR": st.column_config.TextColumn("Valor", disabled=True),
            "DESCRIÇÃO": st.column_config.TextColumn("Descrição", width="large"),
            "PROG": st.column_config.TextColumn("Prog.", disabled=True),
            "PARC": st.column_config.TextColumn("Parc.", disabled=True)})

        # reclassifica com as descrições editadas
        desc_edit = {row["OB"]: str(row["DESCRIÇÃO"]).strip() for _, row in editado.iterrows()}
        lancs = [classificar(info_por_ob[ob], desc_edit.get(ob, ""), dic)
                 for ob in st.session_state.obs_novas]
        lancs = definir_tipos(lancs, razao.chaves_normais())

        # Classificação simples — NÃO bloqueia o avanço:
        #  • válidos   → descrição lida como PETE/PEAE (programa + parcela) → vão ao razão
        #  • descartar → tudo o mais (outro tipo de pagamento) → não vai ao razão
        # Aviso (não-bloqueante): se uma PREFEITURA cair no descarte, pode ser erro de
        # digitação — você confere e ajusta a descrição se quiser, mas a decisão é sua
        # (prefeitura também recebe pagamentos que não são PETE/PEAE).
        def _ok(l):
            return bool(l["programa"]) and l["parcela"] is not None
        def _pref(l):
            return bool(dic.nome_por_cnpj(l["cnpj"]))

        validos = [l for l in lancs if _ok(l)]
        descartar = [l for l in lancs if not _ok(l)]
        pref_descartadas = [l for l in descartar if _pref(l)]
        anomalias = [l for l in validos if l["tipo"] == "EXTRA"]

        st.markdown("---")
        c1, c2, c3 = st.columns(3)
        c1.metric("Serão lançadas", len(validos))
        c2.metric("Descartadas", len(descartar), help="não são PETE/PEAE — não vão ao razão")
        c3.metric("Anomalias", len(anomalias))
        if anomalias:
            st.warning("⚠️ 2ª OB normal na mesma parcela (conferir com a equipe de pagamento): "
                       + ", ".join(f"{a['municipio']} {a['programa']} p{a['parcela']}" for a in anomalias))
        if pref_descartadas:
            itens = "; ".join(f"{l['ob']} ({l['municipio'] or l['credor'][:25]})"
                              for l in pref_descartadas)
            st.warning("ℹ️ Estas OBs de **prefeitura** não foram reconhecidas como PETE/PEAE e "
                       "serão **descartadas**. Se for erro de digitação (ex.: “PEE”/“PETAE”), "
                       "ajuste a descrição acima; senão, pode confirmar normalmente: " + itens)
        if descartar:
            st.caption(f"{len(descartar)} descartada(s) (não vão ao razão): "
                       + ", ".join(l["ob"] for l in descartar))

        if st.button("Confirmar e anexar ao razão", type="primary", use_container_width=True):
            gravados = razao.upsert(validos)
            msg = f"{gravados} lançamento(s) anexado(s) ao razão."
            if descartar:
                msg += f" {len(descartar)} descartada(s) (não é PETE/PEAE)."
            if anomalias:
                msg += f" {len(anomalias)} anomalia(s) marcada(s) para revisão."
            # sincroniza a Página1 do Google se o auto-sync estiver ligado
            if gsheets.get_auto_sync() and gsheets.credenciais_ok():
                try:
                    res = gsheets.atualizar_pagina1(razao.listar())
                    msg += f" Planilha Google atualizada ({res['linhas']})."
                except Exception as e:
                    msg += f" ⚠️ Falha ao atualizar a planilha: {e}"
            st.session_state.msg = ("success", msg)
            resetar_processamento(); st.rerun()

    if st.session_state.msg:
        tipo, txt = st.session_state.msg
        (st.success if tipo == "success" else st.error)(txt)
        st.session_state.msg = None

# ══════════════════════════════════════════════════════════════════════════════
# ABA 2 — RELATÓRIOS
# ══════════════════════════════════════════════════════════════════════════════
with aba_rel:
    if razao.total() == 0:
        st.warning("Razão vazio. Processe um dia na aba Processamento do dia.")
    else:
        st.markdown('<p class="section-title">Texto para WhatsApp (parcelas do dia)</p>',
                    unsafe_allow_html=True)
        datas = razao.datas_disponiveis()
        if datas:
            rotulos = {data_br(d): d for d in datas}
            escolha = st.selectbox("Data do pagamento", list(rotulos.keys()))
            d_iso = rotulos[escolha]
            texto = gerar_texto(razao.do_dia(d_iso), data_br(d_iso))
            render_whatsapp(texto)
            st.caption("Clique em “Copiar texto” e cole no WhatsApp.")

        st.markdown("---")
        st.markdown('<p class="section-title">Dashboards PDF (acumulado do ano)</p>',
                    unsafe_allow_html=True)
        ts = datetime.now().strftime("%d-%m-%Y")
        c1, c2 = st.columns(2)
        for col, prog, emoji in ((c1, "PETE", "🚌"), (c2, "PEAE", "🍎")):
            with col:
                grade = grade_consolidada(razao.listar(prog))
                st.metric(f"{emoji} {prog}", f"{len(grade['linhas'])} municípios",
                          help=f"Total {brl(grade['total_geral'])}")
                if grade["linhas"]:
                    col.download_button(f"Baixar PDF {prog}", data=gerar_pdf(grade, prog),
                        file_name=f"Relatorio {prog} {ts}.pdf", mime="application/pdf",
                        use_container_width=True, type="primary", key=f"pdf_{prog}")

# ══════════════════════════════════════════════════════════════════════════════
# ABA — DASHBOARD (interativo por programa)
# ══════════════════════════════════════════════════════════════════════════════
with aba_dash:
    if razao.total() == 0:
        st.warning("Razão vazio. Importe dados na aba Configurações.")
    else:
        prog = st.radio("Programa", ["PETE", "PEAE"], horizontal=True, key="dash_prog")
        emoji = "🚌" if prog == "PETE" else "🍎"
        grade = grade_consolidada(razao.listar(prog))
        cols_meta = grade["colunas"]            # [(key, rotulo)]
        parcs = [k[1] for (k, _r) in cols_meta if isinstance(k, tuple) and k[0] == "P"]

        k1, k2, k3 = st.columns(3)
        k1.metric(f"{emoji} Total pago — {prog}", brl(grade["total_geral"]))
        k2.metric("Municípios atendidos", len(grade["linhas"]))
        k3.metric("Parcela mais recente", f"{max(parcs)}ª" if parcs else "—")

        # gráfico: total por coluna (parcela / complemento)
        totais = {rot: round(sum(l["valores"].get(key, 0.0) for l in grade["linhas"]), 2)
                  for (key, rot) in cols_meta}
        if totais:
            st.markdown('<p class="section-title">Total por parcela</p>', unsafe_allow_html=True)
            st.bar_chart(pd.DataFrame({"Total (R$)": totais}), color="#0071CE")

        # grade município × parcela
        st.markdown('<p class="section-title">Grade município × parcela</p>', unsafe_allow_html=True)
        linhas_df = []
        for l in grade["linhas"]:
            row = {"MUNICÍPIO": l["municipio"]}
            for (key, rot) in cols_meta:
                v = l["valores"].get(key, 0.0)
                row[rot] = brl(v) if v else "—"
            row["TOTAL"] = brl(l["total"])
            linhas_df.append(row)
        st.dataframe(pd.DataFrame(linhas_df), use_container_width=True,
                     hide_index=True, height=440)

# ══════════════════════════════════════════════════════════════════════════════
# ABA — POR MUNICÍPIO (detalhe filtrado)
# ══════════════════════════════════════════════════════════════════════════════
with aba_mun:
    if razao.total() == 0:
        st.warning("Razão vazio. Importe dados na aba Configurações.")
    else:
        muns = razao.municipios_distintos()
        c1, c2 = st.columns([2, 1])
        mun = c1.selectbox("Município", muns, key="mun_sel")
        prog_op = c2.radio("Programa", ["Ambos", "PETE", "PEAE"], key="mun_prog")
        prog_f = None if prog_op == "Ambos" else prog_op

        rows = razao.por_municipio(mun, prog_f)
        if not rows:
            st.info("Sem lançamentos para este filtro.")
        else:
            # resumo por programa
            for pg in (["PETE", "PEAE"] if prog_f is None else [prog_f]):
                rp = [r for r in rows if r["programa"] == pg]
                if not rp:
                    continue
                total = sum(r["valor"] or 0 for r in rp)
                parcelas = sorted({r["parcela"] for r in rp if r["tipo"] == "NORMAL"})
                em = "🚌" if pg == "PETE" else "🍎"
                st.markdown(
                    f'<div class="base-info">{em} <strong>{pg}</strong> — total '
                    f'{brl(total)} · parcelas pagas: '
                    f'{", ".join(f"{p}ª" for p in parcelas) or "—"}</div>',
                    unsafe_allow_html=True)

            st.markdown('<p class="section-title">Lançamentos</p>', unsafe_allow_html=True)
            df = pd.DataFrame([{
                "DATA": data_br(r["data_pagamento"]), "PROG": r["programa"],
                "PARC": r["parcela"], "TIPO": r["tipo"], "VALOR": brl(r["valor"]),
                "OB": r["ob"], "STATUS": r["status_ob"], "DESCRIÇÃO": r["descricao"],
            } for r in rows])
            st.dataframe(df, use_container_width=True, hide_index=True, height=360)
            st.caption(f"{len(rows)} lançamento(s) · total "
                       f"{brl(sum(r['valor'] or 0 for r in rows))}")

# ══════════════════════════════════════════════════════════════════════════════
# ABA 3 — RAZÃO (visualização)
# ══════════════════════════════════════════════════════════════════════════════
with aba_razao:
    c1, c2 = st.columns(2)
    c1.metric("Lançamentos no razão", f"{razao.total():,}".replace(",", "."))
    c2.metric("Anomalias", len(razao.anomalias()))

    # botão sempre à mão para enviar o razão à planilha de carga (Google · Página1)
    if gsheets.credenciais_ok():
        if st.button("📤 Atualizar planilha de carga (Google · Página1)",
                     type="primary", use_container_width=True, key="carga_razao"):
            with st.spinner("Enviando ao Google Sheets…"):
                try:
                    res = gsheets.atualizar_pagina1(razao.listar())
                    st.success(f"Planilha de carga atualizada: {res['linhas']} lançamentos. "
                               f"[Abrir]({res['url']})")
                except Exception as e:
                    st.error(str(e))
    else:
        st.caption("📤 Planilha de carga (Google): configure as credenciais na aba "
                   "**Configurações** para habilitar o botão de atualização aqui.")

    grupos = razao.grupos_anomalia()
    if grupos:
        with st.expander(f"⚠️ {len(grupos)} parcela(s) com anomalia — compare as OBs"):
            st.caption("Cada bloco mostra TODAS as OBs da mesma parcela, para você decidir "
                       "qual é a correta, qual está errada ou se a OB está duplicada.")
            for gi, g in enumerate(grupos):
                cab = g[0]
                valores = {l["valor"] for l in g}
                obs = [l["ob"] for l in g]
                pista = ("OB DUPLICADA" if len(set(obs)) < len(obs)
                         else "mesmo valor" if len(valores) == 1
                         else "valores diferentes")
                st.markdown(f"**{cab['municipio']} · {cab['programa']} · parcela {cab['parcela']}** "
                            f"— {len(g)} OBs · _{pista}_")
                st.dataframe(pd.DataFrame([{
                    "TIPO": l["tipo"], "OB": l["ob"], "DATA": data_br(l["data_pagamento"]),
                    "VALOR": brl(l["valor"]), "STATUS": l["status_ob"],
                    "PROCESSO": l["processo"], "DESCRIÇÃO": l["descricao"]} for l in g]),
                    use_container_width=True, hide_index=True)

                with st.form(key=f"corrigir_{gi}_{cab['municipio']}_{cab['parcela']}"):
                    st.caption("Corrigir uma OB deste bloco (ex.: descrição capturada com "
                               "o programa/parcela errado) ou marcar como anulada (ex.: "
                               "pagamento cancelado no SIAFE / OB duplicada):")
                    cf1, cf2, cf3, cf4 = st.columns([2, 1, 1, 1])
                    ob_escolhida = cf1.selectbox("OB", obs, key=f"ob_{gi}")
                    linha_atual = next(l for l in g if l["ob"] == ob_escolhida)
                    prog_idx = 0 if linha_atual["programa"] == "PETE" else 1
                    novo_programa = cf2.selectbox("Programa correto", ["PETE", "PEAE"],
                                                  index=prog_idx, key=f"prog_{gi}")
                    nova_parcela = cf3.number_input("Parcela correta", min_value=1, step=1,
                                                    value=int(linha_atual["parcela"]),
                                                    key=f"parc_{gi}")
                    novo_complemento = cf4.checkbox("É complemento?",
                                                    value=bool(linha_atual["complemento"]),
                                                    key=f"compl_{gi}")
                    bt1, bt2 = st.columns(2)
                    salvar = bt1.form_submit_button("💾 Salvar correção",
                                                    use_container_width=True)
                    anular = bt2.form_submit_button("🚫 Marcar OB como anulada",
                                                    use_container_width=True)
                    if salvar:
                        res = corrigir_classificacao(razao, ob_escolhida, novo_programa,
                                                     int(nova_parcela), novo_complemento)
                        st.success(
                            f"OB {ob_escolhida} corrigida para {novo_programa} parcela "
                            f"{nova_parcela}. Grupos revisados: " +
                            ", ".join(f"{k} ({v} OB{'s' if v != 1 else ''})"
                                     for k, v in res["grupos"].items()) +
                            ". Reabra este bloco para ver a lista de anomalias atualizada.")
                    elif anular:
                        res = anular_lancamento(razao, ob_escolhida)
                        st.success(
                            f"OB {ob_escolhida} marcada como ANULADA — sai do PDF e do "
                            f"WhatsApp, mas continua no razão completo para auditoria. "
                            f"{res['restantes']} lançamento(s) ativo(s) restante(s) em "
                            f"{res['chave'][1]} parcela {res['chave'][2]}. Reabra este "
                            f"bloco para ver a lista de anomalias atualizada.")

    st.markdown("---")
    st.markdown('<p class="section-title">Razão completo</p>', unsafe_allow_html=True)
    todos = razao.listar()
    if todos:
        df_all = pd.DataFrame(todos)
        with st.expander(f"Ver {len(todos)} lançamentos"):
            st.dataframe(df_all[["data_pagamento", "municipio", "programa", "parcela",
                                 "tipo", "valor", "ob", "descricao"]],
                         use_container_width=True, hide_index=True, height=300)


# ══════════════════════════════════════════════════════════════════════════════
# ABA 4 — CONFIGURAÇÕES (ações sensíveis, isoladas)
# ══════════════════════════════════════════════════════════════════════════════
with aba_cfg:
    st.caption(f"Dicionário de municípios: {len(dic)} prefeituras (chave = CNPJ).  ·  "
               f"Bot SIAFE: {'disponível' if BOT_DISPONIVEL else 'indisponível (selenium/Chrome ausente)'}.")

    # ── Sincronização com Google Sheets ────────────────────────────────────────
    st.markdown("---")
    st.markdown('<p class="section-title">Planilha de carga (Google Sheets · Página1)</p>',
                unsafe_allow_html=True)
    creds_ok = gsheets.credenciais_ok()
    if not creds_ok:
        st.warning("Credenciais do Google ainda não configuradas. Veja o passo a passo abaixo.")
        with st.expander("Como configurar (uma vez só)"):
            st.markdown(
                "1. No **Google Cloud Console**, crie um projeto e **ative a Google Sheets API** "
                "(e a Google Drive API).\n"
                "2. Crie uma **Conta de Serviço** → **Chaves** → **Adicionar chave** → **JSON** e baixe.\n"
                f"3. Salve o arquivo como:\n`{gsheets.CREDS_PATH}`\n"
                "4. Abra a planilha de destino e **Compartilhe** (permissão **Editor**) com o "
                "**e-mail da conta de serviço** (`client_email` do JSON, algo como "
                "`...@...iam.gserviceaccount.com`).\n"
                "5. Recarregue esta página.")
    else:
        st.caption(f"Conta de serviço: `{gsheets.email_conta_servico()}` — "
                   "a planilha precisa estar compartilhada (Editor) com este e-mail.")

    sid = st.text_input("ID da planilha", value=gsheets.get_sheet_id(),
                        help="O trecho do link entre /d/ e /edit.")
    cc1, cc2 = st.columns([1, 1])
    if cc1.button("Salvar ID da planilha"):
        gsheets.set_sheet_id(sid)
        st.success("ID salvo.")
    if cc2.button("Atualizar planilha de carga com o razão", type="primary",
                  disabled=not creds_ok):
        with st.spinner("Enviando ao Google Sheets…"):
            try:
                res = gsheets.atualizar_pagina1(razao.listar(), sid)
                st.success(f"Página1 atualizada: {res['linhas']} lançamentos.")
                st.markdown(f"[Abrir planilha]({res['url']})")
            except Exception as e:
                st.error(str(e))

    auto = st.checkbox("Atualizar a planilha automaticamente ao confirmar um dia",
                       value=gsheets.get_auto_sync(), disabled=not creds_ok)
    if auto != gsheets.get_auto_sync():
        gsheets.set_auto_sync(auto)

    st.markdown("---")
    st.markdown('<p class="section-title">Recarregar razão inicial (seed)</p>', unsafe_allow_html=True)
    if not SEED_HABILITADO:
        st.info("Função desativada: recarregar o seed apagaria o razão atual, inclusive as "
                "correções manuais. O razão se atualiza ao confirmar cada dia em "
                "**Processamento do dia**.")
    else:
        st.error("⚠️ **Ação destrutiva.** Isto APAGA todo o razão atual (inclusive os dias já "
                 "lançados) e o substitui pelo CSV enviado. Use apenas no primeiro uso ou para "
                 "recarregar o histórico do zero. No dia a dia, o razão se atualiza sozinho ao "
                 "confirmar cada dia em **Processamento do dia** — não é preciso vir aqui.")
        seed = st.file_uploader("Export 'MONITORAMENTO PETE/PEAE - Página2' (CSV)",
                                type=["csv"], key="seed_up")
        confirmar = st.checkbox("Entendo que isto **apaga o razão atual** e quero substituir.",
                                key="seed_confirm")
        if st.button("Importar e SUBSTITUIR o razão", type="primary",
                     disabled=not (seed is not None and confirmar)):
            tmp = DATA_DIR / "_seed_tmp.csv"
            tmp.write_bytes(seed.getvalue())
            try:
                res = importar_seed(str(tmp), razao, dic, substituir=True)
                st.success(f"Razão recarregado: {res['gravados']} lançamentos · "
                           f"{res['anomalias']} anomalia(s).")
                st.session_state.seed_confirm = False
                st.rerun()
            except Exception as e:
                st.error(f"Erro ao importar: {e}")
            finally:
                try:
                    tmp.unlink()
                except Exception:
                    pass
