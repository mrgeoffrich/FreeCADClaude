# Release process

Deliberately lightweight. This is a personal-use addon with exactly one
supported version (`SECURITY.md`: *"Only the latest `main` is supported; there
are no backported fixes"*), so a release is a **marker on `main`**, not a
separate artifact anyone has to build.

## What a release is, and isn't

**Users install from the `main` branch, not from a tag or a release asset.**
Both install paths in the README track `main` directly:

- FreeCAD's **Addon Manager** (the recommended path) is pointed at this repo as
  a *custom repository* with **Branch: `main`** — it clones and pulls that
  branch. `package.xml` declares the same (`<url type="repository"
  branch="main">`).
- `git clone` straight into the `Mod` dir gets `main` too.

So a user who installs today gets whatever HEAD of `main` is, tag or no tag.
Which means:

- **A tag doesn't ship anything.** It records "this commit is what version
  1.0.0 was", so a bug report saying "I'm on 1.0.0" can be resolved to a commit.
- **The GitHub release is the changelog.** There is no `CHANGELOG.md`; the
  release notes are it. That's the whole reason to bother with `gh release`.
- **The version users see in the Addon Manager is `<version>` in
  `package.xml`.** If you tag without bumping that, the Addon Manager keeps
  showing the old number and the tag is invisible to users. Bump it in the
  same commit you tag.

## Versioning

Semver, read against *the user's install*, not the code:

| Bump | When |
|---|---|
| **major** | Something a user has to act on: a raised FreeCAD minimum, a renamed/removed preference under `PARAM_PATH`, a moved artifacts directory, a new external prerequisite. |
| **minor** | New tools, new skills, new panels, prompt/behaviour work — the normal case. |
| **patch** | Fixes only, nothing new. |

Internal refactors that no user can observe don't need a release at all. Not
every push to `main` is a version; cut one when there's something worth writing
notes about.

## Cutting a release

From a clean `main` that's in sync with `origin`.

**1. Sanity-check the thing actually runs.** There's no CI, so this is manual:

```bash
python3 eval/run.py            # end-to-end: launches FreeCAD, runs a real turn
```

Then load it in FreeCAD once (`pwsh -File deploy.ps1` / `./deploy.sh`, restart,
open the **Claude Chat** workbench, send a message). The eval covers the agent
path; it does not cover the dock actually appearing.

**2. Bump the version** in `package.xml` — both fields:

```xml
<version>1.2.0</version>
<date>2026-08-04</date>          <!-- the release date, ISO -->
```

**3. Commit, tag, push:**

```bash
git commit -am "Release 1.2.0"
git tag -a v1.2.0 -m "1.2.0"     # annotated; tag name is v-prefixed, package.xml's isn't
git push origin main --follow-tags
```

**4. Publish the notes:**

```bash
gh release create v1.2.0 --title "1.2.0" --notes-file <notes.md>
```

Or `--generate-notes` for the raw commit list, then edit it down. Write the
notes for someone deciding whether to update — what's new, what changed under
them, what broke — not a commit dump. Reach for `--draft` if you want to read
it on the site before it goes out.

**Don't attach a zip.** A GitHub source zip won't install correctly on its own
(the addon must land in the version-namespaced `Mod` dir as
`Mod/FreeCADClaude/`, and GitHub's zip nests everything under a
`FreeCADClaude-<ref>/` folder). The Addon Manager and `git clone` both handle
that; a downloaded zip invites people to get it wrong.

## After a release

Nothing. No release branch, no backports, no version support matrix — `main`
moves on and the next fix goes straight into it.
