# AbletonMCP (fork) — Ableton Live Model Context Protocol Integration

Connect Ableton Live to an MCP client (Claude Code, Claude Desktop, Cursor) so the model can
drive Live directly: build tracks, load instruments, write MIDI, turn knobs, and automate
parameters inside clips.

> ### About this fork
>
> This is a fork of **[ahujasid/ableton-mcp](https://github.com/ahujasid/ableton-mcp)** by
> [Siddharth Ahuja](https://x.com/sidahuj), MIT licensed. All of the original design — the
> socket protocol, the Remote Script architecture, the browser and arrangement tooling — is his.
>
> Two things differ here:
>
> 1. **Mixing was added.** Upstream can create tracks, clips and notes and load devices, but
>    cannot touch a device's parameters, write automation, reach the master or return tracks,
>    or remove anything it has added. This fork adds twelve tools covering that ground
>    (see [What this fork adds](#what-this-fork-adds)).
> 2. **Telemetry was removed.** Upstream ships an opt-out telemetry client that reports usage
>    to the author's Supabase project. This fork removes it entirely
>    (see [Telemetry](#telemetry)).
>
> If you want the original, unmodified project — go upstream. It is actively maintained and has
> a [community Discord](https://discord.gg/3ZrMyGKnaU); this fork has neither.

## Features

- **Two-way communication**: socket-based server bridging the MCP client and Ableton Live
- **Track manipulation**: create and modify MIDI and audio tracks
- **Instrument and effect selection**: load instruments, effects and sounds from Live's browser
- **Clip creation**: create and edit MIDI clips with notes
- **Device parameters**: read and set any device parameter, plus track volume/pan/sends *(fork)*
- **Clip automation**: write, read back and clear clip envelopes, including approximated ramps *(fork)*
- **Real-unit mixing**: target parameters by what they display — "2 kHz", "-12 dB", "140%" *(fork)*
- **Master and returns**: reachable through negative track indices, sends included *(fork)*
- **Removal**: delete devices, clips and tracks, not just create them *(fork)*
- **Arrangement view composition**: build full songs in Arrangement View
- **Session control**: playback, clip firing, transport across Session and Arrangement View

## What this fork adds

Twelve tools, plus the matching commands in the Remote Script:

| Tool | Purpose |
| --- | --- |
| `get_clip_notes` | Read the notes already inside a MIDI clip |
| `get_device_parameters` | List a device's parameters with current value, range, and display value |
| `set_device_parameter` | Set a parameter by index or name — turn a knob live |
| `set_mixer_parameter` | Shorthand for track volume, panning, and sends |
| `set_clip_envelope` | Write a clip automation envelope from a list of breakpoints |
| `get_clip_envelope` | Read an envelope back by sampling it, to confirm its shape |
| `clear_clip_envelope` | Remove a parameter's clip envelope |
| `set_parameter_to_display` | Set a parameter by what it should read on screen ("2 kHz", "-12 dB") |
| `convert_display_values` | Translate on-screen magnitudes into raw values, changing nothing |
| `delete_device` | Remove one device from a track's chain |
| `delete_clip` | Delete the clip in a Session slot |
| `delete_track` | Delete a track with its clips and devices |

Live keeps the master and the return tracks outside `song.tracks`, so negative `track_index`
values reach them: **`-1` is the master**, **`-2` the first return**, `-3` the second, and so on.
The master additionally exposes `cue_volume` and `crossfader`. A track's sends appear as
`send_0`, `send_1`, … on its mixer, one per return track, and can be automated like any other
parameter.

### Mixing in real units

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

`delete_device`, `delete_clip` and `delete_track` are destructive but covered by Live's undo. Track indices shift
down after a deletion, so re-read the session before deleting another by index.

Throughout, **`device_index: -1` addresses the track's mixer device**, so volume and panning are
automated through the same calls as any plugin parameter.

### How automation works, and what it can't do

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

### The breakpoint at time 0

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

### Parameter values are usually normalized

**Always call `get_device_parameters` first.** Most Live parameters run 0.0–1.0 internally
while *displaying* real-world units — Wavetable's `Filter 1 Freq` is 0.0–1.0 for 20 Hz–20.5 kHz,
not 20–20500. Passing `8000` because you meant 8 kHz targets a value 8000× past the maximum.

`get_device_parameters` reports `min`/`max` alongside `display_min`/`display_max` so the real
range and its meaning are both visible. Out-of-range values are **rejected with an error naming
the valid range** — deliberately, since clamping them silently turns a unit mistake into a
successful-looking call that writes a flat envelope at the maximum.

## Components

1. **Ableton Remote Script** (`AbletonMCP_Remote_Script/__init__.py`): a MIDI Remote Script that
   runs inside Live and exposes a socket server on port 9877
2. **MCP Server** (`MCP_Server/server.py`): implements the Model Context Protocol and forwards
   commands to the Remote Script

## Prerequisites

- Ableton Live 10 or newer (this fork is developed and tested against **Live 11.3.2**)
- Python 3.10 or newer
- [uv package manager](https://docs.astral.sh/uv/getting-started/installation/)

⚠️ Install uv before going further.

> **Note:** upstream's `uvx ableton-mcp` and Smithery installs pull the *published upstream
> package*, not this fork. To run this fork you must clone it and point your client at the clone.

## Installation

### 1. Clone the fork

```bash
git clone <your-fork-url> ableton-mcp
```

### 2. Configure your MCP client

Point the client at your clone. Replace `/path/to/ableton-mcp` with wherever you cloned it.

**Claude Code** — create `.mcp.json` in your project directory:

```json
{
  "mcpServers": {
    "AbletonMCP": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/ableton-mcp", "ableton-mcp"]
    }
  }
}
```

Start `claude` from that directory and approve the server when prompted.

**Claude Desktop** — Settings > Developer > Edit Config > `claude_desktop_config.json`, same
`mcpServers` block as above.

**Cursor** — Settings > MCP, and use:

```
uv run --directory /path/to/ableton-mcp ableton-mcp
```

⚠️ Run only one instance of the MCP server, not one per client.

### 3. Install the Remote Script

1. Copy the `AbletonMCP_Remote_Script` folder into Ableton's MIDI Remote Scripts directory,
   renaming it to `AbletonMCP`. Locations vary by OS and version — one of these should work:

   **macOS:**
   - Applications > right-click Ableton Live → Show Package Contents →
     `Contents/App-Resources/MIDI Remote Scripts/`
   - or `/Users/[Username]/Library/Preferences/Ableton/Live XX/User Remote Scripts`

   **Windows:**
   - `C:\ProgramData\Ableton\Live XX\Resources\MIDI Remote Scripts\`
   - or `C:\Program Files\Ableton\Live XX\Resources\MIDI Remote Scripts\`
   - or `C:\Users\[Username]\AppData\Roaming\Ableton\Live x.x.x\Preferences\User Remote Scripts`

   The directory must end up containing `AbletonMCP/__init__.py`.

2. Launch Ableton Live
3. Settings/Preferences → Link, Tempo & MIDI
4. In the Control Surface dropdown, select **AbletonMCP**
5. Leave Input and Output set to **None** — the script uses a socket, not a MIDI port

If you are updating the Remote Script over a previous install, delete the `__pycache__` folder
next to `__init__.py` and restart Live, so the old bytecode is not reused.

## Usage

1. Make sure the Remote Script is loaded in Ableton (Control Surface set)
2. Make sure the MCP server is configured in your client
3. The connection is established on first tool use

### Example commands

- "Create an 80s synthwave track"
- "Create a full arrangement with an intro, buildup, drop, breakdown, and outro"
- "Create a new MIDI track with a synth bass instrument"
- "Add reverb to my drums"
- "Set the tempo to 120 BPM"
- "Show me every parameter on the Operator in track 1" *(fork)*
- "Set the filter cutoff on track 0's synth to 2 kHz" *(fork)*
- "Automate a filter sweep from 200 Hz to 8 kHz across the clip in track 0" *(fork)*
- "Fade the volume of track 2 up over the first two bars of its clip" *(fork)*

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

### Communication protocol

JSON over TCP sockets, port 9877:

- Commands are JSON objects with a `type` and optional `params`
- Responses are JSON objects with a `status` and either `result` or `message`

State-modifying commands are scheduled onto Live's main thread and awaited through a response
queue, since Live's API is not thread-safe.

### Limitations & security considerations

- Complex arrangements may need to be broken into smaller steps
- Designed around Ableton's default devices and browser items
- Arrangement-timeline automation is not supported (see above)
- The Remote Script opens a socket server on the local machine; anything able to reach that port
  can drive Live
- Save your work before extensive experimentation

## Telemetry

**None. This fork does not collect or transmit anything.**

Upstream includes a telemetry client that reports tool usage to the author's Supabase project,
disableable via `ABLETON_MCP_DISABLE_TELEMETRY=true`. Its consent flag (`_user_consent`), which
gates collection of user prompts, MIDI note data, instrument URIs and track/clip names, ships
defaulting to `True`, and nothing in the codebase sets it to `False` — so the extended tier is
what runs unless telemetry is disabled outright. This appears to be an unflipped default rather
than anything deliberate, but it does not match upstream's README.

In this fork:

- `MCP_Server/telemetry.py` and `MCP_Server/telemetry_decorator.py` are inert no-op stubs
- the `supabase` dependency is removed from `pyproject.toml`
- the telemetry-only `user_prompt` parameter is removed from every tool

No environment variable is needed. The original implementation remains in git history.

## Contributing

Improvements to the upstream project belong
[upstream](https://github.com/ahujasid/ableton-mcp) — please send them there.

## License

MIT. Copyright (c) 2025 Siddharth Ahuja — see [LICENSE](LICENSE). Modifications in this fork are
released under the same terms.

## Disclaimer

This is a third-party integration and not made by Ableton.
