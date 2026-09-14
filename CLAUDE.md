# CLAUDE.md — working notes for this fork

Context for picking this repo up cold. Read the **Landmines** section before
editing any `*.example.json`, and the **Git** section before pushing anything.

## Git — read this first

| Remote | URL | Push? |
|---|---|---|
| `origin` | `https://github.com/twfarley/mlb-led-scoreboard.git` (**the fork — push here**) | yes |
| `upstream` | `https://github.com/MLB-LED-Scoreboard/mlb-led-scoreboard.git` | **`DISABLE`** — never push |

**All work goes to the fork (`origin`).** `upstream`'s push URL is deliberately set
to the literal string `DISABLE` so an accidental `git push upstream` fails. Leave it
that way. Confirm a target before pushing:

```sh
git remote get-url --push origin
```

To check whether a commit ever reached the main repo, don't use
`gh api .../commits/<sha>` — forks share a commit database, so that returns a false
positive. Use the compare endpoint instead; `diverged` means *not* on upstream:

```sh
gh api repos/MLB-LED-Scoreboard/mlb-led-scoreboard/compare/master...<sha> --jq .status
```

### Never push the head branch of an open upstream PR

Upstream's workflows trigger on `pull_request: branches: [dev, master]`, so they run
**in the upstream repo**, not in this fork, for any open PR whose head branch lives
here. Pushing such a branch mails a build result to the whole org
(`mlb-led-scoreboard@noreply.github.com`). That has already happened once —
`feature/config-web-editor @ 6f80589` — and it is pure noise for maintainers who did
not ask for it.

`feature/config-web-editor` was the head of PR #765, which is now **closed**, so no
branch here currently feeds upstream CI and the blocked list is empty. Add to it
whenever you open a new upstream PR.

A `pre-push` hook enforces this, and also refuses `upstream` outright. Hooks are not
tracked by git, so **a fresh clone starts unprotected** — run:

```sh
tools/install-git-hooks.sh
```

It reads the blocked list from `git config --get-all fork.noPushBranches`; edit the
`BLOCKED` line in that script and re-run to add one. `git push --no-verify` overrides
it, which is the right escape hatch when you genuinely mean to update a PR.

Note that pushes to this fork's own `master` are fine: only `readme-toc.yml` runs on
`push`, and that executes in the fork and notifies nobody but you.

### Branches

`master` is the integration branch and what the Pi runs. Everything below is merged into
it; the topic branches are kept so individual pieces stay reviewable.

- `master` — upstream v9.2.2 plus all of the below. **Work here** unless you have a
  reason not to: the layout editor and monitor need the union of the other branches
  (the anchor metadata references elements that only exist once layout is merged).
- `feature/layout` — the 128x64 score bug, the two-arrow inning indicator, and the
  retunes for final / pregame / status / inning-break / no-hitter.
- `feature/layout-editor` — the WYSIWYG editor and the layout monitor. Kept level with
  `master`.
- `feature/homeassistant-plugin` — Home Assistant dashboard plugin (Bullpen). Note the
  singular: `feature/homeassistant-plugins` was its pre-rebase name and is deleted.
