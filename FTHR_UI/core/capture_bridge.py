# capture_bridge.py - Python interface to the C++ capture engine.
#
# The heavy lifting (screen grab + NVENC/x264 encode + ring buffer) lives in a
# native engine process. This file is the skinny Python side that talks to it.
#
# How they talk: a single fixed-layout struct mapped into shared memory. The UI
# writes a command into the "ui_*" fields, the engine reads it, does the thing,
# and writes back into the "engine_*" fields. No sockets, no pipes, no JSON
# serialization tax on the hot path. Galaxy brain moment: just use shared memory.
#
# The catch is that Windows and Linux do shared memory completely differently:
#   - Windows: OpenFileMapping + MapViewOfFile via kernel32, wide (utf-16) strings
#   - Linux:   plain mmap of /dev/shm/<name>, plus narrow (utf-8) byte strings
# So basically every method has a "if win32 / else" fork. It's not pretty but
# the struct layout is the contract and both sides agree on it. don't touch the
# field order unless you also change the C++ side or everything reads garbage.

import sys
import ctypes
from ctypes import Structure, c_uint32, c_bool, c_float, c_uint64
from enum import IntEnum
import time
import os

if sys.platform == 'win32':
    from ctypes import c_wchar


# Command/response codes are just plain ints on the wire. Keep these in lockstep
# with the enums on the C++ side — they are not auto-generated, sadly.
class CommandType(IntEnum):
    NONE = 0
    START_RECORDING = 1
    STOP_RECORDING = 2
    SAVE_CLIP = 3
    SET_RESOLUTION = 4
    SET_QUALITY = 5
    SET_FRAMERATE = 6
    SET_HOTKEY = 7
    SET_TARGET_WINDOW = 8
    GET_STATUS = 9
    RECONFIGURE_ENCODER = 10


class ResponseType(IntEnum):
    NONE = 0
    RECORDING_STARTED = 1
    RECORDING_STOPPED = 2
    CLIP_SAVED = 3
    STATUS_UPDATE = 4
    ERROR_OCCURRED = 5
    SAVE_STARTED = 6       # Phase 3: async SaveClip queued


# THE struct. This binary layout is the entire API contract with the engine.
# Windows uses wide chars (c_wchar) because the engine was born on Win32 and
# everything there is utf-16. Linux uses plain bytes (c_char) and bigger string
# buffers because paths on Linux can get long and weird. Field ORDER and SIZES
# must match the C++ definition byte-for-byte or you'll read pure nonsense.
if sys.platform == 'win32':
    class SharedMemoryLayout(Structure):
        _fields_ = [
            ('ui_command',        c_uint32),
            ('ui_param1',         c_uint32),
            ('ui_param2',         c_uint32),
            ('ui_param3',         c_uint32),
            ('ui_string',         c_wchar * 256),
            ('engine_response',   c_uint32),
            ('engine_param1',     c_uint32),
            ('engine_param2',     c_uint32),
            ('engine_param3',     c_float),
            ('engine_string',     c_wchar * 512),
            ('is_recording',      c_bool),
            ('is_initialized',    c_bool),
            ('frames_captured',   c_uint64),
            ('bytes_written',     c_uint64),
            ('cfg_bitrate_kbps',  c_uint32),
            ('cfg_target_width',  c_uint32),
            ('cfg_target_height', c_uint32),
            ('nvenc_active',      c_bool),
            # v2 fields — must match shared_memory.h byte-for-byte
            ('cfg_codec_pref',    c_uint32),
            ('cfg_preset',        c_uint32),
            ('active_codec',      ctypes.c_char * 64),
            # v3 fields
            ('multiband_enabled',     c_bool),
            ('active_audio_mappings', ctypes.c_char * 1024),
        ]
