# ableton_mcp_server.py
from mcp.server.fastmcp import FastMCP, Context
import socket
import json
import logging
import os
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any, List, Optional, Union

from .telemetry import record_startup
from .telemetry_decorator import telemetry_tool, rich_telemetry_tool

ABLETON_HOST = os.environ.get("ABLETON_HOST", "localhost")
ABLETON_PORT = int(os.environ.get("ABLETON_PORT", "9877"))

# Configure logging
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("AbletonMCPServer")

@dataclass
class AbletonConnection:
    host: str
    port: int
    sock: socket.socket = None
    
    def connect(self) -> bool:
        """Connect to the Ableton Remote Script socket server"""
        if self.sock:
            return True

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((self.host, self.port))
            self.sock.settimeout(None)
            logger.info(f"Connected to Ableton at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Ableton at {self.host}:{self.port}: {str(e)}")
            self.sock = None
            return False
    
    def disconnect(self):
        """Disconnect from the Ableton Remote Script"""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Ableton: {str(e)}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192, timeout=15.0):
        """Receive the complete response, potentially in multiple chunks.

        The timeout has to be passed in: this used to hardcode 15s, which silently
        overrode the wider budget send_command had just set for slow operations like
        booting a Max for Live device."""
        chunks = []
        sock.settimeout(timeout)
        
        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        if not chunks:
                            raise Exception("Connection closed before receiving any data")
                        break
                    
                    chunks.append(chunk)
                    
                    # Check if we've received a complete JSON object
                    try:
                        data = b''.join(chunks)
                        json.loads(data.decode('utf-8'))
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except socket.timeout:
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {str(e)}")
                    raise
        except Exception as e:
            logger.error(f"Error during receive: {str(e)}")
            raise
            
        # If we get here, we either timed out or broke out of the loop
        if chunks:
            data = b''.join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                json.loads(data.decode('utf-8'))
                return data
            except json.JSONDecodeError:
                raise Exception("Incomplete JSON response received")
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Send a command to Ableton and return the response"""
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Ableton")
        
        command = {
            "type": command_type,
            "params": params or {}
        }
        
        # Check if this is a state-modifying command
        is_modifying_command = command_type in [
            "create_midi_track", "create_audio_track", "set_track_name",
            "create_clip", "create_audio_clip", "add_notes_to_clip", "set_clip_name",
            "set_tempo", "fire_clip", "stop_clip", "set_device_parameter",
            "start_playback", "stop_playback", "load_instrument_or_effect",
            # Arrangement view commands
            "switch_to_arrangement_view", "set_current_song_time",
            "duplicate_session_clip_to_arrangement"
        ]

        # Commands whose work on Live's main thread can take noticeably longer
        # than the default modifying-command budget (e.g. importing/decoding a
        # large audio file). Give them a wider socket timeout so we don't time
        # out before the Remote Script's own queue does.
        long_running_commands = {"create_audio_clip": 65.0,
                                 "load_browser_item": 65.0}
        
        try:
            logger.info(f"Sending command: {command_type} with params: {params}")
            
            # Send the command
            self.sock.sendall(json.dumps(command).encode('utf-8'))
            logger.info(f"Command sent, waiting for response...")
            
            # Set timeout based on command type
            if command_type in long_running_commands:
                timeout = long_running_commands[command_type]
            else:
                timeout = 15.0 if is_modifying_command else 10.0
            self.sock.settimeout(timeout)

            # Receive the response, honouring the same budget as the send
            response_data = self.receive_full_response(self.sock, timeout=timeout)
            logger.info(f"Received {len(response_data)} bytes of data")

            # Parse the response
            response = json.loads(response_data.decode('utf-8'))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")

            if response.get("status") == "error":
                logger.error(f"Ableton error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Ableton"))
            
            return response.get("result", {})
        except socket.timeout:
            logger.error("Socket timeout while waiting for response from Ableton")
            self.sock = None
            raise Exception("Timeout waiting for Ableton response")
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {str(e)}")
            self.sock = None
            raise Exception(f"Connection to Ableton lost: {str(e)}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Ableton: {str(e)}")
            if 'response_data' in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            self.sock = None
            raise Exception(f"Invalid response from Ableton: {str(e)}")
        except Exception as e:
            logger.error(f"Error communicating with Ableton: {str(e)}")
            self.sock = None
            raise Exception(f"Communication error with Ableton: {str(e)}")

@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """Manage server startup and shutdown lifecycle"""
    try:
        logger.info("AbletonMCP server starting up")

        # Record startup event for telemetry
        try:
            record_startup()
        except Exception as e:
            logger.debug(f"Failed to record startup telemetry: {e}")

        try:
            ableton = get_ableton_connection()
            logger.info("Successfully connected to Ableton on startup")
        except Exception as e:
            logger.warning(f"Could not connect to Ableton on startup: {str(e)}")
            logger.warning("Make sure the Ableton Remote Script is running")

        yield {}
    finally:
        global _ableton_connection
        if _ableton_connection:
            logger.info("Disconnecting from Ableton on shutdown")
            _ableton_connection.disconnect()
            _ableton_connection = None
        logger.info("AbletonMCP server shut down")

# Create the MCP server with lifespan support
mcp = FastMCP(
    "AbletonMCP",
    lifespan=server_lifespan
)

# Global connection for resources
_ableton_connection = None

def get_ableton_connection():
    """Get or create a persistent Ableton connection"""
    global _ableton_connection

    if _ableton_connection is not None and _ableton_connection.sock is not None:
        try:
            # Check if the socket is still alive by peeking for data
            # MSG_PEEK + MSG_DONTWAIT will raise BlockingIOError if alive but no data,
            # or return b'' if the remote end has closed the connection.
            _ableton_connection.sock.setblocking(False)
            try:
                data = _ableton_connection.sock.recv(1, socket.MSG_PEEK)
                if data == b'':
                    raise ConnectionError("Remote end closed")
            except BlockingIOError:
                pass  # Socket is alive, just no data waiting — this is normal
            finally:
                _ableton_connection.sock.setblocking(True)
            return _ableton_connection
        except Exception as e:
            logger.warning(f"Existing connection is no longer valid: {str(e)}")
            try:
                _ableton_connection.disconnect()
            except:
                pass
            _ableton_connection = None
    
    # Connection doesn't exist or is invalid, create a new one
    if _ableton_connection is None:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"Connecting to Ableton at {ABLETON_HOST}:{ABLETON_PORT} (attempt {attempt}/{max_attempts})...")
                _ableton_connection = AbletonConnection(host=ABLETON_HOST, port=ABLETON_PORT)
                if _ableton_connection.connect():
                    logger.info("Created new persistent connection to Ableton")
                    return _ableton_connection
                else:
                    _ableton_connection = None
            except Exception as e:
                logger.error(f"Connection attempt {attempt} failed: {str(e)}")
                if _ableton_connection:
                    _ableton_connection.disconnect()
                    _ableton_connection = None

            if attempt < max_attempts:
                import time
                time.sleep(1.0)
        
        # If we get here, all connection attempts failed
        if _ableton_connection is None:
            logger.error("Failed to connect to Ableton after multiple attempts")
            raise Exception("Could not connect to Ableton. Make sure the Remote Script is running.")
    
    return _ableton_connection


# Core Tool endpoints

@mcp.tool()
@telemetry_tool("get_session_info")
def get_session_info(ctx: Context) -> str:
    """Get detailed information about the current Ableton session

    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_session_info")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting session info from Ableton: {str(e)}")
        return f"Error getting session info: {str(e)}"

