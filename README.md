# Kora

Kora is a local-first AI companion with genuine personality, emotional states,
comprehensive memory, and autonomous agency.

## Status

V2 rearchitecture in progress. Phase 0 (scaffold) complete, Phase 1 (Foundation) starting.

V1 code is archived on branch `v1-archive`.

## Quick Start

1. Install dependencies:
```bash
python3 -m pip install -e ".[dev]"
```

2. Configure environment:
```bash
cp .env.example .env
# Add your MiniMax API key to .env
```

3. Run tests:
```bash
python -m pytest tests/ -v
```

## Documentation

- **PRD (source of truth):** `Documentation/plans/rearchitecture/PRD/`
- **Build roadmap:** `Documentation/plans/rearchitecture/PRD/roadmap.md`
- **Development guide:** `CLAUDE.md`

## Notes

- Do not commit real credentials. Use local env vars and `.env` files.
- All servers bind to `127.0.0.1` only.
