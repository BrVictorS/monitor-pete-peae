# -*- coding: utf-8 -*-
"""
Persistência do razão (livro-razão de parcelas PETE/PEAE).

Hoje: SQLite local (`data/pete_peae.db`). O esquema espelha as colunas do
razão atual (Página2 do Monitoramento) e foi pensado para migrar 1:1 para uma
tabela no Supabase (PostgreSQL) depois — por isso toda a UI/lógica fala só com
esta interface, nunca com SQL direto.

Sem dependência de Streamlit.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

_DB_PADRAO = Path(__file__).resolve().parent.parent / "data" / "pete_peae.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lancamentos (
    ob              TEXT PRIMARY KEY,      -- dedup: 1 OB = 1 lançamento
    processo        TEXT,
    status_ob       TEXT,
    data_pagamento  TEXT,                  -- ISO 'YYYY-MM-DD'
    valor           REAL,
    objeto          TEXT,
    credor          TEXT,
    cnpj            TEXT,
    municipio       TEXT,
    programa        TEXT,                  -- PETE | PEAE
    parcela         INTEGER,
    complemento     INTEGER DEFAULT 0,     -- 0/1
    tipo            TEXT,                   -- NORMAL | COMPLEMENTO | EXTRA
    descricao       TEXT,
    revisao         INTEGER DEFAULT 0,     -- 1 = anomalia p/ equipe de pagamento
    motivo_revisao  TEXT,
    criado_em       TEXT
);
CREATE INDEX IF NOT EXISTS ix_lanc_prog_parc ON lancamentos(cnpj, programa, parcela);
CREATE INDEX IF NOT EXISTS ix_lanc_data ON lancamentos(data_pagamento);
"""

# colunas na ordem de inserção
_COLS = [
    "ob", "processo", "status_ob", "data_pagamento", "valor", "objeto",
    "credor", "cnpj", "municipio", "programa", "parcela", "complemento",
    "tipo", "descricao", "revisao", "motivo_revisao", "criado_em",
]


def data_iso(data_str: str) -> str:
    """'dd/mm/aaaa' (ou ISO) -> 'aaaa-mm-dd'. Devolve original se não casar."""
    s = str(data_str).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s


