# Project Codex Skills

This folder stores project-managed Codex skills. Codex loads skills from
`%USERPROFILE%\.codex\skills`, so each folder here should be linked into that
global skills directory.

Current linked skills:

- `ui-ux-pro-max` - installed from
  `nextlevelbuilder/ui-ux-pro-max-skill`, source path
  `.claude/skills/ui-ux-pro-max`.

To link or repair all project skills:

```powershell
.\scripts\sync_codex_skills.ps1
```

Restart Codex after adding or updating skills.