@mcp.tool()
@telemetry_tool("get_track_info")
def get_track_info(ctx: Context, track_index: int) -> str:
    """
    Get detailed information about a specific track in Ableton.

    Parameters:
    - track_index: The index of the track to get information about
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_track_info", {"track_index": track_index})
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting track info from Ableton: {str(e)}")
        return f"Error getting track info: {str(e)}"

@mcp.tool()
@telemetry_tool("create_midi_track")
def create_midi_track(ctx: Context, index: int = -1) -> str:
    """
    Create a new MIDI track in the Ableton session.

    Parameters:
    - index: The index to insert the track at (-1 = end of list)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("create_midi_track", {"index": index})
        return f"Created new MIDI track: {result.get('name', 'unknown')}"
    except Exception as e:
        logger.error(f"Error creating MIDI track: {str(e)}")
        return f"Error creating MIDI track: {str(e)}"


@mcp.tool()
@rich_telemetry_tool("set_track_name")
def set_track_name(ctx: Context, track_index: int, name: str) -> str:
    """
    Set the name of a track.

    Parameters:
    - track_index: The index of the track to rename
    - name: The new name for the track
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_track_name", {"track_index": track_index, "name": name})
        return f"Renamed track to: {result.get('name', name)}"
    except Exception as e:
        logger.error(f"Error setting track name: {str(e)}")
        return f"Error setting track name: {str(e)}"

@mcp.tool()
@rich_telemetry_tool("create_clip")
def create_clip(ctx: Context, track_index: int, clip_index: int, length: float = 4.0) -> str:
    """
    Create a new MIDI clip in the specified track and clip slot.

    Parameters:
    - track_index: The index of the track to create the clip in
    - clip_index: The index of the clip slot to create the clip in
    - length: The length of the clip in beats (default: 4.0)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("create_clip", {
            "track_index": track_index, 
            "clip_index": clip_index, 
            "length": length
        })
        return f"Created new clip at track {track_index}, slot {clip_index} with length {length} beats"
    except Exception as e:
        logger.error(f"Error creating clip: {str(e)}")
        return f"Error creating clip: {str(e)}"

@mcp.tool()
@rich_telemetry_tool("create_audio_clip")
def create_audio_clip(ctx: Context, track_index: int, clip_index: int, path: str) -> str:
    """
    Create a new audio clip in an audio track's clip slot by importing a file.

    Requires Ableton Live 12.0.5 or newer — the underlying
    ClipSlot.create_audio_clip Live API was introduced in 12.0.5 and is not
    available in earlier 12.0.x releases.

    Parameters:
    - track_index: The index of the audio track to create the clip in
    - clip_index: The index of the clip slot to create the clip in
    - path: Absolute path to a supported audio file (e.g. a .wav). The target
      track must be an audio track and the clip slot must be empty.
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("create_audio_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "path": path
        })
        return f"Created audio clip '{result.get('name', 'clip')}' at track {track_index}, slot {clip_index} (length {result.get('length', '?')} beats)"
    except Exception as e:
        logger.error(f"Error creating audio clip: {str(e)}")
        return f"Error creating audio clip: {str(e)}"

@mcp.tool()
@rich_telemetry_tool("add_notes_to_clip", capture_notes=True)
def add_notes_to_clip(
    ctx: Context,
    track_index: int,
    clip_index: int,
    notes: List[Dict[str, Union[int, float, bool]]]
) -> str:
    """
    Add MIDI notes to a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - notes: List of note dictionaries, each with pitch, start_time, duration, velocity, and mute
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("add_notes_to_clip", {
            "track_index": track_index,
            "clip_index": clip_index,
            "notes": notes
        })
        return f"Added {len(notes)} notes to clip at track {track_index}, slot {clip_index}"
    except Exception as e:
        logger.error(f"Error adding notes to clip: {str(e)}")
        return f"Error adding notes to clip: {str(e)}"

