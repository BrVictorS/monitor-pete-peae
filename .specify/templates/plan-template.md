# Plano: [NOME DA FUNCIONALIDADE]

- **Spec:** ./spec.md

## Abordagem técnica
Resumo da solução.

## Verificação da constituição
- [ ] I. `core/` continua sem Streamlit/banco
- [ ] II. Acesso a dados só via `store/`
- [ ] III. Razão: dedup por OB, anomalias visíveis
- [ ] IV. Nenhuma gravação em dados reais sem autorização (backup previsto)
- [ ] V. Sem dados reais no repositório
- [ ] VI. Bot isolado em `store/fonte_descricao.py`

## Arquivos afetados
| Camada | Arquivo | Mudança |
|---|---|---|
| core  | | |
| store | | |
| app   | | |

## Modelo de dados / migração
Mudanças de esquema no PostgreSQL e como migrar o banco existente.

## Riscos
-
