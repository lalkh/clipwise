# Contributing

Thanks for taking the time to contribute! A few notes to make things smooth.

## Dev setup

Either use Docker (simplest) or run locally:

```bash
# Local dev
pip install -r requirements.txt
npm install -g @anthropic-ai/claude-code
claude login
./start.sh
```

## Code style

- Python: black-compatible formatting (no strict enforcement; stay consistent with the surrounding code)
- Keep functions focused; when `auto_editor.py` / `video_analyzer.py` grow, extract helpers rather than nesting deeper
- Don't introduce new abstractions without a concrete caller; three similar lines beat a premature class

## Testing changes

```bash
# Syntax-check
python3 -m py_compile app.py services/*.py models/*.py

# Compose syntax
docker compose config --quiet

# Smoke test end-to-end
./deploy.sh rebuild
curl http://localhost:8000/api/config/status
```

Upload a short video (≤ 30s) to verify the analyze + auto-edit flow before opening a PR.

## Pull requests

- Describe **what changed** and **why** in the PR body
- If you touch the Claude prompt in `.claude/skills/*.md`, include a before/after sample of the output
- If you add a dependency, explain why it can't be done with existing libs

## Reporting issues

Please include:

- OS + Docker version (`docker --version`, `docker compose version`)
- Output of `./deploy.sh status`
- Relevant slice of `./deploy.sh logs`
- Reference video length & size (no need to share the actual video)

## Areas we'd welcome contributions

- Windows-native (non-WSL) path handling edge cases
- Better material previews in the UI (thumbnails + duration bars)
- Tests for `services/auto_editor.py` parsing logic (match extraction, EDIT_CONFIG parsing)
- Internationalization (currently mostly Chinese UI strings)
- More cinematography vocabulary in `.claude/skills/video-analyze/SKILL.md`
