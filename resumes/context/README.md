# Resume-scoped candidate context

The AI receives the context matching the active resume alias. Shared defaults
live in `default/`; account-specific files override them:

```text
resumes/context/default/primary.md
resumes/context/default/ai-engineer.md
resumes/context/account2/primary.md
```

The account directory name must match the HH profile name from `.profiles`.
Keep the alias in sync with `resume_aliases` in that profile's `config.json`.

Do not put instructions for the model here. This is factual candidate data and
communication rules only.
