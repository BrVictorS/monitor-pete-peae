# -*- coding: utf-8 -*-
"""
Migra o razão do SQLite antigo (`data/pete_peae.db`) para o PostgreSQL
configurado (ver store.db.dsn_configurado).

Copia as linhas como estão (sem reclassificar, preservando `criado_em` e as
correções manuais), numa transação só, e confere contagem e soma dos valores.
Só roda se a tabela de destino estiver vazia — nunca sobrescreve um razão.

Uso:
    python -m store.migrar_sqlite [caminho.db]
"""
import sqlite3
import sys
from pathlib import Path

from store.db import Razao

_DB_ANTIGO = Path(__file__).resolve().parent.parent / "data" / "pete_peae.db"


def ler_sqlite(caminho: Path) -> list[dict]:
    if not caminho.exists():
        raise FileNotFoundError(f"SQLite não encontrado: {caminho}")
    con = sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)   # só leitura
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM lancamentos ORDER BY ob")]
    finally:
        con.close()


def migrar(caminho: Path = _DB_ANTIGO, razao: Razao | None = None) -> dict:
    razao = razao or Razao()
    linhas = ler_sqlite(caminho)
    gravados = razao.importar_linhas(linhas)

    destino = razao.listar()
    soma_origem = round(sum(l["valor"] or 0 for l in linhas), 2)
    soma_destino = round(sum(l["valor"] or 0 for l in destino), 2)
    obs_ok = {l["ob"] for l in linhas} == {l["ob"] for l in destino}
    return {
        "origem": len(linhas),
        "gravados": gravados,
        "destino": len(destino),
        "soma_origem": soma_origem,
        "soma_destino": soma_destino,
        "conferido": obs_ok and len(linhas) == len(destino) and soma_origem == soma_destino,
    }


if __name__ == "__main__":
    caminho = Path(sys.argv[1]) if len(sys.argv) > 1 else _DB_ANTIGO
    res = migrar(caminho)
    print(res)
    if not res["conferido"]:
        sys.exit("ATENÇÃO: contagem/soma/OBs não conferem entre SQLite e PostgreSQL.")
