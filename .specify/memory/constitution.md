# Constituição — Monitor PETE/PEAE

Princípios que toda spec, plano e tarefa deste projeto deve respeitar.
Alterações nesta constituição exigem aprovação explícita do responsável.

## I. Regra de negócio isolada (`core/` é Python puro)
`core/` não importa Streamlit nem acessa banco. É a peça que migra para o SIMF
(Next.js + Supabase); `app.py` é a única casca descartável.

## II. Persistência atrás de interface (`store/`)
Todo acesso a dados passa por `store/`. Trocar SQLite por Supabase não pode
exigir mudança em `core/` nem nas specs de regra de negócio.

## III. O razão é a fonte da verdade
Dedup por OB. Nada é somado ou escondido: casos fora do padrão viram
**anomalia** visível para conferência humana.

## IV. Dados reais nunca são alterados sem autorização explícita
Banco, CSV e planilhas só mudam com autorização expressa. Confirmar um fato
não é autorizar gravação. Toda correção de dados é precedida de backup.

## V. Sem dados reais no repositório
Exemplos, testes e imagens usam apenas dados fictícios (`exemplos/`).
A lista de municípios/CNPJ (pública) é a única exceção.

## VI. O bot SIAFE é provisório
Captura de descrição fica atrás de `store/fonte_descricao.py`, para ser
substituída por query SQL sem impacto no restante.

---
Versão: 1.0.0 · Ratificada: 2026-11-06