@mcp.tool()
@rich_telemetry_tool("set_clip_name")
def set_clip_name(ctx: Context, track_index: int, clip_index: int, name: str) -> str:
    """
    Set the name of a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    - name: The new name for the clip
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_clip_name", {
            "track_index": track_index,
            "clip_index": clip_index,
            "name": name
        })
        return f"Renamed clip at track {track_index}, slot {clip_index} to '{name}'"
    except Exception as e:
        logger.error(f"Error setting clip name: {str(e)}")
        return f"Error setting clip name: {str(e)}"

@mcp.tool()
@rich_telemetry_tool("set_tempo")
def set_tempo(ctx: Context, tempo: float) -> str:
    """
    Set the tempo of the Ableton session.

    Parameters:
    - tempo: The new tempo in BPM
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_tempo", {"tempo": tempo})
        return f"Set tempo to {tempo} BPM"
    except Exception as e:
        logger.error(f"Error setting tempo: {str(e)}")
        return f"Error setting tempo: {str(e)}"


@mcp.tool()
@rich_telemetry_tool("load_instrument_or_effect")
def load_instrument_or_effect(ctx: Context, track_index: int, uri: str) -> str:
    """
    Load an instrument or effect onto a track using its URI.

    Parameters:
    - track_index: The index of the track to load the instrument on
    - uri: The URI of the instrument or effect to load (e.g., 'query:Synths#Instrument%20Rack:Bass:FileId_5116')
    """
    try:
        ableton = get_ableton_connection()
        # The Remote Script reports only that the load happened, not what appeared, and
        # a device added in one main-thread turn is not reliably visible within it. So
        # the chain is read either side of the load, in round trips of its own.
        def device_names():
            try:
                info = ableton.send_command("get_track_info", {"track_index": track_index})
                return [d["name"] for d in info.get("devices", [])]
            except Exception:
                return None

        before = device_names()
        result = ableton.send_command("load_browser_item", {
            "track_index": track_index,
            "item_uri": uri
        })
        if not result.get("loaded", False):
            return f"Failed to load instrument with URI '{uri}'"

        after = device_names()
        loaded_name = result.get("item_name") or uri
        where = result.get("track_name") or f"track {track_index}"

        if before is not None and after is not None:
            # Compare by position, since a track can legitimately hold two devices
            # with the same name.
            added = after[len(before):] if len(after) > len(before) else [
                name for name in after if name not in before]
            if added:
                return (f"Loaded '{loaded_name}' onto '{where}'. "
                        f"Added: {', '.join(added)}. Chain is now: {' > '.join(after)}")
            return (f"Loaded '{loaded_name}' onto '{where}', but the device chain is "
                    f"unchanged ({' > '.join(after) if after else 'empty'}) - the item may "
                    f"have replaced a selection rather than being appended")
        return f"Loaded '{loaded_name}' onto '{where}'"
    except Exception as e:
        logger.error(f"Error loading instrument by URI: {str(e)}")
        return f"Error loading instrument by URI: {str(e)}"

