# Crystal map randomizer

Randomizes route and dungeon layouts in pokecrystal-speedchoice2 while keeping the
ability requirements between every entry point and every check exactly as in vanilla.

## Status: phase 0 (extraction) done

(The analysis scripts of this phase, analyze.py and crosscheck.py, were removed in the web-app
cleanup; they are in the git history.)

## Web app (web/)

A static page that runs the randomizer in the browser (Pyodide): the player picks a finished ROM
(speedchoice, then zxplus and the Item Randomizer), a seed, and gets the ROM back with new layouts
and a text log. What the Item Randomizer changed in the maps is read from the ROM itself
(`rom_overrides`), so the layouts match its logic; a seed gives the same maps as `randomize.py`.
Maps are spread over a few web workers, slowest first.

- `web/index.html`, `style.css`, `app.js` (page and board), `worker.js` (one Pyodide each).
- `maprando/web.py`: the Python entry points the workers call.
- `web/data/bundle.pkl.gz`: the parsed disassembly and its map symbols, made by
  `python3 tools/build_web.py` and committed. Rebuild it when the romhack or the Disasm classes
  change. The package zip is built on every deploy.
- `.github/workflows/pages.yml` publishes web/ to GitHub Pages on every push to main.

Try it locally: `python3 tools/build_web.py`, then `python3 -m http.server` in web/.

Cursed mode (`--cursed` for build.py and randomize.py, a checkbox on the page): routes are drawn
with every block of their tileset that has the right collision, picked at random (houses, signs,
fences and flowers in a patchwork), and the usual coherence pass is skipped. Collision is matched
exactly, so logic and every check are unchanged; only the look is.

## Code layout (maprando/)

    from maprando.io.disasm import load_disasm
    from maprando.randomizer import Randomizer

    r = Randomizer(load_disasm('../pokecrystal-speedchoice2'))
    maps, log = r.randomize_maps('my seed')       # {name: new MapInfo}, {name: report}

- `randomizer.py` – `Randomizer`: picks the generator for each map and returns the new map and a
  one-line report; a map whose generator fails stays vanilla. `maps.py` lists the maps by kind.
- `io/` – the only part that touches files. `disasm.py` reads map sizes, tilesets, collision,
  cut/whirlpool block swaps, .blk files, events and each map's script text up front; `rom.py`
  writes maps into a built ROM; `item_rando.py` reads what the Item Randomizer mode changes;
  `disasm_write.py`, `render.py` and `ascii.py` are for the source build and for looking at maps.
- `model/` – `movement.py`: per-map cell grid (16x16 px cells, 2x2 per block) following the
  engine's rules: collision permissions, side walls, ledges (jump from the ledge cell),
  waterfalls/currents, cut/whirlpool block swaps, rocks (Rock Smash), boulders (Strength),
  conditionally present NPCs (own requirement bit, e.g. Sudowoodo), item balls and fixed NPCs as
  walls. Anchors: warps, coord events, bg events, objects, and each contiguous run of passable
  cells on a connected map edge. Entries = warps + edge runs. `reach.py`: Pareto-minimal
  requirement search: for each entry, the minimal ability sets needed to reach every anchor
  (directed: ledges and waterfalls are one-way). `zones.py`, `catalog.py`, `metrics.py`.
- `checks/` – rules a layout must keep, used by the generators and by readback.py (trainers,
  people, cutscenes, ledge jumps, boulders, Strength puzzles, S.S. Aqua).
- `generators/` – `caves/` (caves, routes and other areas; one module per step, put together in
  `generator.py`), `ice.py`, `chambers.py` (boulder rooms from pieces) and `gyms/` (`standard.py`
  for trainer gyms, one module per puzzle gym).

Everything outside `io/` is plain Python with no file access, processes or subprocesses, so it
can run in the browser (Pyodide). build.py spreads the maps over worker processes; a map comes
out the same either way because each map has its own seed.

The invariant for generated maps: identical requirement matrix to vanilla.

## Not geometric (left to the Item Randomizer)
Flash (map-wide darkness), Squirtbottle/Pass/tickets (scripts; modelled as NPC blockers where
the blocker is an object), badges.

## Phase 1: cave generator (maprando/generators/caves/)

    python3 build.py --seed N        # generate caves, build ROM -> ../output/crystal-maprando.gbc (+ .sym)
    python3 preview.py --seed N      # vanilla vs generated PNGs -> out/preview/

Signs and hidden items count as reached only from a tile next to them (you face them; standing on
the tile itself does not count). Every accepted map must pass `verify()`:
- requirement matrix identical to vanilla (every entry, incl. every warp, to every anchor, incl. every warp);
- every zone of the map is reachable from some entry;
- encounter terrain matches the tables: a Surf / grass / headbutt table whose terrain is reachable
  in vanilla still has reachable terrain; a map with no such table gets none outside vanilla-locked
  blocks; no unreachable water anywhere, or under an NPC (waterfall/current tiles are exempt).

Layout style (maprando/generators/caves/ `corridors`): land areas are grown to about three times their
vanilla size, then thinned block by block into one-block-wide winding corridors with walls
between them. Kept: every doorway into a locked block, every gate device, one shore per Surf
crossing, and a pocket (side branch) for each item or NPC; item balls go at the ends of those
branches. Each removal keeps the area strongly connected (locked ledges count one way) and is
chosen to lengthen the walk between those points. New areas attach far along the corridor search
(nearest attachment is the fallback after half the tries). Water stays open. Of three valid layouts
per map, the one with the longest walks is kept. Routes keep block-aligned corridors (no erosion),
so walls are solid forest; headbutt trees go in half-block alcoves beside the path.

Trainers (maprando/checks/trainers.py): a trainer that spots you walks up and stays put. For every
trainer, every facing it may have (own facing and the flipped one, since the Item Randomizer
can invert facings; all four for spinners) and every cell it may stop on, the stop must not
split a walkable area and leaving its own cell must not join two areas. Placement only picks
safe spots, and `verify()` rejects any map with an unsafe trainer (vanilla's four existing cases
are allowed at their vanilla spots).

Ice (maprando/generators/ice.py; Ice Path 1F, B1F, B2F Blackthorn side): a different generator from the
corridor one. The requirement model now slides you on ice: stepping onto ice keeps you moving
until the next tile blocks you (you stop on the ice) or you reach a tile that is not ice or a warp.
Every ice room keeps its outline and the vanilla tiles around it; only the rocks are redesigned
(at most one per 2x2 block, in any corner, as the tileset allows), for all rooms on a map at once
since they share the same floor areas. Simulated annealing starts from vanilla's rocks and moves
them to maximise (`objective = 'shape'`)

    path_w * path length - corner_cost * corners - dead_end_cost * dead ends - rock_cost * rocks

