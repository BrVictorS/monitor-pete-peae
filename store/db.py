# -*- coding: utf-8 -*-
"""
Persistência do razão (livro-razão de parcelas PETE/PEAE).

PostgreSQL (local ou Supabase). O esquema espelha as colunas do razão atual
(Página2 do Monitoramento); toda a UI/lógica fala só com esta interface,
nunca com SQL direto.

Conexão: variável de ambiente `DATABASE_URL` ou, na falta dela, a primeira
linha de `secrets/database_url.txt`. Ex.:
    postgresql://usuario:senha@localhost:5432/pete_peae

Sem dependência de Streamlit.
"""
import atexit
import os
import re
import threading
from datetime import datetime
from itertools import groupby
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.adapt import Loader
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_DSN_ARQUIVO = Path(__file__).resolve().parent.parent / "secrets" / "database_url.txt"

# Esquema do razão real. Divide o esquema com outras tabelas (ex.:
# conv_programas_repasses), mas o monitor só lê/grava em `projetos.lancamentos`.
SCHEMA_PADRAO = "projetos"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {t} (
    ob              TEXT PRIMARY KEY,      -- dedup: 1 OB = 1 lancamento (so ASCII: bancos SQL_ASCII)
    processo        TEXT,
    status_ob       TEXT,
    data_pagamento  TEXT,                  -- ISO 'YYYY-MM-DD'
    valor           DOUBLE PRECISION,
    objeto          TEXT,
    credor          TEXT,
    cnpj            TEXT,
    municipio       TEXT,
    programa        TEXT,                  -- PETE | PEAE
    parcela         INTEGER,
    complemento     INTEGER DEFAULT 0,     -- 0/1
    tipo            TEXT,                  -- NORMAL | COMPLEMENTO | EXTRA | ANULADA
    descricao       TEXT,
    revisao         INTEGER DEFAULT 0,     -- 1 = anomalia p/ equipe de pagamento
    motivo_revisao  TEXT,
    criado_em       TEXT
)
"""

_INDICES = """
CREATE INDEX IF NOT EXISTS ix_lanc_prog_parc ON {t} (cnpj, programa, parcela);
CREATE INDEX IF NOT EXISTS ix_lanc_data ON {t} (data_pagamento);
"""

# colunas na ordem de inserção
_COLS = [
    "ob", "processo", "status_ob", "data_pagamento", "valor", "objeto",
    "credor", "cnpj", "municipio", "programa", "parcela", "complemento",
    "tipo", "descricao", "revisao", "motivo_revisao", "criado_em",
]

# Um pool por (dsn, schema), compartilhado por todas as instâncias de Razao do
# processo: o Streamlit cria um Razao() a cada rerun, e sem isso cada clique
# abriria uma conexão nova com o servidor.
_POOLS: dict[tuple[str, str], ConnectionPool] = {}
_POOLS_LOCK = threading.Lock()


@atexit.register
def _fechar_pools():
    # fecha antes do fim do interpretador: deixar para o __del__ do pool gera
    # PythonFinalizationError (não consegue mais encerrar as threads de trabalho)
    with _POOLS_LOCK:
        pools = list(_POOLS.values())
        _POOLS.clear()
    for pool in pools:
        pool.close()


class _TextoMisto(Loader):
    """
    Bancos SQL_ASCII: o psycopg devolve as colunas de texto
    como bytes. Decodifica como UTF-8 (o que o psycopg grava) e, se não for
    válido, como Latin-1 (textos antigos de outros sistemas).
    """

    def load(self, data):
        data = bytes(data)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1")


def _configurar(con: psycopg.Connection) -> None:
    if con.info.encoding == "ascii":   # servidor SQL_ASCII
        for tipo in ("text", "varchar", "bpchar", "name"):
            con.adapters.register_loader(tipo, _TextoMisto)


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


def dsn_configurado() -> str:
    """DSN do PostgreSQL: `DATABASE_URL` ou a 1ª linha útil de secrets/database_url.txt."""
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn and _DSN_ARQUIVO.exists():
        for linha in _DSN_ARQUIVO.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#"):
                dsn = linha
                break
    if not dsn:
        raise RuntimeError(
            "PostgreSQL não configurado: defina a variável de ambiente DATABASE_URL "
            f"ou crie {_DSN_ARQUIVO} com a URL de conexão "
            "(ex.: postgresql://usuario:senha@localhost:5432/pete_peae).")
    return dsn


def _pool(dsn: str, schema: str) -> ConnectionPool:
    chave = (dsn, schema)
    with _POOLS_LOCK:
        pool = _POOLS.get(chave)
        if pool is None:
            # cria o esquema numa conexão direta: erro de autenticação/rede aparece
            # aqui com a mensagem do servidor, e não como timeout do pool
            tabela = sql.Identifier(schema, "lancamentos")
            with psycopg.connect(dsn, connect_timeout=10) as con:
                _configurar(con)
                # só cria o esquema se faltar: CREATE SCHEMA IF NOT EXISTS exige
                # permissão de CREATE no banco mesmo quando o esquema já existe
                existe = con.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s",
                                     (schema,)).fetchone()
                if not existe:
                    con.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                con.execute(sql.SQL(_SCHEMA).format(t=tabela))
                # o IF NOT EXISTS não recria uma `lancamentos` alheia já existente:
                # só segue se a tabela tiver exatamente as colunas do razão
                colunas = {r[0] for r in con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = 'lancamentos'", (schema,))}
                if colunas != set(_COLS):
                    raise RuntimeError(
                        f"A tabela {schema}.lancamentos já existe com outra estrutura "
                        f"(colunas: {sorted(colunas)}); o monitor não grava nela.")
                con.execute(sql.SQL(_INDICES).format(t=tabela))
            pool = ConnectionPool(dsn, min_size=1, max_size=5, open=True,
                                  check=ConnectionPool.check_connection,
                                  kwargs={"connect_timeout": 10}, configure=_configurar,
                                  name=f"razao-{schema}")
            _POOLS[chave] = pool
        return pool


class Razao:
    def __init__(self, dsn: str | None = None, schema: str = SCHEMA_PADRAO):
        """
        `dsn`: URL do PostgreSQL (padrão: ver `dsn_configurado`).
        `schema`: esquema onde fica a tabela `lancamentos` — outro esquema serve
        para demos/testes isolados do razão real (ver `descartar`).
        """
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
            raise ValueError(f"Nome de esquema inválido: {schema!r}")
        self.dsn = dsn or dsn_configurado()
        self.schema = schema
        self._tabela = sql.Identifier(schema, "lancamentos")
        self._pool = _pool(self.dsn, schema)

    def fechar(self):
        """Fecha o pool de conexões deste (dsn, esquema)."""
        with _POOLS_LOCK:
            pool = _POOLS.pop((self.dsn, self.schema), None)
        if pool is not None:
            pool.close()

    def descartar(self) -> None:
        """Apaga o esquema inteiro (só para esquemas de demo/teste) e fecha o pool."""
        if self.schema in (SCHEMA_PADRAO, "public"):
            raise ValueError(f"descartar() não se aplica ao esquema {self.schema} (razão real).")
        with self._pool.connection() as con:
            # DROP ... CASCADE levaria junto qualquer outra tabela do esquema:
            # só apaga se o esquema contiver apenas a `lancamentos` do monitor
            outras = con.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relkind NOT IN ('i', 'S') "
                "AND c.relname <> 'lancamentos'", (self.schema,)).fetchone()[0]
            if outras:
                raise ValueError(f"O esquema {self.schema} tem outros objetos; não será apagado.")
            con.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(self.schema)))
        self.fechar()

    # ── auxiliares ───────────────────────────────────────────────────────────
    def _sql(self, texto: str) -> sql.Composed:
        """Monta a query trocando `{t}` pela tabela qualificada pelo esquema."""
        return sql.SQL(texto).format(t=self._tabela)

    def _linhas(self, texto: str, args=()) -> list[dict]:
        with self._pool.connection() as con, con.cursor(row_factory=dict_row) as cur:
            cur.execute(self._sql(texto), args)
            return cur.fetchall()

    def _coluna(self, texto: str, args=()) -> list:
        with self._pool.connection() as con, con.cursor() as cur:
            cur.execute(self._sql(texto), args)
            return [r[0] for r in cur]

    # ── consultas ────────────────────────────────────────────────────────────
    def total(self) -> int:
        return self._coluna("SELECT COUNT(*) FROM {t}")[0]

    def obs_existentes(self) -> set[str]:
        return set(self._coluna("SELECT ob FROM {t}"))

    def chaves_normais(self) -> set[tuple]:
        """(cnpj, programa, parcela) que já têm um lançamento NORMAL no razão."""
        rows = self._linhas(
            "SELECT cnpj, programa, parcela FROM {t} WHERE tipo='NORMAL'")
        return {(r["cnpj"], r["programa"], r["parcela"]) for r in rows}

    def listar(self, programa: str | None = None) -> list[dict]:
        texto = "SELECT * FROM {t}"
        args = ()
        if programa:
            texto += " WHERE programa = %s"
            args = (programa,)
        texto += " ORDER BY municipio, programa, parcela, ob"
        return self._linhas(texto, args)

    def do_dia(self, data_iso_str: str, programa: str | None = None) -> list[dict]:
        texto = "SELECT * FROM {t} WHERE data_pagamento = %s"
        args = [data_iso_str]
        if programa:
            texto += " AND programa = %s"
            args.append(programa)
        texto += " ORDER BY programa, municipio, parcela, ob"
        return self._linhas(texto, args)

    def municipios_distintos(self, programa: str | None = None) -> list[str]:
        texto = "SELECT DISTINCT municipio FROM {t} WHERE municipio <> ''"
        args = []
        if programa:
            texto += " AND programa = %s"
            args.append(programa)
        texto += " ORDER BY municipio"
        return self._coluna(texto, args)

    def por_municipio(self, municipio: str, programa: str | None = None) -> list[dict]:
        texto = "SELECT * FROM {t} WHERE municipio = %s"
        args = [municipio]
        if programa:
            texto += " AND programa = %s"
            args.append(programa)
        texto += " ORDER BY programa, parcela, tipo, ob"
        return self._linhas(texto, args)

    def datas_disponiveis(self) -> list[str]:
        """Datas (ISO) com lançamentos, mais recente primeiro."""
        datas = self._coluna(
            "SELECT DISTINCT data_pagamento FROM {t} ORDER BY data_pagamento DESC")
        return [d for d in datas if d]

    def obter(self, ob: str) -> dict | None:
        rows = self._linhas("SELECT * FROM {t} WHERE ob = %s", (ob,))
        return rows[0] if rows else None

    def por_chave(self, cnpj: str, programa: str, parcela: int) -> list[dict]:
        return self._linhas(
            "SELECT * FROM {t} WHERE cnpj=%s AND programa=%s AND parcela=%s ORDER BY ob",
            (cnpj, programa, parcela),
        )

    def anomalias(self) -> list[dict]:
        return self._linhas(
            "SELECT * FROM {t} WHERE revisao = 1 ORDER BY municipio, programa, parcela, ob")

    def grupos_anomalia(self) -> list[list[dict]]:
        """
        Para cada parcela com anomalia (há um EXTRA), devolve TODAS as OBs daquele
        (município, programa, parcela) juntas — para comparar qual é a correta,
        qual está errada, ou se a OB está duplicada. Grupos ordenados; dentro de
        cada grupo, NORMAL primeiro, depois EXTRA/COMPLEMENTO.
        """
        rows = self._linhas(
            "SELECT * FROM {t} WHERE (cnpj, programa, parcela) IN ("
            "    SELECT cnpj, programa, parcela FROM {t} WHERE tipo='EXTRA') "
            "ORDER BY cnpj, programa, parcela, "
            "    CASE tipo WHEN 'NORMAL' THEN 0 WHEN 'EXTRA' THEN 1 ELSE 2 END, ob")
        grupos = [list(g) for _, g in groupby(
            rows, key=lambda r: (r["cnpj"], r["programa"], r["parcela"]))]
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
        self._inserir(dados, substituir_existentes=True)
        return len(dados)

    def importar_linhas(self, linhas: list[dict]) -> int:
        """
        Copia linhas já no formato da tabela (ex.: vindas do SQLite antigo), sem
        reclassificar nem trocar `criado_em`. Recusa se o razão não estiver vazio.
        Tudo numa transação só. Devolve nº de linhas gravadas.
        """
        dados = [tuple(l.get(c) for c in _COLS) for l in linhas]
        with self._pool.connection() as con:
            if con.execute(self._sql("SELECT COUNT(*) FROM {t}")).fetchone()[0]:
                raise RuntimeError("O razão de destino não está vazio; importação cancelada.")
            self._inserir(dados, substituir_existentes=False, con=con)
        return len(dados)

    def _inserir(self, dados: list[tuple], substituir_existentes: bool, con=None) -> None:
        colunas = sql.SQL(", ").join(map(sql.Identifier, _COLS))
        if substituir_existentes:
            # mesmo efeito do antigo INSERT OR REPLACE: a OB repetida é sobrescrita
            conflito = sql.SQL("DO UPDATE SET {}").format(sql.SQL(", ").join(
                sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c))
                for c in _COLS if c != "ob"))
        else:
            conflito = sql.SQL("DO NOTHING")
        query = sql.SQL("INSERT INTO {t} ({cols}) VALUES ({vals}) "
                        "ON CONFLICT (ob) {conflito}").format(
            t=self._tabela, cols=colunas,
            vals=sql.SQL(", ").join(sql.Placeholder() * len(_COLS)),
            conflito=conflito)
        if con is None:
            with self._pool.connection() as con, con.cursor() as cur:
                cur.executemany(query, dados)
        else:
            with con.cursor() as cur:
                cur.executemany(query, dados)

    def atualizar(self, ob: str, campos: dict) -> None:
        """Atualiza campos específicos de um lançamento já existente (chave = ob)."""
        if not campos:
            return
        invalidas = set(campos) - set(_COLS)
        if invalidas:
            raise ValueError(f"Coluna(s) inexistente(s) no razão: {sorted(invalidas)}")
        sets = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(c)) for c in campos)
        # bool -> 0/1: as flags são INTEGER (no SQLite isso era implícito)
        valores = [int(v) if isinstance(v, bool) else v for v in campos.values()] + [ob]
        with self._pool.connection() as con:
            con.execute(sql.SQL("UPDATE {t} SET {sets} WHERE ob = %s").format(
                t=self._tabela, sets=sets), valores)

    def apagar_tudo(self) -> None:
        with self._pool.connection() as con:
            con.execute(self._sql("DELETE FROM {t}"))