@mcp.tool()
@telemetry_tool("fire_clip")
def fire_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Start playing a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("fire_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return f"Started playing clip at track {track_index}, slot {clip_index}"
    except Exception as e:
        logger.error(f"Error firing clip: {str(e)}")
        return f"Error firing clip: {str(e)}"

@mcp.tool()
@telemetry_tool("stop_clip")
def stop_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Stop playing a clip.

    Parameters:
    - track_index: The index of the track containing the clip
    - clip_index: The index of the clip slot containing the clip
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("stop_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        return f"Stopped clip at track {track_index}, slot {clip_index}"
    except Exception as e:
        logger.error(f"Error stopping clip: {str(e)}")
        return f"Error stopping clip: {str(e)}"

@mcp.tool()
@telemetry_tool("start_playback")
def start_playback(ctx: Context) -> str:
    """Start playing the Ableton session.

    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("start_playback")
        return "Started playback"
    except Exception as e:
        logger.error(f"Error starting playback: {str(e)}")
        return f"Error starting playback: {str(e)}"

@mcp.tool()
@telemetry_tool("stop_playback")
def stop_playback(ctx: Context) -> str:
    """Stop playing the Ableton session.

    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("stop_playback")
        return "Stopped playback"
    except Exception as e:
        logger.error(f"Error stopping playback: {str(e)}")
        return f"Error stopping playback: {str(e)}"

@mcp.tool()
@rich_telemetry_tool("get_browser_tree")
def get_browser_tree(ctx: Context, category_type: str = "all") -> str:
    """
    Get a hierarchical tree of browser categories from Ableton.

    Parameters:
    - category_type: Type of categories to get ('all', 'instruments', 'sounds', 'drums', 'audio_effects', 'midi_effects')
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_browser_tree", {
            "category_type": category_type
        })
        
        # Check if we got any categories
        if "available_categories" in result and len(result.get("categories", [])) == 0:
            available_cats = result.get("available_categories", [])
            return (f"No categories found for '{category_type}'. "
                   f"Available browser categories: {', '.join(available_cats)}")
        
        # Format the tree in a more readable way
        total_folders = result.get("total_folders", 0)
        formatted_output = f"Browser tree for '{category_type}' (showing {total_folders} folders):\n\n"
        
        def format_tree(item, indent=0):
            output = ""
            if item:
                prefix = "  " * indent
                name = item.get("name", "Unknown")
                path = item.get("path", "")
                has_more = item.get("has_more", False)
                
                # Add this item
                output += f"{prefix}• {name}"
                if path:
                    output += f" (path: {path})"
                if has_more:
                    output += " [...]"
                output += "\n"
                
                # Add children
                for child in item.get("children", []):
                    output += format_tree(child, indent + 1)
            return output
        
        # Format each category
        for category in result.get("categories", []):
            formatted_output += format_tree(category)
            formatted_output += "\n"
        
        return formatted_output
    except Exception as e:
        error_msg = str(e)
        if "Browser is not available" in error_msg:
            logger.error(f"Browser is not available in Ableton: {error_msg}")
            return f"Error: The Ableton browser is not available. Make sure Ableton Live is fully loaded and try again."
        elif "Could not access Live application" in error_msg:
            logger.error(f"Could not access Live application: {error_msg}")
            return f"Error: Could not access the Ableton Live application. Make sure Ableton Live is running and the Remote Script is loaded."
        else:
            logger.error(f"Error getting browser tree: {error_msg}")
            return f"Error getting browser tree: {error_msg}"

@mcp.tool()
@rich_telemetry_tool("get_browser_items_at_path")
def get_browser_items_at_path(ctx: Context, path: str) -> str:
    """
    Get browser items at a specific path in Ableton's browser.

    Parameters:
    - path: Path in the format "category/folder/subfolder"
            where category is one of the available browser categories in Ableton
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_browser_items_at_path", {
            "path": path
        })
        
        # Check if there was an error with available categories
        if "error" in result and "available_categories" in result:
            error = result.get("error", "")
            available_cats = result.get("available_categories", [])
            return (f"Error: {error}\n"
                   f"Available browser categories: {', '.join(available_cats)}")
        
        return json.dumps(result, indent=2)
    except Exception as e:
        error_msg = str(e)
        if "Browser is not available" in error_msg:
            logger.error(f"Browser is not available in Ableton: {error_msg}")
            return f"Error: The Ableton browser is not available. Make sure Ableton Live is fully loaded and try again."
        elif "Could not access Live application" in error_msg:
            logger.error(f"Could not access Live application: {error_msg}")
            return f"Error: Could not access the Ableton Live application. Make sure Ableton Live is running and the Remote Script is loaded."
        elif "Unknown or unavailable category" in error_msg:
            logger.error(f"Invalid browser category: {error_msg}")
            return f"Error: {error_msg}. Please check the available categories using get_browser_tree."
        elif "Path part" in error_msg and "not found" in error_msg:
            logger.error(f"Path not found: {error_msg}")
            return f"Error: {error_msg}. Please check the path and try again."
        else:
            logger.error(f"Error getting browser items at path: {error_msg}")
            return f"Error getting browser items at path: {error_msg}"

@mcp.tool()
@rich_telemetry_tool("load_drum_kit")
def load_drum_kit(ctx: Context, track_index: int, rack_uri: str, kit_path: str) -> str:
    """
    Load a drum rack and then load a specific drum kit into it.

    Parameters:
    - track_index: The index of the track to load on
    - rack_uri: The URI of the drum rack to load (e.g., 'Drums/Drum Rack')
    - kit_path: Path to the drum kit inside the browser (e.g., 'drums/acoustic/kit1')
    """
    try:
        ableton = get_ableton_connection()
        
        # Step 1: Load the drum rack
        result = ableton.send_command("load_browser_item", {
            "track_index": track_index,
            "item_uri": rack_uri
        })
        
        if not result.get("loaded", False):
            return f"Failed to load drum rack with URI '{rack_uri}'"
        
        # Step 2: Get the drum kit items at the specified path
        kit_result = ableton.send_command("get_browser_items_at_path", {
            "path": kit_path
        })
        
        if "error" in kit_result:
            return f"Loaded drum rack but failed to find drum kit: {kit_result.get('error')}"
        
        # Step 3: Find a loadable drum kit
        kit_items = kit_result.get("items", [])
        loadable_kits = [item for item in kit_items if item.get("is_loadable", False)]
        
        if not loadable_kits:
            return f"Loaded drum rack but no loadable drum kits found at '{kit_path}'"
        
        # Step 4: Load the first loadable kit
        kit_uri = loadable_kits[0].get("uri")
        load_result = ableton.send_command("load_browser_item", {
            "track_index": track_index,
            "item_uri": kit_uri
        })
        
        return f"Loaded drum rack and kit '{loadable_kits[0].get('name')}' on track {track_index}"
    except Exception as e:
        logger.error(f"Error loading drum kit: {str(e)}")
        return f"Error loading drum kit: {str(e)}"

# ── Arrangement view tools ────────────────────────────────────────────────────

