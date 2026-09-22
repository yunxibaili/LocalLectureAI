# Releasing Hearsay

Steps to create a new GitHub release with the installer attached.

## Prerequisites

- `build.bat` dependencies (64-bit Python 3.11+, PyInstaller)
- Inno Setup 6+ (`winget install JRSoftware.InnoSetup`)
- GitHub CLI (`winget install GitHub.cli`), authenticated once with `gh auth login`

## Bump the version

Update the version number in all three files (keep them in sync):

- `src/hearsay/__init__.py` — `__version__`
- `src/hearsay/constants.py` — `APP_VERSION`
- `installer.iss` — `AppVersion`

> Do **not** change `AppId` in `installer.iss`. It is the app's permanent identity in Windows (Add/Remove Programs and upgrade matching) and must stay fixed — changing it makes Windows treat a new build as a separate product, so upgrades stop recognizing existing installs.

## Build the installer

```bash
# 1. Bundle the app with PyInstaller (build.bat wraps `pyinstaller Hearsay.spec`)
build.bat

# 2. Compile the Windows installer
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

Output: `installer_output\HearsaySetup.exe`

## Commit, push, then create the release

Commit the version bump (stage explicitly — avoid `git add -A`), push `master`, then create the release from the pushed commit with the installer attached:

```bash
git add src/hearsay/__init__.py src/hearsay/constants.py installer.iss   # + any code/doc changes
git commit -m "…summary… (vX.Y.Z)"
git push origin master

gh release create vX.Y.Z installer_output/HearsaySetup.exe \
  --target master --title "Hearsay vX.Y.Z" --notes-file notes.md

git fetch --tags   # local tags lag behind GitHub until you fetch
```

Write the release notes in a file and pass `--notes-file` rather than inlining them — it sidesteps shell-quoting pitfalls (especially in PowerShell, the project's default shell). To auto-generate notes from commits instead, swap in `--generate-notes`.

## Verify

After creating the release, confirm:

1. The release appears at https://github.com/parkscloud/Hearsay/releases
2. `HearsaySetup.exe` is listed as a downloadable asset
3. The "Installed version" link in README.md resolves to the releases page

## Notes

- The version in `installer.iss` (`AppVersion=`) should match the release tag.
- This file is tracked in the repo for portability.
