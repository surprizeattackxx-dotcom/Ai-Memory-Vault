# Troubleshooting

Written for humans AND for AI assistants. If you're an AI helping someone with their vault: read this file, then diagnose from their actual files and paths, never from guesses.

## Where is my vault, physically?

Open `VAULT-INDEX.md` at the vault root: its "Vault location" section records the full path. Fresh vaults created by this system live at `~/Documents/<vault name>` on Mac and Windows alike, because Documents is pinned in the Finder and File Explorer sidebars. If the index is missing that section, Obsidian itself knows: its vault switcher lists every vault with its path.

## Obsidian opened to a welcome screen instead of my vault

Obsidian only opens vaults it knows about, and it learns about them from a registry file called `obsidian.json` (macOS: `~/Library/Application Support/obsidian/obsidian.json` · Windows: `%APPDATA%\Obsidian\obsidian.json` · Linux: `~/.config/obsidian/obsidian.json`). A vault created as a plain folder isn't in that registry yet, so a fresh Obsidian shows the picker. Two fixes:

1. **By hand, one time:** on the welcome screen choose "Open folder as vault" and pick your vault folder (fresh installs put it at `~/Documents/<vault name>`). Obsidian remembers it from then on.
2. **Ask your agent:** have it register the vault in `obsidian.json` with `"open": true` (Obsidian must be closed while it edits, because the app rewrites that file on quit). Current versions of the setup wizard do this automatically; hitting this screen usually means the vault was created by an older run.

## My vault is NOT cloud-synced. What are my backup options?

If Documents doesn't sync (the setup check tells you, or ask your agent to check), the vault exists on exactly one disk. Free options, any one of which is enough:

- **Turn on cloud sync for Documents:** iCloud's "Desktop & Documents Folders" on Mac, OneDrive backup on Windows. The vault rides along automatically from then on (see the sync entry below for the two things to know).
- **Whole-machine backup:** Time Machine (Mac) or File History (Windows) to any external drive covers the vault with everything else.
- **A private GitHub repo:** your agent can set this up and push on a schedule. Notes are small; even years of them fit in any free account.

## My Documents folder syncs to iCloud or OneDrive. Is that a problem?

Mostly it's a gift: the cloud is keeping a free, automatic backup of your agent's entire memory. Two things to know:

1. **Run ONE sync service per vault.** If iCloud or OneDrive already syncs the folder, don't also turn on Obsidian's paid Sync add-on for that vault. Two sync engines editing the same files fight, and the fight produces conflicted copies.
2. **Stop the cloud from evicting your notes.** To save disk space, iCloud ("Optimize Mac Storage") and OneDrive ("Files On-Demand") can quietly replace files with internet-only placeholders. Your notes aren't lost, but reads get slow or fail offline. The fix is one click: right-click the vault folder, then "Keep Downloaded" on Mac or "Always keep on this device" on Windows. Done once, it sticks.

## I want the vault OUT of cloud sync entirely

- **Windows (OneDrive):** OneDrive settings, then Sync and backup, then Advanced settings, then "Choose folders": untick the vault's folder. It stays on disk, OneDrive ignores it.
- **Mac (iCloud Desktop & Documents):** iCloud offers no per-folder exclusion inside Documents. Two honest options: rename the vault folder with a `.nosync` suffix (iCloud skips it, but the ugly name shows everywhere, including Obsidian), or move the vault somewhere outside Documents and Desktop (your home folder works) and re-point Obsidian and your agent's boot config at the new path. Moving it is the cleaner of the two; ask your agent to handle the re-pointing.

## The AI says it can't read or find the vault

The vault path lives in two places: the `CLAUDE.md` boot config in your working folder, and VAULT-INDEX's "Vault location" section. If the vault moved (or a cloud service relocated it), those paths went stale. Tell your agent the new path and have it update both. If reads fail only sometimes, see the placeholder eviction fix above.