with path_w = 1, corner_cost = 2, dead_end_cost = 4, rock_cost = 0.15. Path length is, for every
pair of floor areas the ice joins in vanilla, the fewest slides on any route between them. A corner
is an ice tile boxed in on two perpendicular sides with at least one of them a placed rock (an
L-shaped pocket, e.g. two rocks touching diagonally): stopping there only leads to the obvious
turn. A dead end is boxed in on three sides (the only way out is back). Pockets made only by the
room's own walls do not count. `objective = 'decisions'` (nontrivial slides) and `'moves'` keep
the earlier objectives for comparison.
Hidden items in rocks on the ice are targets too: you must be able to come to rest on a tile next
to one (then face it), from exactly the same floor areas as in vanilla, and the decisions needed to
get there count toward the score.
Hard rules: exactly vanilla's set of crossings between floor areas (areas are reachable non-ice
floor; ledges split them), and no trap: every tile you can slide to can still reach a warp.
Typical result (slides / corners / dead ends): 1F 54/13/0 -> about 73/5-8/0, B1F 50/4/3 ->
about 68/6-8/0, B2F Blackthorn side 12/2/3 -> about 16/3/1-2. Non-ice parts of these maps stay vanilla.

Ice Path B1F's Strength-boulder room (maprando/checks/boulder_room.py): each boulder falls only through
its own hole (the stonetable pairs hole warps with boulder objects); a boulder on another hole just
sits there. Where a B1F hole is does not matter to B2F (the hole warps to a fixed B2F spot and the
fallen boulder appears at a fixed B2F spot), so holes move too, each with its vanilla block. The
room is built from scratch (`ScratchRoom`): solid rock first, then the free blocks are split into an
open part (with the ways in) and a sealed part holding what vanilla puts behind Strength (the holes
of boulders 3 and 4, and boulder 4). Each boulder's route is planned backwards from its hole (pulled
away 8-16 times with turns), carving its path, the cells the player pushes from and the corner the
player walks round at each turn; the 'gate' boulder (reachable without Strength, hole in the sealed
part) starts in the sealed part's only doorway. Cheapest tunnels then join every piece to the ways
in (and the ways in to each other) without joining the parts except through the doorway; patterns
the tileset cannot draw (diagonal pairs) get one more floor tile. 6000 attempts, best score first.
If none passes, the vanilla room was reshaped instead (`BoulderRoom`: simulated annealing over an
abstract grid of floor/wall quarters, hole blocks and boulder spots). Both were later replaced by
the chamber builder (below) and removed. B1F's holes are one-way (you
cannot arrive through them: model.arrivable), so only the real entrances' requirements matter. A design
is kept only if no boulder can ever be pushed onto another boulder's hole (checked over all plain
floor with the other boulders removed, a safe over-estimate), every boulder can be pushed into its
own hole in some order (several boulders may have to move together; unfallen boulders reset when
the player leaves and comes back), holes, boulders and ways in are grouped into the same areas as
in vanilla with and without Strength, and the drawn map has vanilla's requirement matrix. The score
prefers more pushes and changes of direction. A fixed number of iterations keeps a seed's room the
same on every machine. readback.py re-checks the puzzle in the finished ROM.

Warps move (maprando/generators/caves/ `_movable_warps`, `place_warps`): self-contained warp blocks (ladders,
stairs, holes, warp panels: one block holding only floor, wall and the warp tiles) are placed at the
end of a branch of their zone, and arrival spots on plain floor (landing from a hole above) move to
a far spot of their zone. Doors, cave mouths, map-edge exits and anything near a script, sign,
object or Fly destination stay put. Other maps reach a warp by its number, so arrivals follow. The
Item Randomizer refuses ROMs whose warps are not vanilla, so the pipeline hands it a copy with vanilla
warp positions and puts the moved ones back afterwards (reapply.py).

Item Randomizer patches that rewrite a randomized map's blocks for the chosen mode (e.g. "Remove cut
tree in Ilex") are applied to that map before generation (`--ir-mode`), so the layout matches the
mode's logic. reapply.py restores only bytes the map randomizer changed; the later randomizers'
own edits elsewhere stay. `readback.py <rom> --ir-mode <mode.yml>` checks against the same layouts.

Fly destinations (data/maps/spawn_points.asm) in randomized maps are entries in the requirement
model and keep their vanilla surroundings.