else:
    class SharedMemoryLayout(Structure):
        _fields_ = [
            ('ui_command',        c_uint32),
            ('ui_param1',         c_uint32),
            ('ui_param2',         c_uint32),
            ('ui_param3',         c_uint32),
            ('ui_string',         ctypes.c_char * 1024),
            ('engine_response',   c_uint32),
            ('engine_param1',     c_uint32),
            ('engine_param2',     c_uint32),
            ('engine_param3',     c_float),
            ('engine_string',     ctypes.c_char * 2048),
            ('is_recording',      c_bool),
            ('is_initialized',    c_bool),
            ('frames_captured',   c_uint64),
            ('bytes_written',     c_uint64),
            ('cfg_bitrate_kbps',  c_uint32),
            ('cfg_target_width',  c_uint32),
            ('cfg_target_height', c_uint32),
            ('nvenc_active',      c_bool),
            # v2 fields — must match shared_memory.h byte-for-byte
            ('cfg_codec_pref',    c_uint32),
            ('cfg_preset',        c_uint32),
            ('active_codec',      ctypes.c_char * 64),
            # v3 fields
            ('multiband_enabled',     c_bool),
            ('active_audio_mappings', ctypes.c_char * 1024),
        ]


class CaptureBridge:
    # Bumped the _v1 suffix the day I changed the struct layout and spent two
    # hours wondering why an old engine kept reading my new fields wrong.
    # Versioned name = old + new never accidentally share the same mapping.
    SHARED_MEM_NAME = 'FTHR_SharedMemory_v3'

    # Singleton. There is exactly one engine and one mapping, so one bridge.
    # Anything else just hands you back the same object.
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
            cls._instance._mem_handle = None
            cls._instance._win_map_ptr = None
            cls._instance._layout = None
            cls._instance._linux_mmap = None
            cls._instance._logged_read_errors = set()
        return cls._instance

    def _log_read_error_once(self, where: str, e: Exception):
        """Shared-memory read errors usually mean layout mismatch / dead engine.
        Log each site once so IPC corruption doesn't masquerade as 'feature off'."""
        if where not in self._logged_read_errors:
            self._logged_read_errors.add(where)
            print(f'[CaptureBridge] {where} read failed: {type(e).__name__}: {e}')


    def initialize(self) -> bool:
        if self._initialized:
            return True
        if sys.platform == 'win32':
            return self._initialize_windows()
        else:
            return self._initialize_linux()


    def _initialize_linux(self) -> bool:
        import mmap as _mmap
        shm_path = f'/dev/shm/{self.SHARED_MEM_NAME}'
        try:
            fd = os.open(shm_path, os.O_RDWR)
        except OSError:
            print('Failed to open shared memory - is Linux engine running?')
            return False

        size = ctypes.sizeof(SharedMemoryLayout)

        # Layout check BEFORE mapping. The struct is the entire contract and
        # there is no version field inside it, so the region's size is the only
        # signal available at runtime. If an engine built from a different
        # shared_memory.h is running, every field past the drift point would
        # otherwise be read as garbage — silently. Refusing with a message
        # naming both sizes is the difference between a five-minute diagnosis
        # and a week of "the encoder reports nonsense".
        #
        # The mapping NAME already carries a layout version (_v3), so an old
        # engine and a new UI normally cannot meet at all. This catches the
        # case where someone bumps the struct without bumping the name.
        try:
            actual = os.fstat(fd).st_size
        except OSError:
            actual = -1
        if actual >= 0 and actual != size:
            os.close(fd)
            print(
                f'[Bridge] Shared-memory layout mismatch on {shm_path}: the '
                f'engine created {actual} bytes, this UI expects {size} bytes '
                f'for {self.SHARED_MEM_NAME}.\n'
                f'[Bridge] The running engine was built from a different '
                f'shared_memory.h. Rebuild the Linux engine from this source '
                f'tree (see CONTRIBUTING.md, "The shared-memory contract"). '
                f'Refusing to map it — reading it would return garbage.'
            )
            return False

        try:
            self._linux_mmap = _mmap.mmap(fd, size, access=_mmap.ACCESS_WRITE)
        except Exception as e:
            # No close() here: the finally below owns it. Closing in both places
            # made the second close raise EBADF *out of the finally*, replacing
            # the intended `return False` with an exception.
            print(f'mmap failed: {e}')
            return False
        finally:
            os.close(fd)  # the mmap holds its own ref, so the fd is dead weight now

        # from_buffer maps the struct directly onto the mmap — zero copy, writes
        # go straight to shared memory. This is the whole trick.
        self._layout = SharedMemoryLayout.from_buffer(self._linux_mmap)

        if not self._layout.is_initialized:
            print('Linux engine not initialized yet')
            self._linux_mmap.close()
            self._linux_mmap = None
            self._layout = None
            return False

        self._initialized = True
        print('Connected to Linux capture engine')
        return True


    def _initialize_windows(self) -> bool:
        kernel32 = ctypes.windll.kernel32

        self._mem_handle = kernel32.OpenFileMappingW(
            0xF001F, False, self.SHARED_MEM_NAME)

        if not self._mem_handle:
            print('Failed to open shared memory - is C++ engine running?')
            return False

        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        ptr = kernel32.MapViewOfFile(
            self._mem_handle, 0xF001F, 0, 0,
            ctypes.sizeof(SharedMemoryLayout))

        if not ptr:
            kernel32.CloseHandle(self._mem_handle)
            self._mem_handle = None
            return False

        # Keep the raw mapping pointer — UnmapViewOfFile needs exactly this
        # address, not byref() of the casted struct.
        self._win_map_ptr = ptr
        self._layout = ctypes.cast(
            ptr, ctypes.POINTER(SharedMemoryLayout)).contents

        if not self._layout.is_initialized:
            print('C++ engine not initialized yet')
            self.shutdown()
            return False

        self._initialized = True
        print('Connected to C++ capture engine')
        return True


    def shutdown(self):
        if sys.platform != 'win32':
            # Must release the ctypes from_buffer reference BEFORE closing the
            # mmap — otherwise Python raises BufferError: cannot close exported
            # pointers exist. Order matters here, ask me how I know.
            self._layout = None
            if self._linux_mmap is not None:
                self._linux_mmap.close()
                self._linux_mmap = None
        else:
            if self._layout:
                ctypes.windll.kernel32.UnmapViewOfFile(
                    ctypes.c_void_p(getattr(self, '_win_map_ptr', None) or 0))
                self._win_map_ptr = None
                self._layout = None
            if self._mem_handle:
                ctypes.windll.kernel32.CloseHandle(self._mem_handle)
                self._mem_handle = None
        self._initialized = False
        CaptureBridge._instance = None


    def is_connected(self) -> bool:
        if not self._initialized or self._layout is None:
            return False
        try:
            return self._layout.is_initialized
        except Exception:
            return False


    def save_clip(self, output_path: str, duration: int = 30) -> bool:
        """
        Yell at the engine: "save the last N seconds, NOW."

        Fire-and-mostly-forget. We hand the engine a path + duration, it grabs
        whatever's currently in the ring buffer and starts encoding on its own
        thread. We only block long enough to hear "yeah I got it" (SAVE_STARTED),
        not for the actual encode — that would freeze the UI on long clips.

        Returns True if the save was successfully queued, False otherwise.
        """
        if not self.is_connected():
            print('Not connected to capture engine')
            return False

        # Wipe any leftover response first — narrator: it was not, in fact, fine
        # without this. Stale CLIP_SAVED from a previous run looked like success.
        self._layout.engine_response = ResponseType.NONE
        # Send command — Linux uses c_char (bytes), Windows uses c_wchar (str)
        if sys.platform != 'win32':
            # Encode and truncate at a char boundary so we never split a multi-byte
            # UTF-8 sequence. The buffer is 1024 bytes; leave 1 for the null terminator.
            encoded = output_path.encode('utf-8')
            if len(encoded) > 1023:
                encoded = output_path.encode('utf-8')[:1023].decode('utf-8', errors='ignore').encode('utf-8')
            self._layout.ui_string = encoded
        else:
            # Buffer is c_wchar * 256 — a longer path raises ValueError mid-save.
            if len(output_path) > 255:
                print(f'save_clip: path too long ({len(output_path)} chars), refusing')
                return False
            self._layout.ui_string = output_path
        self._layout.ui_param1 = duration
        # Write the command code LAST. The engine polls ui_command, so once this
        # lands it may read every other field — they all need to be set already.
        self._layout.ui_command = CommandType.SAVE_CLIP

        # Spin until the engine acks. Should be near-instant; the 1s ceiling is
        # purely so a dead/hung engine doesn't lock the UI forever.
        #
        # CLIP_SAVED counts as an ack. The Linux engine runs SaveClip
        # *synchronously* on its command loop: it writes SAVE_STARTED, encodes,
        # then overwrites the same field with CLIP_SAVED. A short clip on a fast
        # encoder finishes inside our poll interval, so SAVE_STARTED is gone
        # before we ever observe it — and this loop used to spin the full second
        # and then report failure for a clip that was written correctly.
        # Verified on Linux/x11grab/av1_nvenc: a 10 s clip did exactly this.
        _ACKS = (ResponseType.SAVE_STARTED,
                 ResponseType.CLIP_SAVED,
                 ResponseType.ERROR_OCCURRED)
        start = time.monotonic()
        while self._layout.engine_response not in _ACKS:
            if time.monotonic() - start > 1.0:
                print('Timeout waiting for save acknowledgement')
                return False
            time.sleep(0.001)

        response = self._layout.engine_response

        if response == ResponseType.ERROR_OCCURRED:
            self._layout.engine_response = ResponseType.NONE
            print('Engine returned ERROR_OCCURRED')
            return False

        if response == ResponseType.CLIP_SAVED:
            # Already finished. Deliberately do NOT consume it — poll_async_result()
            # is what tells the UI the clip landed, and swallowing it here would
            # make the confirmation disappear on exactly the fast saves this
            # branch exists for.
            print(f'Clip saved (engine finished synchronously): {output_path}')
            return True

        # SAVE_STARTED — task queued, encoding continues in the background.
        self._layout.engine_response = ResponseType.NONE
        print(f'Clip save queued: {output_path} ({duration}s)')
        return True


    def pause_recording(self) -> bool:
        if not self.is_connected():
            return False
        self._layout.ui_command = CommandType.STOP_RECORDING
        return True

    def resume_recording(self) -> bool:
        if not self.is_connected():
            return False
        self._layout.ui_command = CommandType.START_RECORDING
        return True

    def wait_for_clip_completion(self, timeout_ms: int = 30000) -> bool:
        """
        Optional: Wait for the queued clip to finish encoding.
        
        Use this if you need to know when the file is ready.
        Not required for normal UI flow.
        
        Args:
            timeout_ms: Maximum time to wait (default 30 seconds)
        
        Returns:
            True if CLIP_SAVED received
            False if timeout or error
        """
        if not self.is_connected():
            return False
        
        timeout_sec = timeout_ms / 1000.0
        start = time.monotonic()
        while True:
            resp = self._layout.engine_response
            if resp == ResponseType.CLIP_SAVED:
                self._layout.engine_response = ResponseType.NONE
                return True
            if resp == ResponseType.ERROR_OCCURRED:
                self._layout.engine_response = ResponseType.NONE
                print('wait_for_clip_completion: engine returned ERROR_OCCURRED')
                return False
            if time.monotonic() - start > timeout_sec:
                print(f'Timeout waiting for clip completion ({timeout_ms}ms)')
                return False
            time.sleep(0.010)


    def poll_async_result(self) -> tuple[str, str] | None:
        """Non-blocking check for a late engine verdict on an async SaveClip.

        save_clip() only waits for SAVE_STARTED — the actual mux/write happens
        on an engine thread afterwards. If that write then fails (disk full,
        path vanished, encoder error) the engine sets ERROR_OCCURRED, but
        nothing was reading it: the UI had already said "SAVED" and the user
        went looking for a clip that was never written.

        Call this from the status poll. Returns:
            ('saved', detail)  — CLIP_SAVED seen
            ('error', detail)  — ERROR_OCCURRED seen
            None               — nothing pending

        Consumes the response (resets it to NONE) so a single event is only
        ever reported once.
        """
        if not self.is_connected():
            return None
        try:
            resp = self._layout.engine_response
            if resp not in (ResponseType.CLIP_SAVED, ResponseType.ERROR_OCCURRED):
                return None
            detail = self._read_engine_string()
            self._layout.engine_response = ResponseType.NONE
            return ('saved' if resp == ResponseType.CLIP_SAVED else 'error', detail)
        except Exception as e:
            self._log_read_error_once('poll_async_result', e)
            return None

    def _read_engine_string(self) -> str:
        """Read engine_string across both layouts (wchar on Windows, bytes on
        Linux) without letting a garbled buffer raise into the caller."""
        try:
            raw = self._layout.engine_string
            if isinstance(raw, bytes):
                return raw.decode('utf-8', errors='replace').rstrip('\x00').strip()
            return str(raw).rstrip('\x00').strip()
        except Exception:
            return ''

    def get_status(self) -> dict:
        if not self.is_connected():
            return {'connected': False}
        try:
            return {
                'connected': True,
                'is_recording': self._layout.is_recording,
                'frames_captured': self._layout.frames_captured,
                'nvenc_active': self._layout.nvenc_active,
            }
        except Exception as e:
            self._log_read_error_once('get_status', e)
            return {'connected': False}

    _CODEC_PREF_MAP = {'auto': 0, 'h264': 1, 'hevc': 2, 'av1': 3}

    def set_encoder_config(self, codec_pref: str, preset: int) -> bool:
        if not self.is_connected():
            return False
        pref_int = self._CODEC_PREF_MAP.get(codec_pref.lower(), 0)
        if codec_pref.lower() not in self._CODEC_PREF_MAP:
            print(f'[CaptureBridge] Unknown codec_pref "{codec_pref}", falling back to auto')
        preset   = max(1, min(7, preset))
        self._layout.cfg_codec_pref = pref_int
        self._layout.cfg_preset     = preset
        self._layout.ui_command     = CommandType.RECONFIGURE_ENCODER
        return True

    def get_active_codec(self) -> str:
        if not self.is_connected():
            return ''
        try:
            raw = self._layout.active_codec
            return raw.decode('utf-8', errors='ignore').rstrip('\x00')
        except Exception as e:
            self._log_read_error_once('get_active_codec', e)
            return ''

    def get_active_preset(self) -> int:
        if not self.is_connected():
            return 4
        try:
            return int(self._layout.cfg_preset) or 4
        except Exception as e:
            self._log_read_error_once('get_active_preset', e)
            return 4

    def get_audio_mappings(self) -> dict:
        """Returns {app_name: category_name} from shared memory."""
        if not self.is_connected():
            return {}
        try:
            raw  = self._layout.active_audio_mappings
            text = raw.decode('utf-8', errors='ignore').rstrip('\x00')
            if not text or text == '{}':
                return {}
            import json
            return json.loads(text)
        except Exception as e:
            self._log_read_error_once('get_audio_mappings', e)
            return {}

    def is_multiband_active(self) -> bool:
        if not self.is_connected():
            return False
        try:
            return bool(self._layout.multiband_enabled)
        except Exception as e:
            self._log_read_error_once('is_multiband_active', e)
            return False
