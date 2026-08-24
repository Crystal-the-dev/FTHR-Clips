"""
Settings Manager - load/save user preferences.

User preferences are stored in a single human-readable JSON file at
~/.fthr/settings.json. JSON keeps this small configuration portable and easy to
recover; a database would add unnecessary schema and migration overhead.
"""
import json
import os
from pathlib import Path


class SettingsManager:
    """Manages application settings persistence"""

    def __init__(self):
        # ~/.fthr is the one true home for all user state (settings, hotkeys,
        # themes). Lives outside the install dir so updates never wipe it.
        self.config_file = Path.home() / '.fthr' / 'settings.json'
        self.settings = self._load_settings()
    
    def _load_settings(self) -> dict:
        """Load settings from config file"""
        default_settings = {
            'clip_length': 30,       # seconds
            'extended_clip_length': 60,  # seconds — used by F10 / EXT. CLIP hotkey
            'framerate': 60,         # FPS
            'resolution': 'source',  # 480p/720p/1080p/1440p/source
            'bitrate_level': 'medium',  # low/medium/high
            'hotkeys': {
                'save_clip': 'F9',
                'save_extended_clip': 'F10',
                'save_screenshot': 'F11'
            },
            'quick_crop': None,      # dict {x,y,w,h,src_w,src_h} or None
            # Stable native endpoint ID on Windows. The friendly name remains
            # only for display and one-time migration of older settings files.
            'mic_device_id': None,
            'mic_device_name': None, # str display name, or None for system default
            'mic_volume': 100,       # 0–200 (scaled in callbacks)
            'mic_loopback': False,   # real-time monitor: hear your own mic
            # 'stretch' = fill the target rect, distort if aspect differs.
            # 'fit'     = preserve aspect, add black bars (letterbox/pillarbox).
            'scaling_mode': 'stretch',
            # Editor audio mix — persisted between clips. Master controls the
            # QMediaPlayer playback volume directly. Per-source values are
            # applied at export time (ffmpeg amix); they only have audible
            # effect on clips recorded with multi-track audio capture.
            'master_volume': 80,     # 0–100, applied to QAudioOutput
            'source_volumes': {      # per-category 0–100, applied at export
                'game':    100,
                'browser': 100,
                'music':   100,
                'discord': 100,
            },
            'sound_volume_clip':        100,  # 0–100, FTHR notification sounds
            'sound_volume_screenshot':  100,
            'sound_volume_error':       100,
            'notification_monitor': 'auto',  # 'auto' = highest refresh rate, or screen name e.g. 'DP-3'
            # Windows: stable monitor device path. Linux: wl_output name.
            'capture_monitor': '',
            'imported_clip_folders': [],  # additional folders from other clipping software
            # ── Upload ────────────────────────────────────────────────────
            'upload_enabled':          False,
            'upload_server_url':       '',
            'upload_auth_header':      '',
            'upload_mode':             'manual',    # 'immediate' | 'interval' | 'manual'
            'upload_interval_value':   5,
            'upload_interval_unit':    'minutes',   # 'minutes' | 'hours' | 'days'
            'upload_auto_delete':      False,
            # Encoder codec + preset (v2 settings)
            'codec_pref':     'auto',   # 'auto' | 'h264' | 'hevc' | 'av1'
            'encoder_preset': 4,        # 1–7
            # Multiband audio
            'multiband_audio_enabled': False,
            'game_detection_enabled':  False,
            'audio_capture_enabled':   True,
            'watermark_enabled':  False,
            'watermark_text':     'FTHR',
            'auto_crop_enabled':  False,
            'anticheat_detection_enabled': False,
            'camera_enabled':       False,
            'camera_device_index':  0,
            'camera_position':      'bottom-right',
            'camera_size':          'medium',
            'audio_categories': [
                {'name': 'Game',     'volume': 100, 'patterns': []},
                {'name': 'Discord',  'volume': 100, 'patterns': ['discord', 'Discord', 'WebRTC']},
                {'name': 'Browser',  'volume': 100, 'patterns': ['firefox', 'chrome', 'chromium', 'brave']},
                {'name': 'Musik',    'volume': 80,  'patterns': ['spotify', 'Spotify', 'vlc', 'mpv']},
                {'name': 'Sonstige', 'volume': 100, 'patterns': []},
            ],
        }
        
        if not self.config_file.exists():
            return default_settings

        try:
            with open(self.config_file, 'r') as f:
                loaded = json.load(f)
            # Merge loaded-over-defaults so adding a NEW setting in a later
            # version doesn't blow up on someone's old config file. Nested dicts
            # (hotkeys, source_volumes) get merged one level deep too — otherwise
            # adding one new key would silently drop the user's existing ones.
            merged = dict(default_settings)
            for k, v in loaded.items():
                if isinstance(v, dict) and isinstance(merged.get(k), dict):
                    merged[k] = {**merged[k], **v}
                else:
                    merged[k] = v
            # Migrate old source_volumes to audio_categories if present in loaded config
            if 'source_volumes' in loaded and 'audio_categories' not in loaded:
                sv = loaded['source_volumes']
                name_map = {'game': 'Game', 'discord': 'Discord',
                            'browser': 'Browser', 'music': 'Musik'}
                cats = merged['audio_categories']
                for old_key, new_name in name_map.items():
                    if old_key in sv:
                        for cat in cats:
                            if cat['name'] == new_name:
                                cat['volume'] = sv[old_key]
            return merged
        except Exception as e:
            print(f"Failed to load settings: {e}")
            # Keep the broken file for diagnosis instead of silently resetting
            # everything — users WILL hand-edit this JSON and break it.
            try:
                self.config_file.replace(self.config_file.with_suffix('.json.corrupt'))
                print(f"Corrupt settings backed up to {self.config_file.with_suffix('.json.corrupt')}")
            except OSError:
                pass
            return default_settings
    
    def save_settings(self):
        """Save current settings to file"""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        # Drop runtime-only scratch values before persisting user settings.
        persistable = {k: v for k, v in self.settings.items() if not k.startswith('_')}
        # Write to a temp file first, then atomically replace.
        # A crash or SIGKILL during a direct write truncates the JSON and
        # loses all settings on next launch.
        tmp = self.config_file.with_suffix('.json.tmp')
        try:
            with open(tmp, 'w') as f:
                json.dump(persistable, f, indent=2)
            os.replace(str(tmp), str(self.config_file))
        except Exception as e:
            print(f"Failed to save settings: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        try:
            print(f"[Settings] Saved to {self.config_file}")
        except Exception:
            # A console encoding failure cannot turn a committed write into a
            # reported settings failure.
            pass
        return True
    
    def get(self, key: str, default=None):
        """Get a setting value"""
        return self.settings.get(key, default)
    
    def set(self, key: str, value):
        """Set a setting value"""
        self.settings[key] = value
    
    def update(self, settings_dict: dict):
        """Update multiple settings at once"""
        self.settings.update(settings_dict)