Scripted puzzles: an object a script moves between three or more spots (Ilex Forest's Farfetch'd)
keeps the vanilla layout over the whole area of those spots. Where two different gates separate the
same two zones (Ilex Forest's apprentice and Kurt), every gate between them stays as in vanilla.

People (maprando/checks/blocking.py): walking NPCs count as passable in the requirement model, but one boxed
in by a wall and a trainer never moves. Placement treats everyone (walking NPCs, NPCs kept at their
vanilla spot, NPCs in gate gaps) as standing in the way, walking NPCs only go where standing still
cuts nothing, and `verify()` rejects layouts where everyone standing still, or a trainer that has
walked up to the player plus everyone else, splits an area more than in vanilla.

Speed: the parsed disassembly is cached in out/.cache (keyed on its location and the source files'
sizes and dates; paths are re-rooted on load) and loaded by every worker; the movement model caches
moves per tile; placement caches per-map lookups and keeps each zone's growth frontier; corridor
thinning only recomputes the walks a removal can change; the ice search keeps slide results between
evaluations, drops only the rows and columns a moved rock can affect, and finds crossings, reachable
tiles and decisions in one search, and after a rock moves drops only the cached slides that can
reach it (walking out from the changed tile to the first solid tile, plus one beyond for ledge
jumps); best-of-3 stops looking for alternatives after k/2 more tries; corridor thinning runs on an
integer graph built once per zone; zone connectivity is memoised per try; small zones that keep
failing to join their neighbours are frozen sooner (after 8 + cells/2 tries, at most 25). `MAPRANDO_TIMES=<file>` makes build.py
log per-map times.

Hidden items always sit at the tip of a one-tile dead end, picked up by facing it from the one tile
in front: a nook cut into a wall beside their zone, or, where no wall fits, a floor tile walled in on
all sides but one (only tiles nothing else needs; the zone stays in one piece; forest nooks may use
headbutt-tree walls, never passable void). Items reached only by surfing get a one-tile water inlet.
Items kept in a vanilla part are moved into a nook when their zone has room. `verify()` rejects any
layout where a moved item is not at such a tip. Exceptions: items reachable from two separate zones
(Route 9, Route 34, Dragon's Den B1F: a walkable nook touching both would join them, so they stay in
the wall between or at their vanilla spot), zones with no room at all (Tin Tower 5F, Route 4's small
generated strip: after 15 failed tries the item keeps its vanilla spot instead of failing the map),
and Ice Path's items inside rocks on the ice. readback.py lists every exception.

Cutscenes (maprando/checks/scenes.py): scripts are written for the vanilla map, and a scripted walk that
runs into a wall or an object never finishes, so the game hangs (Slowpoke Well B1F: Kurt walks to
you after the last grunt). Every `applymovement` path keeps its vanilla blocks: an object's walk is
replayed from its `moveobject` spot or its own position, the player's from the coord event or the
object that starts it (with a one-block margin). Objects whose own script moves objects or the
player (the grunt that starts the Kurt scene) keep their spot and the blocks around it. This covers
18 maps (Mt. Moon, Slowpoke Well, Sprout/Burned/Tin Tower, the Lighthouse, Ilex Forest, Victory
Road, Dragon's Den and several routes). readback.py reports any scripted walk that is blocked.
A movable warp whose zone keeps leaving no room for it (15 tries) stays at its vanilla spot.
Maps whose script reads the player's coordinates on entry (Route 16: Cycling Road keeps you on the
bike) keep their warps where they are. Floors where every doorway moves and nothing is locked (Tin
Tower 2F, 3F, 6F) are thinned from a corner block, with the stairs placed at the far ends of the
corridors.

Counters: facing a counter tile ($90), the game talks to whoever stands two tiles away. People
behind counters (the Goldenrod Underground's shopkeepers) are reached from across the counter in the
model, and each stall keeps its vanilla blocks: the person, the counter and the spot in front.

Script block swaps (changeblock): doors that open, holes that appear and rocks that drop must
connect the same way as in vanilla. `verify()` also compares the requirement matrix with each swap
applied (one at a time, then all together), so the room behind Team Rocket Base B3F's password door
or below Burned Tower's hole stays reachable as in vanilla; readback.py reports any difference.

Ledge jumps (maprando/checks/jumps.py): standing on a ledge tile and pressing its way when the step is
blocked makes the player jump two tiles, and the game never checks where they land: into a wall, on
water or onto a person, the player is stuck. The tiles in front of and beyond every ledge keep their
vanilla blocks (and the ice generator leaves them alone), `verify()` rejects any jump into something
the player cannot stand on that vanilla does not have, and readback.py reports them.

Hidden items under rocks (Mt. Moon Square's Moon Stone, Route 40's Hyper Potion): facing the tile
talks to the rock until it is smashed, so the model adds the rock's requirement (Rock Smash) to the
item, and rock and item stay together at their vanilla spot. This applies to Rock Smash rocks only.
The Item Randomizer mode moves two hidden items off a tile where an NPC stands (Route 32's Great
Ball, Lake of Rage's Rare Candy): `build.py` and readback.py read those
event positions from the mode's patches (`ir_event_overrides`), like its block changes, and
generate and check against the map as the Item Randomizer leaves it.

S.S. Aqua (maprando/checks/ship.py): on the ship a battle the player cannot win is a softlock, so every
trainer on its five maps spins (the 17 standing ones become SPINRANDOM_SLOW): the player can always
wait until a trainer looks away and walk past. Fast Ship B1F is generated and checked with the
trainers spinning, the movement bytes are written again after the Item Randomizer, and readback.py
reports any ship trainer that does not spin.

Hidden items reached from one land area and from the water (Route 12's Elixir on an island) get a
dead end in the land area. When no land dead end fits, the item may sit on a water tile walled in on
three sides with land in front of it (picked up from the land, so it needs nothing more). Among the
best-of layouts, the one with the most hidden items at a dead end wins, then the longest walks. Items reached from two land areas move into one of them when it is never
harder to reach than the other from any entrance; otherwise they stay in the wall between. Items
that keep their vanilla spot, and why: the Moon Stone and Route 40's Hyper Potion (under Rock Smash
rocks), Route 9's Ether and Dragon's Den B1F's Revive (reached from two areas, neither always
easier), Route 34's Super Potion (inside the Day-Care cutscene area), Tin Tower 5F's two items
(inside its hop-tile structure), Route 41's Max Ether (a 2x2 vanilla island), Route 4's Ultra Ball
(only a sliver of Route 4 is rebuilt) and the Ice Path items (in rocks on the ice).

Doormats (warp carpets): you leave by pressing in the mat's direction, and the game only warps you
if that step is blocked. The tile beyond every doormat keeps its vanilla block, and `verify()` and
readback.py reject a doormat with walkable ground beyond it.

Boulders (maprando/checks/boulders.py): the requirement model treats a Strength boulder as a cell you
pass with Strength, but in the game it moves one tile per push. From every side it can be
approached, some straight-line push (the player may stand on a ladder; the boulder may not land on
water, a warp or an object) must leave it where everything joined through its cell stays joined.
An item ball still lying on a tile blocks a push onto it, so item ball tiles never count as places
a boulder can land. The blocks on both sides of a generated boulder gate stay fully open and free of objects, and
`verify()` rejects layouts that fail (Burned Tower B1F's vanilla boulder, which cannot be pushed
onto its ladder from the south, is allowed at its vanilla spot).

Passes after layout: erosion and smoothing (dungeons only), void fill, object placement,
hidden items in one-cell wall nooks (stray nooks are filled or widened), block choice,
block re-selection using all four neighbours. Maps are generated in parallel worker processes,
each from its own seed, so the result is the same however many cores run.

Map dimensions and event counts are unchanged, so the ROM's symbol table is identical and
zxplus / the Item Randomizer work unmodified.

Maps: Ilex Forest, Victory Road, Ice Path B3F, caves (Union Cave, Dark Cave, Mt. Mortar, Rock Tunnel, Mt. Moon, Diglett's Cave, Silver
Cave, Whirl Islands, Slowpoke Well, Tohjo Falls, Dragon's Den 1F) and towers (Sprout Tower,
Burned Tower, Tin Tower incl. roof, Olivine Lighthouse), and routes. Also: Route 23, Mt. Silver
outside, Lake of Rage, Mt. Moon Square, the Kanto Underground Path, the Power Plant, Team Rocket
Base B3F, the Goldenrod Underground and its warehouse, and Fast Ship B1F. Towns are not randomized.
Left vanilla on purpose: National Park (its layout is shared with the Bug Contest version, which
has its own events), Team Rocket Base B1F (almost all of it is locked by the traps) and B2F (laser
barrier puzzle), the Underground switch room (its doors change as switches are flipped, and only
the starting state is checked), Ruins of Alph, Radio Tower, Dragon's Shrine and Celadon Mansion roof
(no layout found, or nothing left to change).
Route 45 is randomized since ledge surroundings stay vanilla (its ledge staircases are kept and the
space between them is rebuilt). Not randomized: Ice Path B2F Mahogany side (its ice puzzle depends on where the boulders dropped
through B1F's holes land). A map that fails all its tries in a given seed stays vanilla
for that seed; in testing every other map succeeded on every seed.

Area size: each land area grows to about its vanilla size x 3 (randomly 0.6-1.3 times that)
before it is thinned into corridors, but never less than its share (by vanilla size) of 80% of the
map's unlocked blocks (`fill`). Small areas on big empty maps (Tin Tower's roof, Silver Cave room
3, Diglett's Cave) then wind across the whole map instead of a corner of it; routes, which already
span their map, are unchanged.

Block style: a map's blocks are picked from every map that uses its tileset, except for maps with
a style of their own (`OWN_STYLE` in maprando/model/catalog.py): Tin Tower's roof and Fast Ship B1F only
use blocks from maps with the same tileset and the same kind of area, so the roof keeps its golden
tiles and the ship does not turn into Lighthouse stone and grass.

Hidden items reached from two separate areas sit in the wall between them; hidden items reached
only by surfing sit in a rock at the water's edge; wall objects with no room on the floor (fruit
trees) may stand in the wall beside their area. An area that keeps failing to attach keeps its
vanilla blocks and the rest of the map is still generated around it.

Routes additionally keep: the edge rows/columns that join a neighbouring map (so connections line
up), buildings and gatehouses (blocks flooded out from each door), signs, ledges and the blocks
they land on, Strength boulders, smashable rocks, cut trees and blocking NPCs that sit between
two areas. New gates (cut trees, ledges, water, one-tile object passages) are built only where the
vanilla logic needs them. Grass is placed per area at roughly the vanilla amount; headbutt trees
face walkable ground.

Gyms (maprando/generators/gyms/standard.py, `GymGenerator`): every supported gym has no requirements at all, so the
only thing that matters is the walk from the door to the leader. Violet, Azalea, Goldenrod, Pewter,
Celadon and Fuchsia are designed from scratch on the tile grid and drawn afterwards. Only the
entrance (statues, guide, door), scripted spots and the gym's frame (back wall, solid edge blocks)
stay. Walls are laid as straight runs of tiles; gyms that are mirror-symmetric in vanilla (all but
Goldenrod) stay symmetric. A run is kept only if every block it touches can still be drawn with one
of the gym's own blocks. Simulated annealing (3 chains, 40 steps per free tile) scores the grid as a
level: + the walk to the leader (up to about 1.6x vanilla), + a second way to the leader, + trainers
whose view crosses a way; - floor nobody reaches, dead ends, lone wall tiles, ragged run ends, big
empty floor, and a wall share far from vanilla's. The leader keeps the vanilla spot (the dais; also
Whitney's "stops crying" scene is a coord event below her). Trainers with mirrored facings stand as
mirror twins, twins as a pair, and nobody stands - or stops after walking up to the player, either
facing - where it would cut the gym in two. Blocks are then chosen per 2x2 pattern from the gym's
own blocks and re-chosen for the most natural seams. The best designs go through the usual verifier.
Cianwood uses the same designer with a Strength gate: anchors that need Strength in vanilla (Chuck,
the black belt beside him; the boulder next to Chuck stays put) must end up behind a wall across
the hall whose only gap holds the row of boulders. The search can lay such a wall (with two rows of
floor on both sides of the gap: the middle boulder is pushed two tiles so the gap opens beside it).
Touching boulders count as one gate in `boulders.bad_boulders`: from each side, some push of some
boulder in the group must open the way to every area around it (vanilla's own row only ever works
through its middle boulder).
Cianwood's gate boulders no longer stay in their vanilla row (test32): each stands on its own tile,
the first on the way to Chuck, the others anywhere (another gap, the way itself, or just in the way).
With them in place the way to Chuck must be shut; every boulder can be walked up to without
Strength; some boulder can be pushed from outside into floor inside; touching boulders work as a
group and every group must open from each side with the other boulders as walls (as
`bad_boulders` checks); nobody stands within two tiles of a boulder. The gate wall's gap is 1-3
tiles wide.

Fuchsia is a maze (test33, `MAZE_GYMS`): its walls are invisible, so the designer rewards corridor
tiles and dead ends, makes open floor costly, lets the walk to the leader grow to 4x vanilla and is
not mirrored. Janine's look-alikes only spin and have no sight (you talk to them), so they are walls:
each stands where the walk to the leader gets longest, everyone still reachable and no floor shut
off behind anyone; the design is scored on the walk with people in it. Janine moves too: she stands
at the maze's far end (the reachable tile farthest from the door, a dead-end tip if there is one).
Walks to her: about 45-55 tiles with people, vanilla 13.

Strength boulders are not compared in the requirement matrix (`requirement_matrix(mm, boulders=False)`,
in the generator and the readback): nothing is there, and every way past a boulder is an anchor of its
own, so a boulder reachable only with Strength is fine. What needs Strength to reach still must.

Cianwood's gate is a puzzle (test34, `optimize_gate`, maprando/checks/pushes.py): the layouts are scored as
before, then for the best two a joint search flips wall tiles (drawable blocks only) and moves the
gate boulders for 12 x the fewest boulder moves to Chuck + the layout score. A move is a run of pushes
of one boulder (AAABBCA is 4 moves); `min_moves` is a 0-1 search over boulder positions and the
player's area (pushing the boulder pushed last costs nothing). Areas that only open after some pushes
are fine (like a ledge's) as long as no person stands there: trainers outside the gate stand where
no push is needed, Chuck's side where the leader is. Then no position may shut the player in
(`player_traps`, people as walls), and the drawn map is checked push by push again (`gym_puzzle`,
also in the readback, which replaces the 'cannot be pushed out of the way' check there). Vanilla takes
3 moves; test runs 3-6.

Speed (time2, CPU-seconds per map): Cianwood 100 -> 6, Blackthorn 2F 65 -> 25, Goldenrod 35 -> 7; all maps
420 -> 228. Gyms score layouts alone during the search and place people (and a gate's boulders) on the
best layouts afterwards (Fuchsia, a maze, still places its people at every step); the push searches and
the trap check work on bitmasks (`min_moves`, `player_traps`, `push_search`); Blackthorn runs 2 searches
instead of 4. `MAPRANDO_TIMES=<file>` logs each map's time; slow maps are started first. A crash in
one map's generator now keeps that map vanilla (logged) instead of failing the build.

Ice Path B1F's boulder room from chambers (ice1, maprando/generators/chambers.py): a chamber is a block-aligned
piece with one doorway (and a porch tile outside it), two boulders and their two holes, designed whole -
walls, holes and boulders together - by annealing with exact checks: the fewest boulder moves to drop
both (`pushes.drop_moves`: A* over every boulder at once, dead spots pruned; AABACD = 5 moves), holes
reached without pushing ('open', vanilla's holes 1-2) or not ('gated', holes 3-4, Strength), no boulder
reaching the other's hole, the doorway or the porch, no position that shuts the player in. A library of
236 pieces (3-4 moves each; sizes 4x4, 4x3, 3x4 blocks, every doorway side, both kinds) is made ahead by
`tools/make_pieces.py` (resumable, about 30 minutes) into maprando/data/IcePathB1F_pieces.json. A build places
an open and a gated piece on the room's free blocks (nothing that stays vanilla may touch a piece; the
ladders stay), picks library pieces that fit (more moves more likely), lays corridors joining both ladders
and both porches (rock all round each piece but the porch; a long walk between them, little open floor),
draws it from the vanilla room's own blocks, and checks it as before. 6-8 boulder moves (vanilla 4), a few
seconds. The readback now reports each boulder-hole room's exact move count. Blackthorn 2F's builder
(gymboulders) also scores rooms on the exact count, polishing among valid rooms for 800 steps: 4-5 moves.

Submoves (sub1): within a move, a run of pushes in one direction is a submove - A up x3, B down, B up,
A right, A left, A right is 3 moves and 1 + 2 + 3 = 6 submoves. `min_moves` and `drop_moves` minimise
both together (cost moves x 10000 + submoves: the fewest moves, then the fewest submoves among those;
another push the same way costs nothing, a turn of the same boulder 1 submove, another boulder a move
and a submove; `detail=True` returns both). Every puzzle score uses submoves as a tiebreak (Cianwood 12
per move + 1 per submove, Blackthorn / Ice Path rooms 15 + 1, pieces 8 + 0.6); the library pieces carry
their submoves (`tools/rescore_pieces.py` re-checks the library: 3-4 moves, 3-8 submoves) and are picked
by both; the build log and the readback report both counts.

Blackthorn 2F from chambers (ch1): the chamber builder is per map (`piece_kinds`: the open and gated
holes from vanilla's requirements; boulders with no hole go inside the pieces as blockers, the larger
share to the gated piece - its gate). Blackthorn draws only floor and pillars along a block's top, left
or right half, so nothing can be rock just above a piece: `rock_sides_for` finds such sides from the
tileset and a piece seals them with its own edge (rock but for the doorway; steps that open it are never
taken), and only a piece's floor forces rock outside it. Pieces and corridors also step whole blocks to
another drawable shape (one tile at a time would pass through undrawable ones). Stairs (both maps' now)
and Blackthorn's trainers (walls in the corridors, never shutting anything off, never beside the stairs)
are placed with the corridors. Libraries: maprando/data/<map>_pieces.json (`tools/make_pieces.py --map`);
Blackthorn 240 pieces of 3x3, 3x4, 4x3 blocks, 3-5 moves each. Rooms take 2-7 s and need 6-8 moves
(vanilla 6); the builder before (gymboulders) is the fallback.

Ecruteak (maprando/generators/gyms/ecruteak.py): the invisible floor is drawn with only four dark blocks (pits along
the top, left or right, or dark wall), so a design picks one per dark block; annealing rewards the
walk to Morty, turns on it and unseen side branches. Trainers in the dark move to corridor ends,
looking down the corridor. Pit warps move onto the new pits (spares double up); the two pit warps
vanilla never lets you reach go on pits nobody can reach, and the one warp on plain floor keeps its
tile. A pit that drops you on its own map and that no warp leads to is never an entry
(model.arrivable), and the readback's "objects next to warps" check only counts warps you arrive by.

Mahogany (maprando/generators/gyms/mahogany.py): ice designed as a grid of ice, single floor tiles (you slide onto them
and stop) and two-tile pillars, drawn with the gym's own blocks; scored by presses to Pryce (up to 2x
vanilla), with no trap tiles, everyone reachable and stop tiles / pillars near vanilla's share; then
checked with the real movement model (matrix, traps, trainers, jumps).

Blackthorn 2F is a boulder room like Ice Path B1F: each boulder may only fall through its own hole
(stonetable), the holes move with their blocks (where a boulder lands on 1F is fixed by 1F's
changeblocks), and the readback runs the same puzzle check on both maps. Its own blocks are only
half-walls (no solid block), so the from-scratch builder (which carves rooms out of solid rock)
cannot draw it: the vanilla room is reshaped (`BoulderRoom`, up to 8 sub-seeds). Blackthorn 1F stays
vanilla (the fallen boulders' lava bridges lock 41 of its 45 blocks).

Boulder rooms, fixed (test29d): the wrong-hole check stopped as soon as a boulder reached its own
hole, so other holes it could also reach went unnoticed (Blackthorn test29: boulders 2 and 3 into
each other's holes); it now searches every push (`holes_reached`). Blackthorn's three boulders with
no hole (not in the stonetable) were not checked at all: one on a hole does nothing and blocks it
until the player leaves. They now move with the design and may reach no hole; the solver may push
them aside (vanilla needs that). People in the room are walls (a boulder was placed on a trainer)
and part of the area grouping (a boulder may not wall a trainer off). Indoor rooms (no connections)
keep only their top block row fixed, not every edge block (Blackthorn is 5 blocks wide: two of its
holes sat in the fixed edge columns); a hole leaving a block the design may not otherwise change
leaves that block's shape with the hole tile turned to floor. The search rewards being unlike
vanilla (holes, boulders and blocks moved) and runs 900 steps for Blackthorn (300 for Ice Path).
Blackthorn 2F: about 24 of 45 blocks change, 2-3 holes and 4 boulders move (was 1-9 blocks).
`randomize.py --reuse-maps` reuses a seed's map step from an earlier run.

Blackthorn 2F from scratch (test30, maprando/generators/gyms/blackthorn.py): every block below the top wall row is
designed anew and everything moves: both staircases and the three holes (as their own blocks), all
six boulders, and the two trainers (walls: they stand still and see one tile). Blocks are only the
room's own (floor; pillars along the top, left or right half; stairs; hole): other maps with this
tileset bring plants, carpets and lava, and none has a matching solid block. The design is annealed
with an energy that counts what is still wrong: what each staircase reaches without pushing (and
the Strength-gated hole and boulder it must not), wrong holes over every push, and how close each
boulder gets to its own hole; valid designs are scored by solution length and walls. A run stops
after 25 distinct valid designs; 4 runs, the best drawn designs must match vanilla's requirement
matrix and pass the boulder-room and trainer checks (reshaped room as the fallback). About 30-60 s
per seed; test30 changed 32 of 45 blocks. `MAPRANDO_CACHE=<folder>` keeps each generated map so a
rerun with the same seed reuses it (development only: it does not notice code changes).

Staircases in boulder rooms: a staircase or ladder is land for a boulder (the push code only asks for
LAND_TILE collision, and COLL_LADDER is), so a boulder can be pushed onto and across one; the player
never stands on it (stepping on it warps). The wrong-hole checks (`stair_cells`, `boxonly` in
`_push` / `push_search`) let boulders cross them, in the builder and the readback check. Vanilla does it too:
Blackthorn 2F's boulders 2 and 3 can each be pushed onto a staircase.

The solver pushes boulders over the stairs too (`boxonly` in `_push` / `_joint`, in the builder and
the check). Traps (`player_traps`): every position the player can push the boulders into from the
ways in (a boulder in its own hole is gone) is explored, and a position from which no sequence of
pushes leads back to a staircase or hole (pushing a boulder off the stairs steps onto them: a way
out) fails the room: a gym has no Fly, Dig or Escape Rope. Blackthorn 2F, all six boulders together
(about 80,000 positions, ~12 s); Ice Path B1F's boulders roam the whole map's plain floor, so every
boulder alone and every pair together are checked (a trap needing three boulders at once would go
unnoticed). Both builders reject trapping rooms; vanilla has none.

Saffron (maprando/generators/gyms/saffron.py): walls stay; the 30 teleport pads (15 two-way pairs between 9 rooms)
are re-paired, never two pads of one room, scoring pad hops from the entrance room to Sabrina's
(test21: 7, vanilla 4), trainer rooms off the quickest way, and no more one-pad dead-end rooms than
vanilla. The movement model does not follow warps within a map (each pad's requirement row covers
only its own room), so every room being reachable is guaranteed by the generator and checked again
by the readback (`pad_rooms_ok`). Warp destinations within a map are now written by patch_map and
restored by reapply.py like coordinates. Viridian uses the gym designer.

Kept vanilla: Cerulean (the Rocket scene, swimmers in the pool), Seafoam (tiny cave), Vermilion (the
trash-can puzzle locks most of it), Olivine (Jasmine's dais and carpet are its only full-floor
blocks), Blackthorn 1F. Objects that only turn on the spot in a script (Fuchsia's ninja spin) no longer lock
their block. `reapply.py` now also restores maps logged as 'randomized (...)' (gyms, ice-only rooms).

Locks on routes (test22): route edges that connect to a neighbouring map keep only their openings
(edge blocks whose outermost tiles you can walk out of); the rest of the edge is free, but its
outermost tiles stay wall (`seal_edges` in the plan, `edges_ok` in verify), so no new way out leads
into the neighbour's wall. Ledge surroundings are no longer locked up front: verify's jump check
already rejects a design with a bad jump; a map that has found nothing after half its tries locks
them then (`ledge_lock`). Blocks locked on routes went from 25% to 15%; tiles whose walkability
changed went from 28% to 32% overall (Route 1: 8% -> 38%, Route 22: 6% -> 39%, Route 21: 18% -> 43%).
Ledges (test24): a ledge run no longer grows the locked area (the old rule locked every block
with a ledge tile next to a locked block, so one lock spread along whole runs: Route 4 kept 80 of
180 blocks); the generator builds its own ledges where its areas need a one-way drop. What stays is
a ledge tile inside a block locked for another reason, with the tiles you jump from, over and onto
(the landing is two tiles past the ledge tile in the model), to a fixed point, also for locks
added while generating (`ledge_close`, run before every try). Vanilla's decorative ledges that
jump into a wall sit in pockets you cannot reach: free tiles around such a pocket stay wall
(`edge_walls`), so no layout opens a way to them. Tiles whose walkability changed: 28% (test21) ->
35% (test24) over all routes; Route 4 1% -> 15%, Route 10 South 13% -> 27%, Route 46 23% -> 35%,
Route 9 26% -> 39%. Route 45 (26 ledge drops) still finds nothing in half its tries and falls back
to the ledge lock; Routes 10 North and 19 are mostly locked by other rules.

Ledge loops (test28-29): on maps whose vanilla has a real ledge network (18 or more ledge tiles
whose jump starts and lands in the same area: Routes 1, 3, 4, 7, 9, 22, 29, 38, 39, Silver Cave
outside and Room 1; since loops3 any map with 10 or more), one land area is split into 2 to 6 parts
(the cap is 2 plus one per 8 such tiles past 10, and the area's free tiles / 40; the top two values
of the range are picked, more ledges being better) along the map's long side, each with at least 30
free tiles (`split_zone`), with 1 to k-2 extra one-way ledges between parts on top of the cycle. If
no layout is found, the map tries one part fewer, then no loop. loops3: 15 maps with a loop. The parts are joined only by
one-way ledges in a cycle (A -> B -> ... -> A): every part still reaches every other, so the
requirements stay the same, but you cannot walk between them; you drop down one ledge and go round
to another to get back. Links to other areas and anchors go to the part whose tiles they touch (else
a random one). Each loop ledge is drawn 1-3 blocks wide (mostly 1-2) where the ground on both sides can
grow with it (`widen_ledges`): a drop, not a wall of ledges. verify checks that no plain walk joins two parts (`loop_ok`). A map with no valid
layout in a quarter of its tries is generated again without the split (`generate_cave`).

Doors move (test23): for the logic a house is only its door block (door tile, wall around it, left
stepping down onto the tile below). Door blocks are movable units (`door_units`): each goes into
unclaimed wall right above a floor block of the area you step out into, and the tile below the door
stays floor; the rest of the house is wall the generator may change (the building flood and the
"cells around kept signs/objects" rule skip doors that move). House signs and people standing by a
door no longer pin it (signs keep their own blocks, people are placed anew); doors next to a coord
event or a Fly landing spot, on an edge row, or in maps whose scripts read the player's coordinates
stay. About 20 of the ~35 route doors move per seed (Bill's cottage, Routes 2, 26, 28, 30, 39, 43,
Lake of Rage, ...); a door that finds no wall above its area in 15 tries keeps its vanilla spot.

Hidden items that do not reach a dead end (investigated on test24-26; Rock Smash and Ice Path items
excluded on purpose): an item reached from land whose own area has no room for a dead end, or that
is reached from two areas, may now sit at a dead end in any land area that needs exactly the same
from every entrance (`_retarget_items`, matched against the requirement matrix); an item reached
from one land area and from the water may leave a locked block for a dead end in its land area.
What still keeps items where they are is the logic itself: Tin Tower 5F's two items are each the
only thing in a 3-tile pocket that is kept as part of a gate, and no other area needs the same;
Route 9's Ether and Dragon's Den B1F's Revive are reached from two areas, one easier from some
entrances and the other from others, so they must stay in the wall between them (an item on a
dead-end tip is reached from one side only); Route 34's Super Potion's land area is kept with the
gate object beside it except for one tile, and its other approach is by water (a dead end there
would need Surf). Gyms drawn by the gym designer retry with up to three more sub-seeds when every
design fails the checks (Cianwood on test25); Ecruteak's trainers keep clear of every warp you
arrive by (the landing spot after a fall included).

Phantom areas (test27): the model let you walk where the game never lets you, in two ways.
(1) Arriving on a door, staircase or cave tile (collision $71, $79, $7a, $7b) the game walks you
one step down (engine/overworld/player_movement.asm, .warps); the model let you step off it in any
direction, onto the cliff top around a cave mouth. Now only down (reach.EJECT_DOWN). (2) Every
walkable tile along a connected edge counted as a way in, whether or not anything across the edge
could be walked from. Now an edge tile is a way in only if the tile across it, in the neighbouring
map, is reached from somewhere you can really arrive (warps, Fly spots), over every map joined by
edges (model._world_reach, computed once on the vanilla maps). Areas walkable on both sides of an
edge but reached from nowhere else (Route 9's bottom strip and Route 10 North's left strip, each the
other's way in) are no longer reached. Together: 8158 tiles in 48 maps were scenery the model took
for floor (mountain and cliff tops, route borders; Dark Cave's Violet entrance 746, Route 26 491,
Route 45 437, Route 25 402, Route 3 392, ...). The generator no longer has to keep them connected,
so those maps are much freer; items next to them now have one approach (Route 9's Ether and Dragon's
Den B1F's Revive are at dead ends). Edge openings in the generator are the model's real openings;
other edge tiles must not become newly reachable (`edges_ok`).

After the Item Randomizer runs, `reapply.py` copies back our blocks and event coordinates for
randomized maps: some of its patches rewrite whole event records at vanilla positions (trainer
facing, Route 30's battle, Route 32's hidden item next to Frieda). Only the coordinates are
restored; its other changes stay. An event's coordinates (and a warp's coordinates and destination)
are restored as one unit: restoring byte by byte left a coordinate we happened to keep at its
vanilla value with the Item Randomizer's value (Route 32's hidden Great Ball, which its mode moves
one tile left: our y with its x put the item on open floor beside its dead end). `readback.py <rom>` re-extracts every map from a final ROM
and checks it against vanilla logic.

Kept exactly as vanilla: warp blocks and the doorway/cave-mouth block drawn next to them, holes
and warp panels, coord-event blocks, objects moved by scripts, blocks changed by `changeblock`
scripts (plus neighbours), and any zone that cannot be rebuilt in the space available. Objects
and hidden items are never placed on or next to a warp.

## Blackthorn / Cianwood: back to the earlier generators
The move-maximising designs (Blackthorn chambers, `BoulderGymScratch` move energy, Cianwood
`optimize_gate`) made dull puzzles, so builds use the earlier methods again. The newer code stays
behind switches: `build.CHAMBERS_BLACKTHORN`, `BoulderGymScratch.classic` (True = earlier scoring),
`GymGenerator.puzzle_gate`. Every Strength-gate gym (Cianwood) is still checked push by push with
`gym_puzzle` whatever the switch: a layout is rejected unless Chuck is reachable and no reachable
boulder position shuts the player in.

## Ice Path 1F: decisions objective
`IceGenerator(objective='decisions', spread=True, edge_trivial=True)`: no two placed rocks touch,
even diagonally; the score is the number of real decisions on the solution. A stop with only one
way on counts 0, and so does a choice where one of the two slides runs into the room's edge.

## Blackthorn / Cianwood: harder, same style (submoves)
Both gyms keep the earlier designs' walls; only the boulders are moved, for more **submoves**
(the fewest runs of one boulder pushed one way that solve the puzzle; `pushes.min_moves` /
`drop_moves` with `weight=0`; moves themselves are not scored).
- Cianwood: `GymGenerator.puzzle_gate = True` with `gate_walls = False` - `optimize_gate` moves the
  gate boulders only (the layout is the earlier search's), for the most submoves to Chuck.
- Blackthorn 2F (`BoulderGymScratch.HARD_MAPS`; not Ice Path B1F's fallback): the valid earlier
  (classic) designs are ranked by submoves, then the best few (`hard_designs`) have their boulders
  moved one at a time (`harder`, `hard_steps`, at most `hard_time` s) as long as every earlier check
  still passes and the submoves do not drop.
Readback prints the fewest submoves for both puzzles (and the moves/submoves at fewest moves).
- Blackthorn 2F also builds chamber rooms again (`build.CHAMBERS_BLACKTHORN`, `CHAMBER_TRIES` seeds):
  pieces are picked by their fewest submoves (`fewest_submoves` in the library, added by
  `tools/subscore_pieces.py`; `chambers.SUBMOVE_MAPS`). The chamber rooms and the earlier design
  compete on the finished room's fewest submoves (`pushes.room_submoves`); a tie keeps the earlier.
- Cianwood: `gate_layouts = 4` layouts get their boulders moved (2 gave a 1-submove gate on some seeds).
- Cianwood: the gate boulders are designed with the walls (`GymGenerator.joint_gate`): the layout
  search's state holds the boulders too (a step moves one, `joint_move`; a new gate wall puts them in
  its gap), and every step scores the layout plus `joint_w` per submove to Chuck (`gate_submoves`).
  The best designs then get a short boulder-only polish (`joint_polish`), people, and the trap check.
- Cianwood: every gate boulder touches another, diagonals included (`gate_touch`; the boulder kept
  beside Chuck does not count). The search counts `joint_lone` per lone boulder and moves boulders
  beside another half the time; the polish and the final check reject any lone one; readback reports it.
  `joint_w = 16` per submove (clustered gates open more easily; 8 gave 1-5 submoves, 16 gives 4-5).

## Hidden-item nooks and cut trees
A nook must stay a one-tile dead end whatever the player clears: a cut tree (or whirlpool) beside
the item counts as open (`model.open_tile`, from the block's after-Cut/Whirlpool collision). Used by
`hidden_ok`, `nook_count`, `_vanilla_nook` and readback's dead-end report; `carve_nook` and
`make_nook` skip spots beside a gate device's block (`_device_cells`). (test37: Lake of Rage's hidden
item sat beside a cut tree, so cutting it opened the nook.)

## Nobody starts in a wall
Objects that stand on floor in vanilla (trainers included) are never placed in a wall: the
"no room on the floor" fallback that puts a wall object inside the wall is only for objects that
stand in a wall in vanilla too (fruit trees, NPCs behind counters). `verify` rejects a map where
one does (`standing_ok`, `gen.standing_in_walls`), and readback lists "people and items starting
in a wall" (objects buried out of reach on purpose are not counted). (test38: Union Cave 1F's
Firebreather Ray stood in the rock.)

## Cutscene actors nobody may talk to
Burned Tower 1F's rival is a trainer-type object with no talk script (`ObjectEvent`): talking to
him crashes the game, so he may only be reached through his coord event (the rival battle trigger).
`gen.guarded_actors` finds such objects (only this one in the game); every block of the actor's
vanilla pocket and of the walls bounding it is locked (`guard_pocket`: Burned Tower's runs from the
rival up to (7,5)), and `verify` rejects a map where a tile beside one can be reached from the
entrances without stepping on a coord event (`guard_leaks`; Cut/Rock Smash allowed, people out of
the way; only warps on warp tiles count as walls - the leftover G/S warps on plain floor are floor). Readback: "cutscene actors you can talk to without their scene (crash)". (test39: the
floor west of the rival was opened.)

## No way round a scene trigger
Coord events whose scene moves the player are gates (`scenes.gate_triggers`): Route 32's
Cooltrainer (walks you back to Violet without the Zephyr Badge), Burned Tower 1F's and Sprout
Tower 3F's rival scenes, Mt. Moon Square's Clefairy dance, Team Rocket Base B3F's boss/rival.
With those tiles as walls, the generated map's requirement matrix over fixed places (warps, map
edges, Fly spots, coord events; items and people are placed anew on purpose) must equal vanilla's
(`gen.trigger_matrix`, `verify`'s `triggers_ok`). Readback: "ways round a scene trigger that moves
the player". (test39: Route 32 had a second way south past the Cooltrainer.)

## Goldenrod Underground shopkeepers move
Only the shopkeepers matter, not their stalls: a person standing in a closed pocket behind a
counter (the Underground's Bargain Merchant, Haircut Brothers and Granny) is placed anew like
anyone else, anywhere on the floor of the area he is talked to from; the counter and stall are not
kept. The counter lock (person, counter and talking spot kept as in vanilla) now applies only to an
object a counter gives a second way to (Rocket Base B3F's item ball); someone the counter gives
nothing extra (Teru) is placed anew as usual.

## Ledge steps merged
An area that is only a step between ledges (A drops into B, every way out of B is a free drop into
C) and holds nothing (no person, item, warp, door, trigger, Fly spot; signs do not count) is merged into C before
generating (`merge_ledge_steps`, repeated; checked against vanilla's raw zone links `zg.edges`, gate
people included): the requirements stay the same and the pointless ledge goes. 35 merges over Dark
Cave (both), Tin Tower 5F, Routes 5, 26, 45 and Silver Cave outside; a map that finds no layout with
its merges in a quarter of its tries is generated without them (`MergeError`: Tin Tower 5F, Route 45).

## Plain signs do not count
A sign whose script is a single `jumptext`/`jumpstd` (`model.plain_signs`) is not an anchor: nothing
needs to reach it, so it neither pins the layout nor blocks a merge. bg events whose scripts do
anything else (the Underground's basement door) and hidden items still count.
Plain signs no longer lock their blocks either (the sign graphic is just scenery; the bg event
keeps its vanilla coordinates), and doors on a map's edge row move like any other door (Route 29's
gatehouse to Route 46; other warps on an edge row still stay). Locked blocks over all generated
maps: 1628 -> 1392 (Route 42 -14, Routes 13 and 30 -10, Routes 32 and 39 -9, Route 29 16 -> 9).
Plain signposts move with their block (`sign_units`, placed in `place_warps`): a signpost block
(post tile, floor around it) goes to a floor block of the area it is read from, away from the
border and other warp blocks, with the tile it is read from kept floor (in the block, or in a
floor block of the same area); the bg event moves with it (`sign_place`) and verify checks it can
still be read (`signs_ok`). Signs set into a wall (Rocket Base posters, building signs) keep their
wall locked; areas with fewer than 6 free blocks keep their signs where they are (Dragon's Den's
shrine island); a map that finds no layout with its signs moved in a quarter of its tries is
generated with them in place (`SignError`: Route 13). signs3: 47 of 101 plain signs moved, all readable.

## Cave mouths move like doors
A cave mouth (collision 0x7b: Mt. Mortar's, Union Cave's, Dark Cave's entrances on routes, and
the like) is a door unit too: the cave tile sits in a row of wall, entered walking up and left
stepping down, so its block goes into unclaimed wall right above a floor block of the area outside
(`door_units`); the tiles above it in the block (the cliff top: floor collision nobody reaches) are
planned as wall (`cave_bids`). caves1: 208 warps moved (171 before), every check fine.

## Cleanup for the web app

The fallback boulder-room generators (`ScratchRoom`, `BoulderRoom`'s own search) and other code
that builds never ran were removed; maprando was regrouped into io/model/checks/generators, the
cave generator split into one module per step, and `Randomizer` added as the entry point. Each
step gave a byte-identical ROM for the same seed.