@mcp.tool()
@telemetry_tool("switch_to_arrangement_view")
def switch_to_arrangement_view(ctx: Context) -> str:
    """Switch Ableton's main window to the Arrangement view.

    """
    try:
        ableton = get_ableton_connection()
        ableton.send_command("switch_to_arrangement_view")
        return "Switched to Arrangement view"
    except Exception as e:
        logger.error(f"Error switching to arrangement view: {str(e)}")
        return f"Error switching to arrangement view: {str(e)}"


@mcp.tool()
@rich_telemetry_tool("set_arrangement_time")
def set_arrangement_time(ctx: Context, time: float) -> str:
    """
    Move the arrangement playhead to a specific position.

    Parameters:
    - time: Position in beats from the start of the arrangement (e.g. 8.0 = bar 3 in 4/4)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_current_song_time", {"time": time})
        return f"Playhead moved to beat {result.get('current_song_time', time)}"
    except Exception as e:
        logger.error(f"Error setting arrangement time: {str(e)}")
        return f"Error setting arrangement time: {str(e)}"


@mcp.tool()
@telemetry_tool("get_arrangement_clips")
def get_arrangement_clips(ctx: Context, track_index: int) -> str:
    """
    List all clips placed in the Arrangement timeline for a track.

    Returns each clip's name, start_time, end_time, length, and type.

    Parameters:
    - track_index: The index of the track to inspect
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_arrangement_clips", {"track_index": track_index})
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting arrangement clips: {str(e)}")
        return f"Error getting arrangement clips: {str(e)}"


@mcp.tool()
@rich_telemetry_tool("duplicate_to_arrangement")
def duplicate_to_arrangement(
    ctx: Context,
    track_index: int,
    clip_index: int,
    destination_time: float
) -> str:
    """
    Copy a Session-view clip into the Arrangement timeline.

    Uses Live's track.duplicate_clip_to_arrangement() API (Live 11 / 12).
    The clip is placed at destination_time beats from the start of the
    arrangement on the same track it lives in.

    Typical workflow:
      1. create_clip / add_notes_to_clip to build a Session clip
      2. Call duplicate_to_arrangement once per bar/section you need
      3. Call switch_to_arrangement_view to confirm the result in Live

    Parameters:
    - track_index:       Index of the track that owns the Session clip
    - clip_index:        Index of the clip slot in that track (Session view)
    - destination_time:  Beat position in the arrangement to place the clip
                         (e.g. 0.0 = start, 8.0 = bar 3 in 4/4)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command(
            "duplicate_session_clip_to_arrangement",
            {
                "track_index": track_index,
                "clip_index": clip_index,
                "destination_time": destination_time
            }
        )
        clip_name = result.get("clip_name", "clip")
        track_name = result.get("track_name", f"track {track_index}")
        return (
            f"Duplicated '{clip_name}' from Session slot {clip_index} "
            f"on '{track_name}' to arrangement at beat {destination_time}"
        )
    except Exception as e:
        logger.error(f"Error duplicating clip to arrangement: {str(e)}")
        return f"Error duplicating clip to arrangement: {str(e)}"


# Device parameter / automation endpoints


@mcp.tool()
@telemetry_tool("get_device_parameters")
def get_device_parameters(
    ctx: Context,
    track_index: int,
    device_index: int
) -> str:
    """
    List every automatable parameter ("knob") of a device, with its current value and range.

    Call this before set_device_parameter or set_clip_envelope to discover the exact
    parameter names/indices and their valid min/max ranges. Use get_track_info first
    to see which devices a track has.

    Parameters:
    - track_index:  Index of the track; -1 is the master track, -2 the first return, -3 the second
    - device_index: Index of the device on that track, or -1 for the track's mixer
                    (volume, panning, sends)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_device_parameters", {
            "track_index": track_index,
            "device_index": device_index
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error getting device parameters: {str(e)}")
        return f"Error getting device parameters: {str(e)}"


@mcp.tool()
@telemetry_tool("set_device_parameter")
def set_device_parameter(
    ctx: Context,
    track_index: int,
    device_index: int,
    value: float,
    parameter_index: Optional[int] = None,
    parameter_name: Optional[str] = None
) -> str:
    """
    Set a device parameter (turn a knob) to a value, live.

    Identify the parameter by either parameter_index or parameter_name (case-insensitive);
    supply exactly one. Quantized (switch-like) parameters are rounded to the nearest step.

    Note that most Live parameters are normalized to 0.0-1.0 while *displaying* real-world
    units: a filter's "Filter 1 Freq" runs 0.0-1.0 for 20 Hz - 20.5 kHz, not 20-20500. Call
    get_device_parameters first and use its min/max (and display_min/display_max) — passing
    a value outside the range is rejected with an error, not clamped.

    Parameters:
    - track_index:     Index of the track; -1 is the master track, -2 the first return, -3 the second
    - device_index:    Index of the device on that track, or -1 for the track's mixer
    - value:           Target value, in the parameter's own units (see get_device_parameters)
    - parameter_index: Index of the parameter to set
    - parameter_name:  Name of the parameter to set, e.g. "Filter Freq"
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_device_parameter", {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "parameter_name": parameter_name,
            "value": value
        })
        shown = result.get("display_value", result.get("value"))
        return (
            f"Set '{result.get('parameter_name')}' on "
            f"'{result.get('device_name')}' to {shown}"
        )
    except Exception as e:
        logger.error(f"Error setting device parameter: {str(e)}")
        return f"Error setting device parameter: {str(e)}"


@mcp.tool()
@telemetry_tool("set_mixer_parameter")
def set_mixer_parameter(
    ctx: Context,
    track_index: int,
    parameter_name: str,
    value: float
) -> str:
    """
    Set a track mixer parameter: volume, panning, or a send level.

    Shorthand for set_device_parameter with device_index=-1.

    Parameters:
    - track_index:    Index of the track; -1 is the master track, -2 the first return, -3 the second
    - parameter_name: "volume" (0.0-1.0, 0.85 = 0 dB), "panning" (-1.0 left to 1.0 right),
                      "track_activator" (0/1), or "send_0", "send_1", ... for sends.
                      The master track additionally exposes "cue_volume" and "crossfader".
    - value:          Target value
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_mixer_parameter", {
            "track_index": track_index,
            "parameter_name": parameter_name,
            "value": value
        })
        shown = result.get("display_value", result.get("value"))
        return f"Set {result.get('parameter_name')} on track {track_index} to {shown}"
    except Exception as e:
        logger.error(f"Error setting mixer parameter: {str(e)}")
        return f"Error setting mixer parameter: {str(e)}"


