# Custom Coordinates

These JSON files are named in correspondence to the dimensions of the LED board used when running the software. A file, located in the `coordinates` directory with a filename `w<cols>h<rows>.example.json` tells the scoreboard that those dimensions are officially supported. This `.example` file is required and you will need to copy one of the existing files into a file that matches your dimensions.

You can edit these coordinates to display parts of the scoreboard in any way you choose. Simply copy the file corresponding to your board's dimensions to `w<cols>h<rows>.json`. This JSON file only needs to contain the parts you wish to override but it's often easier to just make a copy of the full example file and edit the values you want to change.

> [!WARNING]
> **DO NOT** edit or remove `.example.json` or `.schema.json` files!
>
> These are checked by the software to determine which matrix dimensions are supported. If you remove the file, the scoreboard may fail to start.

## Example

**Customizing coordinates for a 64x32 board:**
1. Copy the example using the user interface or a command such as `cp coordinates/w64h32.example.json coordinates/w64h32.json`
2. Edit the coordinates in `w64h32.json` as you see fit
3. Your customized coordinates will always take precedence over the example defaults

## Alternative Layouts

A file whose name carries a suffix after the dimensions, such as `w128h64VERBOSE.example.json`, is an alternative arrangement for that board size rather than a separate size. Load one with the `layout_variant` config option (`"layout_variant": "VERBOSE"`), and customize it by copying it to `w128h64VERBOSE.json` — the same `.json` override rule as any other layout.

> [!IMPORTANT]
> Do not rename a variant to the plain `w<cols>h<rows>` name. A custom file is reconciled against the example whose name it matches, and keys the example does not have are **deleted**, so a variant copied to `w128h64.json` would lose everything specific to it on the next update.

## Fonts
Any scoreboard element that prints text can accept a `"font_name"` attribute. Supported fonts need to be named with `<width>x<height>.bdf` (or `<width>x<height>B.bdf` for bold fonts). The font loader will search `assets/` first for the specified font and then it will fall back to searching `matrix/fonts/` if one was not found.

## States
The layout can have a couple of different states where things are rendered differently. Adding an object named for the layout state and giving it the same properties from the parent object will change the positioning of that parent object only when that state is found. For instance, when a game enters the `Warmup` state, the text `Warmup` appears under the time and the scrolling text is moved down.
* `warmup` will	only render on the `pregame` screen and appears when a game enters the `Warmup` status. This usually happens 15-20 minutes before a game begins.
* `nohit` and `perfect_game` will only render on the live game screen and appears when a game returns that it is currently a no hitter or perfect game and the `innings_until_display` of `nohitter` has passed.
* The `line_score` section configures the line score (RHE) on the game screen.
  * Runs are always displayed.
  * `show_hits_and_errors` toggles displaying hits and errors.
  * `compress_digits` will reduce the space between digits when the number of runs or hits is > 9.
  * `spacing` is the number of pixels between the runs/hits and hits/errors.
  * When the line score is high (greater than 3 total digits), use `shorten_team_name_on_high_line_score` to shorten team names to prevent overflow.

## Pitch Data
* `enabled` (true/false) Turn feature on/off
* `mph` (true/false) When rendering pitch speed add mph after (99 mph)
* `desc_length` (short/long) The short or long pitch type description, you can change both the short and long description to your liking in data/pitches as long as you do not change the index value.

## Play Result
* `enabled` (true/false) Turn feature on/off
* `desc_length` (short/long) The short or long play result description.

## Team Records During Games
Team records can be displayed on the team banner during most game states, such as pregame, live, and postgame.
* `enabled` (true/false) Turn feature on/off
* `position` (absolute/relative) Defines origin of the text. When `absolute`, the origin is always `(0, 0)`. When `relative`, the origin is relative to the end of the team name in the banner.

`final.record` is separate, and only draws on the final screen. The team banner is drawn on every screen, so a record placed there with `teams.record` also appears mid-game; a layout whose live screen already fills the space beside the score can only show records once the game is over.
* `enabled` (true/false) Turn feature on/off
* `away` / `home` Where each record is drawn.

## At-Bat Detail
These are all off unless a layout gives them coordinates and sets `enabled`. A layout that does not mention them renders exactly as it did before.
* `atbat.batter_order` The batter's spot in the order, drawn where the `AB:` label otherwise goes. When shown, the label is dropped — the number occupies the same space and says more. Falls back to `AB:` for a batter with no spot in the order, such as a pitcher in a DH game.
* `atbat.batter_stats` The batter's season AVG, HR and RBI. These are laid out right-to-left from the panel's right edge rather than placed at an `x`, because their combined width depends on the numbers themselves; only `y` is configurable.
  * `show_era` (true/false) Also draw the pitcher's season ERA on the pitcher row, aligned with the column above it, replacing the `P:` label.
* `atbat.play_description` A line of play-by-play text. It is drawn in place when it fits the configured `width` and scrolled once when it does not.
  * The text is the resolved play when MLB has published one, otherwise a description of the pitch just thrown, otherwise the last resolved play held over. MLB only fills in a play's description once the at-bat *ends*, so without the fallbacks the line would be empty through most of every at-bat.

## Inning Indicator
`inning.arrow` accepts either placement:
* `x_offset` / `y_offset` — one arrow, positioned relative to the inning number, coloured `inning.arrow.up` or `inning.arrow.down` depending on the half being played.
* `x` / `y` — the tip of each arrow, placed absolutely. Both arrows are drawn: the active half in `inning.arrow.active` and the other in `inning.arrow.inactive`, so the indicator keeps the same shape all game instead of moving between two positions. Through an inning break the upcoming half blinks.

## Inning Break
* `inning.break.show_field` (true/false) Replaces the `Mid 5th` text with the live screen's furniture: the diamond and out markers in `inning.break.inactive`, plus the inning indicator. Between halves of an inning there are no runners and no outs to report, hence the dimming. Only usable on a layout whose due-up display leaves the diamond's space free.
* `inning.break.due_up.scroll` Draws the due-up batters on one scrolling line, with each batter's spot in the order, instead of the stacked `leadoff` / `on_deck` / `in_hole` rows and the `Due` / `Up:` labels.

## Updates
The software develops and releases features with full support for the default layouts, so custom layouts may look unsatisfactory if you update to later versions of the scoreboard. If you as a user decide to create a custom layout file, you are responsible for tweaking the coordinates to your liking with each update.

## Current Issues
A couple of things are not completely implemented or have some implementation details you should understand.

* `bases` currently requires an even `size` value to be rendered correctly
* Not all options are enabled on all board sizes by default. For example pitch count and pitch type are not enabled by default on boards smaller than 64x64. Options are "disabled" by forcing them to render outside the board, by setting X and Y coordinates less than 0 or greater than the height or width of the board.
