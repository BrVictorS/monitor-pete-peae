# -*- coding: utf-8 -*-
"""
Gera o texto de WhatsApp do dia — só as parcelas pagas na data informada,
agrupadas por programa (🚌 PETE / 🍎 PEAE), uma linha por município.

Formato (espelha o do AppScript atual, com pequenos ajustes de legibilidade):

    📌 *RELATÓRIO DE PAGAMENTOS - 11/06/2026*

    🚌 *PETE:*
    ABAETETUBA = parcela 4
    ACARA = parcela 3, 4

    ───────────────────

    🍎 *PEAE:*
    BELEM = parcela 2

Sem dependência de Streamlit.
"""

_EMOJI = {"PETE": "🚌", "PEAE": "🍎"}
_SEP = "───────────────────"


def _rotulo_parcela(lanc: dict) -> str:
    """'4' p/ normal; '4 (compl.)'; '4 (extra)'."""
    p = lanc.get("parcela")
    tipo = lanc.get("tipo", "")
    base = str(p) if p is not None else "?"
    if tipo == "COMPLEMENTO":
        return f"{base} (compl.)"
    if tipo == "EXTRA":
        return f"{base} (extra)"
    return base


def _chave_ordenacao(rot: str):
    """Ordena parcelas numericamente; extras/complementos depois da normal."""
    num = "".join(ch for ch in rot if ch.isdigit())
    prioridade = 0 if "(" not in rot else (1 if "extra" in rot else 2)
    return (int(num) if num else 99, prioridade)


def _bloco(lancs_programa: list[dict], programa: str) -> str:
    if not lancs_programa:
        return ""
    por_mun: dict[str, list[str]] = {}
    for l in lancs_programa:
        por_mun.setdefault(l.get("municipio", "?"), []).append(_rotulo_parcela(l))

    linhas = [f"\n{_EMOJI.get(programa, '')} *{programa}:*"]
    for mun in sorted(por_mun):
        parcelas = sorted(set(por_mun[mun]), key=_chave_ordenacao)
        linhas.append(f"{mun} = parcela {', '.join(parcelas)}")
    return "\n".join(linhas) + "\n"


def gerar_texto(lancamentos_do_dia: list[dict], data_br: str) -> str:
    """
    `lancamentos_do_dia`: lançamentos (dicts do razão) da data escolhida.
    `data_br`: data formatada 'dd/mm/aaaa' para o cabeçalho.
    """
    ativos = [l for l in lancamentos_do_dia if l.get("tipo") != "ANULADA"]
    pete = [l for l in ativos if l.get("programa") == "PETE"]
    peae = [l for l in ativos if l.get("programa") == "PEAE"]

    msg = f"📌 *RELATÓRIO DE PAGAMENTOS - {data_br}*\n"
    msg += _bloco(pete, "PETE")
    if pete and peae:
        msg += f"\n{_SEP}\n"
    msg += _bloco(peae, "PEAE")
    return msg.rstrip() + "\n"
