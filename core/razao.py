# -*- coding: utf-8 -*-
"""
Regras do razão: definição do tipo de cada lançamento e montagem da grade
(município × parcelas) usada nos PDFs.

Tipo de um lançamento dentro de um (cnpj, programa, parcela):
  • COMPLEMENTO  — descrição traz "(COMPLEMENTO)"; é uma parcela extra (não soma)
  • NORMAL       — primeiro pagamento normal daquela parcela
  • EXTRA        — 2º pagamento NORMAL na mesma parcela → ANOMALIA: marca
                   `revisao` para conferência com a equipe de pagamento.

Sem dependência de Streamlit.
"""

# ordem das "faixas" de coluna na grade, dado um mesmo número de parcela
_ORDEM_TIPO = {"NORMAL": 0, "EXTRA": 1, "COMPLEMENTO": 2, "ANULADA": 3}


def definir_tipos(lancamentos: list[dict],
                  chaves_normais_existentes: set[tuple] | None = None) -> list[dict]:
    """
    Atribui `tipo` a cada lançamento (mutando uma cópia) considerando o que já
    existe no razão (`chaves_normais_existentes` = set de (cnpj,programa,parcela)
    com NORMAL) e os lançamentos anteriores do próprio lote.

    Preserva qualquer `revisao` que a classificação já tenha marcado
    (descrição fora do padrão, município fora do dicionário, etc.).
    """
    normais = set(chaves_normais_existentes or set())
    saida = []
    for orig in lancamentos:
        l = dict(orig)
        chave = (l.get("cnpj", ""), l.get("programa", ""), l.get("parcela"))

        if l.get("parcela") is None or not l.get("programa"):
            # sem parcela/programa resolvidos: não dá p/ tipar; já está em revisão
            l["tipo"] = ""
            l["revisao"] = True
        elif l.get("complemento"):
            l["tipo"] = "COMPLEMENTO"
        elif chave in normais:
            l["tipo"] = "EXTRA"
            l["revisao"] = True
            motivo = l.get("motivo_revisao", "")
            extra = "2ª OB normal na mesma parcela — conferir com a equipe de pagamento"
            l["motivo_revisao"] = (motivo + "; " + extra).lstrip("; ") if motivo else extra
        else:
            l["tipo"] = "NORMAL"
            normais.add(chave)

        saida.append(l)
    return saida


def _retipar_chave(razao, cnpj: str, programa: str, parcela: int) -> int:
    """
    Reprocessa NORMAL/EXTRA/COMPLEMENTO de todos os lançamentos ATIVOS
    (tipo != ANULADA) de um (cnpj, programa, parcela), de forma determinística
    (ordenado por OB). Lançamentos ANULADOS não competem pela vaga de NORMAL,
    mas continuam no razão com seu tipo preservado (auditoria).
    Devolve o nº de lançamentos ativos retipados.
    """
    if not programa or parcela is None:
        return 0
    grupo = razao.por_chave(cnpj, programa, parcela)
    ja_tem_normal = False
    ativos = 0
    for l in grupo:
        if l.get("tipo") == "ANULADA":
            continue
        ativos += 1
        if l.get("complemento"):
            tipo, revisao, motivo = "COMPLEMENTO", False, ""
        elif not ja_tem_normal:
            tipo, revisao, motivo = "NORMAL", False, ""
            ja_tem_normal = True
        else:
            tipo, revisao = "EXTRA", True
            motivo = "2ª OB normal na mesma parcela — conferir com a equipe de pagamento"
        razao.atualizar(l["ob"], {
            "tipo": tipo, "revisao": 1 if revisao else 0, "motivo_revisao": motivo,
        })
    return ativos


def corrigir_classificacao(razao, ob: str, novo_programa: str, nova_parcela: int,
                           novo_complemento: bool) -> dict:
    """
    Corrige manualmente o programa/parcela/complemento de UM lançamento (ex.:
    a descrição foi capturada errada pelo bot — caso clássico de anomalia
    EXTRA que na verdade é de outro programa) e retipa do zero os dois grupos
    afetados (cnpj, programa, parcela): o que a OB deixou e o que passou a
    ocupar. A retipagem é determinística (ordenada por OB), já que uma
    correção manual invalida a ordem histórica de chegada usada por
    `definir_tipos`.
    """
    atual = razao.obter(ob)
    if not atual:
        raise ValueError(f"OB {ob} não encontrada no razão.")

    chave_antiga = (atual["cnpj"], atual["programa"], atual["parcela"])
    chave_nova = (atual["cnpj"], novo_programa, nova_parcela)

    razao.atualizar(ob, {
        "programa": novo_programa,
        "parcela": nova_parcela,
        "complemento": 1 if novo_complemento else 0,
    })

    resumo = {}
    for cnpj, programa, parcela in {chave_antiga, chave_nova}:
        n = _retipar_chave(razao, cnpj, programa, parcela)
        if programa and parcela is not None:
            resumo[f"{programa} parcela {parcela}"] = n

    return {"chave_antiga": chave_antiga, "chave_nova": chave_nova, "grupos": resumo}


