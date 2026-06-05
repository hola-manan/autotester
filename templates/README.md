# Steering templates

Copy these into each project you want to test, under a `.autotester/` folder:

```
your-project/
└── .autotester/
    ├── intent.md   ← from templates/intent.md  (set once; what the project should do)
    └── focus.md    ← from templates/focus.md   (edit per run; the feature to check)
```

The tester auto-discovers them — you still only pass the project folder path:

```
auto-tester run --path "C:\path\to\your-project"
```

Both files are optional:
- No `intent.md` → intent is inferred from the code/README (less precise).
- No `focus.md` → a broad check instead of a targeted one.

Shortcut to scaffold straight into a project (no manual copy):

```
auto-tester template intent --out "C:\path\to\your-project"
auto-tester template focus  --out "C:\path\to\your-project"
```
