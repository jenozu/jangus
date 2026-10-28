# Choosing a license (owner decision)

The repository currently has **no license file**, which means default copyright
applies: others may view the code but have no right to use, copy, modify or
distribute it. [CONTRIBUTING.md](CONTRIBUTING.md) says not to add an arbitrary
license, and choosing one is a legal act that belongs to the copyright holder.
This note lists common options; it is not legal advice.

| Option | What it allows | Typical fit |
|---|---|---|
| MIT | Use, modify and redistribute with attribution; no patent grant | Maximum adoption, minimal obligations |
| Apache-2.0 | Same, plus an explicit patent grant and trademark clause | Corporate-friendly, safer for contributors |
| GPL-3.0 / AGPL-3.0 | Use and modify, but derivatives must stay open (AGPL covers network use) | You want improvements shared back |
| Proprietary / all rights reserved | Keep as is, optionally add an explicit notice | Private or commercial plans |

## Steps once you decide

1. Add the standard license text as `LICENSE` at the repository root, with the
   correct copyright holder and year.
2. Add `license` (SPDX identifier) and `readme` metadata to `[project]` in
   `pyproject.toml`, and add `/LICENSE` to the sdist include list.
3. Check dependency licenses are compatible (`uv.lock` and `package-lock.json`).
4. Mention the license in `README.md`.
