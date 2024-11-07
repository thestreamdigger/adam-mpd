import time
import signal
from core.config import Config
from core.mpd_client import MPDClient
from hardware.led.controller import LEDController
from hardware.display.tm1637 import TM1637
from hardware.button.controller import ButtonController

class PlayerService:
    """Main service that coordinates all components"""
    def __init__(self):
        self.config = Config()
        self.mpd = MPDClient()
        self.led_controller = LEDController()
        self.display = TM1637()
        self.display.show_dashes()
        self.button_controller = ButtonController()
        self.config.add_observer(self._handle_config_update)
        self.running = False
        self.display_mode = self.config.get('display.mode', 'elapsed')
        self.last_volume = None
        self.volume_display_until = 0
        self.colon_state = False
        self.default_update_interval = self.config.get('timing.update_interval', 1)
        self.volume_update_interval = self.config.get('timing.volume_update_interval', 0.1)
        self.stop_display_state = 0
        self.stop_state_changed_at = 0
        self.last_track_number = None
        self.track_display_until = 0
        self.pause_last_toggle = 0
        self.pause_blink_interval = self.config.get('display.pause_mode.blink_interval', 1)
        self.track_number_time = self.config.get('display.play_mode.track_number_time', 2)
        self._load_display_config()

    def _load_display_config(self):
        """Load all display-related configurations"""
        # Load existing stop mode config
        self._load_stop_mode_config()
        
        # Load pause and play mode configs
        self.pause_blink_interval = self.config.get('display.pause_mode.blink_interval', 1)
        self.track_number_time = self.config.get('display.play_mode.track_number_time', 2)

    def _load_stop_mode_config(self):
        """Load stop mode display configuration"""
        self.stop_mode_times = {
            'symbol': self.config.get('display.stop_mode.stop_symbol_time', 2),
            'tracks': self.config.get('display.stop_mode.track_total_time', 2),
            'total': self.config.get('display.stop_mode.total_time_display', 2)
        }

    def _handle_config_update(self):
        """Handle configuration updates"""
        self.display_mode = self.config.get('display.mode', 'elapsed')
        self._load_display_config()

    def _update_stop_display(self):
        """Handle stop state display rotation"""
        current_time = time.time()
        
        current_duration = self.stop_mode_times.get(
            ['symbol', 'tracks', 'total'][self.stop_display_state], 
            2
        )
        
        if current_time - self.stop_state_changed_at >= current_duration:
            self.stop_display_state = (self.stop_display_state + 1) % 3
            self.stop_state_changed_at = current_time
        
        playlist_info = self.mpd.get_playlist_info()
        
        if self.stop_display_state == 0:
            self.display.show_dashes()
        elif self.stop_display_state == 1:
            self.display.show_track_total(playlist_info['total_tracks'])
        elif self.stop_display_state == 2:
            total_time = sum(
                float(track.get('duration', 0)) 
                for track in playlist_info['tracks']
            )
            minutes = int(total_time) // 60
            seconds = int(total_time) % 60
            self.display.show_time(minutes, seconds, True)

    def _check_track_change(self, current_song):
        """Check if track has changed and update display accordingly"""
        if not current_song:
            return
        
        track_number = current_song.get('track', '0')
        
        # Only update if track number changed
        if track_number and track_number != self.last_track_number:
            self.last_track_number = track_number
            if track_number.isdigit():
                track_num = int(track_number)
                if 1 <= track_num <= 99:
                    self.track_display_until = time.time() + self.track_number_time
                    self.display.show_track_number(track_num)

    def _update_pause_display(self, elapsed_time, total_time):
        """Handle pause state display with blinking effect"""
        # Usa tempo absoluto para determinar estado do display
        phase = int(time.time() / self.pause_blink_interval) % 2
        
        if phase == 0:
            minutes = int(float(elapsed_time)) // 60
            seconds = int(float(elapsed_time)) % 60
            self.display.show_time(minutes, seconds, True)
        else:
            self.display.clear()

    def _update_time_display(self, elapsed_time, total_time):
        """Update display with current time information"""
        try:
            if self.display_mode == "remaining" and total_time != 'N/A':
                time_value = float(total_time) - float(elapsed_time)
            else:
                time_value = float(elapsed_time)
                
            minutes = int(time_value) // 60
            seconds = int(time_value) % 60
            self.display.show_time(minutes, seconds, True)
        except (ValueError, TypeError):
            self.display.show_dashes()

    def _update_display(self, status):
        """Update display based on current state"""
        current_time = time.time()
        
        if current_time < self.volume_display_until:
            current_volume = int(status.get('volume', '0'))
            self.display.show_volume(current_volume)
            return
        
        state = status.get('state', 'stop')
        
        if state == 'play':
            current_song = self.mpd.get_current_song()
            self._check_track_change(current_song)
            
            if current_time < self.track_display_until:
                return
                
            elapsed_time = status.get('elapsed', '0')
            total_time = status.get('duration', '0')
            self._update_time_display(elapsed_time, total_time)
            
        elif state == 'pause':
            self._update_pause_display(
                status.get('elapsed', '0'),
                status.get('duration', '0')
            )
        elif state == 'stop':
            self._update_stop_display()

    def show_volume(self, status):
        """Show volume temporarily"""
        try:
            current_volume = int(status.get('volume', '0'))
            self.display.show_volume(current_volume)
            self.volume_display_until = time.time() + self.config.get('timing.volume_display_duration', 3)
        except (ValueError, TypeError):
            return

    def start(self):
        """Start the service"""
        self.running = True
        
        def handle_signal(signum, frame):
            self.running = False
        
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        try:
            while self.running:
                start_time = time.time()
                
                status = self.mpd.get_status()
                if status:
                    # Update LEDs
                    self.led_controller.update_from_mpd_status(status)
                    
                    # Handle volume display
                    current_volume = status.get('volume', '0')
                    if current_volume != self.last_volume:
                        self.show_volume(status)
                        self.last_volume = current_volume
                    
                    # Update display
                    self._update_display(status)
                
                # Define o intervalo de atualização
                current_time = time.time()
                update_interval = (self.volume_update_interval 
                                 if current_time < self.volume_display_until 
                                 else self.default_update_interval)
                
                # Sleep for remaining time
                elapsed = time.time() - start_time
                sleep_time = max(0, update_interval - elapsed)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        finally:
            self.cleanup()

    def cleanup(self):
        """Cleanup all resources"""
        self.config.remove_observer(self._handle_config_update)
        self.led_controller.cleanup()
        self.display.cleanup()
        self.button_controller.cleanup()
        self.mpd.close()
        self.config.stop_observer()