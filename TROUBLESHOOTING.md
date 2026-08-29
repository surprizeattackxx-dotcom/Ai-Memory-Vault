# Troubleshooting

Written for humans AND for AI assistants. If you're an AI helping someone with their vault: read this file, then diagnose from their actual files and paths, never from guesses.

## Where is my vault, physically?

Open `VAULT-INDEX.md` at the vault root: its "Vault location" section records the full path. Fresh vaults created by this system live at `~/<vault name>`, directly in your home folder, on Mac, Windows, and Linux alike: the same place the agent folder lives, and on a Mac the one place that anything your agent does without you at the keyboard can reach (Documents, Desktop, and cloud folders are walled off from background work). A vault from an older install may live in Documents; that is fine, see the entry below. If the index is missing that section, Obsidian itself knows: its vault switcher lists every vault with its path.

## Obsidian opened to a welcome screen instead of my vault

Obsidian only opens vaults it knows about, and it learns about them from a registry file called `obsidian.json` (macOS: `~/Library/Application Support/obsidian/obsidian.json` · Windows: `%APPDATA%\Obsidian\obsidian.json` · Linux: `~/.config/obsidian/obsidian.json`). A vault created as a plain folder isn't in that registry yet, so a fresh Obsidian shows the picker. Two fixes:

1. **By hand, one time:** on the welcome screen choose "Open folder as vault" and pick your vault folder (fresh installs put it at `~/<vault name>` in your home folder; older installs used `~/Documents/<vault name>`). Obsidian remembers it from then on.
2. **Ask your agent:** have it register the vault in `obsidian.json` with `"open": true` (Obsidian must be closed while it edits, because the app rewrites that file on quit). Current versions of the setup wizard do this automatically; hitting this screen usually means the vault was created by an older run.

## How do I back up my vault?

A fresh vault lives in your home folder and is not cloud-synced, by design, so it exists on exactly one disk. Free options, any one of which is enough:

- **Whole-machine backup:** Time Machine (Mac) covers your home folder automatically. File History (Windows) skips a home-folder vault unless you add the folder to its list, one time.
- **A private GitHub repo:** your agent can set this up and push on a schedule. Notes are small; even years of them fit in any free account.

The one thing to avoid is moving the vault into iCloud Drive or OneDrive to get sync: on a Mac that puts it back behind the wall that blocks your agent's background work.

## My vault is in Documents from an older install. Should I move it?

