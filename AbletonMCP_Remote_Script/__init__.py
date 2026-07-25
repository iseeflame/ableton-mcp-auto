# AbletonMCP/init.py
from __future__ import absolute_import, print_function, unicode_literals

from _Framework.ControlSurface import ControlSurface
import os
import re
import socket
import json
import threading
import time
import traceback

# Change queue import for Python 2
try:
    import Queue as queue  # Python 2
except ImportError:
    import queue  # Python 3

# Constants for socket communication
DEFAULT_PORT = 9877
HOST = "0.0.0.0"

def create_instance(c_instance):
    """Create and return the AbletonMCP script instance"""
    return AbletonMCP(c_instance)

class AbletonMCP(ControlSurface):
    """AbletonMCP Remote Script for Ableton Live"""
    
    def __init__(self, c_instance):
        """Initialize the control surface"""
        ControlSurface.__init__(self, c_instance)
        self.log_message("AbletonMCP Remote Script initializing...")
        
        # Socket server for communication
        self.server = None
        self.client_threads = []
        self.server_thread = None
        self.running = False
        
        # Cache the song reference for easier access
        self._song = self.song()
        
        # Start the socket server
        self.start_server()
        
        self.log_message("AbletonMCP initialized")
        
        # Show a message in Ableton
        self.show_message("AbletonMCP: Listening for commands on port " + str(DEFAULT_PORT))
    
    def disconnect(self):
        """Called when Ableton closes or the control surface is removed"""
        self.log_message("AbletonMCP disconnecting...")
        self.running = False
        
        # Stop the server
        if self.server:
            try:
                self.server.close()
            except:
                pass
        
        # Wait for the server thread to exit
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(1.0)
            
        # Clean up any client threads
        for client_thread in self.client_threads[:]:
            if client_thread.is_alive():
                # We don't join them as they might be stuck
                self.log_message("Client thread still alive during disconnect")
        
        ControlSurface.disconnect(self)
        self.log_message("AbletonMCP disconnected")
    
    def start_server(self):
        """Start the socket server in a separate thread"""
        try:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind((HOST, DEFAULT_PORT))
            self.server.listen(5)  # Allow up to 5 pending connections
            
            self.running = True
            self.server_thread = threading.Thread(target=self._server_thread)
            self.server_thread.daemon = True
            self.server_thread.start()
            
            self.log_message("Server started on port " + str(DEFAULT_PORT))
        except Exception as e:
            self.log_message("Error starting server: " + str(e))
            self.show_message("AbletonMCP: Error starting server - " + str(e))
    
    def _server_thread(self):
        """Server thread implementation - handles client connections"""
        try:
            self.log_message("Server thread started")
            # Set a timeout to allow regular checking of running flag
            self.server.settimeout(1.0)
            
            while self.running:
                try:
                    # Accept connections with timeout
                    client, address = self.server.accept()
                    self.log_message("Connection accepted from " + str(address))
                    self.show_message("AbletonMCP: Client connected")
                    
                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client,
                        args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                    
                    # Keep track of client threads
                    self.client_threads.append(client_thread)
                    
                    # Clean up finished client threads
                    self.client_threads = [t for t in self.client_threads if t.is_alive()]
                    
                except socket.timeout:
                    # No connection yet, just continue
                    continue
                except Exception as e:
                    if self.running:  # Only log if still running
                        self.log_message("Server accept error: " + str(e))
                    time.sleep(0.5)
            
            self.log_message("Server thread stopped")
        except Exception as e:
            self.log_message("Server thread error: " + str(e))
    
    def _handle_client(self, client):
        """Handle communication with a connected client"""
        self.log_message("Client handler started")
        client.settimeout(None)  # No timeout for client socket
        buffer = ''  # Changed from b'' to '' for Python 2
        
        try:
            while self.running:
                try:
                    # Receive data
                    data = client.recv(8192)
                    
                    if not data:
                        # Client disconnected
                        self.log_message("Client disconnected")
                        break
                    
                    # Accumulate data in buffer with explicit encoding/decoding
                    try:
                        # Python 3: data is bytes, decode to string
                        buffer += data.decode('utf-8')
                    except AttributeError:
                        # Python 2: data is already string
                        buffer += data
                    
                    try:
                        # Try to parse command from buffer
                        command = json.loads(buffer)  # Removed decode('utf-8')
                        buffer = ''  # Clear buffer after successful parse
                        
                        self.log_message("Received command: " + str(command.get("type", "unknown")))
                        
                        # Process the command and get response
                        response = self._process_command(command)
                        
                        # Send the response with explicit encoding
                        try:
                            # Python 3: encode string to bytes
                            client.sendall(json.dumps(response).encode('utf-8'))
                        except AttributeError:
                            # Python 2: string is already bytes
                            client.sendall(json.dumps(response))
                    except ValueError:
                        # Incomplete data, wait for more
                        continue
                        
                except Exception as e:
                    self.log_message("Error handling client data: " + str(e))
                    self.log_message(traceback.format_exc())
                    
                    # Send error response if possible
                    error_response = {
                        "status": "error",
                        "message": str(e)
                    }
                    try:
                        # Python 3: encode string to bytes
                        client.sendall(json.dumps(error_response).encode('utf-8'))
                    except AttributeError:
                        # Python 2: string is already bytes
                        client.sendall(json.dumps(error_response))
                    except:
                        # If we can't send the error, the connection is probably dead
                        break
                    
                    # For serious errors, break the loop
                    if not isinstance(e, ValueError):
                        break
        except Exception as e:
            self.log_message("Error in client handler: " + str(e))
        finally:
            try:
                client.close()
            except:
                pass
            self.log_message("Client handler stopped")
    
    def _process_command(self, command):
        """Process a command from the client and return a response"""
        command_type = command.get("type", "")
        params = command.get("params", {})
        
        # Initialize response
        response = {
            "status": "success",
            "result": {}
        }
        
        try:
            # Route the command to the appropriate handler
            if command_type == "get_session_info":
                response["result"] = self._get_session_info()
            elif command_type == "get_track_info":
                track_index = params.get("track_index", 0)
                response["result"] = self._get_track_info(track_index)
            # Commands that modify Live's state should be scheduled on the main thread
            elif command_type in ["create_midi_track", "create_audio_track",
                                 "create_return_track", "set_track_name",
                                 "create_clip", "create_audio_clip", "add_notes_to_clip", "set_clip_name",
                                 "set_tempo", "fire_clip", "stop_clip",
                                 "start_playback", "stop_playback", "load_browser_item",
                                 # Arrangement view – must run on the main thread
                                 "switch_to_arrangement_view", "set_current_song_time",
                                 "duplicate_session_clip_to_arrangement",
                                 # Device parameters / automation – must run on the main thread
                                 "set_device_parameter", "set_mixer_parameter",
                                 "set_clip_envelope", "clear_clip_envelope",
                                 # Deletion – must run on the main thread
                                 "delete_clip", "delete_track", "delete_device",
                                 # Note editing - must run on the main thread
                                 "remove_clip_notes", "modify_clip_notes",
                                 # Device chain order – must run on the main thread
                                 "move_device", "select_drum_pad",
                                 # Display-value targeting – must run on the main thread
                                 "set_parameter_to_display"]:
                # Use a thread-safe approach with a response queue
                response_queue = queue.Queue()
                
                # Define a function to execute on the main thread
                def main_thread_task():
                    try:
                        result = None
                        if command_type == "create_midi_track":
                            index = params.get("index", -1)
                            result = self._create_midi_track(index)
                        elif command_type == "create_audio_track":
                            index = params.get("index", -1)
                            result = self._create_audio_track(index)
                        elif command_type == "create_return_track":
                            result = self._create_return_track()
                        elif command_type == "set_track_name":
                            track_index = params.get("track_index", 0)
                            name = params.get("name", "")
                            result = self._set_track_name(track_index, name)
                        elif command_type == "create_clip":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            length = params.get("length", 4.0)
                            result = self._create_clip(track_index, clip_index, length)
                        elif command_type == "create_audio_clip":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            path = params.get("path", "")
                            result = self._create_audio_clip(track_index, clip_index, path)
                        elif command_type == "add_notes_to_clip":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            notes = params.get("notes", [])
                            result = self._add_notes_to_clip(track_index, clip_index, notes)
                        elif command_type == "set_clip_name":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            name = params.get("name", "")
                            result = self._set_clip_name(track_index, clip_index, name)
                        elif command_type == "set_tempo":
                            tempo = params.get("tempo", 120.0)
                            result = self._set_tempo(tempo)
                        elif command_type == "fire_clip":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            result = self._fire_clip(track_index, clip_index)
                        elif command_type == "stop_clip":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            result = self._stop_clip(track_index, clip_index)
                        elif command_type == "start_playback":
                            result = self._start_playback()
                        elif command_type == "stop_playback":
                            result = self._stop_playback()
                        elif command_type == "load_instrument_or_effect":
                            track_index = params.get("track_index", 0)
                            uri = params.get("uri", "")
                            result = self._load_instrument_or_effect(track_index, uri)
                        elif command_type == "load_browser_item":
                            track_index = params.get("track_index", 0)
                            item_uri = params.get("item_uri", "")
                            result = self._load_browser_item(track_index, item_uri)
                        # ── Arrangement view commands ──────────────────────────────
                        elif command_type == "switch_to_arrangement_view":
                            result = self._switch_to_arrangement_view()
                        elif command_type == "set_current_song_time":
                            time_val = params.get("time", 0.0)
                            result = self._set_current_song_time(time_val)
                        elif command_type == "duplicate_session_clip_to_arrangement":
                            track_index = params.get("track_index", 0)
                            clip_index = params.get("clip_index", 0)
                            destination_time = params.get("destination_time", 0.0)
                            result = self._duplicate_session_clip_to_arrangement(
                                track_index, clip_index, destination_time)
                        # ── Device parameters / automation ─────────────────────────
                        elif command_type == "set_device_parameter":
                            result = self._set_device_parameter(
                                params.get("track_index", 0),
                                params.get("device_index", 0),
                                params.get("parameter_index", None),
                                params.get("parameter_name", None),
                                params.get("value", None),
                                params.get("device_path", None))
                        elif command_type == "set_mixer_parameter":
                            result = self._set_device_parameter(
                                params.get("track_index", 0),
                                -1,
                                None,
                                params.get("parameter_name", "volume"),
                                params.get("value", None))
                        elif command_type == "set_clip_envelope":
                            result = self._set_clip_envelope(
                                params.get("track_index", 0),
                                params.get("clip_index", 0),
                                params.get("device_index", 0),
                                params.get("parameter_index", None),
                                params.get("parameter_name", None),
                                params.get("points", []),
                                params.get("interpolate", False),
                                params.get("step_size", 0.0625),
                                params.get("clear_existing", True),
                                params.get("device_path", None))
                        elif command_type == "clear_clip_envelope":
                            result = self._clear_clip_envelope(
                                params.get("track_index", 0),
                                params.get("clip_index", 0),
                                params.get("device_index", 0),
                                params.get("parameter_index", None),
                                params.get("parameter_name", None),
                                params.get("device_path", None))
                        # ── Deletion ────────────────────────────────────────────────
                        elif command_type == "delete_clip":
                            result = self._delete_clip(
                                params.get("track_index", 0),
                                params.get("clip_index", 0))
                        elif command_type == "delete_track":
                            result = self._delete_track(params.get("track_index", 0))
                        elif command_type == "select_drum_pad":
                            result = self._select_drum_pad(
                                params.get("track_index", 0),
                                params.get("note", 36),
                                params.get("device_path", None))
                        elif command_type == "move_device":
                            result = self._move_device(
                                params.get("track_index", 0),
                                params.get("device_index", 0),
                                params.get("to_position", 0),
                                params.get("to_track_index", None),
                                params.get("from_path", None),
                                params.get("to_path", None))
                        elif command_type == "remove_clip_notes":
                            result = self._remove_clip_notes(
                                params.get("track_index", 0),
                                params.get("clip_index", 0),
                                params.get("from_time", 0.0),
                                params.get("time_span", None),
                                params.get("from_pitch", 0),
                                params.get("pitch_span", 128))
                        elif command_type == "modify_clip_notes":
                            result = self._modify_clip_notes(
                                params.get("track_index", 0),
                                params.get("clip_index", 0),
                                params.get("modifications", []))
                        elif command_type == "delete_device":
                            result = self._delete_device(
                                params.get("track_index", 0),
                                params.get("device_index", 0),
                                params.get("device_path", None))
                        elif command_type == "set_parameter_to_display":
                            result = self._set_parameter_to_display(
                                params.get("track_index", 0),
                                params.get("device_index", 0),
                                params.get("parameter_index", None),
                                params.get("parameter_name", None),
                                params.get("target", None),
                                params.get("device_path", None))

                        # Put the result in the queue
                        response_queue.put({"status": "success", "result": result})
                    except Exception as e:
                        self.log_message("Error in main thread task: " + str(e))
                        self.log_message(traceback.format_exc())
                        response_queue.put({"status": "error", "message": str(e)})
                
                # Schedule the task to run on the main thread
                try:
                    self.schedule_message(0, main_thread_task)
                except AssertionError:
                    # If we're already on the main thread, execute directly
                    main_thread_task()
                
                # Wait for the response with a timeout. Some commands (notably
                # create_audio_clip, which decodes/imports the audio file on
                # the main thread) can take longer than the default 10s on
                # larger files — give them more headroom.
                # Max for Live instruments can take tens of seconds to boot, and the
                # load reports failure while actually succeeding if we give up early.
                long_running_commands = {"create_audio_clip": 60.0,
                                         "load_browser_item": 60.0}
                queue_timeout = long_running_commands.get(command_type, 10.0)
                try:
                    task_response = response_queue.get(timeout=queue_timeout)
                    if task_response.get("status") == "error":
                        response["status"] = "error"
                        response["message"] = task_response.get("message", "Unknown error")
                    else:
                        response["result"] = task_response.get("result", {})
                except queue.Empty:
                    response["status"] = "error"
                    response["message"] = "Timeout waiting for operation to complete"
            elif command_type == "get_browser_item":
                uri = params.get("uri", None)
                path = params.get("path", None)
                response["result"] = self._get_browser_item(uri, path)
            elif command_type == "get_browser_categories":
                category_type = params.get("category_type", "all")
                response["result"] = self._get_browser_categories(category_type)
            elif command_type == "get_browser_items":
                path = params.get("path", "")
                item_type = params.get("item_type", "all")
                response["result"] = self._get_browser_items(path, item_type)
            # Add the new browser commands
            elif command_type == "get_browser_tree":
                category_type = params.get("category_type", "all")
                response["result"] = self.get_browser_tree(category_type)
            elif command_type == "get_browser_items_at_path":
                path = params.get("path", "")
                response["result"] = self.get_browser_items_at_path(path)
            # Read-only arrangement command – no main-thread scheduling required
            elif command_type == "get_arrangement_clips":
                track_index = params.get("track_index", 0)
                response["result"] = self._get_arrangement_clips(track_index)
            # Read-only device parameter inspection – no main-thread scheduling required
            elif command_type == "get_device_parameters":
                response["result"] = self._get_device_parameters(
                    params.get("track_index", 0),
                    params.get("device_index", 0),
                    params.get("device_path", None))
            elif command_type == "get_clip_notes":
                response["result"] = self._get_clip_notes(
                    params.get("track_index", 0),
                    params.get("clip_index", 0),
                    params.get("from_time", 0.0),
                    params.get("time_span", None),
                    params.get("from_pitch", 0),
                    params.get("pitch_span", 128))
            elif command_type == "get_device_tree":
                response["result"] = self._get_device_tree(
                    params.get("track_index", 0),
                    params.get("max_depth", 4))
            elif command_type == "describe_live_api":
                response["result"] = self._describe_live_api(
                    params.get("path", ""),
                    params.get("include_members", True),
                    params.get("root", "song"))
            elif command_type == "convert_display_values":
                response["result"] = self._convert_display_values(
                    params.get("track_index", 0),
                    params.get("device_index", 0),
                    params.get("parameter_index", None),
                    params.get("parameter_name", None),
                    params.get("targets", []),
                    params.get("device_path", None))
            elif command_type == "get_clip_envelope":
                response["result"] = self._get_clip_envelope(
                    params.get("track_index", 0),
                    params.get("clip_index", 0),
                    params.get("device_index", 0),
                    params.get("parameter_index", None),
                    params.get("parameter_name", None),
                    params.get("from_time", 0.0),
                    params.get("to_time", None),
                    params.get("samples", 17),
                    params.get("device_path", None))
            else:
                response["status"] = "error"
                response["message"] = "Unknown command: " + command_type
        except Exception as e:
            self.log_message("Error processing command: " + str(e))
            self.log_message(traceback.format_exc())
            response["status"] = "error"
            response["message"] = str(e)
        
        return response
    
    # Command implementations
    
    def _safe_song_property(self, attr, cast, default):
        """Read self._song.<attr> with cast, returning default on common failures.
        Catches only narrow exceptions so genuine bugs still surface."""
        try:
            return cast(getattr(self._song, attr))
        except (AttributeError, TypeError, ValueError):
            return default

    def _get_session_info(self):
        """Get information about the current session"""
        try:
            result = {
                "tempo": self._song.tempo,
                "signature_numerator": self._song.signature_numerator,
                "signature_denominator": self._song.signature_denominator,
                "track_count": len(self._song.tracks),
                "return_track_count": len(self._song.return_tracks),
                "master_track": {
                    "name": "Master",
                    "volume": self._song.master_track.mixer_device.volume.value,
                    "panning": self._song.master_track.mixer_device.panning.value
                },
                # Transport / playback state — lets clients render a live
                # playhead without polling separately. Each property is read
                # via _safe_song_property so an attribute missing on a given
                # Live version falls back to its default rather than breaking
                # the response shape.
                "is_playing":        self._safe_song_property("is_playing",        bool,  False),
                "current_song_time": self._safe_song_property("current_song_time", float, 0.0),
                "song_length":       self._safe_song_property("song_length",       float, 0.0),
                "loop":              self._safe_song_property("loop",              bool,  False),
                "loop_start":        self._safe_song_property("loop_start",        float, 0.0),
                "loop_length":       self._safe_song_property("loop_length",       float, 0.0),
            }
            return result
        except Exception as e:
            self.log_message("Error getting session info: " + str(e))
            raise
    
    def _get_track_info(self, track_index):
        """Get information about a track"""
        try:
            track = self._resolve_track(track_index)

            # The master and return tracks have no clip slots, and Live signals absent
            # members by raising rather than omitting them.
            try:
                slots = track.clip_slots
            except Exception:
                slots = []

            # Get clip slots
            clip_slots = []
            for slot_index, slot in enumerate(slots):
                clip_info = None
                if slot.has_clip:
                    clip = slot.clip
                    clip_info = {
                        "name": clip.name,
                        "length": clip.length,
                        "is_playing": clip.is_playing,
                        "is_recording": clip.is_recording
                    }
                
                clip_slots.append({
                    "index": slot_index,
                    "has_clip": slot.has_clip,
                    "clip": clip_info
                })
            
            # Get devices
            devices = []
            for device_index, device in enumerate(track.devices):
                devices.append({
                    "index": device_index,
                    "name": device.name,
                    "class_name": device.class_name,
                    "type": self._get_device_type(device)
                })
            
            def optional(name):
                """Read a track attribute that only regular tracks carry."""
                try:
                    return getattr(track, name)
                except Exception:
                    return None

            kind = "regular"
            if track_index == -1:
                kind = "master"
            elif track_index <= -2:
                kind = "return"

            result = {
                "index": track_index,
                "name": track.name,
                "kind": kind,
                "is_audio_track": optional("has_audio_input"),
                "is_midi_track": optional("has_midi_input"),
                "mute": optional("mute"),
                "solo": optional("solo"),
                "arm": optional("arm"),
                "volume": track.mixer_device.volume.value,
                "panning": track.mixer_device.panning.value,
                "clip_slots": clip_slots,
                "devices": devices
            }
            return result
        except Exception as e:
            self.log_message("Error getting track info: " + str(e))
            raise
    
    def _create_midi_track(self, index):
        """Create a new MIDI track at the specified index"""
        try:
            # Create the track
            self._song.create_midi_track(index)
            
            # Get the new track
            new_track_index = len(self._song.tracks) - 1 if index == -1 else index
            new_track = self._song.tracks[new_track_index]
            
            result = {
                "index": new_track_index,
                "name": new_track.name
            }
            return result
        except Exception as e:
            self.log_message("Error creating MIDI track: " + str(e))
            raise
    
    
    def _create_audio_track(self, index):
        """Create a new audio track at the given index (-1 appends)"""
        try:
            self._song.create_audio_track(index)
            new_index = len(self._song.tracks) - 1 if index == -1 else index
            track = self._song.tracks[new_index]
            return {
                "index": new_index,
                "name": track.name,
                "is_audio_track": True
            }
        except Exception as e:
            self.log_message("Error creating audio track: " + str(e))
            raise

    def _create_return_track(self):
        """Create a new return track. Live appends returns rather than taking an index,
        and keeps them outside song.tracks, so the negative index for addressing it
        afterwards is reported back."""
        try:
            before = len(getattr(self._song, "return_tracks", None) or [])
            self._song.create_return_track()
            returns = getattr(self._song, "return_tracks", None) or []
            if len(returns) <= before:
                raise RuntimeError("Live did not add a return track")

            position = len(returns) - 1
            track = returns[position]
            return {
                "return_index": position,
                "track_index": -(position + 2),
                "name": track.name,
                "return_count": len(returns),
                "send_name": "send_" + str(position)
            }
        except Exception as e:
            self.log_message("Error creating return track: " + str(e))
            raise

    def _set_track_name(self, track_index, name):
        """Set the name of a track"""
        try:
            track = self._resolve_track(track_index)
            track.name = name
            
            result = {
                "name": track.name
            }
            return result
        except Exception as e:
            self.log_message("Error setting track name: " + str(e))
            raise
    
    def _create_clip(self, track_index, clip_index, length):
        """Create a new MIDI clip in the specified track and clip slot"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            
            track = self._song.tracks[track_index]
            
            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")
            
            clip_slot = track.clip_slots[clip_index]
            
            # Check if the clip slot already has a clip
            if clip_slot.has_clip:
                raise Exception("Clip slot already has a clip")
            
            # Create the clip
            clip_slot.create_clip(length)
            
            result = {
                "name": clip_slot.clip.name,
                "length": clip_slot.clip.length
            }
            return result
        except Exception as e:
            self.log_message("Error creating clip: " + str(e))
            raise

    def _create_audio_clip(self, track_index, clip_index, path):
        """Create an audio clip in the specified audio track clip slot by importing a file.

        Requires Ableton Live 12.0.5 or newer (the underlying
        ClipSlot.create_audio_clip Live API was introduced in 12.0.5 — it is
        not available in earlier 12.0.x releases).
        """
        try:
            if not path:
                raise ValueError("Audio file path is required")

            if not os.path.isabs(path):
                raise ValueError("Audio file path must be absolute (got: %s)" % path)

            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            # Must be an audio track. Audio tracks expose audio input; MIDI
            # tracks don't. Reject MIDI / return tracks up front so the caller
            # gets a clear error instead of a Live API exception.
            if getattr(track, "has_midi_input", False) or not getattr(track, "has_audio_input", True):
                raise ValueError("Track %d is not an audio track" % track_index)

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]

            if clip_slot.has_clip:
                raise Exception("Clip slot already has a clip")

            if not hasattr(clip_slot, "create_audio_clip"):
                raise Exception(
                    "ClipSlot.create_audio_clip is unavailable in this Ableton Live "
                    "version. Requires Live 12.0.5 or newer."
                )

            clip_slot.create_audio_clip(path)

            result = {
                "name": clip_slot.clip.name,
                "length": clip_slot.clip.length,
                "is_audio_clip": clip_slot.clip.is_audio_clip
            }
            return result
        except Exception as e:
            self.log_message("Error creating audio clip: " + str(e))
            raise

    def _add_notes_to_clip(self, track_index, clip_index, notes):
        """Add MIDI notes to a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            
            track = self._song.tracks[track_index]
            
            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")
            
            clip_slot = track.clip_slots[clip_index]
            
            if not clip_slot.has_clip:
                raise Exception("No clip in slot")
            
            clip = clip_slot.clip
            
            # Convert note data to Live's format
            live_notes = []
            for note in notes:
                pitch = note.get("pitch", 60)
                start_time = note.get("start_time", 0.0)
                duration = note.get("duration", 0.25)
                velocity = note.get("velocity", 100)
                mute = note.get("mute", False)
                
                live_notes.append((pitch, start_time, duration, velocity, mute))
            
            # Add the notes
            clip.set_notes(tuple(live_notes))
            
            result = {
                "note_count": len(notes)
            }
            return result
        except Exception as e:
            self.log_message("Error adding notes to clip: " + str(e))
            raise
    
    def _set_clip_name(self, track_index, clip_index, name):
        """Set the name of a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            
            track = self._song.tracks[track_index]
            
            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")
            
            clip_slot = track.clip_slots[clip_index]
            
            if not clip_slot.has_clip:
                raise Exception("No clip in slot")
            
            clip = clip_slot.clip
            clip.name = name
            
            result = {
                "name": clip.name
            }
            return result
        except Exception as e:
            self.log_message("Error setting clip name: " + str(e))
            raise
    
    def _set_tempo(self, tempo):
        """Set the tempo of the session"""
        try:
            self._song.tempo = tempo
            
            result = {
                "tempo": self._song.tempo
            }
            return result
        except Exception as e:
            self.log_message("Error setting tempo: " + str(e))
            raise
    
    def _fire_clip(self, track_index, clip_index):
        """Fire a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            
            track = self._song.tracks[track_index]
            
            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")
            
            clip_slot = track.clip_slots[clip_index]
            
            if not clip_slot.has_clip:
                raise Exception("No clip in slot")
            
            clip_slot.fire()
            
            result = {
                "fired": True
            }
            return result
        except Exception as e:
            self.log_message("Error firing clip: " + str(e))
            raise
    
    def _stop_clip(self, track_index, clip_index):
        """Stop a clip"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            
            track = self._song.tracks[track_index]
            
            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")
            
            clip_slot = track.clip_slots[clip_index]
            
            clip_slot.stop()
            
            result = {
                "stopped": True
            }
            return result
        except Exception as e:
            self.log_message("Error stopping clip: " + str(e))
            raise
    
    
    def _start_playback(self):
        """Start playing the session"""
        try:
            self._song.start_playing()
            
            result = {
                "playing": self._song.is_playing
            }
            return result
        except Exception as e:
            self.log_message("Error starting playback: " + str(e))
            raise
    
    def _stop_playback(self):
        """Stop playing the session"""
        try:
            self._song.stop_playing()
            
            result = {
                "playing": self._song.is_playing
            }
            return result
        except Exception as e:
            self.log_message("Error stopping playback: " + str(e))
            raise
    
    # ── Arrangement view implementations ──────────────────────────────────────

    def _switch_to_arrangement_view(self):
        """Switch Ableton's main window to the Arrangement view"""
        try:
            self.application().view.show_view("Arranger")
            return {"view": "Arranger"}
        except Exception as e:
            self.log_message("Error switching to arrangement view: " + str(e))
            raise

    def _set_current_song_time(self, time_val):
        """Move the arrangement playhead to a position in beats"""
        try:
            self._song.current_song_time = float(time_val)
            return {"current_song_time": self._song.current_song_time}
        except Exception as e:
            self.log_message("Error setting current song time: " + str(e))
            raise

    def _get_arrangement_clips(self, track_index):
        """Return all clips placed in the Arrangement timeline for a track.

        Each clip dict contains:
          name, start_time, end_time, length, color,
          is_midi_clip, is_audio_clip, is_playing
        """
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]
            clips = []

            # track.arrangement_clips is available in Live 11 / 12
            for clip in track.arrangement_clips:
                clips.append({
                    "name": clip.name,
                    "start_time": clip.start_time,
                    "end_time": clip.end_time,
                    "length": clip.length,
                    "color": clip.color,
                    "is_midi_clip": clip.is_midi_clip,
                    "is_audio_clip": clip.is_audio_clip,
                    "is_playing": clip.is_playing
                })

            return {
                "track_index": track_index,
                "track_name": track.name,
                "clip_count": len(clips),
                "clips": clips
            }
        except Exception as e:
            self.log_message("Error getting arrangement clips: " + str(e))
            raise

    def _duplicate_session_clip_to_arrangement(self, track_index, clip_index, destination_time):
        """Copy a Session-view clip into the Arrangement timeline.

        Uses the real Live API:
          track.duplicate_clip_to_arrangement(clip, destination_time)

        Available in Live 11 / 12.  destination_time is in beats from the
        start of the arrangement.
        """
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]

            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip slot index out of range")

            clip_slot = track.clip_slots[clip_index]

            if not clip_slot.has_clip:
                raise Exception(
                    "No clip in slot " + str(clip_index) +
                    " on track " + str(track_index)
                )

            clip = clip_slot.clip

            # Duplicate to arrangement at the requested beat position
            track.duplicate_clip_to_arrangement(clip, float(destination_time))

            return {
                "success": True,
                "track_index": track_index,
                "track_name": track.name,
                "clip_name": clip.name,
                "destination_time": destination_time
            }
        except Exception as e:
            self.log_message("Error duplicating clip to arrangement: " + str(e))
            raise

    # ── Deletion implementations ──────────────────────────────────────────────

    def _delete_clip(self, track_index, clip_index):
        """Delete the clip in a Session clip slot"""
        try:
            if track_index < 0 or track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")
            track = self._song.tracks[track_index]
            if clip_index < 0 or clip_index >= len(track.clip_slots):
                raise IndexError("Clip index out of range")

            clip_slot = track.clip_slots[clip_index]
            if not clip_slot.has_clip:
                raise Exception("No clip in track " + str(track_index) +
                                ", slot " + str(clip_index))

            # Report what was removed so the deletion is auditable after the fact.
            clip_name = clip_slot.clip.name
            clip_slot.delete_clip()

            return {
                "success": True,
                "track_index": track_index,
                "track_name": track.name,
                "clip_index": clip_index,
                "deleted_clip_name": clip_name
            }
        except Exception as e:
            self.log_message("Error deleting clip: " + str(e))
            raise

    def _chains_of(self, device):
        """A rack's chains, or None. Live raises on .chains for plain devices."""
        try:
            chains = device.chains
        except Exception:
            return None
        return chains

    def _resolve_device_path(self, track_index, path):
        """Resolve a dotted path into a device or a chain inside a rack.

        Indices alternate: the first is a device on the track, the next a chain inside
        that device, the next a device inside that chain, and so on. An odd number of
        segments therefore always lands on a device, an even number on a chain."""
        track = self._resolve_track(track_index)
        segments = [seg for seg in str(path).split(".") if seg != ""]
        if not segments:
            raise ValueError("A device path needs at least one index, e.g. '0' or '0.0.1'")

        node, kind, walked = track, "track", []
        for segment in segments:
            try:
                index = int(segment)
            except ValueError:
                raise ValueError("Device path segments must be numbers, got '" + segment + "'")
            walked.append(segment)
            here = ".".join(walked)

            if kind in ("track", "chain"):
                devices = node.devices
                if index < 0 or index >= len(devices):
                    raise IndexError("No device " + str(index) + " at '" + here +
                                     "'; there are " + str(len(devices)))
                node, kind = devices[index], "device"
            else:
                chains = self._chains_of(node)
                if chains is None:
                    raise ValueError("'" + node.name + "' is not a rack, so it has no "
                                     "chains to descend into at '" + here + "'")
                if index < 0 or index >= len(chains):
                    raise IndexError("No chain " + str(index) + " at '" + here +
                                     "'; there are " + str(len(chains)))
                node, kind = chains[index], "chain"
        return node, kind

    def _describe_chain_notes(self, device):
        """Map a drum rack's chains, by index, to the pad note that triggers them.

        Live hands out a fresh Python wrapper on every attribute access, so id() is
        meaningless here and recycled addresses quietly produce a plausible but wrong
        mapping. Chains are compared with == instead, and if that yields nothing the
        result falls back to pad order - marked as inferred, so a guess is never
        mistaken for a fact."""
        mapping = {}
        try:
            pads = list(device.drum_pads)
            chains = list(device.chains)
        except Exception:
            return mapping
        if not chains:
            return mapping

        filled = []
        for pad in pads:
            try:
                pad_chains = list(pad.chains)
            except Exception:
                continue
            if not pad_chains:
                continue
            filled.append((pad, pad_chains))

        for pad, pad_chains in filled:
            for pad_chain in pad_chains:
                for index, chain in enumerate(chains):
                    if index in mapping:
                        continue
                    try:
                        same = bool(chain == pad_chain)
                    except Exception:
                        same = False
                    if same:
                        mapping[index] = {"note": pad.note, "pad_name": pad.name,
                                          "note_source": "matched"}
                        break

        if not mapping and len(filled) == len(chains):
            # Live lists a drum rack's chains in pad order, so position lines them up
            # when the objects refuse to compare.
            for index, (pad, _) in enumerate(sorted(filled, key=lambda item: item[0].note)):
                mapping[index] = {"note": pad.note, "pad_name": pad.name,
                                  "note_source": "inferred_by_order"}
        return mapping

    def _get_device_tree(self, track_index, max_depth):
        """Walk devices, their chains, and the devices inside those chains.

        Every node carries the path that addresses it, so a nested device or chain can
        be named in later calls without counting indices by hand."""
        try:
            track = self._resolve_track(track_index)
            depth_limit = max(1, min(int(max_depth), 8))

            def walk_devices(container, prefix, depth):
                out = []
                for index, device in enumerate(container.devices):
                    path = (prefix + "." if prefix else "") + str(index)
                    entry = {
                        "path": path,
                        "name": device.name,
                        "class_name": device.class_name,
                        "type": self._get_device_type(device)
                    }
                    chains = self._chains_of(device) if depth < depth_limit else None
                    if chains is not None and len(chains):
                        notes = self._describe_chain_notes(device)
                        entry["chains"] = []
                        for chain_index, chain in enumerate(chains):
                            chain_path = path + "." + str(chain_index)
                            chain_entry = {
                                "path": chain_path,
                                "name": chain.name,
                                "device_count": len(chain.devices)
                            }
                            chain_entry.update(notes.get(chain_index, {}))
                            if depth + 1 < depth_limit:
                                chain_entry["devices"] = walk_devices(
                                    chain, chain_path, depth + 2)
                            entry["chains"].append(chain_entry)
                    out.append(entry)
                return out

            return {
                "track_index": track_index,
                "track_name": track.name,
                "devices": walk_devices(track, "", 1)
            }
        except Exception as e:
            self.log_message("Error building device tree: " + str(e))
            raise

    def _find_drum_rack(self, track_index, device_path):
        """The drum rack to work on: the one named by device_path, or the first one
        found anywhere in the track's device tree."""
        if device_path is not None:
            node, kind = self._resolve_device_path(track_index, device_path)
            if kind != "device":
                raise ValueError("device_path must point at a device, not a chain")
            return node

        def search(container, depth=0):
            if depth > 6:
                return None
            for device in container.devices:
                try:
                    if device.can_have_drum_pads:
                        return device
                except Exception:
                    pass
                chains = self._chains_of(device)
                for chain in (chains or []):
                    found = search(chain, depth + 1)
                    if found is not None:
                        return found
            return None

        rack = search(self._resolve_track(track_index))
        if rack is None:
            raise ValueError(
                "No drum rack on this track. Load one first, or pass device_path.")
        return rack

    def _select_drum_pad(self, track_index, note, device_path=None):
        """Point Live's drum rack at one pad.

        The browser loads a sample onto whichever pad is selected, which is the only
        way to place samples on specific pads: there is no API that loads into a pad
        directly, and an empty pad has no chain to move a device into."""
        try:
            rack = self._find_drum_rack(track_index, device_path)
            wanted = int(note)

            target = None
            for pad in rack.drum_pads:
                if pad.note == wanted:
                    target = pad
                    break
            if target is None:
                raise IndexError("No drum pad for note " + str(wanted) +
                                 "; drum racks cover 0-127")

            self._song.view.selected_track = self._resolve_track(track_index)
            rack.view.selected_drum_pad = target

            return {
                "success": True,
                "track_index": track_index,
                "rack_name": rack.name,
                "note": wanted,
                "pad_name": target.name,
                "devices_on_pad": [d.name for chain in target.chains for d in chain.devices]
            }
        except Exception as e:
            self.log_message("Error selecting drum pad: " + str(e))
            raise

    def _move_device(self, track_index, device_index, to_position, to_track_index,
                     from_path=None, to_path=None):
        """Reorder a device within its chain, or move it onto another track.

        Position 0 puts it before everything, len(devices) at the end. Live silently
        settles for the nearest legal position when the requested one is impossible,
        so find_device_position is consulted first and any difference is reported
        rather than passed off as success."""
        try:
            source = self._resolve_track(track_index)
            if from_path is not None:
                device, kind = self._resolve_device_path(track_index, from_path)
                if kind != "device":
                    raise ValueError("from_path must point at a device, not a chain")
            else:
                if device_index < 0 or device_index >= len(source.devices):
                    raise IndexError(
                        "Device index out of range: '" + source.name + "' has " +
                        str(len(source.devices)) + " device(s)")
                device = source.devices[device_index]
            device_name = device.name

            if to_path is not None:
                destination, kind = self._resolve_device_path(
                    track_index if to_track_index is None else to_track_index, to_path)
                if kind != "chain":
                    raise ValueError(
                        "to_path must point at a chain, so it needs an even number of "
                        "segments - '0.0' is the first chain of the first device")
            elif to_track_index is None:
                destination = source
            else:
                destination = self._resolve_track(to_track_index)
            requested = int(to_position)

            landing = self._song.find_device_position(device, destination, requested)
            if landing < 0:
                raise ValueError(
                    "'" + device_name + "' cannot go into '" + destination.name +
                    "' at all - an instrument will not fit an audio track, and a MIDI "
                    "effect will not fit after an instrument.")

            actual = self._song.move_device(device, destination, requested)

            return {
                "success": True,
                "device_name": device_name,
                "from_track_index": track_index,
                "from_track_name": source.name,
                "from_device_index": device_index,
                "to_track_index": track_index if to_track_index is None else to_track_index,
                "to_name": destination.name,
                "to_path": to_path,
                "requested_position": requested,
                "final_position": actual,
                "adjusted": actual != requested,
                "chain": [d.name for d in destination.devices]
            }
        except Exception as e:
            self.log_message("Error moving device: " + str(e))
            raise

    def _delete_device(self, track_index, device_index, device_path=None):
        """Remove one device, from a track's chain or from inside a rack"""
        try:
            if device_path is not None:
                # The owner is the container one level up, so the last segment is the
                # index to delete and everything before it names the container.
                segments = [seg for seg in str(device_path).split(".") if seg != ""]
                if not segments:
                    raise ValueError("device_path needs at least one index")
                if len(segments) % 2 == 0:
                    raise ValueError(
                        "device_path must point at a device, not a chain - an odd number "
                        "of segments, like '0' or '0.0.1'")
                target_index = int(segments[-1])
                if len(segments) == 1:
                    owner = self._resolve_track(track_index)
                    owner_name = owner.name
                else:
                    owner, kind = self._resolve_device_path(
                        track_index, ".".join(segments[:-1]))
                    owner_name = owner.name
                if target_index < 0 or target_index >= len(owner.devices):
                    raise IndexError(
                        "No device " + str(target_index) + " in '" + owner_name +
                        "'; there are " + str(len(owner.devices)))
                device_name = owner.devices[target_index].name
                owner.delete_device(target_index)
                return {
                    "success": True,
                    "track_index": track_index,
                    "track_name": owner_name,
                    "device_path": device_path,
                    "deleted_device_name": device_name,
                    "remaining_devices": [d.name for d in owner.devices]
                }

            if device_index == -1:
                raise ValueError(
                    "device_index -1 addresses the mixer, which is part of the track and "
                    "cannot be deleted. Pass the index of a device in the chain.")

            track = self._resolve_track(track_index)
            if device_index < 0 or device_index >= len(track.devices):
                raise IndexError(
                    "Device index out of range: '" + track.name + "' has " +
                    str(len(track.devices)) + " device(s)")

            # Report what went, so the deletion can be checked after the fact.
            device_name = track.devices[device_index].name
            track.delete_device(device_index)

            return {
                "success": True,
                "track_index": track_index,
                "track_name": track.name,
                "device_index": device_index,
                "deleted_device_name": device_name,
                "remaining_devices": [d.name for d in track.devices]
            }
        except Exception as e:
            self.log_message("Error deleting device: " + str(e))
            raise

    def _delete_track(self, track_index):
        """Delete an entire track, with everything on it.

        Accepts the same negative indices as everything else: -2, -3 ... remove return
        tracks, which Live deletes through a separate call. -1 is the master, which
        cannot be removed at all."""
        try:
            if track_index == -1:
                raise ValueError(
                    "The master track cannot be deleted; every Live set has exactly one.")

            if track_index <= -2:
                returns = getattr(self._song, "return_tracks", None) or []
                position = -track_index - 2
                if position >= len(returns):
                    raise IndexError(
                        "Return track index " + str(track_index) + " asks for return " +
                        str(position) + ", but this set has " + str(len(returns)))
                target = returns[position]
                name = target.name
                device_count = len(target.devices)
                self._song.delete_return_track(position)
                return {
                    "success": True,
                    "track_index": track_index,
                    "deleted_track_name": name,
                    "deleted_clips": 0,
                    "deleted_devices": device_count,
                    "remaining_tracks": len(self._song.tracks),
                    "remaining_returns": len(getattr(self._song, "return_tracks", None) or [])
                }

            if track_index >= len(self._song.tracks):
                raise IndexError("Track index out of range")

            track = self._song.tracks[track_index]
            track_name = track.name
            clip_count = len([slot for slot in track.clip_slots if slot.has_clip])
            device_count = len(track.devices)

            self._song.delete_track(track_index)

            return {
                "success": True,
                "track_index": track_index,
                "deleted_track_name": track_name,
                "deleted_clips": clip_count,
                "deleted_devices": device_count,
                "remaining_tracks": len(self._song.tracks)
            }
        except Exception as e:
            self.log_message("Error deleting track: " + str(e))
            raise

    # ── Device parameter / automation implementations ─────────────────────────

    # Upper bound on steps written by a single set_clip_envelope call, so a tiny
    # step_size over a long clip can't lock up the main thread.
    MAX_ENVELOPE_STEPS = 4000

    def _get_mixer_parameters(self, track):
        """Ordered (key, DeviceParameter) pairs for a track's mixer device.

        cue_volume and crossfader exist only on the master track, and Live signals that
        by RAISING on attribute access rather than omitting the attribute - getattr's
        default would not catch it, so each access needs its own guard."""
        mixer = track.mixer_device
        entries = []
        for attr in ("volume", "panning", "track_activator", "cue_volume", "crossfader"):
            try:
                param = getattr(mixer, attr)
            except Exception:
                continue
            if param is not None and hasattr(param, "value"):
                entries.append((attr, param))
        try:
            sends = mixer.sends or []
        except Exception:
            sends = []
        for send_index, send in enumerate(sends):
            entries.append(("send_" + str(send_index), send))
        return entries

    def _resolve_track(self, track_index):
        """Resolve a track index to a track object.

        Live keeps the master and the returns out of song.tracks, so negative indices
        address them:  -1 = master,  -2 = return A,  -3 = return B, and so on."""
        if track_index == -1:
            master = getattr(self._song, "master_track", None)
            if master is None:
                raise RuntimeError("This Live set has no master track")
            return master

        if track_index <= -2:
            returns = getattr(self._song, "return_tracks", None) or []
            position = -track_index - 2
            if position >= len(returns):
                raise IndexError(
                    "Return track index " + str(track_index) + " asks for return " +
                    str(position) + ", but this set has " + str(len(returns)) +
                    " (use -2 for the first return, -3 for the second)")
            return returns[position]

        if track_index >= len(self._song.tracks):
            raise IndexError("Track index out of range")
        return self._song.tracks[track_index]

    def _get_parameter_entries(self, track_index, device_index, device_path=None):
        """Resolve a track+device to (entries, owner_name, class_name).

        track_index == -1 addresses the master track and device_index == -1 the mixer.
        device_path overrides device_index and reaches devices nested inside racks, so
        a plugin sitting on one drum pad is as addressable as one on the track."""
        if device_path is not None:
            node, kind = self._resolve_device_path(track_index, device_path)
            if kind != "device":
                raise ValueError(
                    "device_path must point at a device, not a chain - an odd number of "
                    "segments, like '0' or '0.0.0.2.1'")
            return ([(param.name, param) for param in node.parameters],
                    node.name, node.class_name)

        track = self._resolve_track(track_index)

        if device_index == -1:
            return self._get_mixer_parameters(track), "Mixer", "MixerDevice"

        if device_index < 0 or device_index >= len(track.devices):
            raise IndexError("Device index out of range")
        device = track.devices[device_index]
        entries = [(param.name, param) for param in device.parameters]
        return entries, device.name, device.class_name

    def _resolve_parameter(self, track_index, device_index, parameter_index,
                           parameter_name, device_path=None):
        """Look up a single DeviceParameter by index or by (case-insensitive) name."""
        entries, owner_name, _ = self._get_parameter_entries(
            track_index, device_index, device_path)

        if parameter_index is not None:
            if parameter_index < 0 or parameter_index >= len(entries):
                raise IndexError("Parameter index out of range ('" + owner_name +
                                 "' has " + str(len(entries)) + " parameters)")
            return entries[parameter_index][1], owner_name, parameter_index

        if parameter_name:
            wanted = str(parameter_name).strip().lower()
            matches = [(index, param) for index, (key, param) in enumerate(entries)
                       if key.lower() == wanted or param.name.lower() == wanted]
            # Max for Live devices routinely reuse names - Bengal has seven "Attack"
            # parameters. Silently taking the first would edit an arbitrary one.
            if len(matches) > 1:
                raise ValueError(
                    "'" + str(parameter_name) + "' is ambiguous on '" + owner_name +
                    "': it matches " + str(len(matches)) + " parameters, at indices " +
                    ", ".join([str(i) for i, _ in matches[:12]]) +
                    ". Pass parameter_index instead.")
            if matches:
                return matches[0][1], owner_name, matches[0][0]
            available = ", ".join([key for key, _ in entries][:30])
            raise ValueError("No parameter named '" + str(parameter_name) + "' on '" +
                             owner_name + "'. Available: " + available)

        raise ValueError("Either parameter_index or parameter_name must be provided")

    def _str_for_value(self, param, value):
        """param.str_for_value() or None. Live raises its own exception types here,
        so this catches broadly - it is presentation only."""
        try:
            return str(param.str_for_value(value))
        except Exception:
            return None

    def _param_info(self, key, param, index):
        """JSON-safe description of a single DeviceParameter"""
        info = {
            "index": index,
            "name": key,
            "display_name": param.name,
            "value": param.value,
            "min": param.min,
            "max": param.max,
            "is_quantized": bool(getattr(param, "is_quantized", False)),
            "is_enabled": bool(getattr(param, "is_enabled", True))
        }

        # Many parameters are normalised (0.0-1.0) while displaying real-world units,
        # so report what the range means as well as its numeric bounds.
        info["display_value"] = self._str_for_value(param, param.value) or str(param.value)
        display_min = self._str_for_value(param, param.min)
        display_max = self._str_for_value(param, param.max)
        if display_min is not None and display_max is not None:
            info["display_min"] = display_min
            info["display_max"] = display_max

        # value_items is a property that RAISES on non-quantized parameters, so it must
        # be gated on is_quantized - getattr's default only covers AttributeError.
        if info["is_quantized"]:
            try:
                info["value_items"] = [str(item) for item in param.value_items]
            except Exception:
                pass
        return info

    # Suffix -> multiplier that puts a displayed magnitude on one scale, so that
    # "2.15 kHz" and "995 Hz" are comparable and "12.3 ms" and "1.20 s" are too.
    # Live switches units mid-range, which is exactly why this is needed.
    # Order matters: longer suffixes are tested first, so "kHz" is not read as a bare
    # "k" and "ms" is not read as "m". A bare "k" is not hypothetical - some devices
    # display a frequency as "22.0k" with no unit at all, and reading that as 22 turns
    # the whole range upside down.
    DISPLAY_UNIT_SCALE = (
        ("khz", 1000.0), ("hz", 1.0),
        ("ms", 0.001), ("sec", 1.0), ("s", 1.0),
        ("db", 1.0), ("%", 1.0), ("st", 1.0),
        ("k", 1000.0)
    )

    def _parse_display_number(self, text):
        """Magnitude of a Live display string, normalised to its base unit
        (Hz, seconds, dB, %). None when the display carries no number, which is
        the case for switch-like parameters showing names such as 'Low Shelf'."""
        if text is None:
            return None
        raw = str(text).strip()
        lowered = raw.lower()
        if "inf" in lowered:
            return -1.0e9 if raw.lstrip().startswith("-") else 1.0e9

        match = re.search(r"-?\d+(?:\.\d+)?", raw)
        if match is None:
            return None
        magnitude = float(match.group(0))

        tail = raw[match.end():].strip().lower()
        for suffix, scale in self.DISPLAY_UNIT_SCALE:
            if tail.startswith(suffix):
                return magnitude * scale
        return magnitude

    def _display_number_at(self, param, value):
        return self._parse_display_number(self._str_for_value(param, value))

    def _find_value_for_display(self, param, target, iterations=48):
        """Binary search the raw value whose displayed magnitude is closest to target.

        str_for_value() evaluates a candidate without assigning it, so the whole search
        happens without touching the parameter. Direction is detected from the endpoints
        rather than assumed, since not every parameter's display rises with its value."""
        # Reject switches on the is_quantized flag rather than by hunting for digits in
        # the display: option names carry numbers of their own ("High Pass 48dB"), which
        # reads as a plausible magnitude and sends the search off after nonsense.
        if getattr(param, "is_quantized", False):
            options = None
            try:
                options = [str(item) for item in param.value_items]
            except Exception:
                pass
            detail = (" Its options are: " + ", ".join(options)) if options else ""
            raise ValueError(
                "'" + param.name + "' is a switch-like parameter, so matching it by "
                "displayed value does not apply. Set it directly with set_device_parameter "
                "using the option index (" + str(param.min) + " to " + str(param.max) +
                ")." + detail)

        low, high = param.min, param.max
        at_low = self._display_number_at(param, low)
        at_high = self._display_number_at(param, high)
        if at_low is None or at_high is None:
            raise ValueError(
                "'" + param.name + "' does not display a numeric value, so it cannot be "
                "matched by display. Set it directly with set_device_parameter instead.")

        ascending = at_high >= at_low
        reachable_low = min(at_low, at_high)
        reachable_high = max(at_low, at_high)
        clamped = max(reachable_low, min(reachable_high, float(target)))

        best_value, best_display, best_error = None, None, None
        for _ in range(iterations):
            middle = (low + high) / 2.0
            shown = self._str_for_value(param, middle)
            got = self._parse_display_number(shown)
            if got is None:
                break
            error = abs(got - clamped)
            if best_error is None or error < best_error:
                best_value, best_display, best_error = middle, shown, error
            if error <= max(abs(clamped) * 0.002, 1e-9):
                break
            if (got < clamped) == ascending:
                low = middle
            else:
                high = middle

        if best_value is None:
            raise RuntimeError(
                "'" + param.name + "' stopped displaying a comparable number partway "
                "through the search, so no value could be matched. Set it directly with "
                "set_device_parameter.")
        return {
            "value": best_value,
            "display_value": best_display,
            "requested_target": float(target),
            "matched_target": clamped,
            "out_of_reach": abs(clamped - float(target)) > max(abs(float(target)) * 0.002, 1e-9),
            "reachable_range": [reachable_low, reachable_high],
            "display_min": self._str_for_value(param, param.min),
            "display_max": self._str_for_value(param, param.max)
        }

    def _convert_display_values(self, track_index, device_index, parameter_index,
                                parameter_name, targets, device_path=None):
        """Resolve displayed magnitudes to raw values without changing anything"""
        try:
            param, owner_name, resolved_index = self._resolve_parameter(
                track_index, device_index, parameter_index, parameter_name, device_path)
            if not isinstance(targets, (list, tuple)):
                targets = [targets]
            return {
                "track_index": track_index,
                "device_index": device_index,
                "device_name": owner_name,
                "parameter_index": resolved_index,
                "parameter_name": param.name,
                "min": param.min,
                "max": param.max,
                "converted": [self._find_value_for_display(param, t) for t in targets]
            }
        except Exception as e:
            self.log_message("Error converting display values: " + str(e))
            raise

    def _set_parameter_to_display(self, track_index, device_index, parameter_index,
                                  parameter_name, target, device_path=None):
        """Set a parameter by the value it should read on screen, e.g. 2000 for 2 kHz"""
        try:
            param, owner_name, resolved_index = self._resolve_parameter(
                track_index, device_index, parameter_index, parameter_name, device_path)
            if not getattr(param, "is_enabled", True):
                raise RuntimeError("Parameter '" + param.name + "' on '" + owner_name +
                                   "' is not currently settable")

            found = self._find_value_for_display(param, target)
            param.value = self._coerce_value(param, found["value"])

            result = dict(found)
            result.update({
                "track_index": track_index,
                "device_index": device_index,
                "device_name": owner_name,
                "parameter_index": resolved_index,
                "parameter_name": param.name,
                "value": param.value,
                "display_value": self._str_for_value(param, param.value)
            })
            return result
        except Exception as e:
            self.log_message("Error setting parameter by display value: " + str(e))
            raise

    def _coerce_value(self, param, value):
        """Validate a value against the parameter's range, rounding quantized ones.

        Out-of-range values are rejected rather than clamped. Most Live parameters are
        normalised (0.0-1.0) while displaying real-world units, so a caller passing
        '8000' meaning 8 kHz would otherwise be silently pinned to the maximum and
        reported as success."""
        if value is None:
            raise ValueError("A 'value' must be provided")
        coerced = float(value)
        low, high = param.min, param.max

        # Tolerate float noise at the edges; reject anything genuinely outside.
        epsilon = (high - low) * 1e-6 if high > low else 1e-6
        if coerced < low - epsilon or coerced > high + epsilon:
            hint = ""
            display_min = self._str_for_value(param, low)
            display_max = self._str_for_value(param, high)
            if display_min is not None and display_max is not None:
                hint = " (" + display_min + " to " + display_max + ")"
            raise ValueError(
                "Value " + str(value) + " is outside the range of '" + param.name +
                "', which accepts " + str(low) + " to " + str(high) + hint +
                ". Call get_device_parameters for the parameter's actual range.")

        coerced = max(low, min(high, coerced))
        if getattr(param, "is_quantized", False):
            coerced = float(int(round(coerced)))
        return coerced

    def _get_device_parameters(self, track_index, device_index, device_path=None):
        """List every parameter of a device (or the mixer, with device_index -1)"""
        try:
            entries, owner_name, class_name = self._get_parameter_entries(
                track_index, device_index, device_path)
            return {
                "track_index": track_index,
                "track_name": self._resolve_track(track_index).name,
                "device_index": device_index,
                "device_name": owner_name,
                "class_name": class_name,
                "parameters": [self._param_info(key, param, index)
                               for index, (key, param) in enumerate(entries)]
            }
        except Exception as e:
            self.log_message("Error getting device parameters: " + str(e))
            raise

    def _set_device_parameter(self, track_index, device_index, parameter_index,
                              parameter_name, value, device_path=None):
        """Set a single device (or mixer) parameter to a value"""
        try:
            param, owner_name, resolved_index = self._resolve_parameter(
                track_index, device_index, parameter_index, parameter_name, device_path)

            if not getattr(param, "is_enabled", True):
                raise RuntimeError("Parameter '" + param.name + "' on '" + owner_name +
                                   "' is not currently settable (it may be driven by a "
                                   "macro or rack chain)")

            param.value = self._coerce_value(param, value)

            result = {
                "track_index": track_index,
                "device_index": device_index,
                "device_name": owner_name,
                "parameter_index": resolved_index,
                "parameter_name": param.name,
                "value": param.value
            }
            try:
                result["display_value"] = str(param.str_for_value(param.value))
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
            return result
        except Exception as e:
            self.log_message("Error setting device parameter: " + str(e))
            raise

    def _get_clip_for_envelope(self, track_index, clip_index):
        """Fetch the session clip at track_index/clip_index, or raise"""
        if track_index < 0 or track_index >= len(self._song.tracks):
            raise IndexError("Track index out of range")
        track = self._song.tracks[track_index]
        if clip_index < 0 or clip_index >= len(track.clip_slots):
            raise IndexError("Clip index out of range")
        clip_slot = track.clip_slots[clip_index]
        if not clip_slot.has_clip:
            raise Exception("No clip in track " + str(track_index) +
                            ", slot " + str(clip_index))
        return clip_slot.clip

    def _set_clip_envelope(self, track_index, clip_index, device_index, parameter_index,
                           parameter_name, points, interpolate, step_size, clear_existing,
                           device_path=None):
        """Write a clip automation envelope for a device/mixer parameter.

        points is a list of {time, value, length?} breakpoints in beats. Live's API only
        exposes flat steps (insert_step), so interpolate=True approximates a ramp between
        consecutive points with a series of step_size-wide steps."""
        try:
            param, owner_name, resolved_index = self._resolve_parameter(
                track_index, device_index, parameter_index, parameter_name, device_path)
            clip = self._get_clip_for_envelope(track_index, clip_index)

            if not points:
                raise ValueError("'points' must contain at least one {time, value} entry")

            normalised = []
            for point in points:
                if "time" not in point or "value" not in point:
                    raise ValueError("Each point needs both 'time' and 'value'")
                length = point.get("length")
                normalised.append({
                    "time": float(point["time"]),
                    "value": self._coerce_value(param, point["value"]),
                    "length": float(length) if length is not None else None
                })
            normalised.sort(key=lambda entry: entry["time"])

            size = max(float(step_size), 0.001)
            clip_end = float(clip.length)

            if clear_existing:
                clip.clear_envelope(param)

            # A new envelope is born with a breakpoint at time 0 holding the parameter's
            # value, and insert_step() cannot overwrite it. Assigning param.value here
            # would not help: the seeded point reflects the value as it stood when this
            # main-thread turn began, so it only observes writes committed by an earlier
            # command. Aligning the parameter is therefore the caller's job - the MCP
            # server does it in its own round trip before calling this.
            envelope = clip.automation_envelope(param)
            if envelope is None:
                envelope = clip.create_automation_envelope(param)
            if envelope is None:
                raise RuntimeError("Could not create an automation envelope for '" +
                                   param.name + "' on '" + owner_name + "'")

            steps_written = 0
            skipped_past_end = 0
            for index, point in enumerate(normalised):
                following = normalised[index + 1] if index + 1 < len(normalised) else None

                # A breakpoint sitting at or past the clip's end would write a step
                # outside the playable range; the value is unreachable, so skip it.
                if clip_end > 0 and point["time"] >= clip_end:
                    skipped_past_end += 1
                    continue

                if point["length"] is not None:
                    segment_end = point["time"] + point["length"]
                elif following is not None:
                    segment_end = following["time"]
                else:
                    segment_end = clip_end
                if segment_end <= point["time"]:
                    segment_end = point["time"] + size

                if interpolate and following is not None:
                    span = segment_end - point["time"]
                    cursor = point["time"]
                    while cursor < segment_end - 1e-9:
                        if steps_written >= self.MAX_ENVELOPE_STEPS:
                            raise RuntimeError(
                                "Envelope would exceed " + str(self.MAX_ENVELOPE_STEPS) +
                                " steps; use a larger step_size or a shorter range")
                        width = min(size, segment_end - cursor)
                        fraction = (cursor - point["time"]) / span if span > 0 else 0.0
                        ramped = point["value"] + (following["value"] - point["value"]) * fraction
                        envelope.insert_step(cursor, width, self._coerce_value(param, ramped))
                        steps_written += 1
                        cursor += size
                else:
                    if steps_written >= self.MAX_ENVELOPE_STEPS:
                        raise RuntimeError(
                            "Envelope would exceed " + str(self.MAX_ENVELOPE_STEPS) + " steps")
                    envelope.insert_step(point["time"], segment_end - point["time"], point["value"])
                    steps_written += 1

            return {
                "success": True,
                "track_index": track_index,
                "clip_index": clip_index,
                "clip_name": clip.name,
                "device_index": device_index,
                "device_name": owner_name,
                "parameter_index": resolved_index,
                "parameter_name": param.name,
                "interpolated": bool(interpolate),
                "steps_written": steps_written,
                "points_skipped_past_clip_end": skipped_past_end,
                "clip_length": clip_end
            }
        except Exception as e:
            self.log_message("Error setting clip envelope: " + str(e))
            raise

    # Bounds on how finely an envelope may be sampled in one read.
    MIN_ENVELOPE_SAMPLES = 2
    MAX_ENVELOPE_SAMPLES = 512

    def _describe_live_api(self, path, include_members, root="song"):
        """Report what Live's own objects expose, resolved from song by dotted path.

        The Live API is Boost.Python, which keeps each method's signature in its
        __doc__, so this answers "what arguments does move_device take" from the
        running application rather than from documentation that may not match this
        build. Numeric path segments index into lists: "tracks.0.devices.0".

        Read-only: it resolves attributes and reads docstrings, never calls anything."""
        try:
            root_name = str(root or "song").lower()
            if root_name == "song":
                target = self._song
            elif root_name in ("app", "application"):
                target = self.application()
            elif root_name == "browser":
                target = self.application().browser
            else:
                raise ValueError(
                    "Unknown root '" + str(root) + "'; use song, app or browser")
            if target is None:
                raise RuntimeError("Could not reach the '" + root_name + "' object")
            walked = [root_name]
            for segment in [p for p in str(path).split(".") if p]:
                if segment.lstrip("-").isdigit():
                    target = target[int(segment)]
                else:
                    target = getattr(target, segment)
                walked.append(segment)

            result = {
                "path": ".".join(walked),
                "type": type(target).__name__,
                "callable": callable(target)
            }

            doc = getattr(target, "__doc__", None)
            if doc:
                # Boost.Python packs the signature into the first lines of __doc__.
                result["doc"] = str(doc)[:2000]

            if not callable(target):
                try:
                    result["value"] = str(target)[:200]
                except Exception:
                    pass
                try:
                    result["length"] = len(target)
                except Exception:
                    pass

            if include_members:
                methods, properties = [], []
                for name in dir(target):
                    if name.startswith("_"):
                        continue
                    try:
                        member = getattr(target, name)
                    except Exception:
                        # Live raises on members that do not apply to this object;
                        # that is itself worth reporting rather than hiding.
                        properties.append(name + " (raises on access)")
                        continue
                    (methods if callable(member) else properties).append(name)
                result["methods"] = methods
                result["properties"] = properties

            return result
        except Exception as e:
            self.log_message("Error describing Live API: " + str(e))
            raise

    def _get_clip_notes(self, track_index, clip_index, from_time, time_span,
                        from_pitch, pitch_span):
        """Read the notes already inside a MIDI clip.

        Live 11 offers get_notes_extended(), which carries per-note probability and
        velocity range; the older get_notes() returns plain tuples. Their argument
        orders differ, so each call site spells them out."""
        try:
            clip = self._get_clip_for_envelope(track_index, clip_index)
            if not getattr(clip, "is_midi_clip", True):
                raise ValueError("Clip in track " + str(track_index) + ", slot " +
                                 str(clip_index) + " is an audio clip and has no notes")

            start = float(from_time)
            span = float(time_span) if time_span is not None else float(clip.length)
            low_pitch = int(from_pitch)
            pitch_range = int(pitch_span)

            notes = []
            source = None
            if hasattr(clip, "get_notes_extended"):
                source = "get_notes_extended"
                for n in clip.get_notes_extended(low_pitch, pitch_range, start, span):
                    entry = {
                        "pitch": n.pitch,
                        "start_time": n.start_time,
                        "duration": n.duration,
                        "velocity": n.velocity,
                        "mute": bool(n.mute)
                    }
                    for extra in ("note_id", "probability", "velocity_deviation",
                                  "release_velocity"):
                        try:
                            entry[extra] = getattr(n, extra)
                        except Exception:
                            pass
                    notes.append(entry)
            else:
                source = "get_notes"
                for tup in clip.get_notes(start, low_pitch, span, pitch_range):
                    notes.append({
                        "pitch": tup[0], "start_time": tup[1], "duration": tup[2],
                        "velocity": tup[3], "mute": bool(tup[4])
                    })

            notes.sort(key=lambda entry: (entry["start_time"], entry["pitch"]))
            return {
                "track_index": track_index,
                "clip_index": clip_index,
                "clip_name": clip.name,
                "clip_length": float(clip.length),
                "read_via": source,
                "from_time": start,
                "time_span": span,
                "note_count": len(notes),
                "notes": notes
            }
        except Exception as e:
            self.log_message("Error reading clip notes: " + str(e))
            raise

    # Note properties that can be changed in place. release_velocity and the
    # probability pair only exist in Live 11+, hence the per-field guard below.
    EDITABLE_NOTE_FIELDS = ("pitch", "start_time", "duration", "velocity", "mute",
                            "probability", "velocity_deviation", "release_velocity")

    def _count_notes(self, clip):
        try:
            return len(clip.get_notes_extended(0, 128, 0.0, float(clip.length)))
        except AttributeError:
            return len(clip.get_notes(0.0, 0, float(clip.length), 128))

    def _remove_clip_notes(self, track_index, clip_index, from_time, time_span,
                           from_pitch, pitch_span):
        """Delete the notes inside a time and pitch window, leaving the clip itself -
        and therefore its automation envelopes - untouched."""
        try:
            clip = self._get_clip_for_envelope(track_index, clip_index)
            if not getattr(clip, "is_midi_clip", True):
                raise ValueError("Clip in track " + str(track_index) + ", slot " +
                                 str(clip_index) + " is an audio clip and has no notes")

            start = float(from_time)
            span = float(time_span) if time_span is not None else float(clip.length)
            low_pitch = int(from_pitch)
            pitch_range = int(pitch_span)

            before = self._count_notes(clip)
            if hasattr(clip, "remove_notes_extended"):
                clip.remove_notes_extended(low_pitch, pitch_range, start, span)
            else:
                clip.remove_notes(start, low_pitch, span, pitch_range)
            after = self._count_notes(clip)

            return {
                "success": True,
                "track_index": track_index,
                "clip_index": clip_index,
                "clip_name": clip.name,
                "from_time": start,
                "time_span": span,
                "from_pitch": low_pitch,
                "pitch_span": pitch_range,
                "notes_removed": before - after,
                "notes_remaining": after
            }
        except Exception as e:
            self.log_message("Error removing clip notes: " + str(e))
            raise

    def _modify_clip_notes(self, track_index, clip_index, modifications):
        """Change existing notes in place, addressed by the note_id from get_clip_notes.

        apply_note_modifications() wants the note objects Live handed out, so the clip
        vector is fetched, the requested members are mutated, and the whole vector goes
        back. Constructing MidiNote objects from scratch is not required."""
        try:
            clip = self._get_clip_for_envelope(track_index, clip_index)
            if not hasattr(clip, "apply_note_modifications"):
                raise RuntimeError(
                    "This Live version cannot modify notes in place. Use "
                    "remove_clip_notes followed by add_notes_to_clip instead.")
            if not modifications:
                raise ValueError("'modifications' must contain at least one entry")

            vector = clip.get_notes_extended(0, 128, 0.0, float(clip.length))
            by_id = {}
            for note in vector:
                by_id[note.note_id] = note

            applied, changed_fields, missing = 0, [], []
            for change in modifications:
                note_id = change.get("note_id")
                if note_id is None:
                    raise ValueError(
                        "Every modification needs a 'note_id'; read them with get_clip_notes")
                target = by_id.get(note_id)
                if target is None:
                    missing.append(note_id)
                    continue

                touched = False
                for field in self.EDITABLE_NOTE_FIELDS:
                    if field not in change or change[field] is None:
                        continue
                    value = change[field]
                    if field == "pitch":
                        value = max(0, min(127, int(value)))
                    elif field == "velocity":
                        value = max(0.0, min(127.0, float(value)))
                    elif field == "mute":
                        value = bool(value)
                    elif field in ("start_time", "duration"):
                        value = float(value)
                        if field == "duration" and value <= 0:
                            raise ValueError("duration must be greater than 0")
                    else:
                        value = float(value)
                    try:
                        setattr(target, field, value)
                    except Exception:
                        # Older Live builds lack probability and friends; skip rather
                        # than failing a whole edit over an optional property.
                        continue
                    if field not in changed_fields:
                        changed_fields.append(field)
                    touched = True
                if touched:
                    applied += 1

            if missing:
                raise ValueError(
                    "No note with id " + ", ".join([str(m) for m in missing[:8]]) +
                    " in this clip. note_ids change when notes are removed and re-added, "
                    "so re-read the clip with get_clip_notes before modifying.")

            clip.apply_note_modifications(vector)

            return {
                "success": True,
                "track_index": track_index,
                "clip_index": clip_index,
                "clip_name": clip.name,
                "notes_modified": applied,
                "fields_changed": changed_fields,
                "note_count": self._count_notes(clip)
            }
        except Exception as e:
            self.log_message("Error modifying clip notes: " + str(e))
            raise

    def _get_clip_envelope(self, track_index, clip_index, device_index, parameter_index,
                           parameter_name, from_time, to_time, samples, device_path=None):
        """Sample an existing clip envelope at evenly spaced times.

        Live exposes no way to enumerate an envelope's breakpoints, so the shape is
        reconstructed by evaluating value_at_time() across the requested range."""
        try:
            param, owner_name, resolved_index = self._resolve_parameter(
                track_index, device_index, parameter_index, parameter_name, device_path)
            clip = self._get_clip_for_envelope(track_index, clip_index)

            result = {
                "track_index": track_index,
                "clip_index": clip_index,
                "clip_name": clip.name,
                "clip_length": float(clip.length),
                "device_index": device_index,
                "device_name": owner_name,
                "parameter_index": resolved_index,
                "parameter_name": param.name,
                "min": param.min,
                "max": param.max
            }
            display_min = self._str_for_value(param, param.min)
            display_max = self._str_for_value(param, param.max)
            if display_min is not None and display_max is not None:
                result["display_min"] = display_min
                result["display_max"] = display_max

            # automation_envelope() returns None rather than creating one, so this
            # stays a pure read.
            envelope = clip.automation_envelope(param)
            if envelope is None:
                result["has_envelope"] = False
                result["samples"] = []
                return result
            result["has_envelope"] = True

            start = float(from_time)
            end = float(to_time) if to_time is not None else float(clip.length)
            if end < start:
                start, end = end, start

            count = int(samples)
            count = max(self.MIN_ENVELOPE_SAMPLES, min(count, self.MAX_ENVELOPE_SAMPLES))
            span = (end - start) / (count - 1) if count > 1 and end > start else 0.0

            sampled = []
            for position in range(count):
                moment = start + span * position
                value = envelope.value_at_time(moment)
                entry = {"time": round(moment, 6), "value": value}
                display = self._str_for_value(param, value)
                if display is not None:
                    entry["display_value"] = display
                sampled.append(entry)

            result["from_time"] = start
            result["to_time"] = end
            result["samples"] = sampled
            return result
        except Exception as e:
            self.log_message("Error reading clip envelope: " + str(e))
            raise

    def _clear_clip_envelope(self, track_index, clip_index, device_index,
                             parameter_index, parameter_name, device_path=None):
        """Remove the clip automation envelope for a device/mixer parameter"""
        try:
            param, owner_name, resolved_index = self._resolve_parameter(
                track_index, device_index, parameter_index, parameter_name, device_path)
            clip = self._get_clip_for_envelope(track_index, clip_index)
            clip.clear_envelope(param)
            return {
                "success": True,
                "track_index": track_index,
                "clip_index": clip_index,
                "clip_name": clip.name,
                "device_index": device_index,
                "device_name": owner_name,
                "parameter_index": resolved_index,
                "parameter_name": param.name
            }
        except Exception as e:
            self.log_message("Error clearing clip envelope: " + str(e))
            raise

    # ── Browser implementations ───────────────────────────────────────────────

    def _get_browser_item(self, uri, path):
        """Get a browser item by URI or path"""
        try:
            # Access the application's browser instance instead of creating a new one
            app = self.application()
            if not app:
                raise RuntimeError("Could not access Live application")
                
            result = {
                "uri": uri,
                "path": path,
                "found": False
            }
            
            # Try to find by URI first if provided
            if uri:
                item = self._find_browser_item_by_uri(app.browser, uri)
                if item:
                    result["found"] = True
                    result["item"] = {
                        "name": item.name,
                        "is_folder": item.is_folder,
                        "is_device": item.is_device,
                        "is_loadable": item.is_loadable,
                        "uri": item.uri
                    }
                    return result
            
            # If URI not provided or not found, try by path
            if path:
                # Parse the path and navigate to the specified item
                path_parts = path.split("/")
                
                # Determine the root based on the first part
                current_item = None
                if path_parts[0].lower() == "instruments":
                    current_item = app.browser.instruments
                elif path_parts[0].lower() == "sounds":
                    current_item = app.browser.sounds
                elif path_parts[0].lower() == "drums":
                    current_item = app.browser.drums
                elif path_parts[0].lower() == "audio_effects":
                    current_item = app.browser.audio_effects
                elif path_parts[0].lower() == "midi_effects":
                    current_item = app.browser.midi_effects
                else:
                    # Default to instruments if not specified
                    current_item = app.browser.instruments
                    # Don't skip the first part in this case
                    path_parts = ["instruments"] + path_parts
                
                # Navigate through the path
                for i in range(1, len(path_parts)):
                    part = path_parts[i]
                    if not part:  # Skip empty parts
                        continue
                    
                    found = False
                    for child in current_item.children:
                        if child.name.lower() == part.lower():
                            current_item = child
                            found = True
                            break
                    
                    if not found:
                        result["error"] = "Path part '{0}' not found".format(part)
                        return result
                
                # Found the item
                result["found"] = True
                result["item"] = {
                    "name": current_item.name,
                    "is_folder": current_item.is_folder,
                    "is_device": current_item.is_device,
                    "is_loadable": current_item.is_loadable,
                    "uri": current_item.uri
                }
            
            return result
        except Exception as e:
            self.log_message("Error getting browser item: " + str(e))
            self.log_message(traceback.format_exc())
            raise   
    
    
    
    def _load_browser_item(self, track_index, item_uri):
        """Load a browser item onto a track by its URI"""
        try:
            track = self._resolve_track(track_index)
            
            # Access the application's browser instance instead of creating a new one
            app = self.application()
            
            # Find the browser item by URI
            item = self._find_browser_item_by_uri(app.browser, item_uri)
            
            if not item:
                raise ValueError("Browser item with URI '{0}' not found".format(item_uri))
            
            # Select the track
            self._song.view.selected_track = track
            
            # Load the item
            app.browser.load_item(item)
            
            result = {
                "loaded": True,
                "item_name": item.name,
                "track_name": track.name,
                "uri": item_uri
            }
            return result
        except Exception as e:
            self.log_message("Error loading browser item: {0}".format(str(e)))
            self.log_message(traceback.format_exc())
            raise
    
    # Substring markers that point a URI at a likely root. If no marker
    # matches we fall back to the default order, so this is purely an
    # optimisation — never a correctness change.
    _URI_ROOT_HINTS = (
        ('plugins',       ('vst:', 'vst3:', 'au:', 'query:plugins', 'plugin#')),
        ('max_for_live',  ('max for live', 'maxforlive', 'm4l', 'query:max')),
        ('user_library',  ('user library', 'userlibrary', 'query:user library', 'query:user-library')),
        ('packs',         ('query:packs', '/packs/')),
        ('samples',       ('query:samples', 'sample:', '/samples/')),
        ('drums',         ('query:drums', '/drums/')),
        ('instruments',   ('query:instruments', '/instruments/')),
        ('sounds',        ('query:sounds', '/sounds/')),
        ('audio_effects', ('query:audio effects', 'audioeffects', '/audio_effects/')),
        ('midi_effects',  ('query:midi effects', 'midieffects', '/midi_effects/')),
        ('user_folders',  ('userfolder:',)),
    )

    def _order_roots_by_uri(self, roots, uri):
        """Reorder ``roots`` so the URI's likely root is walked first."""
        if not isinstance(uri, (bytes, str)) or not uri:
            return roots
        lowered = uri.lower()
        for attr, markers in self._URI_ROOT_HINTS:
            if any(m in lowered for m in markers):
                head = [(a, r) for (a, r) in roots if a == attr]
                tail = [(a, r) for (a, r) in roots if a != attr]
                return head + tail
        return roots

    def _find_user_folder_item(self, browser, uri):
        """Resolve a userfolder: URI directly, without searching.

        These URIs describe their own location: the Place's own URI sits before the
        '#', and the segments after it are colon separated. Walking instead of
        descending means crawling every Place in the sidebar - one of which may be a
        whole drive - and that cost about half a minute per sample load.

        Each level is matched on the child's own URI, built up piece by piece, so no
        percent-decoding or name comparison is involved."""
        if not isinstance(uri, str) or not uri.lower().startswith("userfolder:"):
            return None

        head, separator, tail = uri.partition("#")
        if not separator:
            return None

        try:
            folders = list(getattr(browser, "user_folders", None) or [])
        except Exception:
            return None

        node = None
        for folder in folders:
            if str(getattr(folder, "uri", "") or "").lower() == head.lower():
                node = folder
                break
        if node is None:
            return None

        prefix = head + "#"
        for index, segment in enumerate([x for x in tail.split(":") if x]):
            prefix = prefix + segment if index == 0 else prefix + ":" + segment
            wanted = prefix.lower()
            step = None
            try:
                children = node.children
            except Exception:
                return None
            for child in children:
                if str(getattr(child, "uri", "") or "").lower() == wanted:
                    step = child
                    break
            if step is None:
                return None
            node = step
        return node

    def _find_browser_item_by_uri(self, browser_or_item, uri, max_depth=10, current_depth=0):
        """Find a browser item by its URI.

        Top-level lookups are memoised on ``self._uri_cache`` so repeated
        loads of the same URI don't re-walk the entire browser tree.
        """
        if current_depth == 0:
            cache = getattr(self, '_uri_cache', None)
            if cache is None:
                self._uri_cache = cache = {}
            if uri in cache:
                return cache[uri]
            result = self._find_user_folder_item(browser_or_item, uri)
            if result is None:
                result = self._walk_browser_for_uri(browser_or_item, uri, max_depth, 0)
            if result is not None:
                cache[uri] = result
            return result
        return self._walk_browser_for_uri(browser_or_item, uri, max_depth, current_depth)

    def _walk_browser_for_uri(self, browser_or_item, uri, max_depth, current_depth):
        """Recursive walk used by :py:meth:`_find_browser_item_by_uri`."""
        try:
            # Check if this is the item we're looking for
            if hasattr(browser_or_item, 'uri') and browser_or_item.uri == uri:
                return browser_or_item

            # Stop recursion if we've reached max depth
            if current_depth >= max_depth:
                return None

            # Check if this is a browser with root categories
            if hasattr(browser_or_item, 'instruments'):
                roots = [
                    ('instruments', browser_or_item.instruments),
                    ('sounds', browser_or_item.sounds),
                    ('drums', browser_or_item.drums),
                    ('audio_effects', browser_or_item.audio_effects),
                    ('midi_effects', browser_or_item.midi_effects),
                ]
                for extra_attr in ('plugins', 'max_for_live', 'user_library', 'packs', 'samples'):
                    if hasattr(browser_or_item, extra_attr):
                        try:
                            roots.append((extra_attr, getattr(browser_or_item, extra_attr)))
                        except (AttributeError, RuntimeError) as e:
                            self.log_message("Could not access browser.{0}: {1}".format(extra_attr, str(e)))

                # The folders a user adds to Places are a list rather than a named
                # attribute, so each one is appended as its own root. Without this the
                # sidebar is browsable but nothing in it can be loaded by URI.
                try:
                    for folder in (getattr(browser_or_item, 'user_folders', None) or []):
                        roots.append(('user_folders', folder))
                except Exception as e:
                    self.log_message("Could not access browser.user_folders: {0}".format(str(e)))

                for _attr, category in self._order_roots_by_uri(roots, uri):
                    item = self._find_browser_item_by_uri(category, uri, max_depth, current_depth + 1)
                    if item:
                        return item

                return None

            # Check if this item has children
            if hasattr(browser_or_item, 'children') and browser_or_item.children:
                for child in browser_or_item.children:
                    item = self._find_browser_item_by_uri(child, uri, max_depth, current_depth + 1)
                    if item:
                        return item

            return None
        except Exception as e:
            self.log_message("Error finding browser item by URI: {0}".format(str(e)))
            return None
    
    # Helper methods
    
    def _get_device_type(self, device):
        """Get the type of a device"""
        try:
            # Simple heuristic - in a real implementation you'd look at the device class
            if device.can_have_drum_pads:
                return "drum_machine"
            elif device.can_have_chains:
                return "rack"
            elif "instrument" in device.class_display_name.lower():
                return "instrument"
            elif "audio_effect" in device.class_name.lower():
                return "audio_effect"
            elif "midi_effect" in device.class_name.lower():
                return "midi_effect"
            else:
                return "unknown"
        except:
            return "unknown"
    
    def get_browser_tree(self, category_type="all"):
        """
        Get a simplified tree of browser categories.
        
        Args:
            category_type: Type of categories to get ('all', 'instruments', 'sounds', etc.)
            
        Returns:
            Dictionary with the browser tree structure
        """
        try:
            # Access the application's browser instance instead of creating a new one
            app = self.application()
            if not app:
                raise RuntimeError("Could not access Live application")
                
            # Check if browser is available
            if not hasattr(app, 'browser') or app.browser is None:
                raise RuntimeError("Browser is not available in the Live application")
            
            # Log available browser attributes to help diagnose issues
            browser_attrs = [attr for attr in dir(app.browser) if not attr.startswith('_')]
            self.log_message("Available browser attributes: {0}".format(browser_attrs))
            
            result = {
                "type": category_type,
                "categories": [],
                "available_categories": browser_attrs
            }
            
            # Helper function to process a browser item and its children
            def process_item(item, depth=0):
                if not item:
                    return None
                
                result = {
                    "name": item.name if hasattr(item, 'name') else "Unknown",
                    "is_folder": hasattr(item, 'children') and bool(item.children),
                    "is_device": hasattr(item, 'is_device') and item.is_device,
                    "is_loadable": hasattr(item, 'is_loadable') and item.is_loadable,
                    "uri": item.uri if hasattr(item, 'uri') else None,
                    "children": []
                }
                
                
                return result
            
            # Process based on category type and available attributes
            if (category_type == "all" or category_type == "instruments") and hasattr(app.browser, 'instruments'):
                try:
                    instruments = process_item(app.browser.instruments)
                    if instruments:
                        instruments["name"] = "Instruments"  # Ensure consistent naming
                        result["categories"].append(instruments)
                except Exception as e:
                    self.log_message("Error processing instruments: {0}".format(str(e)))
            
            if (category_type == "all" or category_type == "sounds") and hasattr(app.browser, 'sounds'):
                try:
                    sounds = process_item(app.browser.sounds)
                    if sounds:
                        sounds["name"] = "Sounds"  # Ensure consistent naming
                        result["categories"].append(sounds)
                except Exception as e:
                    self.log_message("Error processing sounds: {0}".format(str(e)))
            
            if (category_type == "all" or category_type == "drums") and hasattr(app.browser, 'drums'):
                try:
                    drums = process_item(app.browser.drums)
                    if drums:
                        drums["name"] = "Drums"  # Ensure consistent naming
                        result["categories"].append(drums)
                except Exception as e:
                    self.log_message("Error processing drums: {0}".format(str(e)))
            
            if (category_type == "all" or category_type == "audio_effects") and hasattr(app.browser, 'audio_effects'):
                try:
                    audio_effects = process_item(app.browser.audio_effects)
                    if audio_effects:
                        audio_effects["name"] = "Audio Effects"  # Ensure consistent naming
                        result["categories"].append(audio_effects)
                except Exception as e:
                    self.log_message("Error processing audio_effects: {0}".format(str(e)))
            
            if (category_type == "all" or category_type == "midi_effects") and hasattr(app.browser, 'midi_effects'):
                try:
                    midi_effects = process_item(app.browser.midi_effects)
                    if midi_effects:
                        midi_effects["name"] = "MIDI Effects"
                        result["categories"].append(midi_effects)
                except Exception as e:
                    self.log_message("Error processing midi_effects: {0}".format(str(e)))
            
            # Try to process other potentially available categories
            for attr in browser_attrs:
                if attr not in ['instruments', 'sounds', 'drums', 'audio_effects', 'midi_effects'] and \
                   (category_type == "all" or category_type == attr):
                    try:
                        item = getattr(app.browser, attr)
                        if hasattr(item, 'children') or hasattr(item, 'name'):
                            category = process_item(item)
                            if category:
                                category["name"] = attr.capitalize()
                                result["categories"].append(category)
                    except Exception as e:
                        self.log_message("Error processing {0}: {1}".format(attr, str(e)))
            
            self.log_message("Browser tree generated for {0} with {1} root categories".format(
                category_type, len(result['categories'])))
            return result
            
        except Exception as e:
            self.log_message("Error getting browser tree: {0}".format(str(e)))
            self.log_message(traceback.format_exc())
            raise
    
    def get_browser_items_at_path(self, path):
        """
        Get browser items at a specific path.
        
        Args:
            path: Path in the format "category/folder/subfolder"
                 where category is one of: instruments, sounds, drums, audio_effects, midi_effects
                 or any other available browser category
                 
        Returns:
            Dictionary with items at the specified path
        """
        try:
            # Access the application's browser instance instead of creating a new one
            app = self.application()
            if not app:
                raise RuntimeError("Could not access Live application")
                
            # Check if browser is available
            if not hasattr(app, 'browser') or app.browser is None:
                raise RuntimeError("Browser is not available in the Live application")
            
            # Log available browser attributes to help diagnose issues
            browser_attrs = [attr for attr in dir(app.browser) if not attr.startswith('_')]
            self.log_message("Available browser attributes: {0}".format(browser_attrs))
                
            # Parse the path
            path_parts = path.split("/")
            if not path_parts:
                raise ValueError("Invalid path")
            
            # Determine the root category
            root_category = path_parts[0].lower()
            current_item = None

            descend_from = 1

            # "places" reaches the folders the user added to the sidebar. They live in a
            # list rather than as named attributes, so the folder is matched by name and
            # the normal descent picks up from the segment after it.
            if root_category in ("places", "user_folders"):
                folders = list(getattr(app.browser, "user_folders", None) or [])
                if len(path_parts) < 2 or not path_parts[1]:
                    return {
                        "path": path,
                        "name": "Places",
                        "items": [{
                            "name": getattr(f, "name", "Unknown"),
                            "is_folder": True,
                            "is_device": False,
                            "is_loadable": bool(getattr(f, "is_loadable", False)),
                            "uri": getattr(f, "uri", None)
                        } for f in folders]
                    }
                wanted = path_parts[1].lower()
                for folder in folders:
                    if str(getattr(folder, "name", "")).lower() == wanted:
                        current_item = folder
                        break
                if current_item is None:
                    return {
                        "path": path,
                        "error": "No user folder named '{0}'".format(path_parts[1]),
                        "available_places": [getattr(f, "name", "Unknown") for f in folders],
                        "items": []
                    }
                descend_from = 2
            
            # Check standard categories first
            elif root_category == "instruments" and hasattr(app.browser, 'instruments'):
                current_item = app.browser.instruments
            elif root_category == "sounds" and hasattr(app.browser, 'sounds'):
                current_item = app.browser.sounds
            elif root_category == "drums" and hasattr(app.browser, 'drums'):
                current_item = app.browser.drums
            elif root_category == "audio_effects" and hasattr(app.browser, 'audio_effects'):
                current_item = app.browser.audio_effects
            elif root_category == "midi_effects" and hasattr(app.browser, 'midi_effects'):
                current_item = app.browser.midi_effects
            else:
                # Try to find the category in other browser attributes
                found = False
                for attr in browser_attrs:
                    if attr.lower() == root_category:
                        try:
                            current_item = getattr(app.browser, attr)
                            found = True
                            break
                        except Exception as e:
                            self.log_message("Error accessing browser attribute {0}: {1}".format(attr, str(e)))
                
                if not found:
                    # If we still haven't found the category, return available categories
                    return {
                        "path": path,
                        "error": "Unknown or unavailable category: {0}".format(root_category),
                        "available_categories": browser_attrs,
                        "items": []
                    }
            
            # Navigate through the path
            for i in range(descend_from, len(path_parts)):
                part = path_parts[i]
                if not part:  # Skip empty parts
                    continue
                
                if not hasattr(current_item, 'children'):
                    return {
                        "path": path,
                        "error": "Item at '{0}' has no children".format('/'.join(path_parts[:i])),
                        "items": []
                    }
                
                found = False
                for child in current_item.children:
                    if hasattr(child, 'name') and child.name.lower() == part.lower():
                        current_item = child
                        found = True
                        break
                
                if not found:
                    return {
                        "path": path,
                        "error": "Path part '{0}' not found".format(part),
                        "items": []
                    }
            
            # Get items at the current path
            items = []
            if hasattr(current_item, 'children'):
                for child in current_item.children:
                    item_info = {
                        "name": child.name if hasattr(child, 'name') else "Unknown",
                        "is_folder": hasattr(child, 'children') and bool(child.children),
                        "is_device": hasattr(child, 'is_device') and child.is_device,
                        "is_loadable": hasattr(child, 'is_loadable') and child.is_loadable,
                        "uri": child.uri if hasattr(child, 'uri') else None
                    }
                    items.append(item_info)
            
            result = {
                "path": path,
                "name": current_item.name if hasattr(current_item, 'name') else "Unknown",
                "uri": current_item.uri if hasattr(current_item, 'uri') else None,
                "is_folder": hasattr(current_item, 'children') and bool(current_item.children),
                "is_device": hasattr(current_item, 'is_device') and current_item.is_device,
                "is_loadable": hasattr(current_item, 'is_loadable') and current_item.is_loadable,
                "items": items
            }
            
            self.log_message("Retrieved {0} items at path: {1}".format(len(items), path))
            return result
            
        except Exception as e:
            self.log_message("Error getting browser items at path: {0}".format(str(e)))
            self.log_message(traceback.format_exc())
            raise
