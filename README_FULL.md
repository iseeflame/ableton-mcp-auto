# ableton-mcp-auto — notes and gotchas

The detail behind the tool list in the [README](README.md): how Live's API actually
behaves, and the traps that cost real time when they are hit blind. Nothing here is
needed to get started — it is what to read when something does not do what you expect.

## Editing notes without losing automation

Upstream can only append notes, so changing a part meant deleting the clip and building
it again — which throws away every automation envelope written into that clip.

`remove_clip_notes` and `modify_clip_notes` remove that trap. The clip is never
destroyed, so its envelopes stay put:

- **Replace a section**: `get_clip_notes` to see what is there, `remove_clip_notes` over
  the window being replaced, then `add_notes_to_clip` with the new material. The window
  is a time and pitch rectangle, so one bar is `from_time=4, time_span=4` and the hi-hats
  alone are `from_pitch=42, pitch_span=1`.
- **Adjust what is already there**: `modify_clip_notes` with the `note_id` values from
  `get_clip_notes`. Send only the fields that change; the rest are left alone. Values are
  absolute, so transposing means sending each pitch minus 12.

note_ids describe the clip's current contents and are reissued when notes are removed and
re-added, so re-read between rounds of edits rather than reusing a stale list.

## Device order

Devices load at the end of a chain, but order changes the sound. `move_device` reorders an
existing device — settings and automation travel with it, which reloading would lose — and
can also hand a device to another track.

Live quietly settles for the nearest legal position when the requested one is impossible,
so the move is checked with `find_device_position` first: the reply says where the device
actually landed and flags any difference, and moves that cannot work at all (an instrument
onto an audio track) are refused rather than relocated.

## Working inside racks

`get_track_info` stops at the top level, which is not where a drum kit keeps anything
interesting. A factory kit is usually an Instrument Rack holding a Drum Rack whose every
pad is a chain with its own devices, so "put a delay on the snare and nothing else" is
several levels down from the track.

`get_device_tree` walks that structure and hands back a path for every node. Indices
alternate between devices and chains: `0` is the first device on the track, `0.0` its
first chain, `0.0.1` the second device inside that chain. An odd number of segments
always names a device, an even number a chain. Chains belonging to drum pads also report
the MIDI note that triggers them, so a snare can be found by note rather than by counting.

Those paths work throughout: `load_device_into_chain` puts a plugin on one pad,
`move_device` takes `from_path` and `to_path`, `delete_device` takes `device_path`, and
every parameter tool — reading, setting, targeting by display value, and all three
envelope operations — accepts `device_path`. A plugin on a single drum pad is therefore
as controllable as one sitting on the track, automation included.

Two things Live enforces, both reported rather than worked around: a chain holds one
instrument, so an existing one must be deleted before another will fit, and the browser
can only append to a track — `load_device_into_chain` loads and then moves, in separate
steps, because a device added during one Remote Script turn is not yet visible to code
later in that same turn.

## Building a drum kit pad by pad

`load_sample_to_drum_pad` selects a pad and then loads, which is what a person does by
dragging. It only works on a pad that already holds something: an **empty pad has no
chain**, so there is nothing for a device to be moved into, and Live responds to the load
by replacing the whole rack with a Simpler. To assemble a kit from scratch, start from a
factory kit whose pads are populated and swap the samples out — delete the Simpler in the
pad's chain, then move the new one in, since a chain holds one instrument.

## Reaching your own sample folders

Upstream resolves a browser path by matching its first segment against a *named attribute*
of the browser — `instruments`, `drums`, `sounds`. The folders you add to Live's Places
sidebar are not attributes; they sit in a list, so a folder called `Scale` had nothing to
match and was invisible.

`get_browser_items_at_path("places")` now lists those folders, and
`"places/<folder>/..."` descends into one like any other path. Loading works too: the URI
resolver searches user folders as well, which it previously did not, so a Place could be
browsed but nothing in it could actually be loaded.

A `userfolder:` URI describes its own location — the Place's URI before the `#`, then
colon-separated segments — so it is resolved by descending that path rather than by
searching. Searching meant crawling every folder in the sidebar, and if one of them is a
whole drive a single sample load took roughly half a minute; descending brings it under
a second.

## Asking Live what it can do

`describe_live_api` is a development aid, not a music tool. Live's Python API is
Boost.Python, which stores each method's real signature in its docstring, so this answers
"what arguments does `move_device` take" from the build in front of you rather than from
documentation that may not match it. Paths start at the song by default; pass
`root="app"` or `root="browser"` to inspect those instead. It also lists members that *raise* on access — Live
signals an inapplicable property by throwing, which is easy to mistake for a bug elsewhere,
and which cost this fork several debugging rounds before the pattern became obvious.

## Mixing in real units

