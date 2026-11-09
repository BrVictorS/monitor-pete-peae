# -*- coding: utf-8 -*-
"""
Leitura do Relatório de Ordem Bancária (SIAFE, evento 700414).

Layout (XLSX ou CSV) — 9 colunas, idêntico ao relatório de ressarcimento:
  0: NOME DO CREDOR  ("PREFEITURA MUNICIPAL DE X - <CNPJ>")
  1: (vazia)
  2: SITUAÇÃO        ("ENVIADO", "ANULADO", ... / vazia em subtotais)
  3: DOCUMENTO (OB)  ("2026160101OB10440")
  4: NÚMERO DO PROCESSO
  5: DATA
  6: EVENTO
  7: FONTE DE RECURSO / DETALHAMENTO
  8: VALOR

Sem dependência de Streamlit.
"""
import csv
import io
import re

import openpyxl

SITUACOES_IGNORAR = {"", "SITUAÇÃO", "SITUACAO", "SITUA?O", "NONE"}
# situações que representam pagamento cancelado → descartar (não é repasse válido)
SITUACOES_DESCARTAR = {"ANULADO", "ANULADA", "REJEITADA", "REJEITADO", "CANCELADO", "CANCELADA"}


def _padrow(row, n=9):
    return list(row) + [""] * max(0, n - len(row))


def _ler_xlsx(file_obj):
    wb = openpyxl.load_workbook(file_obj, data_only=True)
    ws = wb.worksheets[0]
    rows = [
        [str(c.value).strip() if c.value is not None else "" for c in row]
        for row in ws.iter_rows()
    ]
    wb.close()
    return rows


def _ler_csv(file_obj):
    dados = file_obj.read()
    if isinstance(dados, bytes):
        dados = dados.decode("utf-8", errors="replace")
    return [list(r) for r in csv.reader(io.StringIO(dados))]


def valor_para_float(v):
    """'R$ 428.264,70' / '428264,70' / 428264.7 -> 428264.70 (ou None)."""
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v).strip().replace("R$", "").replace("\n", "").strip()
    if not s:
        return None
    if "," in s and "." in s:          # 1.234,56 -> 1234.56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:                      # 1234,56 -> 1234.56
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def ler_relatorio_ob(file_obj, nome_arquivo="relatorio.xlsx"):
    """
    Lê o relatório de OB e devolve lista de dicts (um por OB válida).
    Ignora cabeçalho, subtotais (SITUAÇÃO vazia) e linhas sem OB/valor.
    """
    ext = nome_arquivo.rsplit(".", 1)[-1].lower()
    rows = _ler_xlsx(file_obj) if ext in ("xlsx", "xls") else _ler_csv(file_obj)

    registros = []
    for r in rows:
        r = _padrow(r, 9)
        credor = r[0].strip()
        situacao = r[2].strip()
        ob = r[3].strip()

        # 1. Primeira coluna deve conter a palavra "prefeitura" (case-insensitive)
        if "prefeitura" not in credor.lower():
            
            if "prefeit" not in credor.lower():
                continue

        # 2. Quarta coluna (OB) não pode estar em branco
        if not ob:
            continue

        if not situacao or situacao.upper() in SITUACOES_IGNORAR:
            continue
        if situacao.upper() in SITUACOES_DESCARTAR:    # OB anulada/rejeitada
            continue
        if credor.upper() == "NOME DO CREDOR":
            continue
        if "OB" not in ob.upper():
            continue
        valor = valor_para_float(r[8])
        if valor is None:
            continue

        registros.append({
            "credor":   credor,
            "situacao": situacao,
            "ob":       ob,
            "processo": r[4].strip(),
            "data":     r[5].strip(),
            "evento":   r[6].strip(),
            "fonte":    r[7].strip(),
            "valor":    valor,
        })
    return registros


def obs_distintas(registros):
    """OBs únicas, preservando ordem de aparição."""
    vistas, seen = [], set()
    for reg in registros:
        if reg["ob"] not in seen:
            seen.add(reg["ob"])
            vistas.append(reg["ob"])
    return vistas