Not today, and not by hand. It works fine for everyday use. The one catch is on a Mac: anything your agent does without you at the keyboard can't reach into Documents, so when you add background automation, the vault needs to be in your home folder. Don't drag it there: too many things point at that folder (Obsidian's registry, your boot config, the index, any connector configs). A dedicated move tool that re-points all of them is the right way, and you run it when you are ready. Until then, nothing about your setup is wrong.

## My vault is in a folder that syncs to iCloud or OneDrive (an older install). Is that a problem?

Mostly it's a gift: the cloud is keeping a free, automatic backup of your agent's entire memory. Two things to know:

1. **Run ONE sync service per vault.** If iCloud or OneDrive already syncs the folder, don't also turn on Obsidian's paid Sync add-on for that vault. Two sync engines editing the same files fight, and the fight produces conflicted copies.
2. **Stop the cloud from evicting your notes.** To save disk space, iCloud ("Optimize Mac Storage") and OneDrive ("Files On-Demand") can quietly replace files with internet-only placeholders. Your notes aren't lost, but reads get slow or fail offline. The fix is one click: right-click the vault folder, then "Keep Downloaded" on Mac or "Always keep on this device" on Windows. Done once, it sticks.

## I want the vault OUT of cloud sync entirely

- **Windows (OneDrive):** OneDrive settings, then Sync and backup, then Advanced settings, then "Choose folders": untick the vault's folder. It stays on disk, OneDrive ignores it.
- **Mac (iCloud Desktop & Documents):** iCloud offers no per-folder exclusion inside Documents. Two honest options: rename the vault folder with a `.nosync` suffix (iCloud skips it, but the ugly name shows everywhere, including Obsidian), or move the vault to your home folder, which is a proper job, not a drag in Finder: too many things point at the folder, and a dedicated move tool that re-points all of them is the way to do it (see the entry above). Until you run that, the `.nosync` rename is the safe option.

## The AI says it can't read or find the vault

The vault path lives in two places: the `CLAUDE.md` boot config in your working folder, and VAULT-INDEX's "Vault location" section. If the vault moved (or a cloud service relocated it), those paths went stale. Tell your agent the new path and have it update both. If reads fail only sometimes, see the placeholder eviction fix above.

## What is `MEMORY_PROTOCOL.md` and do I need it?

It's the full rulebook behind how your AI reads, writes, and resolves conflicts in memory — the eight operations (boot, retrieve, write, resolve a conflict, health-check, and so on), spelled out precisely enough that any capable AI agent can implement them, not only Claude Code. A copy ships inside your vault at `Resources/MEMORY_PROTOCOL.md`. You don't need to read it day to day — `CLAUDE.md` and `VAULT-INDEX.md` already carry the operational summary your AI actually runs on. Open it when you want the exact definition behind a term like `memory_status` or `candidate`, or if you're wiring up a second AI tool and want it to follow the same rules.

## A note has `memory_status`, `confidence`, or `source` in its frontmatter — what do those mean?

These are optional tags a small number of fact-bearing notes carry (a Key People entry, a preference, a project fact) — most notes, like daily logs, indexes, and Jobs, never need them. In short: `memory_status` is where the fact is in its life (`candidate` = an unconfirmed guess, `current` = confirmed and current, `superseded`/`deprecated` = replaced or retired but kept for history). `source` says how the AI came to believe it (`explicit` = you said it, `inferred` = the AI concluded it). `confidence` is how sure the AI is. None of this is required — a note without these fields is perfectly normal, especially anything from before this layer existed. Full definitions are in `Resources/MEMORY_PROTOCOL.md`.

## The AI flagged a conflict and won't just pick an answer

That's working as intended. When two things in your vault genuinely can't both be true and there's no way to tell which is current from what's written, your AI is supposed to say so rather than guess — silently picking one is exactly the kind of quiet data corruption this layer exists to prevent. Answer the question it's asking (which one is actually true now, or are they both true at different times) and it'll resolve the note, linking the old one as superseded rather than deleting it.

## My AI said "INCOMPATIBLE PROTOCOL STATE DETECTED" — what does that mean?

Different from a `PARTIAL UPGRADE DETECTED` message (that one just means an upgrade is mid-way). This one means two of the files your AI reads to know the rules — usually `Resources/MEMORY_PROTOCOL.md` and your `VAULT-INDEX.md` — actually disagree about what a specific term means, not just that one of them is behind. Your AI should name the two files and the exact term or field they disagree on. Until you say which one is right, it won't guess, and it won't quietly go with whichever one looks newer — it treats any note whose meaning depends on that disputed term as "can't tell yet," while everything else keeps working normally. Tell it which file is correct (usually `MEMORY_PROTOCOL.md`, since it's the canonical source) and have it sync the other one to match.

## The health-check report said PARTIAL, or a coverage audit flagged HC-FALSE-PASS — is the vault broken?

No — that's the honesty system working. `PARTIAL` means the health check didn't cover everything it was asked to (too big for one pass, told to stop early, or an unreadable file), and it's telling you about it instead of quietly claiming a clean scan. `HC-FALSE-PASS` from `tools/audit_health_coverage.py` means a manifest claimed `PASS` while the vault's own file list proves it didn't inspect everything — which the audit rejects automatically; the scan's coverage record (the Inspection Manifest) and reality disagree. Neither means the vault itself is damaged. Ask the AI to re-run the check at a scope it can genuinely finish, or check why it stopped. A `BLOCKED` verdict is different: one of the check's own inputs was unavailable (vault unreadable, malformed manifest, a blocked dependency), and it's telling you it couldn't complete rather than pretending it could.

## My vault is older and doesn't have any of this — is it broken?

No. Everything above is optional and additive. A vault built before this layer existed keeps working exactly as it always has; nothing about it needs fixing or rebuilding. If you want the new pieces (the protocol file, the health-check Job, the metadata fields), just ask your agent — it'll add them without touching your existing notes, and new fields only get added to a note when you're already editing it for some other reason.