- `feature/config-web-editor` — schema-driven local web config editor. Was
  [PR #765](https://github.com/MLB-LED-Scoreboard/mlb-led-scoreboard/pull/765), now
  **closed**: maintainers preferred extending their static editor, and upstream's
  `leagues` key superseded the `sport_ids` option it added.
- `homekit` — 27 commits that exist nowhere else, ~155 behind upstream. Left alone
  deliberately; contains API-throttling and caching work with no counterpart in master.
- Local tags `backup/*` mark pre-rebase tips. They are **not pushed**, so they only
  exist on this machine.

Maintainers prefer **plugins over core changes**. Core edits are fine on this fork;
they just cost merge friction and are a harder sell upstream.

## Landmines (each of these cost real time)

1. **`*.example.json` files are GENERATED from `schemas/`.** Never hand-edit them —
   `python -m schemas --check` is a CI check and will fail with
   *"config at … differs from schema"*. To change an example, edit the schema's
   `default` block and regenerate:
   ```sh
   venv/bin/python -m schemas --schema schemas/coordinates/w128h64.schema.json \
       --output coordinates/w128h64.example.json --overwrite
   ```
   Edit schemas by **surgical text replacement**, not `json.dump` round-trips —
   dumping reformats the whole file (compact `"required": ["x"]` blows up to
   multi-line) and buries the real change in noise.

2. **`validate_config.py` reconciles custom files against the EXAMPLE, not the JSON
   schema**, and *deletes* keys the example doesn't have. So a new coordinate/colour
   key must be added to the schema **and** regenerated into the example, or it will
   be silently stripped from `coordinates/w128h64.json` on the next run.

3. **`validate_config.py` eats any root `config*.json`.** It treats every
   `config*.json` with a matching `config.example.json` as a custom config and strips
   unknown keys. This destroyed a `config.ha-stash.json` backup once. **Never name a
   file `config.*.json`.** The HA config removed from the rotation now lives in
   **`ha_stash.json`** (safe name, ignored by the scanner).

4. **Colour keys must be mirrored into `tests/fixtures/colors/scoreboard.json`**, which
   sets every colour to `r:1,g:2,b:3` and asserts they all merge through. Add a colour
   to the schema without adding it there and `test_custom_config_values` fails.

5. **Runtime config is gitignored**: `/config*.json`, `coordinates/*.json`. Anything you
   want tracked has to go through the schema → example path.

6. **`schemas/coordinates/_anchors.json` has a `screens` map that must list every
   element a screen draws.** The layout monitor's `--deep` overlap check renders each
   element with all *others* displaced off-panel; anything a screen draws that is not
   in that screen's prefix list never gets displaced, so it appears in every element's
   claim set and every pair "overlaps". Making the break screen draw the bases turned
   into **771 findings** across six board sizes until `bases.`/`outs.` were added to
   `live_break`. A sudden explosion of `overlap` findings that all share one bounding
   box means a missing prefix, not a layout fault.

7. **`AVAILABLE_OPTIONAL_KEYS` gating means `layout.coords()` only substitutes a state
   sub-dict at the exact node you ask for.** `coords("teams.record")` picks up a
   `nohit` child; `coords("teams.record.away")` does not, because the walk reaches
   `away` and only then looks for the state key. So a per-state variant has to be
   repeated at every keypath the renderer actually reads. This is why the FINAL records
   are a separate `final.record` block rather than a state variant of `teams.record`.

## Rotation model (non-obvious)

- **Higher priority number = more important.** `0` is reserved for "no games".
- Only the **single highest** matched priority level renders.
- `kind: "game"` rules are *active* (they raise the level). `secondary_game` is
  **passive** — it only adds games to a level some active rule already reached, so a
  passive rule at a level nothing activates renders **nothing**.
- A working fallback chain is a descending set of *active* game rules, e.g.
  preferred-team live (4) → any live (3) → pregame (2) → final (1) → news/standings (0).
- `required_status: "live"` also matches the transient `Game Over` state
  (`is_fresh`), so just-ended games briefly appear as live.

## Bullpen plugins

- `data/plugins.py` builds each plugin with `config.layout.for_plugin(name)` and
  `config.scoreboard_colors.for_plugin(name)`, so a plugin's keys are namespaced:
  coords live at `coordinates.*.plugins.<name>`, colours at
  `colors/scoreboard.json → plugins.<name>`, and the plugin reads `layout.coords("<name>.foo")`.
- **Plugins get no game data.** `MLBConfig` exposes only `scrolling_speed`,
  `time_format`, `plugin_config`, `parse_today`, `is_postseason`. A plugin that wants
  games must fetch them itself.
- A plugin **screen** may only carry `kind`, `with_priority`, `seconds` — the core
  schema rejects `teams`/`required_status` there. All filtering goes in the plugin's
  own config block.
- **Plugin screens can't replace the default game screen.** They only appear at
  priority levels a `game` rule activates, and that same rule draws the stock screen —
  so you always get both, alternating. This is exactly why the score-bug plugin
  approach was abandoned in favour of gated core additions.

## The 128x64 score bug (current work)

Recovered from commits reverted by `fca45c9` (originals are still reachable:
`git show 6512927:renderers/games/game.py`).

Everything added to core is **opt-in behind an `enabled` flag defaulting to false**, so
other board sizes render byte-identically:

- `data/game.py` — `batter_stat()`, `batter_batting_order()`, `pitcher_era()`,
  `current_play_description()` (held `PLAY_DESCRIPTION_HOLD` seconds — MLB only
  populates `description` once a play *resolves*, so it is empty mid-at-bat).
- `renderers/games/game.py` — `atbat.batter_stats` (AVG/HR/RBI, `show_era` adds the
  pitcher's ERA in the same column), `atbat.batter_order`, `atbat.play_description`
  (scrolls once; `situation_fallback` substitutes `Top 9 · 3-1 · 2 out · 2B+3B` when
  there's no description).
- `coordinates/w128h64.example.json` is now the score-bug layout (teams bottom,
  batter/pitcher rows top). The previous layout is in git history:
  `git show cbca405^:coordinates/w128h64.example.json`.
- The superseded local override lived on the Pi as `coordinates/w128h64.old.json` — note the
  order. That name ENDS in `.json`, and `custom_config_files()` derives the schema from
  `file.split(".")[0]`, so it resolved to `w128h64.example.json` and was being reconciled and
  rewritten on every run. It was never a backup. Both it and the real custom file now live in
  `~/scoreboard-backups/<stamp>/` on the Pi, outside the scanned directory.

Master draws only the **active** inning arrow (`inning.arrow.up/down` are
`x_offset`/`y_offset` relative to `inning.number`). The old dual bright/dim arrow pair
and the pitch-speed overlay were **not** ported.

## Open items

1. ~~Branch is misnamed and carries dead code.~~ **Done** — commit `636603f`, which
   added the unused `score_bug/` Bullpen plugin directory, has been dropped, and the
   branch renamed `feature/score-bug-plugin` → **`feature/score-bug-128x64`** (the work
   is gated core additions, not a plugin). That directory was the sole cause of the
   typecheck CI failure: `mypy .` went from 9 errors — all inside `score_bug/` — to
   clean. The old history is tagged `backup/score-bug-postrebase` if the plugin
   scaffolding is ever wanted again.
2. **Pregame / status screens were laid out assuming teams on top.** Now that teams
   sit at y=28–63 on 128x64, the `pregame.*` and `status.*` coordinates likely want
   retuning. ~~The final screen renders acceptably but was not designed for this
   arrangement.~~ **Final is done** — `FINAL` (+ extra innings) and the W/L/SV/blurb
   scroll now sit in the free band above the banner, `final.nohit_text` moved up
   beside `FINAL`, and season records draw to the right of the score from a new
   `final.record` block. Records come from the postgame renderer, **not** from the
   banner's `teams.record`: the banner draws on every screen, and the live screen
   already fills that space. `gameData.teams.<side>.record` for a completed game
   already includes that game (checked against the standings on a just-final game),
   so there is nothing to wait for.
3. ~~Branch is based on a stale local `master`.~~ **Done** — rebased onto upstream
   `master` at v9.2.2 (`fbc1a4d`). Two commits were dropped as superseded upstream:
   `7aa3579` ("Delayed: Tiebreaker" status, merged upstream) and `fea9d6b` (drop
   sportId 51 — upstream's `data/leagues.py` now narrows WBC to `leagueId=159,160`,
   which fixes the same Appalachian-League leak without losing WBC). Pre-rebase tip is
   tagged `backup/score-bug-prerebase`. Upstream's `leagues` work still **supersedes**
   the `sport_ids` approach on `feature/config-web-editor` — reconcile PR #765
   against it.
4. ~~The old `origin/feature/score-bug-plugin` branch is stale.~~ **Done** — deleted
   from the fork. Its history (including `score_bug/`) now survives **only** in the
   local tags `backup/score-bug-prerebase` and `backup/score-bug-postrebase`, which
   have not been pushed. Push them if you want that recoverable off this machine.
5. **The fork's own `master` (`origin/master`) is 49 commits behind upstream**, still
   at `f7d1e8d`. Local `master` has been fast-forwarded to `fbc1a4d`; the fork has not.
   A stale fork master is what let the last round of drift go unnoticed.

## Commands

```sh
# emulator (browser adapter on :8888)
venv/bin/python main.py --emulated --led-cols 128 --led-rows 64

# the CI checks (.github/workflows/) — all four must pass
venv/bin/black --check .
venv/bin/mypy .
venv/bin/python -m schemas --check      # silence = OK
PYTHONPATH=. venv/bin/python -m unittest discover
```

Use `venv/bin/python` — the system Python lacks `bullpen`, `statsapi`, etc. Several
modules need `PYTHONPATH=.`.

**`venv/` is gitignored and will not exist in a fresh clone.** Rebuild it with a
**Python 3.10+** interpreter — upstream now requires it (`data/leagues.py` uses
`str | int` unions), and macOS system Python is 3.9, which cannot even install
`requirements.txt`. CI runs 3.13; 3.10 is the floor and is in the unittest matrix:

```sh
uv venv --python /opt/homebrew/bin/python3.10 venv
VIRTUAL_ENV=venv uv pip install -r requirements.txt -r requirements.dev.txt
```

`uv venv --python 3.13` (auto-download) fails behind the proxy — python-build-standalone
is fetched from GitHub releases, which is blocked. Point `--python` at a Homebrew
interpreter instead. PyPI itself is reachable.

**The test suite needs live network** — `test_data_up_to_date`, `test_game` and
`test_schedule` (13 tests) hit `statsapi.mlb.com` for real. With API access the full
suite is **94/94 green**; without it those 13 fail and the emulator cannot render real
games.

**Apple Claude Code blocks `statsapi.mlb.com` by default.** Allowlist it — the list is
**machine-wide**, there is no project-scoped option, and changes apply on the next
request with no restart:

```sh
echo "statsapi.mlb.com" >> ~/.claude/apple/dangerous_allowed_domains.csv
```

Subdomains inherit, and `*.apple.com` is always allowed. GitHub *releases* are still
blocked (see the `uv` note above); PyPI and the GitHub *API* work. If the suite fails
en masse with `ProxyError ... Max retries exceeded`, this is why — not your change.
Compare against a baseline worktree before assuming a regression:

```sh
git worktree add /tmp/mlb-baseline upstream/master
```

**Commit signing is off for this repo** (`commit.gpgsign=false`, set locally) — the
global `~/.gitconfig` turns on x509 signing with no `user.signingkey`, so every commit
fails with *"gpg failed to sign the data"*. Note that `git rebase` snapshots the `-S`
flag into `.git/rebase-merge/gpg_sign_opt` when it starts, so a rebase begun before the
config change keeps failing; `rm` that file to recover mid-rebase.

## Deploying to the Pi

As of 2026-09-12 the Pi is on **`master`**, which is where all the layout work lives. It was on
`feature/homeassistant-plugins` — a branch that no longer exists on the fork, since it was rebased
into `feature/homeassistant-plugin` and merged into `master`. The Pi's local copy is the last one.

```sh
cd ~/mlb-led-scoreboard
git pull
sudo service mlb-scoreboard restart
sudo journalctl -u mlb-scoreboard -n 40 --no-pager
```

Check the journal even when `systemctl is-active` says `active`: the unit is `Restart=on-failure`,
so a crash loop still reports active between attempts. A healthy start logs
`MLB LED Scoreboard - v9.2.2 (128x64)`.

Three things that bite on a bigger jump:

1. **`master` needs Python 3.10+.** `data/leagues.py` uses `dict[str, str | int]` with no
   `from __future__ import annotations`, so it is evaluated at import time and 3.9 cannot parse it.
   Check `venv/bin/python3 -V` first.
2. **Run `sudo venv/bin/pip install -r requirements.txt`** after crossing a version boundary;
   v9.2.2 added dependencies older branches never had. Site-packages is root-owned, hence `sudo`.
3. **A custom `coordinates/<size>.json` silently defeats layout changes.** `validate_config`
   reconciles a custom file against the example by adding missing keys and deleting unknown ones,
   but *keeping existing values* — so repositioning an element in the example changes nothing while
   a custom file exists. Move it out of `coordinates/` (not just rename it — see the naming trap
   above) and the example becomes the layout. `Config file .../w128h64.json not found. Using
   default values` in the log confirms it.

`config.json` and `colors/scoreboard.json` are different: keep those, since `validate_config` merges
new keys into them without disturbing your teams, rotation and colours.

Cannot be reached from inside a Claude Code session — SSH to port 22 is blocked below the Bash
sandbox, and the ACC domain allowlist only governs proxied HTTP, so adding the Pi's IP does not
help. `--dangerously-skip-permissions` does not help either. Run Pi commands from a normal terminal.

## Raspberry Pi

The physical board runs at **`pi@mlbled.local`** (use the hostname; DHCP moves the IP,
and mDNS is occasionally flaky — retry). Repo at `/home/pi/mlb-led-scoreboard`, venv
site-packages is root-owned (`sudo venv/bin/pip install ./<plugin>`). It runs as the
systemd unit **`mlb-scoreboard`**; apply changes with
`sudo service mlb-scoreboard restart` (**don't reboot**). Logs:
`sudo journalctl -u mlb-scoreboard -b`. Its `config.json`, `colors/scoreboard.json` and
`coordinates/*.json` are local to the Pi — **merge**, never overwrite.

> Journald there has lost prior boots before, so a crash's logs may be gone after a
> power-cycle. Capture logs before rebooting.

## The upstream PR branch (`feature/verbose-128x64`)

Branched from **upstream/master at `fbc1a4d` (v9.2.2)**, not from this fork's `master`,
and contains only the layout work plus the statsapi data it needs. Six commits, each
self-contained. Deliberately **excluded**: the layout editor, the layout monitor,
`layout_preview.py`, `_anchors.json`, the Bullpen plugins, `homekit`, this file, the
fork's `config.schema.json` preferences (rotation defaults, `api_refresh_rate`,
`wbc_team`, the `x-*` editor annotations) and every other board size's coordinates.

Two things differ from `master` on purpose and must not be "fixed" by copying master's
version over:

1. **`master` broke the other layouts and the PR branch does not.** On `master`,
   `inning.arrow` was converted to absolute coordinates on *all six* sizes and
   `_render_inning_break` was deleted, so no layout draws "Mid 5th" any more. On the PR
   branch both schemes coexist: `x_offset`/`y_offset` keeps the original single arrow
   and colours (`inning.arrow.up`/`down`), `x`/`y` opts into the stacked pair
   (`inning.arrow.active`/`inactive`), and the break screen keeps its text unless
   `inning.break.show_field` is set. `master`'s flag was `show_bases_and_outs`; the PR
   calls it `show_field` because it governs the inning indicator too.
2. **`layout_variant` is PR-only.** Renaming `w128h64VERBOSE.example.json` to
   `w128h64.json` — the obvious way to ship this — is a trap: `validate_config.py`
   reconciles a custom file against the example matching its name and *deletes* unknown
   keys, so it would strip every verbose key, including silently swapping the arrow's
   `x`/`y` back for `x_offset`/`y_offset`. Hence `"layout_variant": "VERBOSE"` in
   `config.json`, which loads `w128h64VERBOSE.example.json` directly.

Verified by hashing all **54** existing size/screen renders against upstream: byte-identical.
Reproduce with a scratch copy of `layout_preview.py` (untracked — never commit it there,
and note its `_SIZE_RE` rejects a name with a suffix until you widen it). **Importing it
rewrites `scrolling_speed` in the tracked `config.example.json`** — `git status` before
committing anything after a preview run:

```sh
git show master:layout_preview.py > layout_preview.py
git show master:preview_emulator_config.json > preview_emulator_config.json
# then hash layout_preview.render(size, screen) for every size/screen, both here and in
# a worktree at upstream/master, and diff the two maps
```

`teams.name.max_width` was **dropped** from the PR: it fixes w128h32's "Nationals"
overlap, which would mean editing another size's coordinates. Still on `master`
(`renderers/games/teams.py` + `tests/test_team_renderer.py`) if it is ever worth its own PR.