Most Live parameters are stored 0.0–1.0 and only *display* real units, usually on a curve — a
filter's frequency is 0.0–1.0 for 10 Hz–22 kHz, and Utility's width is 0–2 shown as 0–400%. Raw
numbers are therefore useless for anything a musician would state in units.

`set_parameter_to_display` closes that gap: give it `2000` and it lands on 2 kHz. It binary-searches
inside Live using `str_for_value()`, which evaluates a candidate *without assigning it*, so the
whole search costs one round trip and never disturbs the parameter. Direction is read from the
endpoints rather than assumed, units are normalised (Live switches between Hz and kHz mid-range),
and an unreachable target returns the nearest value together with the range it actually spans.

Use `convert_display_values` when the numbers are destined for `set_clip_envelope`, whose
breakpoints need raw values: convert `[200, 8000]` first, then write the sweep.

`delete_device`, `delete_clip` and `delete_track` are destructive but covered by Live's undo. Track indices
shift down after a deletion, so re-read the session before deleting another by index. `delete_track` also
removes return tracks through their negative index; the master is refused.

Throughout, **`device_index: -1` addresses the track's mixer device**, so volume and panning are
automated through the same calls as any plugin parameter.

## How automation works, and what it can't do

These are **clip envelopes** — automation stored inside a Session clip, which plays back with
that clip. Automation drawn on the Arrangement timeline is a different mechanism and is
essentially not writable through Live's Python API; this fork does not attempt it.

Live's API exposes only `insert_step(time, length, value)`, which writes a *flat* step. So:

- `interpolate: false` (default) — each breakpoint becomes one flat step held until the next
- `interpolate: true` — the gap between breakpoints is filled with `step_size`-wide steps that
  walk linearly from one value to the next, approximating a ramp

A single call is capped at 4000 steps, so a very small `step_size` over a long clip fails loudly
instead of stalling Live's main thread.

Live cannot enumerate an envelope's breakpoints, so `get_clip_envelope` reconstructs the curve by
evaluating it at evenly spaced times and reports each sample's display value. Use it to confirm a
sweep actually ramps rather than sitting flat.

## The breakpoint at time 0

Live gives every new envelope a breakpoint at time 0 holding the parameter's current value, and
`insert_step()` cannot overwrite it. Left alone, a sweep starting at 0.15 keeps a spike of
whatever the knob happened to be resting on — an audible click at the clip start, and one that
looks like success in the write's return value.

`set_clip_envelope` handles this by moving the parameter onto the first breakpoint's value before
creating the envelope, then putting it back once the steps exist. This has to happen in a
*separate* command: the seeded point reflects the parameter as it stood when the Remote Script's
main-thread turn began, so assigning it inside the same turn has no effect. Anything driving the
Remote Script's socket directly, rather than going through this MCP server, has to do the same
two-step dance itself.

Example — a filter sweep across a 4-beat clip:

```json
{
  "track_index": 0, "clip_index": 0, "device_index": 0,
  "parameter_name": "Filter 1 Freq",
  "points": [{"time": 0.0, "value": 0.15}, {"time": 4.0, "value": 0.85}],
  "interpolate": true
}
```

## Parameter values are usually normalized

**Always call `get_device_parameters` first.** Most Live parameters run 0.0–1.0 internally
while *displaying* real-world units — Wavetable's `Filter 1 Freq` is 0.0–1.0 for 20 Hz–20.5 kHz,
not 20–20500. Passing `8000` because you meant 8 kHz targets a value 8000× past the maximum.

`get_device_parameters` reports `min`/`max` alongside `display_min`/`display_max` so the real
range and its meaning are both visible. Out-of-range values are **rejected with an error naming
the valid range** — deliberately, since clamping them silently turns a unit mistake into a
successful-looking call that writes a flat envelope at the maximum.

## Troubleshooting

- **Connection issues**: confirm the Remote Script is selected as a Control Surface and the MCP
  server is configured in your client
- **Timeout errors**: break large requests into smaller steps
- **Remote Script changes not taking effect**: delete `__pycache__` next to `__init__.py`, then
  restart Ableton
- **"Parameter is not currently settable"**: the parameter is driven by a macro or rack chain;
  set it on the controlling device instead
- **Still stuck**: restart both Ableton and your MCP client

## Technical details

## Communication protocol

JSON over TCP sockets, port 9877:

- Commands are JSON objects with a `type` and optional `params`
- Responses are JSON objects with a `status` and either `result` or `message`

State-modifying commands are scheduled onto Live's main thread and awaited through a response
queue, since Live's API is not thread-safe.

## Limitations & security considerations

- Complex arrangements may need to be broken into smaller steps
- Designed around Ableton's default devices and browser items
- Arrangement-timeline automation is not supported (see above)
- The Remote Script opens a socket server on the local machine; anything able to reach that port
  can drive Live
- Save your work before extensive experimentation