@mcp.tool()
@rich_telemetry_tool("set_clip_envelope")
def set_clip_envelope(
    ctx: Context,
    track_index: int,
    clip_index: int,
    device_index: int,
    points: List[Dict[str, float]],
    parameter_index: Optional[int] = None,
    parameter_name: Optional[str] = None,
    interpolate: bool = False,
    step_size: float = 0.0625,
    clear_existing: bool = True
) -> str:
    """
    Write a clip automation envelope for a device or mixer parameter.

    This automates a parameter *inside a Session clip* (Live's clip envelopes), so the
    movement plays back with that clip. Identify the parameter by parameter_index or
    parameter_name, as with set_device_parameter.

    Live's API can only insert flat steps, so smooth ramps are approximated: with
    interpolate=True the gap between consecutive points is filled with step_size-wide
    steps that walk linearly from one value to the next. With interpolate=False each
    point becomes a single flat step holding its value until the next point.

    Example - filter sweep over a 4-beat clip:
      points=[{"time": 0.0, "value": 200.0}, {"time": 4.0, "value": 8000.0}],
      interpolate=True

    Parameters:
    - track_index:     Index of the track
    - clip_index:      Index of the Session clip slot (must already contain a clip)
    - device_index:    Index of the device, or -1 for the track's mixer
    - points:          Breakpoints as [{"time": beats, "value": v}], optionally with
                       "length" in beats to force a step's width
    - parameter_index: Index of the parameter to automate
    - parameter_name:  Name of the parameter to automate
    - interpolate:     Approximate ramps between points instead of flat steps
    - step_size:       Width in beats of each generated step when interpolating
                       (default 0.0625 = 1/16 note)
    - clear_existing:  Clear any existing envelope for this parameter first
    """
    try:
        ableton = get_ableton_connection()
        ident = {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "parameter_name": parameter_name
        }

        # Live creates an envelope with a breakpoint at time 0 holding the parameter's
        # current value, and that point cannot be overwritten by inserting a step there.
        # It also only sees values committed by an earlier command, so aligning the
        # parameter has to happen in its own round trip: without this, a sweep starting
        # at 0.15 keeps a spike of whatever the knob happened to be sitting on.
        original_value = None
        aligned = False
        first_value = min(points, key=lambda p: p["time"])["value"] if points else None
        if first_value is not None:
            try:
                info = ableton.send_command("get_device_parameters", {
                    "track_index": track_index, "device_index": device_index})
                for entry in info.get("parameters", []):
                    matches_index = (parameter_index is not None
                                     and entry["index"] == parameter_index)
                    matches_name = (parameter_name is not None
                                    and parameter_name.strip().lower() in
                                    (entry["name"].lower(), entry["display_name"].lower()))
                    if matches_index or matches_name:
                        original_value = entry["value"]
                        break
                ableton.send_command("set_device_parameter",
                                     dict(ident, value=first_value))
                aligned = True
            except Exception as align_error:
                # Not fatal: the envelope is still worth writing, it just keeps the spike.
                logger.warning(f"Could not align parameter before writing envelope: {align_error}")
                original_value = None

        try:
            result = ableton.send_command("set_clip_envelope", {
                "track_index": track_index,
                "clip_index": clip_index,
                "device_index": device_index,
                "parameter_index": parameter_index,
                "parameter_name": parameter_name,
                "points": points,
                "interpolate": interpolate,
                "step_size": step_size,
                "clear_existing": clear_existing
            })
        finally:
            # The breakpoint is fixed once the steps exist, so the knob can go back.
            if original_value is not None:
                try:
                    ableton.send_command("set_device_parameter",
                                         dict(ident, value=original_value))
                except Exception as restore_error:
                    logger.warning(f"Could not restore parameter value: {restore_error}")

        message = (
            f"Automated '{result.get('parameter_name')}' on "
            f"'{result.get('device_name')}' in clip '{result.get('clip_name')}' "
            f"({result.get('steps_written')} steps)"
        )
        skipped = result.get("points_skipped_past_clip_end") or 0
        if skipped:
            message += f"; {skipped} point(s) at or past the clip end were skipped"
        if not aligned:
            message += "; could not align the parameter first, so the clip start may hold a stale value"
        return message
    except Exception as e:
        logger.error(f"Error setting clip envelope: {str(e)}")
        return f"Error setting clip envelope: {str(e)}"


