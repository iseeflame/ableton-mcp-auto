# ableton-mcp-auto — Ableton Live Model Context Protocol Integration

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
>    or remove anything it has added. This fork adds twenty-one tools covering that ground
>    (see [What this fork adds](#what-this-fork-adds)).
> 2. **Telemetry was removed.** Upstream ships an opt-out telemetry client that reports usage
>    to the author's Supabase project. This fork removes it entirely
>    (see [Telemetry](#telemetry)).
>
> If you want the original, unmodified project — go upstream. It is actively maintained and has
> a [community Discord](https://discord.gg/3ZrMyGKnaU); this fork has neither.

## Features

- **Two-way communication**: socket-based server bridging the MCP client and Ableton Live
- **Track manipulation**: create MIDI, audio and return tracks; rename and delete them *(fork)*
- **Instrument and effect selection**: load instruments, effects and sounds from Live's browser,
  including the user folders in its Places sidebar *(fork)*
- **Clip creation**: create MIDI clips, and read, edit and delete their notes *(fork)*
- **Device parameters**: read and set any device parameter, plus track volume/pan/sends *(fork)*
- **Clip automation**: write, read back and clear clip envelopes, including approximated ramps *(fork)*
- **Real-unit mixing**: target parameters by what they display — "2 kHz", "-12 dB", "140%" *(fork)*
- **Master and returns**: reachable through negative track indices, sends included *(fork)*
- **Removal**: delete devices, clips and tracks, not just create them *(fork)*
- **Inside racks**: reach, process and automate a single drum pad or rack chain *(fork)*
- **Arrangement view composition**: build full songs in Arrangement View
- **Session control**: playback, clip firing, transport across Session and Arrangement View

## What this fork adds

Twenty-one tools, plus the matching commands in the Remote Script:

| Tool | Purpose |
| --- | --- |
| `create_audio_track` | Create an audio track |
| `create_return_track` | Create a return track, adding a send to every track |
| `get_clip_notes` | Read the notes already inside a MIDI clip |
| `remove_clip_notes` | Delete notes in a time and pitch window, keeping the clip |
| `modify_clip_notes` | Change existing notes in place, by note_id |
| `get_device_parameters` | List a device's parameters with current value, range, and display value |
| `set_device_parameter` | Set a parameter by index or name — turn a knob live |
| `set_mixer_parameter` | Shorthand for track volume, panning, and sends |
| `set_clip_envelope` | Write a clip automation envelope from a list of breakpoints |
| `get_clip_envelope` | Read an envelope back by sampling it, to confirm its shape |
| `clear_clip_envelope` | Remove a parameter's clip envelope |
| `set_parameter_to_display` | Set a parameter by what it should read on screen ("2 kHz", "-12 dB") |
| `convert_display_values` | Translate on-screen magnitudes into raw values, changing nothing |
| `move_device` | Reorder a device in its chain, or move it into another chain or track |
| `get_device_tree` | List a track's devices including everything nested inside racks |
| `load_device_into_chain` | Load a device into one chain of a rack, not onto the track |
| `load_sample_to_drum_pad` | Put a sample on one pad of a drum rack |
| `delete_device` | Remove one device from a track's chain |
| `describe_live_api` | Inspect Live's own API from inside the running application |
| `delete_clip` | Delete the clip in a Session slot |
| `delete_track` | Delete a track with its clips and devices |

## Before you start

Live's API has sharp edges, and several of them look like bugs in your own code the
first time. The short version:

- Most parameters are stored 0.0–1.0 and only *display* real units, so `2000` is not
  2 kHz — use `set_parameter_to_display` for anything you would say out loud in units.
- `track_index` −1 is the master and −2, −3 … the returns; `device_index` −1 is a
  track's mixer. Live keeps none of them in `song.tracks`.
- Clip envelopes work in Session clips only. Automation on the Arrangement timeline
  cannot be read or written at all.
- Drum pads, rack chains and nested devices are addressed by path from
  `get_device_tree`.
- An empty drum pad cannot be filled: it has no chain, and loading onto it replaces the
  whole rack. Start from a populated kit and swap the samples.

**[README_FULL.md](README_FULL.md)** explains each of these, plus device order, browsing
your own Places, and reading the API from inside Live.

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
git clone https://github.com/iseeflame/ableton-mcp-auto.git
```

### 2. Configure your MCP client

Point the client at your clone. Replace `/path/to/ableton-mcp-auto` with wherever you cloned it.

**Claude Code** — create `.mcp.json` in your project directory:

```json
{
  "mcpServers": {
    "AbletonMCP": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/ableton-mcp-auto", "ableton-mcp-auto"]
    }
  }
}
```

Start `claude` from that directory and approve the server when prompted.

**Claude Desktop** — Settings > Developer > Edit Config > `claude_desktop_config.json`, same
`mcpServers` block as above.

**Cursor** — Settings > MCP, and use:

```
uv run --directory /path/to/ableton-mcp-auto ableton-mcp-auto
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

## Telemetry

**None. This fork does not collect or transmit anything.**

Upstream ships a Supabase-backed telemetry client whose consent flag defaults to on,
which collects prompts, MIDI notes and track names despite its README. Here
`MCP_Server/telemetry.py` and `telemetry_decorator.py` are inert stubs, the `supabase`
dependency is gone, and the telemetry-only `user_prompt` argument is off every tool. No
environment variable is needed; the original code is in git history.

## Contributing

Improvements to the upstream project belong
[upstream](https://github.com/ahujasid/ableton-mcp) — please send them there.

## License

MIT. Copyright (c) 2025 Siddharth Ahuja — see [LICENSE](LICENSE). Modifications in this fork are
released under the same terms.

## Disclaimer

This is a third-party integration and not made by Ableton.
