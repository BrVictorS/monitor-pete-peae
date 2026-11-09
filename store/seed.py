# -*- coding: utf-8 -*-
"""
Importa o razão inicial (seed) a partir do export "MONITORAMENTO PETE/PEAE 2026
- Página2" (CSV). Reaproveita a mesma classificação do fluxo diário, então o
seed entra exatamente como entrariam os lançamentos novos.

Colunas do CSV (0-based):
  0 PROCESSO | 1 OB | 2 STATUS OB | 3 DATA DE PAGAMENTO | 4 (vazia) |
  5 VALOR | 6 OBJETO | 7 CREDOR | 8 DESCRIÇÃO | 9 CATEGORIA | 10 MUNICÍPIO |
  11 PARCELA | 12 CAT_MUN

Usa apenas OB, PROCESSO, STATUS, DATA, VALOR, OBJETO, CREDOR, DESCRIÇÃO —
a categoria/município/parcela são RE-derivados pela classificação (mais robusto).
"""
import csv

from core.classificacao import classificar
from core.leitura import valor_para_float, SITUACOES_DESCARTAR
from core.municipios import DicionarioMunicipios
from core.razao import definir_tipos
from store.db import Razao


def carregar_registros_seed(caminho_csv: str) -> list[tuple[dict, str]]:
    """Lê o CSV e devolve [(registro, descricao), ...] no formato da classificação."""
    with open(caminho_csv, encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f))

    pares = []
    for r in rows[1:]:
        if len(r) < 9:
            continue
        ob = r[1].strip()
        if not ob:
            continue
        if r[2].strip().upper() in SITUACOES_DESCARTAR:   # descarta OB anulada
            continue
        registro = {
            "credor":   r[7].strip(),
            "situacao": r[2].strip(),
            "ob":       ob,
            "processo": r[0].strip(),
            "data":     r[3].strip(),
            "evento":   "",
            "fonte":    r[6].strip(),
            "valor":    valor_para_float(r[5]),
        }
        pares.append((registro, r[8].strip()))
    return pares


def importar_seed(caminho_csv: str, razao: Razao | None = None,
                  dic: DicionarioMunicipios | None = None,
                  substituir: bool = True) -> dict:
    """
    Importa o seed para o razão. Por padrão (`substituir=True`) zera o razão antes
    — o seed é o ponto de partida do ano. Devolve um resumo.
    """
    razao = razao or Razao()
    dic = dic or DicionarioMunicipios()

    pares = carregar_registros_seed(caminho_csv)
    lancamentos = [classificar(reg, desc, dic) for reg, desc in pares]

    if substituir:
        razao.apagar_tudo()

    lancamentos = definir_tipos(lancamentos, razao.chaves_normais())
    gravados = razao.upsert(lancamentos)

    return {
        "lidos": len(pares),
        "gravados": gravados,
        "total_razao": razao.total(),
        "anomalias": len(razao.anomalias()),
        "em_revisao": sum(1 for l in lancamentos if l.get("revisao")),
    }


if __name__ == "__main__":
    import sys
    # Desativado: roda sobre data/pete_peae.db e apagaria o razão inteiro,
    # inclusive as correções manuais. Só religar para montar um razão do zero.
    sys.exit("Recarga do seed desativada: apagaria o razão atual (data/pete_peae.db).")
    caminho = sys.argv[1] if len(sys.argv) > 1 else \
        r"C:\Users\SEDUC\Downloads\MONITORAMENTO PETE_PEAE 2026 - Página2.csv"
    print(importar_seed(caminho))