@mcp.tool()
@telemetry_tool("get_clip_envelope")
def get_clip_envelope(
    ctx: Context,
    track_index: int,
    clip_index: int,
    device_index: int,
    parameter_index: Optional[int] = None,
    parameter_name: Optional[str] = None,
    from_time: float = 0.0,
    to_time: Optional[float] = None,
    samples: int = 17
) -> str:
    """
    Read back a clip automation envelope, to check its shape after writing it.

    Live cannot enumerate an envelope's breakpoints, so the curve is reconstructed by
    evaluating it at evenly spaced times. Each sample reports the raw value and its
    display value (e.g. "2.15 kHz"), which is the practical way to confirm a sweep
    actually ramps instead of sitting flat.

    Returns has_envelope: false when the parameter has no envelope in this clip.

    Parameters:
    - track_index:     Index of the track
    - clip_index:      Index of the Session clip slot
    - device_index:    Index of the device, or -1 for the track's mixer
    - parameter_index: Index of the parameter to read
    - parameter_name:  Name of the parameter to read
    - from_time:       Start of the sampled range in beats (default 0.0)
    - to_time:         End of the sampled range in beats (default: end of the clip)
    - samples:         How many evenly spaced samples to take (2-512, default 17)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_clip_envelope", {
            "track_index": track_index,
            "clip_index": clip_index,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "parameter_name": parameter_name,
            "from_time": from_time,
            "to_time": to_time,
            "samples": samples
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error reading clip envelope: {str(e)}")
        return f"Error reading clip envelope: {str(e)}"


@mcp.tool()
@telemetry_tool("clear_clip_envelope")
def clear_clip_envelope(
    ctx: Context,
    track_index: int,
    clip_index: int,
    device_index: int,
    parameter_index: Optional[int] = None,
    parameter_name: Optional[str] = None
) -> str:
    """
    Remove a clip's automation envelope for one device or mixer parameter.

    Parameters:
    - track_index:     Index of the track
    - clip_index:      Index of the Session clip slot
    - device_index:    Index of the device, or -1 for the track's mixer
    - parameter_index: Index of the parameter to clear
    - parameter_name:  Name of the parameter to clear
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("clear_clip_envelope", {
            "track_index": track_index,
            "clip_index": clip_index,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "parameter_name": parameter_name
        })
        return (
            f"Cleared automation for '{result.get('parameter_name')}' "
            f"in clip '{result.get('clip_name')}'"
        )
    except Exception as e:
        logger.error(f"Error clearing clip envelope: {str(e)}")
        return f"Error clearing clip envelope: {str(e)}"


@mcp.tool()
@telemetry_tool("get_clip_notes")
def get_clip_notes(
    ctx: Context,
    track_index: int,
    clip_index: int,
    from_time: float = 0.0,
    time_span: Optional[float] = None,
    from_pitch: int = 0,
    pitch_span: int = 128
) -> str:
    """
    Read the notes already inside a MIDI clip.

    Everything else here only writes notes, so without this a part that already exists is
    invisible: you cannot reason about a melody, harmonise with it, or edit it. Read the
    clip first, then rebuild it with the changes you want.

    Each note reports pitch (MIDI number, 60 = C3 in Live's naming), start_time and
    duration in beats, velocity, and mute; Live 11 adds probability and velocity_deviation.

    Note that there is no way to edit or delete individual notes — add_notes_to_clip only
    appends. To change a part, read it, delete the clip, recreate it, and write back the
    notes you want.

    Parameters:
    - track_index: Index of the track
    - clip_index:  Index of the Session clip slot
    - from_time:   Start of the range to read, in beats (default 0.0)
    - time_span:   Length of the range in beats (default: the whole clip)
    - from_pitch:  Lowest MIDI pitch to include (default 0)
    - pitch_span:  How many semitones upward to include (default 128, i.e. everything)
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("get_clip_notes", {
            "track_index": track_index,
            "clip_index": clip_index,
            "from_time": from_time,
            "time_span": time_span,
            "from_pitch": from_pitch,
            "pitch_span": pitch_span
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error reading clip notes: {str(e)}")
        return f"Error reading clip notes: {str(e)}"


# Display-value targeting endpoints


@mcp.tool()
@telemetry_tool("set_parameter_to_display")
def set_parameter_to_display(
    ctx: Context,
    track_index: int,
    device_index: int,
    target: float,
    parameter_index: Optional[int] = None,
    parameter_name: Optional[str] = None
) -> str:
    """
    Set a parameter by the value it should READ ON SCREEN, in real-world units.

    Use this for anything a musician would state in units — "filter at 2 kHz", "master at
    -12 dB", "width 140%". Most Live parameters are stored as 0.0-1.0 and only *display*
    real units, often on a curve, so set_device_parameter needs the raw number while this
    tool finds it for you.

    Give target in the parameter's base unit:
      Hz for frequencies (2000 for 2 kHz), dB for gains (-12), % for percentages (140),
      seconds for times (0.25 for 250 ms), semitones for pitch.

    The search runs inside Live without touching the parameter, then assigns the winner.
    If the target lies outside what the parameter can reach, the nearest reachable value
    is used and the reply says so. Switch-like parameters showing names rather than
    numbers (filter types, on/off) are rejected — set those with set_device_parameter.

    Parameters:
    - track_index:     Index of the track; -1 is the master track, -2 the first return, -3 the second
    - device_index:    Index of the device, or -1 for the track's mixer
    - target:          Desired on-screen magnitude, in the base unit described above
    - parameter_index: Index of the parameter to set
    - parameter_name:  Name of the parameter to set
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("set_parameter_to_display", {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "parameter_name": parameter_name,
            "target": target
        })
        message = (
            f"Set '{result.get('parameter_name')}' on '{result.get('device_name')}' "
            f"to {result.get('display_value')} (raw {round(result.get('value', 0.0), 5)})"
        )
        if result.get("out_of_reach"):
            message += (
                f"; {target} was out of reach, this parameter spans "
                f"{result.get('display_min')} to {result.get('display_max')}"
            )
        return message
    except Exception as e:
        logger.error(f"Error setting parameter by display value: {str(e)}")
        return f"Error setting parameter by display value: {str(e)}"