def anular_lancamento(razao, ob: str) -> dict:
    """
    Marca uma OB como ANULADA (parcela cancelada — ex.: pagamento anulado
    no SIAFE após entrar no razão, ou OB duplicada que não deve contar).
    Some da grade de pagamentos (PDF/WhatsApp) e libera a vaga NORMAL/EXTRA
    da sua parcela para o(s) lançamento(s) que restarem, mas continua
    visível no razão completo para auditoria.
    """
    atual = razao.obter(ob)
    if not atual:
        raise ValueError(f"OB {ob} não encontrada no razão.")

    chave = (atual["cnpj"], atual["programa"], atual["parcela"])
    razao.atualizar(ob, {"tipo": "ANULADA", "revisao": 0, "motivo_revisao": ""})
    restantes = _retipar_chave(razao, *chave)
    return {"chave": chave, "restantes": restantes}


def rotulo_coluna(parcela: int, tipo: str) -> str:
    """Rótulo de coluna da grade. Ex.: (1,'NORMAL')->'1ª PARC'; (2,'COMPLEMENTO')->'2ª COMPL'."""
    sufixo = {"NORMAL": "PARC", "EXTRA": "EXTRA", "COMPLEMENTO": "COMPL",
              "ANULADA": "ANULADA"}.get(tipo, "PARC")
    return f"{parcela}ª {sufixo}"


def montar_grade(lancamentos: list[dict]) -> dict:
    """
    Monta a grade de um conjunto de lançamentos (já de um único programa).

    Devolve:
      {
        "colunas": [(parcela, tipo, rotulo), ...]  # ordenadas
        "linhas":  [{"municipio": str, "valores": {col_id: valor}, "total": float}, ...]
        "total_geral": float,
      }
    onde col_id = (parcela, tipo). Valores NÃO são somados entre parcelas
    distintas; só se houver, excepcionalmente, mais de uma OB na mesma célula
    (mesmo município+parcela+tipo) elas se somam.
    """
    colunas_set: set[tuple] = set()
    por_mun: dict[str, dict] = {}

    for l in lancamentos:
        parc, tipo, mun = l.get("parcela"), l.get("tipo"), l.get("municipio", "")
        valor = l.get("valor") or 0.0
        if parc is None or not tipo:
            continue
        col_id = (parc, tipo)
        colunas_set.add(col_id)
        cel = por_mun.setdefault(mun, {})
        cel[col_id] = round(cel.get(col_id, 0.0) + float(valor), 2)

    colunas = sorted(colunas_set, key=lambda c: (c[0], _ORDEM_TIPO.get(c[1], 9)))
    colunas_meta = [(p, t, rotulo_coluna(p, t)) for p, t in colunas]

    linhas = []
    for mun in sorted(por_mun):
        valores = por_mun[mun]
        total = round(sum(valores.values()), 2)
        linhas.append({"municipio": mun, "valores": valores, "total": total})

    total_geral = round(sum(lin["total"] for lin in linhas), 2)
    return {"colunas": colunas_meta, "linhas": linhas, "total_geral": total_geral}


def grade_consolidada(lancamentos: list[dict]) -> dict:
    """
    Grade compacta para o PDF (cabe em A4 paisagem mesmo com 10 parcelas):
      colunas = 1ª PARC … Nª PARC  +  uma única COMPL.  (sem colunas por parcela)

    • PARC = valor do lançamento NORMAL daquela parcela.
    • COMPL. = soma de TODOS os complementos do município (uma coluna só).
    • EXTRA (anomalias) NÃO entra no relatório — fica na aba Razão p/ conferência.

    Devolve {colunas:[(key,rotulo)], linhas:[{municipio,valores{key},total}], total_geral}
    onde key é ("P", n) para parcela e "C" para complemento.
    """
    max_parc = 0
    tem_compl = False
    por_mun: dict[str, dict] = {}

    for l in lancamentos:
        parc, tipo, mun = l.get("parcela"), l.get("tipo"), l.get("municipio", "")
        valor = float(l.get("valor") or 0.0)
        if parc is None or not tipo:
            continue
        cel = por_mun.setdefault(mun, {})
        if tipo == "COMPLEMENTO":
            cel["C"] = round(cel.get("C", 0.0) + valor, 2)
            tem_compl = True
        elif tipo == "NORMAL":
            cel[("P", parc)] = round(cel.get(("P", parc), 0.0) + valor, 2)
            max_parc = max(max_parc, parc)
        # EXTRA é ignorado de propósito (anomalia)

    colunas = [(("P", k), f"{k}ª PARC") for k in range(1, max_parc + 1)]
    if tem_compl:
        colunas.append(("C", "COMPL."))

    linhas = []
    for mun in sorted(por_mun):
        valores = por_mun[mun]
        total = round(sum(valores.values()), 2)
        linhas.append({"municipio": mun, "valores": valores, "total": total})

    total_geral = round(sum(lin["total"] for lin in linhas), 2)
    return {"colunas": colunas, "linhas": linhas, "total_geral": total_geral}
