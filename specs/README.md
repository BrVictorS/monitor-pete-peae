# Specs

Uma pasta por funcionalidade, numerada em sequência:

```
specs/
  001-nome-curto/
    spec.md    ← o quê e por quê    (de .specify/templates/spec-template.md)
    plan.md    ← como               (de .specify/templates/plan-template.md)
    tasks.md   ← passos executáveis (de .specify/templates/tasks-template.md)
```

Fluxo: **spec → plano → tarefas → implementação**. Todo plano passa pela
verificação da constituição (`.specify/memory/constitution.md`).