@mcp.tool()
@telemetry_tool("convert_display_values")
def convert_display_values(
    ctx: Context,
    track_index: int,
    device_index: int,
    targets: List[float],
    parameter_index: Optional[int] = None,
    parameter_name: Optional[str] = None
) -> str:
    """
    Translate on-screen magnitudes into the raw values a parameter expects, changing nothing.

    This is the companion to set_clip_envelope, whose points need raw values: convert the
    musical intent first ("sweep 200 Hz to 8 kHz" -> [200, 8000]), then feed the returned
    values in as breakpoints.

    Targets use the parameter's base unit, as in set_parameter_to_display. Each result
    reports the raw value, what it displays as, and whether the target was reachable.

    Parameters:
    - track_index:     Index of the track; -1 is the master track, -2 the first return, -3 the second
    - device_index:    Index of the device, or -1 for the track's mixer
    - targets:         On-screen magnitudes to convert, e.g. [200, 8000]
    - parameter_index: Index of the parameter
    - parameter_name:  Name of the parameter
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("convert_display_values", {
            "track_index": track_index,
            "device_index": device_index,
            "parameter_index": parameter_index,
            "parameter_name": parameter_name,
            "targets": targets
        })
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error converting display values: {str(e)}")
        return f"Error converting display values: {str(e)}"


# Deletion endpoints


@mcp.tool()
@telemetry_tool("delete_clip")
def delete_clip(ctx: Context, track_index: int, clip_index: int) -> str:
    """
    Delete the clip in a Session clip slot.

    Destructive, but covered by Live's own undo (Ctrl/Cmd+Z). The clip's name is
    returned so the deletion can be checked afterwards.

    Parameters:
    - track_index: Index of the track
    - clip_index:  Index of the clip slot to empty
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_clip", {
            "track_index": track_index,
            "clip_index": clip_index
        })
        name = result.get("deleted_clip_name") or "(unnamed)"
        return (
            f"Deleted clip '{name}' from slot {clip_index} "
            f"on '{result.get('track_name')}'"
        )
    except Exception as e:
        logger.error(f"Error deleting clip: {str(e)}")
        return f"Error deleting clip: {str(e)}"


@mcp.tool()
@telemetry_tool("delete_device")
def delete_device(ctx: Context, track_index: int, device_index: int) -> str:
    """
    Remove one device from a track's chain.

    The counterpart to load_instrument_or_effect — use it to undo an effect you added, or
    to strip a chain back. Device indices shift down afterwards, so re-read the track with
    get_track_info before deleting another by index. Destructive, but covered by Live's undo.

    Parameters:
    - track_index:  Index of the track; -1 is the master track, -2 the first return
    - device_index: Index of the device in the chain (get_track_info lists them).
                    -1 is not accepted here: it means "mixer" elsewhere, and the mixer
                    is part of the track rather than a removable device.
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_device", {
            "track_index": track_index,
            "device_index": device_index
        })
        remaining = result.get("remaining_devices") or []
        return (
            f"Deleted '{result.get('deleted_device_name')}' from "
            f"'{result.get('track_name')}'. Chain is now: "
            f"{' > '.join(remaining) if remaining else 'empty'}"
        )
    except Exception as e:
        logger.error(f"Error deleting device: {str(e)}")
        return f"Error deleting device: {str(e)}"


@mcp.tool()
@telemetry_tool("delete_track")
def delete_track(ctx: Context, track_index: int) -> str:
    """
    Delete an entire track, along with every clip and device on it.

    Destructive and wider-reaching than delete_clip, though still covered by Live's
    undo. Track indices shift down after a deletion, so re-read the session with
    get_session_info before deleting another one by index.

    Parameters:
    - track_index: Index of the track to delete
    """
    try:
        ableton = get_ableton_connection()
        result = ableton.send_command("delete_track", {"track_index": track_index})
        return (
            f"Deleted track '{result.get('deleted_track_name')}' "
            f"({result.get('deleted_clips')} clips, {result.get('deleted_devices')} devices); "
            f"{result.get('remaining_tracks')} tracks remain"
        )
    except Exception as e:
        logger.error(f"Error deleting track: {str(e)}")
        return f"Error deleting track: {str(e)}"


# Main execution
def main():
    """Run the MCP server"""
    mcp.run()

if __name__ == "__main__":
    main()