def data_br(data_iso_str: str) -> str:
    """'aaaa-mm-dd' -> 'dd/mm/aaaa'. Devolve original se não casar."""
    try:
        return datetime.strptime(str(data_iso_str).strip(), "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(data_iso_str).strip()


class Razao:
    def __init__(self, caminho_db: Path | str | None = None):
        self.caminho = Path(caminho_db) if caminho_db else _DB_PADRAO
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._conectar()

    def _conectar(self):
        # check_same_thread=False: o Streamlit reusa a conexão (cache_resource)
        # entre threads de rerun; o acesso é sequencial por sessão.
        self.con = sqlite3.connect(str(self.caminho), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(_SCHEMA)
        self.con.commit()

    def fechar(self):
        try:
            self.con.close()
        except Exception:
            pass

    # ── consultas ────────────────────────────────────────────────────────────
    def total(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0]

    def obs_existentes(self) -> set[str]:
        return {r[0] for r in self.con.execute("SELECT ob FROM lancamentos")}

    def chaves_normais(self) -> set[tuple]:
        """(cnpj, programa, parcela) que já têm um lançamento NORMAL no razão."""
        rows = self.con.execute(
            "SELECT cnpj, programa, parcela FROM lancamentos WHERE tipo='NORMAL'"
        )
        return {(r["cnpj"], r["programa"], r["parcela"]) for r in rows}

    def listar(self, programa: str | None = None) -> list[dict]:
        sql = "SELECT * FROM lancamentos"
        args = ()
        if programa:
            sql += " WHERE programa = ?"
            args = (programa,)
        sql += " ORDER BY municipio, programa, parcela"
        return [dict(r) for r in self.con.execute(sql, args)]

    def do_dia(self, data_iso_str: str, programa: str | None = None) -> list[dict]:
        sql = "SELECT * FROM lancamentos WHERE data_pagamento = ?"
        args = [data_iso_str]
        if programa:
            sql += " AND programa = ?"
            args.append(programa)
        sql += " ORDER BY programa, municipio, parcela"
        return [dict(r) for r in self.con.execute(sql, args)]

    def municipios_distintos(self, programa: str | None = None) -> list[str]:
        sql = "SELECT DISTINCT municipio FROM lancamentos WHERE municipio <> ''"
        args = []
        if programa:
            sql += " AND programa = ?"
            args.append(programa)
        sql += " ORDER BY municipio"
        return [r[0] for r in self.con.execute(sql, args)]

    def por_municipio(self, municipio: str, programa: str | None = None) -> list[dict]:
        sql = "SELECT * FROM lancamentos WHERE municipio = ?"
        args = [municipio]
        if programa:
            sql += " AND programa = ?"
            args.append(programa)
        sql += " ORDER BY programa, parcela, tipo"
        return [dict(r) for r in self.con.execute(sql, args)]

    def datas_disponiveis(self) -> list[str]:
        """Datas (ISO) com lançamentos, mais recente primeiro."""
        rows = self.con.execute(
            "SELECT DISTINCT data_pagamento FROM lancamentos "
            "ORDER BY data_pagamento DESC"
        )
        return [r[0] for r in rows if r[0]]

    def obter(self, ob: str) -> dict | None:
        row = self.con.execute(
            "SELECT * FROM lancamentos WHERE ob = ?", (ob,)).fetchone()
        return dict(row) if row else None

    def por_chave(self, cnpj: str, programa: str, parcela: int) -> list[dict]:
        rows = self.con.execute(
            "SELECT * FROM lancamentos WHERE cnpj=? AND programa=? AND parcela=? "
            "ORDER BY ob",
            (cnpj, programa, parcela),
        )
        return [dict(r) for r in rows]

    def anomalias(self) -> list[dict]:
        return [dict(r) for r in self.con.execute(
            "SELECT * FROM lancamentos WHERE revisao = 1 ORDER BY municipio, programa, parcela"
        )]

    def grupos_anomalia(self) -> list[list[dict]]:
        """
        Para cada parcela com anomalia (há um EXTRA), devolve TODAS as OBs daquele
        (município, programa, parcela) juntas — para comparar qual é a correta,
        qual está errada, ou se a OB está duplicada. Grupos ordenados; dentro de
        cada grupo, NORMAL primeiro, depois EXTRA/COMPLEMENTO.
        """
        chaves = self.con.execute(
            "SELECT DISTINCT cnpj, programa, parcela FROM lancamentos WHERE tipo='EXTRA' "
            "ORDER BY programa, parcela"
        ).fetchall()
        ordem_tipo = "CASE tipo WHEN 'NORMAL' THEN 0 WHEN 'EXTRA' THEN 1 ELSE 2 END"
        grupos = []
        for k in chaves:
            rows = self.con.execute(
                f"SELECT * FROM lancamentos WHERE cnpj=? AND programa=? AND parcela=? "
                f"ORDER BY {ordem_tipo}, ob",
                (k["cnpj"], k["programa"], k["parcela"]),
            ).fetchall()
            grupos.append([dict(r) for r in rows])
        # ordena os grupos pelo nome do município
        grupos.sort(key=lambda g: (g[0]["municipio"], g[0]["programa"], g[0]["parcela"]))
        return grupos

    # ── escrita ──────────────────────────────────────────────────────────────
    def upsert(self, lancamentos: list[dict]) -> int:
        """
        Insere/atualiza lançamentos (chave = ob). Espera dicts já com `tipo`
        definido (ver core.razao.definir_tipos). Devolve nº de linhas gravadas.
        """
        agora = datetime.now().isoformat(timespec="seconds")
        placeholders = ",".join("?" * len(_COLS))
        sql = (f"INSERT OR REPLACE INTO lancamentos ({','.join(_COLS)}) "
               f"VALUES ({placeholders})")
        dados = []
        for l in lancamentos:
            dados.append((
                l.get("ob", ""), l.get("processo", ""), l.get("status_ob", ""),
                data_iso(l.get("data_pagamento", "")), l.get("valor"),
                l.get("objeto", ""), l.get("credor", ""), l.get("cnpj", ""),
                l.get("municipio", ""), l.get("programa", ""), l.get("parcela"),
                1 if l.get("complemento") else 0, l.get("tipo", ""),
                l.get("descricao", ""), 1 if l.get("revisao") else 0,
                l.get("motivo_revisao", ""), agora,
            ))
        self.con.executemany(sql, dados)
        self.con.commit()
        return len(dados)

    def atualizar(self, ob: str, campos: dict) -> None:
        """Atualiza campos específicos de um lançamento já existente (chave = ob)."""
        if not campos:
            return
        sets = ", ".join(f"{c} = ?" for c in campos)
        valores = list(campos.values()) + [ob]
        self.con.execute(f"UPDATE lancamentos SET {sets} WHERE ob = ?", valores)
        self.con.commit()

    def apagar_tudo(self) -> None:
        self.con.execute("DELETE FROM lancamentos")
        self.con.commit()
