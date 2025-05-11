import sys
import os
from dataclasses import dataclass
from datetime import datetime
import requests
import re
from packaging import version
import json
import asyncio

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit,
    QLabel, QFileDialog, QListWidget, QTextEdit, QTabWidget, QButtonGroup, QRadioButton,
    QAbstractItemView, QSpacerItem, QSizePolicy, QProgressBar, QCheckBox, QDialog,
    QDialogButtonBox, QComboBox, QStyledItemDelegate
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QTimer, QTime, QSettings, QSize
from PyQt6.QtGui import QIcon, QTextCursor, QDesktopServices, QPixmap, QBrush
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from getMetadata import get_filtered_data, parse_uri, SpotifyInvalidUrlException, get_raw_spotify_data
from getTracks import TrackDownloader
import SquidWTF

@dataclass
class Track:
    external_urls: str
    title: str
    artists: str
    album: str
    track_number: int
    duration_ms: int
    id: str

class MetadataFetchWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    
    def __init__(self, url):
        super().__init__()
        self.url = url
        
    def run(self):
        try:
            metadata = get_filtered_data(self.url)
            if "error" in metadata:
                self.error.emit(metadata["error"])
            else:
                self.finished.emit(metadata)
        except SpotifyInvalidUrlException as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(f'Failed to fetch metadata: {str(e)}')

class DownloadWorker(QThread):
    finished = pyqtSignal(bool, str, list)
    progress = pyqtSignal(str, int)
    
    def __init__(self, tracks, outpath, is_single_track=False, is_album=False, is_playlist=False, 
                 album_or_playlist_name='', filename_format='title_artist', use_track_numbers=True,
                 use_album_subfolders=False, use_fallback=False, service="amazon", timeout=30,
                 qobuz_region="us"):
        super().__init__()
        self.tracks = tracks
        self.outpath = outpath
        self.is_single_track = is_single_track
        self.is_album = is_album
        self.is_playlist = is_playlist
        self.album_or_playlist_name = album_or_playlist_name
        self.filename_format = filename_format
        self.use_track_numbers = use_track_numbers
        self.use_album_subfolders = use_album_subfolders
        self.use_fallback = use_fallback
        self.service = service
        self.timeout = timeout
        self.qobuz_region = qobuz_region
        self.is_paused = False
        self.is_stopped = False
        self.failed_tracks = []

    def get_formatted_filename(self, track):
        if self.filename_format == "artist_title":
            filename = f"{track.artists} - {track.title}.flac"
        else:
            filename = f"{track.title} - {track.artists}.flac"
        return re.sub(r'[<>:"/\\|?*]', '_', filename)

    def run(self):
        try:
            downloader = TrackDownloader(self.use_fallback, self.timeout)
            
            def progress_update_lucida(current, total, current_overall_progress):
                if total > 0:
                    percent = (current / total) * 100
                    current_mb = current / (1024 * 1024)
                    total_mb = total / (1024 * 1024)
                    self.progress.emit(f"Download progress: {percent:.2f}% ({current_mb:.2f}MB/{total_mb:.2f}MB)", 
                                    current_overall_progress)
                else:
                    self.progress.emit(f"Processing metadata...", current_overall_progress)
            
            total_tracks = len(self.tracks)
            
            for i, track in enumerate(self.tracks):
                while self.is_paused:
                    if self.is_stopped:
                        return
                    self.msleep(100)
                if self.is_stopped:
                    return

                current_overall_progress = int((i / total_tracks) * 100)
                next_overall_progress = int(((i + 1) / total_tracks) * 100)

                self.progress.emit(f"Starting download ({i+1}/{total_tracks}): {track.title} - {track.artists}", 
                                current_overall_progress)
                
                try:
                    track_id = track.id
                    self.progress.emit(f"Getting track info for ID: {track_id} from {self.service}", current_overall_progress) 
                    
                    if self.is_playlist and self.use_album_subfolders:
                        album_folder = re.sub(r'[<>:"/\\|?*]', '_', track.album)
                        track_outpath = os.path.join(self.outpath, album_folder)
                        os.makedirs(track_outpath, exist_ok=True)
                    else:
                        track_outpath = self.outpath
                    
                    if self.service == "qobuz":
                        self.progress.emit(f"Getting track metadata for: {track.title} - {track.artists}", current_overall_progress)
                        
                        isrc = None
                        try:
                            track_url = track.external_urls
                            self.progress.emit(f"Fetching Spotify metadata for ISRC...", current_overall_progress)
                            raw_data = get_raw_spotify_data(track_url)
                            if raw_data and "external_ids" in raw_data and "isrc" in raw_data["external_ids"]:
                                isrc = raw_data["external_ids"]["isrc"]
                                self.progress.emit(f"Found ISRC from Spotify: {isrc}", current_overall_progress)
                        except Exception as e:
                            self.progress.emit(f"Could not get ISRC from Spotify raw data: {str(e)}", current_overall_progress)
                        
                        if not isrc:
                            self.progress.emit(f"No ISRC found, searching by title and artist", current_overall_progress)
                            search_query = f"{track.title} {track.artists}"
                            try:
                                self.progress.emit(f"Searching Qobuz for: {search_query}", current_overall_progress)
                                qobuz_track_info = SquidWTF.search_track(track.title, track.artists, strict_match=True, region=self.qobuz_region)
                                if qobuz_track_info:
                                    qobuz_track_id = qobuz_track_info["id"]
                                    self.progress.emit(f"Found track on Qobuz by title search: {qobuz_track_info['title']} - {qobuz_track_info['performer']['name']}", current_overall_progress)
                                    found_artist = qobuz_track_info['performer']['name'].lower()
                                    expected_artist = track.artists.lower()
                                    if expected_artist not in found_artist and found_artist not in expected_artist:
                                        self.progress.emit(f"Warning: Artist mismatch! Expected: {track.artists}, Found: {qobuz_track_info['performer']['name']}", current_overall_progress)
                                        raise Exception(f"Artist mismatch: Expected '{track.artists}', found '{qobuz_track_info['performer']['name']}'")
                                else:
                                    raise Exception(f"Could not find track on Qobuz: {track.title} - {track.artists}")
                            except Exception as e:
                                self.progress.emit(f"Search by title failed: {str(e)}", current_overall_progress)
                                raise Exception(f"Could not find track on Qobuz: {track.title} - {track.artists}")
                        else:
                            self.progress.emit(f"Searching Qobuz with ISRC: {isrc}", current_overall_progress)
                            qobuz_track_info = SquidWTF.get_track_info(isrc, region=self.qobuz_region)
                            qobuz_track_id = qobuz_track_info["id"]
                            self.progress.emit(f"Found track on Qobuz: {qobuz_track_info['title']} - {qobuz_track_info['performer']['name']}", current_overall_progress)
                            found_artist = qobuz_track_info['performer']['name'].lower()
                            expected_artist = track.artists.lower()
                            if expected_artist not in found_artist and found_artist not in expected_artist:
                                self.progress.emit(f"Warning: Artist mismatch! Expected: {track.artists}, Found: {qobuz_track_info['performer']['name']}", current_overall_progress)
                        
                        download_url = SquidWTF.get_download_url(qobuz_track_id, region=self.qobuz_region)
                        os.makedirs(track_outpath, exist_ok=True)
                        temp_filename = os.path.join(track_outpath, f"temp_{qobuz_track_id}.flac")
                        self.progress.emit(f"Downloading from Qobuz...", current_overall_progress)
                        
                        def progress_callback_qobuz(current, total):
                            if total > 0:
                                percent = (current / total) * 100
                                current_mb = current / (1024 * 1024)
                                total_mb = total / (1024 * 1024)
                                self.progress.emit(f"Download progress: {percent:.2f}% ({current_mb:.2f}MB/{total_mb:.2f}MB)", 
                                                current_overall_progress)
                        
                        try:
                            SquidWTF.download_file(download_url, temp_filename, progress_callback_qobuz)
                            
                            if not os.path.exists(temp_filename) or os.path.getsize(temp_filename) == 0:
                                raise Exception(f"Downloaded file is empty or does not exist: {temp_filename}")
                            
                            self.progress.emit(f"Embedding metadata...", current_overall_progress)
                            SquidWTF.embed_metadata(temp_filename, qobuz_track_info)
                            
                            if (self.is_album or (self.is_playlist and self.use_album_subfolders)) and self.use_track_numbers:
                                new_filename = f"{track.track_number:02d} - {self.get_formatted_filename(track)}"
                            else:
                                new_filename = self.get_formatted_filename(track)
                            
                            new_filename = re.sub(r'[<>:"/\\|?*]', '_', new_filename)
                            new_filepath = os.path.join(track_outpath, new_filename)
                            
                            if os.path.exists(new_filepath):
                                os.remove(new_filepath)
                            os.rename(temp_filename, new_filepath)
                            
                            downloaded_file = new_filepath
                            self.progress.emit(f"File renamed to: {new_filename}", current_overall_progress)
                        except Exception as e:
                            self.progress.emit(f"Error during download or processing: {str(e)}", current_overall_progress)
                            if os.path.exists(temp_filename):
                                try:
                                    os.remove(temp_filename)
                                    self.progress.emit(f"Removed incomplete download file", current_overall_progress)
                                except:
                                    pass
                            raise Exception(f"Failed to download or process file: {str(e)}")
                    else:
                        metadata = asyncio.run(downloader.get_track_info(track_id, self.service))
                        
                        self.progress.emit(f"Track info received, starting download process", current_overall_progress)
                        
                        is_paused_callback = lambda: self.is_paused
                        is_stopped_callback = lambda: self.is_stopped
                        
                        downloader.set_progress_callback(
                            lambda current, total: progress_update_lucida(current, total, current_overall_progress)
                        )

                        downloaded_file = downloader.download(
                            metadata, 
                            track_outpath,
                            is_paused_callback=is_paused_callback,
                            is_stopped_callback=is_stopped_callback
                        )
                        
                        if (self.is_album or (self.is_playlist and self.use_album_subfolders)) and self.use_track_numbers:
                            new_filename = f"{track.track_number:02d} - {self.get_formatted_filename(track)}"
                        else:
                            new_filename = self.get_formatted_filename(track)
                        
                        new_filename = re.sub(r'[<>:"/\\|?*]', '_', new_filename)
                        new_filepath = os.path.join(track_outpath, new_filename)
                        
                        if os.path.exists(downloaded_file) and downloaded_file != new_filepath:
                            if os.path.exists(new_filepath):
                                os.remove(new_filepath)
                            os.rename(downloaded_file, new_filepath)
                            self.progress.emit(f"File renamed to: {new_filename}", current_overall_progress)
                    
                    self.progress.emit(f"Successfully downloaded: {track.title} - {track.artists}", 
                                    next_overall_progress)
                except Exception as e:
                    self.failed_tracks.append((track.title, track.artists, str(e)))
                    self.progress.emit(f"Failed to download: {track.title} - {track.artists}\nError: {str(e)}", 
                                    next_overall_progress)
                    continue

            if not self.is_stopped:
                success_message = "Download completed!"
                if self.failed_tracks:
                    success_message += f"\n\nFailed downloads: {len(self.failed_tracks)} tracks"
                self.finished.emit(True, success_message, self.failed_tracks)
                
        except Exception as e:
            self.finished.emit(False, str(e), self.failed_tracks)

    def pause(self):
        self.is_paused = True
        self.progress.emit("Download process paused.", 0)

    def resume(self):
        self.is_paused = False
        self.progress.emit("Download process resumed.", 0)

    def stop(self): 
        self.is_stopped = True
        self.is_paused = False

class UpdateDialog(QDialog):
    def __init__(self, current_version, new_version, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update Available")
        self.setFixedWidth(400)
        self.setModal(True)

        layout = QVBoxLayout()

        message = QLabel(f"A new version of SpotiFLAC is available!\n\n"
                        f"Current version: v{current_version}\n"
                        f"New version: v{new_version}")
        message.setWordWrap(True)
        layout.addWidget(message)

        self.disable_check = QCheckBox("Turn off update checking")
        self.disable_check.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.disable_check)

        button_box = QDialogButtonBox()
        self.update_button = QPushButton("Update")
        self.update_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        
        button_box.addButton(self.update_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton(self.cancel_button, QDialogButtonBox.ButtonRole.RejectRole)
        
        layout.addWidget(button_box)

        self.setLayout(layout)

        self.update_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
class ServiceStatusChecker(QThread):
    status_updated = pyqtSignal(dict)
    error = pyqtSignal(str)
    
    def run(self):
        services_status = {
            'amazon': False,
            'tidal': False,
            'deezer': False,
            'qobuz': False
        }
        
        try:
            response = requests.get("https://lucida.to/api/stats", timeout=5)
            if response.status_code == 200:
                try:
                    data = response.json()
                    current_services = data.get('all', {}).get('downloads', {}).get('current', {}).get('services', {})
                    
                    services_status['amazon'] = current_services.get('amazon', 0) > 0
                    services_status['tidal'] = current_services.get('tidal', 0) > 0
                    services_status['deezer'] = current_services.get('deezer', 0) > 0
                    
                except json.JSONDecodeError:
                    pass
                except Exception:
                    pass
        except requests.exceptions.RequestException:
            pass
        except Exception:
            pass
        
        eu_online = False
        us_online = False

        try:
            eu_response = requests.get("https://eu.qobuz.squid.wtf", timeout=5)
            eu_online = eu_response.status_code in [200, 304]
        except Exception:
            pass

        try:
            us_response = requests.get("https://us.qobuz.squid.wtf", timeout=5)
            us_online = us_response.status_code in [200, 304]
        except Exception:
            pass

        services_status['qobuz'] = eu_online or us_online
        
        self.status_updated.emit(services_status)

class StatusIndicatorDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        item_data = index.data(Qt.ItemDataRole.UserRole)
        is_online = item_data.get('online', False) if item_data else False
        
        super().paint(painter, option, index)
        
        if item_data and item_data.get('online') is None:
            return
          
        indicator_color = Qt.GlobalColor.green if is_online else Qt.GlobalColor.red
        
        circle_size = 6
        circle_y = option.rect.center().y() - circle_size // 2
        circle_x = option.rect.right() - circle_size - 10
        
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(indicator_color))
        painter.drawEllipse(circle_x, circle_y, circle_size, circle_size)
        painter.restore()

class ServiceComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(16, 16))
        self.services_status = {}
        
        self.setItemDelegate(StatusIndicatorDelegate())
        
        self.setup_items()
        
        self.status_checker = ServiceStatusChecker()
        self.status_checker.status_updated.connect(self.update_service_status)
        self.status_checker.error.connect(lambda e: print(f"Status check error: {e}"))
        self.status_checker.start()
        
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(5000)
        
    def setup_items(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        self.services = [
            {'id': 'tidal', 'name': 'Tidal', 'icon': 'tidal.png', 'online': False},
            {'id': 'amazon', 'name': 'Amazon Music', 'icon': 'amazon.png', 'online': False},
            {'id': 'deezer', 'name': 'Deezer', 'icon': 'deezer.png', 'online': False},
            {'id': 'qobuz', 'name': 'Qobuz', 'icon': 'qobuz.png', 'online': False}
        ]
        
        for service in self.services:
            icon_path = os.path.join(current_dir, service['icon'])
            if not os.path.exists(icon_path):
                self.create_placeholder_icon(icon_path)
            
            icon = QIcon(icon_path)
            
            self.addItem(icon, service['name'])
            item_index = self.count() - 1
            self.setItemData(item_index, service['id'], Qt.ItemDataRole.UserRole + 1)
            self.setItemData(item_index, service, Qt.ItemDataRole.UserRole)
    
    def create_placeholder_icon(self, path):
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.save(path)
    
    def update_service_status(self, status_dict):
        self.services_status = status_dict
        
        for i in range(self.count()):
            service_id = self.itemData(i, Qt.ItemDataRole.UserRole + 1)
            
            if service_id in self.services_status:
                service_data = self.itemData(i, Qt.ItemDataRole.UserRole)
                if isinstance(service_data, dict):
                    service_data['online'] = self.services_status[service_id]
                    self.setItemData(i, service_data, Qt.ItemDataRole.UserRole)
        
        self.update()
    
    def refresh_status(self):
        self.status_checker = ServiceStatusChecker()
        self.status_checker.status_updated.connect(self.update_service_status)
        self.status_checker.error.connect(lambda e: print(f"Status check error: {e}"))
        self.status_checker.start()
        
    def currentData(self, role=Qt.ItemDataRole.UserRole + 1):
        return super().currentData(role)
        
class QobuzRegionComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(16, 16))
        self.regions_status = {}
        
        self.setItemDelegate(StatusIndicatorDelegate())
        
        self.setup_items()
        
        self.status_checker = QThread()
        self.status_checker.run = self.check_status
        self.status_checker.finished.connect(self.update_region_status)
        self.status_checker.start()
        
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh_status)
        self.status_timer.start(5000)
        
    def setup_items(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        self.regions = [
            {'id': 'eu', 'name': 'Europe', 'icon': 'eu.svg', 'online': False, 'url': 'https://eu.qobuz.squid.wtf'},
            {'id': 'us', 'name': 'North America', 'icon': 'us.svg', 'online': False, 'url': 'https://us.qobuz.squid.wtf'}
        ]
        
        for region in self.regions:
            icon_path = os.path.join(current_dir, region['icon'])
            if not os.path.exists(icon_path):
                self.create_placeholder_icon(icon_path)
            
            icon = QIcon(icon_path)
            
            self.addItem(icon, region['name'])
            item_index = self.count() - 1
            self.setItemData(item_index, region['id'], Qt.ItemDataRole.UserRole + 1)
            self.setItemData(item_index, region, Qt.ItemDataRole.UserRole)
      
      
    def create_placeholder_icon(self, path):
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.save(path)
      
    def check_status(self):
        regions_status = {}
        
        for region in self.regions:
            try:
                response = requests.get(region['url'], timeout=5)
                regions_status[region['id']] = response.status_code in [200, 304]
            except requests.exceptions.RequestException:
                regions_status[region['id']] = False
            except Exception:
                regions_status[region['id']] = False
          
        self.regions_status = regions_status
      
    def update_region_status(self):
        for i in range(self.count()):
            region_id = self.itemData(i, Qt.ItemDataRole.UserRole + 1)
            
            if region_id in self.regions_status:
                region_data = self.itemData(i, Qt.ItemDataRole.UserRole)
                if isinstance(region_data, dict):
                    region_data['online'] = self.regions_status[region_id]
                    self.setItemData(i, region_data, Qt.ItemDataRole.UserRole)
          
        self.update()
      
    def refresh_status(self):
        self.status_checker = QThread()
        self.status_checker.run = self.check_status
        self.status_checker.finished.connect(self.update_region_status)
        self.status_checker.start()
      
    def currentData(self, role=Qt.ItemDataRole.UserRole + 1):
        return super().currentData(role)
        
class SpotiFLACGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.current_version = "2.8"
        self.tracks = []
        self.reset_state()
        
        self.settings = QSettings('SpotiFLAC', 'Settings')
        self.last_output_path = self.settings.value('output_path', os.path.expanduser("~\\Music"))
        self.last_url = self.settings.value('spotify_url', '')
        
        self.filename_format = self.settings.value('filename_format', 'title_artist')
        self.use_track_numbers = self.settings.value('use_track_numbers', False, type=bool)
        self.use_album_subfolders = self.settings.value('use_album_subfolders', False, type=bool)
        self.use_fallback = self.settings.value('use_fallback', False, type=bool)
        self.service = self.settings.value('service', 'amazon')
        self.qobuz_region = self.settings.value('qobuz_region', 'us')
        self.timeout_value = self.settings.value('timeout_value', 30, type=int)
        self.check_for_updates = self.settings.value('check_for_updates', True, type=bool)
        
        self.elapsed_time = QTime(0, 0, 0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_timer)
        
        self.network_manager = QNetworkAccessManager()
        self.network_manager.finished.connect(self.on_cover_loaded)
        
        self.initUI()
        
        if self.check_for_updates:
            QTimer.singleShot(0, self.check_updates)

    def check_updates(self):
        try:
            response = requests.get("https://raw.githubusercontent.com/afkarxyz/SpotiFLAC/refs/heads/main/version.json")
            if response.status_code == 200:
                data = response.json()
                new_version = data.get("version")
                
                if new_version and version.parse(new_version) > version.parse(self.current_version):
                    dialog = UpdateDialog(self.current_version, new_version, self)
                    result = dialog.exec()
                    
                    if dialog.disable_check.isChecked():
                        self.settings.setValue('check_for_updates', False)
                        self.check_for_updates = False
                    
                    if result == QDialog.DialogCode.Accepted:
                        QDesktopServices.openUrl(QUrl("https://github.com/afkarxyz/SpotiFLAC/releases"))
                        
        except Exception:
            pass

    @staticmethod
    def format_duration(ms):
        minutes = ms // 60000
        seconds = (ms % 60000) // 1000
        return f"{minutes}:{seconds:02d}"
    
    def reset_state(self):
        self.tracks.clear()
        self.is_album = False
        self.is_playlist = False 
        self.is_single_track = False
        self.album_or_playlist_name = ''

    def reset_ui(self):
        self.track_list.clear()
        self.log_output.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        self.stop_btn.hide()
        self.pause_resume_btn.hide()
        self.pause_resume_btn.setText('Pause')
        self.reset_info_widget()
        self.hide_track_buttons()

    def initUI(self):
        self.setWindowTitle('SpotiFLAC')
        self.setFixedWidth(650)
        self.setFixedHeight(350)
        
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
            
        self.main_layout = QVBoxLayout()
        
        self.setup_spotify_section()
        self.setup_tabs()
        
        self.setLayout(self.main_layout)
        QTimer.singleShot(0, self.update_service_ui_visibility)

    def setup_spotify_section(self):
        spotify_layout = QHBoxLayout()
        spotify_label = QLabel('Spotify URL:')
        spotify_label.setFixedWidth(100)
        
        self.spotify_url = QLineEdit()
        self.spotify_url.setPlaceholderText("Please enter the Spotify URL")
        self.spotify_url.setClearButtonEnabled(True)
        self.spotify_url.setText(self.last_url)
        self.spotify_url.textChanged.connect(self.save_url)
        
        self.fetch_btn = QPushButton('Fetch')
        self.fetch_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fetch_btn.clicked.connect(self.fetch_tracks)
        
        spotify_layout.addWidget(spotify_label)
        spotify_layout.addWidget(self.spotify_url)
        spotify_layout.addWidget(self.fetch_btn)
        self.main_layout.addLayout(spotify_layout)

    def browse_output(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory:
            self.output_dir.setText(directory)
            self.save_settings()

    def setup_tabs(self):
        self.tab_widget = QTabWidget()
        self.main_layout.addWidget(self.tab_widget)

        self.setup_dashboard_tab()
        self.setup_process_tab()
        self.setup_settings_tab()
        self.setup_about_tab()

    def setup_dashboard_tab(self):
        dashboard_tab = QWidget()
        dashboard_layout = QVBoxLayout()

        self.setup_info_widget()
        dashboard_layout.addWidget(self.info_widget)

        self.track_list = QListWidget()
        self.track_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        dashboard_layout.addWidget(self.track_list)
        
        self.setup_track_buttons()
        dashboard_layout.addLayout(self.btn_layout)

        dashboard_tab.setLayout(dashboard_layout)
        self.tab_widget.addTab(dashboard_tab, "Dashboard")

        self.hide_track_buttons()

    def setup_info_widget(self):
        self.info_widget = QWidget()
        info_layout = QHBoxLayout()
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(80, 80)
        self.cover_label.setScaledContents(True)
        info_layout.addWidget(self.cover_label)

        text_info_layout = QVBoxLayout()
        
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.title_label.setWordWrap(True)
        
        self.artists_label = QLabel()
        self.artists_label.setWordWrap(True)

        self.followers_label = QLabel()
        self.followers_label.setWordWrap(True)
        
        self.release_date_label = QLabel()
        self.release_date_label.setWordWrap(True)
        
        self.type_label = QLabel()
        self.type_label.setStyleSheet("font-size: 12px;")
        
        text_info_layout.addWidget(self.title_label)
        text_info_layout.addWidget(self.artists_label)
        text_info_layout.addWidget(self.followers_label)
        text_info_layout.addWidget(self.release_date_label)
        text_info_layout.addWidget(self.type_label)
        text_info_layout.addStretch()

        info_layout.addLayout(text_info_layout, 1)
        self.info_widget.setLayout(info_layout)
        self.info_widget.setFixedHeight(100)
        self.info_widget.hide()

    def setup_track_buttons(self):
        self.btn_layout = QHBoxLayout()
        self.download_selected_btn = QPushButton('Download Selected')
        self.download_all_btn = QPushButton('Download All')
        self.remove_btn = QPushButton('Remove Selected')
        self.clear_btn = QPushButton('Clear')
        
        for btn in [self.download_selected_btn, self.download_all_btn, self.remove_btn, self.clear_btn]:
            btn.setFixedWidth(150)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            
        self.download_selected_btn.clicked.connect(self.download_selected)
        self.download_all_btn.clicked.connect(self.download_all)
        self.remove_btn.clicked.connect(self.remove_selected_tracks)
        self.clear_btn.clicked.connect(self.clear_tracks)
        
        self.btn_layout.addStretch()
        for btn in [self.download_selected_btn, self.download_all_btn, self.remove_btn, self.clear_btn]:
            self.btn_layout.addWidget(btn)
        self.btn_layout.addStretch()

    def setup_process_tab(self):
        self.process_tab = QWidget()
        process_layout = QVBoxLayout()
        process_layout.setSpacing(5)
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        process_layout.addWidget(self.log_output)
        
        progress_time_layout = QVBoxLayout()
        progress_time_layout.setSpacing(2)
        
        self.progress_bar = QProgressBar()
        progress_time_layout.addWidget(self.progress_bar)
        
        self.time_label = QLabel("00:00:00")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_time_layout.addWidget(self.time_label)
        
        process_layout.addLayout(progress_time_layout)
        
        control_layout = QHBoxLayout()
        self.stop_btn = QPushButton('Stop')
        self.pause_resume_btn = QPushButton('Pause')
        
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_resume_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        
        self.stop_btn.clicked.connect(self.stop_download)
        self.pause_resume_btn.clicked.connect(self.toggle_pause_resume)
        control_layout.addWidget(self.stop_btn)
        control_layout.addWidget(self.pause_resume_btn)
        
        process_layout.addLayout(control_layout)
        
        self.process_tab.setLayout(process_layout)
        
        self.tab_widget.addTab(self.process_tab, "Process")
        
        self.progress_bar.hide()
        self.time_label.hide()
        self.stop_btn.hide()
        self.pause_resume_btn.hide()

    def setup_settings_tab(self):
        settings_tab = QWidget()
        settings_layout = QVBoxLayout()
        settings_layout.setSpacing(10)
        settings_layout.setContentsMargins(9, 9, 9, 9)

        output_group = QWidget()
        output_layout = QVBoxLayout(output_group)
        output_layout.setSpacing(5)
        
        output_label = QLabel('Output Directory')
        output_label.setStyleSheet("font-weight: bold;")
        output_layout.addWidget(output_label)
        
        output_dir_layout = QHBoxLayout()
        self.output_dir = QLineEdit()
        self.output_dir.setText(self.last_output_path)
        self.output_dir.textChanged.connect(self.save_settings)
        
        self.output_browse = QPushButton('Browse')
        self.output_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.output_browse.clicked.connect(self.browse_output)
        
        output_dir_layout.addWidget(self.output_dir)
        output_dir_layout.addWidget(self.output_browse)
        output_layout.addLayout(output_dir_layout)
        
        settings_layout.addWidget(output_group)

        file_group = QWidget()
        file_layout = QVBoxLayout(file_group)
        file_layout.setSpacing(5)
        
        file_label = QLabel('File Settings')
        file_label.setStyleSheet("font-weight: bold;")
        file_layout.addWidget(file_label)
        
        format_layout = QHBoxLayout()
        format_label = QLabel('Filename Format:')
        
        self.format_group = QButtonGroup(self)
        self.title_artist_radio = QRadioButton('Title - Artist')
        self.title_artist_radio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.title_artist_radio.toggled.connect(self.save_filename_format)
        
        self.artist_title_radio = QRadioButton('Artist - Title')
        self.artist_title_radio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.artist_title_radio.toggled.connect(self.save_filename_format)
        
        if hasattr(self, 'filename_format') and self.filename_format == "artist_title":
            self.artist_title_radio.setChecked(True)
        else:
            self.title_artist_radio.setChecked(True)
        
        self.format_group.addButton(self.title_artist_radio)
        self.format_group.addButton(self.artist_title_radio)
        
        format_layout.addWidget(format_label)
        format_layout.addWidget(self.title_artist_radio)
        format_layout.addWidget(self.artist_title_radio)
        format_layout.addStretch()
        file_layout.addLayout(format_layout)

        checkbox_layout = QHBoxLayout()
        
        self.track_number_checkbox = QCheckBox('Add Track Numbers to Album Files')
        self.track_number_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.track_number_checkbox.setChecked(self.use_track_numbers)
        self.track_number_checkbox.toggled.connect(self.save_track_numbering)
        checkbox_layout.addWidget(self.track_number_checkbox)
        
        self.album_subfolder_checkbox = QCheckBox('Create Album Subfolders for Playlist Downloads')
        self.album_subfolder_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.album_subfolder_checkbox.setChecked(self.use_album_subfolders)
        self.album_subfolder_checkbox.toggled.connect(self.save_album_subfolder_setting)
        checkbox_layout.addWidget(self.album_subfolder_checkbox)
        
        checkbox_layout.addStretch()
        file_layout.addLayout(checkbox_layout)
        
        settings_layout.addWidget(file_group)

        auth_group = QWidget()
        auth_layout = QVBoxLayout(auth_group)
        auth_layout.setSpacing(5)
        
        auth_label = QLabel('Service Setting')
        auth_label.setStyleSheet("font-weight: bold;")
        auth_layout.addWidget(auth_label)

        source_fallback_layout = QHBoxLayout()

        service_label = QLabel('Source:')
        
        self.service_dropdown = ServiceComboBox()
        self.service_dropdown.currentIndexChanged.connect(self.save_service_setting)
        
        saved_service = self.service
        for i in range(self.service_dropdown.count()):
            if self.service_dropdown.itemData(i, Qt.ItemDataRole.UserRole + 1) == saved_service:
                self.service_dropdown.setCurrentIndex(i)
                break
        
        source_fallback_layout.addWidget(service_label)
        source_fallback_layout.addWidget(self.service_dropdown)
        
        self.qobuz_region_dropdown = QobuzRegionComboBox()
        self.qobuz_region_dropdown.setFixedWidth(150)
        self.qobuz_region_dropdown.currentIndexChanged.connect(self.save_qobuz_region_setting)
        self.qobuz_region_dropdown.setVisible(False)
        source_fallback_layout.addWidget(self.qobuz_region_dropdown)

        saved_region = self.qobuz_region
        for i in range(self.qobuz_region_dropdown.count()):
            if self.qobuz_region_dropdown.itemData(i, Qt.ItemDataRole.UserRole + 1) == saved_region:
                self.qobuz_region_dropdown.setCurrentIndex(i)
                break
        
        source_fallback_layout.addSpacing(20)
        
        self.fallback_checkbox = QCheckBox('Fallback')
        self.fallback_checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fallback_checkbox.setChecked(self.use_fallback)
        self.fallback_checkbox.toggled.connect(self.save_fallback_setting)
        source_fallback_layout.addWidget(self.fallback_checkbox)
        
        source_fallback_layout.addSpacing(20)
        
        timeout_label = QLabel('Timeout:')
        self.timeout_label = timeout_label
        self.timeout_input = QLineEdit()
        self.timeout_input.setText(str(self.timeout_value))
        self.timeout_input.setFixedWidth(60)
        self.timeout_input.textChanged.connect(self.save_timeout_setting)
        source_fallback_layout.addWidget(timeout_label)
        source_fallback_layout.addWidget(self.timeout_input)

        source_fallback_layout.addStretch()
        auth_layout.addLayout(source_fallback_layout)
        
        settings_layout.addWidget(auth_group)

        settings_layout.addStretch()
        settings_tab.setLayout(settings_layout)
        self.tab_widget.addTab(settings_tab, "Settings")
    
    def update_service_ui_visibility(self):
        if hasattr(self, 'fallback_checkbox') and hasattr(self, 'timeout_input') and hasattr(self, 'timeout_label'):
            service = self.service_dropdown.currentData() if hasattr(self, 'service_dropdown') else self.service
            
            if service == "qobuz":
                self.fallback_checkbox.setVisible(False)
                self.timeout_input.setVisible(False)
                self.timeout_label.setVisible(False)
                if hasattr(self, 'qobuz_region_dropdown'):
                    self.qobuz_region_dropdown.setVisible(True)
            else:
                self.fallback_checkbox.setVisible(True)
                self.timeout_input.setVisible(True)
                self.timeout_label.setVisible(True)
                if hasattr(self, 'qobuz_region_dropdown'):
                    self.qobuz_region_dropdown.setVisible(False)
    
    def setup_about_tab(self):
        about_tab = QWidget()
        about_layout = QVBoxLayout()
        about_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.setSpacing(3)

        sections = [
            ("Check for Updates", "https://github.com/afkarxyz/SpotiFLAC/releases"),
            ("Report an Issue", "https://github.com/afkarxyz/SpotiFLAC/issues"),
            ("Lucida Site", "https://lucida.to/stats")
        ]

        for title, url in sections:
            section_widget = QWidget()
            section_layout = QVBoxLayout(section_widget)
            section_layout.setSpacing(10)
            section_layout.setContentsMargins(0, 0, 0, 0)

            label = QLabel(title)
            label.setStyleSheet("color: palette(text); font-weight: bold;")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            section_layout.addWidget(label)

            button = QPushButton("Click Here!")
            button.setFixedWidth(150)
            button.setStyleSheet("""
                QPushButton {
                    background-color: palette(button);
                    color: palette(button-text);
                    border: 1px solid palette(mid);
                    padding: 6px;
                    border-radius: 15px;
                }
                QPushButton:hover {
                    background-color: palette(light);
                }
                QPushButton:pressed {
                    background-color: palette(midlight);
                }
            """)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _, url=url: QDesktopServices.openUrl(QUrl(url)))
            section_layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignCenter)

            about_layout.addWidget(section_widget)
            
            if sections.index((title, url)) < len(sections) - 1:
                spacer = QSpacerItem(20, 6, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
                about_layout.addItem(spacer)

        footer_label = QLabel("v2.8 | May 2025")
        footer_label.setStyleSheet("font-size: 12px; margin-top: 10px;")
        about_layout.addWidget(footer_label, alignment=Qt.AlignmentFlag.AlignCenter)

        about_tab.setLayout(about_layout)
        self.tab_widget.addTab(about_tab, "About")

    def save_url(self):
        self.settings.setValue('spotify_url', self.spotify_url.text().strip())
        self.settings.sync()
        
    def save_filename_format(self):
        self.filename_format = "artist_title" if self.artist_title_radio.isChecked() else "title_artist"
        self.settings.setValue('filename_format', self.filename_format)
        self.settings.sync()
        
    def save_track_numbering(self):
        self.use_track_numbers = self.track_number_checkbox.isChecked()
        self.settings.setValue('use_track_numbers', self.use_track_numbers)
        self.settings.sync()
        
    def save_album_subfolder_setting(self):
        self.use_album_subfolders = self.album_subfolder_checkbox.isChecked()
        self.settings.setValue('use_album_subfolders', self.use_album_subfolders)
        self.settings.sync()
    
    def save_fallback_setting(self):
        self.use_fallback = self.fallback_checkbox.isChecked()
        self.settings.setValue('use_fallback', self.use_fallback)
        self.settings.sync()
        self.log_output.append("Fallback setting saved successfully!")
    
    def save_timeout_setting(self):
        try:
            timeout = int(self.timeout_input.text())
            if timeout > 0:
                self.timeout_value = timeout
                self.settings.setValue('timeout_value', self.timeout_value)
                self.settings.sync()
                self.log_output.append(f"Timeout setting saved: {self.timeout_value} seconds")
            else:
                self.timeout_input.setText(str(self.timeout_value))
                self.log_output.append("Timeout must be a positive number")
        except ValueError:
            self.timeout_input.setText(str(self.timeout_value))
            self.log_output.append("Timeout must be a valid number")
    
    def save_service_setting(self):
        service = self.service_dropdown.currentData()
        self.service = service
        self.settings.setValue('service', service)
        self.settings.sync()
        self.log_output.append(f"Service setting saved: {self.service_dropdown.currentText()}")
        
        if hasattr(self, 'qobuz_region_dropdown'):
            self.qobuz_region_dropdown.setVisible(service == "qobuz")
        
        self.update_service_ui_visibility()
    
    def save_qobuz_region_setting(self):
        region = self.qobuz_region_dropdown.currentData()
        self.qobuz_region = region
        self.settings.setValue('qobuz_region', region)
        self.settings.sync()
        self.log_output.append(f"Qobuz region setting saved: {self.qobuz_region_dropdown.currentText()}")
    
    def save_settings(self):
        self.settings.setValue('output_path', self.output_dir.text().strip())
        self.settings.sync()
        self.log_output.append("Settings saved successfully!")

    def update_timer(self):
        self.elapsed_time = self.elapsed_time.addSecs(1)
        self.time_label.setText(self.elapsed_time.toString("hh:mm:ss"))
                        
    def fetch_tracks(self):
        url = self.spotify_url.text().strip()
        
        if not url:
            self.log_output.append('Warning: Please enter a Spotify URL.')
            return

        try:
            self.reset_state()
            self.reset_ui()
            
            self.log_output.append('Just a moment. Fetching metadata...')
            self.tab_widget.setCurrentWidget(self.process_tab)
            
            self.metadata_worker = MetadataFetchWorker(url)
            self.metadata_worker.finished.connect(self.on_metadata_fetched)
            self.metadata_worker.error.connect(self.on_metadata_error)
            self.metadata_worker.start()
            
        except Exception as e:
            self.log_output.append(f'Error: Failed to start metadata fetch: {str(e)}')
    
    def on_metadata_fetched(self, metadata):
        try:
            url_info = parse_uri(self.spotify_url.text().strip())
            
            if url_info["type"] == "track":
                self.handle_track_metadata(metadata["track"])
            elif url_info["type"] == "album":
                self.handle_album_metadata(metadata)
            elif url_info["type"] == "playlist":
                self.handle_playlist_metadata(metadata)
                
            self.update_button_states()
            self.tab_widget.setCurrentIndex(0)
        except Exception as e:
            self.log_output.append(f'Error: {str(e)}')
    
    def on_metadata_error(self, error_message):
        self.log_output.append(f'Error: {error_message}')

    def handle_track_metadata(self, track_data):
        track_id = track_data["external_urls"].split("/")[-1]
        
        self.tracks = [Track(
            external_urls=track_data["external_urls"],
            title=track_data["name"],
            artists=track_data["artists"],
            album=track_data["album_name"],
            track_number=1,
            duration_ms=track_data.get("duration_ms", 0),
            id=track_id
        )]
        self.is_single_track = True
        self.is_album = self.is_playlist = False
        self.album_or_playlist_name = f"{self.tracks[0].title} - {self.tracks[0].artists}"
        
        metadata = {
            'title': track_data["name"],
            'artists': track_data["artists"],
            'releaseDate': track_data["release_date"],
            'cover': track_data["images"],
            'duration_ms': track_data.get("duration_ms", 0)
        }
        self.update_display_after_fetch(metadata)

    def handle_album_metadata(self, album_data):
        self.album_or_playlist_name = album_data["album_info"]["name"]
        self.tracks = []
        
        for track in album_data["track_list"]:
            track_id = track["external_urls"].split("/")[-1]
            
            self.tracks.append(Track(
                external_urls=track["external_urls"],
                title=track["name"],
                artists=track["artists"],
                album=self.album_or_playlist_name,
                track_number=track["track_number"],
                duration_ms=track.get("duration_ms", 0),
                id=track_id
            ))
            
        self.is_album = True
        self.is_playlist = self.is_single_track = False
        
        metadata = {
            'title': album_data["album_info"]["name"],
            'artists': album_data["album_info"]["artists"],
            'releaseDate': album_data["album_info"]["release_date"],
            'cover': album_data["album_info"]["images"],
            'total_tracks': album_data["album_info"]["total_tracks"]
        }
        self.update_display_after_fetch(metadata)

    def handle_playlist_metadata(self, playlist_data):
        self.album_or_playlist_name = playlist_data["playlist_info"]["owner"]["name"]
        self.tracks = []
        
        for track in playlist_data["track_list"]:
            track_id = track["external_urls"].split("/")[-1]
            
            self.tracks.append(Track(
                external_urls=track["external_urls"],
                title=track["name"],
                artists=track["artists"],
                album=track["album_name"],
                track_number=len(self.tracks) + 1,
                duration_ms=track.get("duration_ms", 0),
                id=track_id
            ))
            
        self.is_playlist = True
        self.is_album = self.is_single_track = False
        
        metadata = {
            'title': playlist_data["playlist_info"]["owner"]["name"],
            'artists': playlist_data["playlist_info"]["owner"]["display_name"],
            'cover': playlist_data["playlist_info"]["owner"]["images"],
            'followers': playlist_data["playlist_info"]["followers"]["total"],
            'total_tracks': playlist_data["playlist_info"]["tracks"]["total"]
        }
        self.update_display_after_fetch(metadata)

    def update_display_after_fetch(self, metadata):
        self.track_list.setVisible(not self.is_single_track)
        
        if not self.is_single_track:
            self.track_list.clear()
            for i, track in enumerate(self.tracks, 1):
                duration = self.format_duration(track.duration_ms)
                self.track_list.addItem(f"{i}. {track.title} - {track.artists} • {duration}")
        
        self.update_info_widget(metadata)

    def update_info_widget(self, metadata):
        self.title_label.setText(metadata['title'])
        
        if self.is_single_track or self.is_album:
            artists = metadata['artists'] if isinstance(metadata['artists'], list) else metadata['artists'].split(", ")
            label_text = "Artists" if len(artists) > 1 else "Artist"
            artists_text = ", ".join(artists)
            self.artists_label.setText(f"<b>{label_text}</b> {artists_text}")
        else:
            self.artists_label.setText(f"<b>Owner</b> {metadata['artists']}")
        
        if self.is_playlist and 'followers' in metadata:
            self.followers_label.setText(f"<b>Followers</b> {metadata['followers']:,}")
            self.followers_label.show()
        else:
            self.followers_label.hide()
        
        if metadata.get('releaseDate'):
            try:
                release_date = metadata['releaseDate']
                if len(release_date) == 4:
                    date_obj = datetime.strptime(release_date, "%Y")
                elif len(release_date) == 7:
                    date_obj = datetime.strptime(release_date, "%Y-%m")
                else:
                    date_obj = datetime.strptime(release_date, "%Y-%m-%d")
                
                formatted_date = date_obj.strftime("%d-%m-%Y")
                self.release_date_label.setText(f"<b>Released</b> {formatted_date}")
                self.release_date_label.show()
            except ValueError:
                self.release_date_label.setText(f"<b>Released</b> {metadata['releaseDate']}")
                self.release_date_label.show()
        else:
            self.release_date_label.hide()
        
        if self.is_single_track:
            duration = self.format_duration(metadata.get('duration_ms', 0))
            self.type_label.setText(f"<b>Duration</b> {duration}")
        elif self.is_album:
            total_tracks = metadata.get('total_tracks', 0)
            self.type_label.setText(f"<b>Album</b> • {total_tracks} tracks")
        elif self.is_playlist:
            total_tracks = metadata.get('total_tracks', 0)
            self.type_label.setText(f"<b>Playlist</b> • {total_tracks} tracks")
        
        self.network_manager.get(QNetworkRequest(QUrl(metadata['cover'])))
        
        self.info_widget.show()

    def reset_info_widget(self):
        self.title_label.clear()
        self.artists_label.clear()
        self.followers_label.clear()
        self.release_date_label.clear()
        self.type_label.clear()
        self.cover_label.clear()
        self.info_widget.hide()

    def on_cover_loaded(self, reply):
        if reply.error() == QNetworkReply.NetworkError.NoError:
            data = reply.readAll()
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            self.cover_label.setPixmap(pixmap)

    def update_button_states(self):
        if self.is_single_track:
            self.download_selected_btn.hide()
            self.remove_btn.hide()
            self.download_all_btn.setText('Download')
            self.clear_btn.setText('Clear')
        else:
            self.download_selected_btn.show()
            self.remove_btn.show()
            self.download_all_btn.setText('Download All')
            self.clear_btn.setText('Clear')
        
        self.download_all_btn.show()
        self.clear_btn.show()
        
        self.download_selected_btn.setEnabled(True)
        self.download_all_btn.setEnabled(True)

    def hide_track_buttons(self):
        buttons = [
            self.download_selected_btn,
            self.download_all_btn,
            self.remove_btn,
            self.clear_btn
        ]
        for btn in buttons:
            btn.hide()

    def download_selected(self):
        if self.is_single_track:
            self.download_all()
        else:
            selected_items = self.track_list.selectedItems()
            if not selected_items:
                self.log_output.append('Warning: Please select tracks to download.')
                return
            self.download_tracks([self.track_list.row(item) for item in selected_items])

    def download_all(self):
        if self.is_single_track:
            self.download_tracks([0])
        else:
            self.download_tracks(range(self.track_list.count()))

    def download_tracks(self, indices):
        self.log_output.clear()
        outpath = self.output_dir.text()
        if not os.path.exists(outpath):
            self.log_output.append('Warning: Invalid output directory.')
            return

        tracks_to_download = self.tracks if self.is_single_track else [self.tracks[i] for i in indices]

        if self.is_album or self.is_playlist:
            folder_name = re.sub(r'[<>:"/\\|?*]', '_', self.album_or_playlist_name)
            outpath = os.path.join(outpath, folder_name)
            os.makedirs(outpath, exist_ok=True)

        try:
            self.start_download_worker(tracks_to_download, outpath)
        except Exception as e:
            self.log_output.append(f"Error: An error occurred while starting the download: {str(e)}")

    def start_download_worker(self, tracks_to_download, outpath):
        service = self.service_dropdown.currentData()
        qobuz_region_val = self.qobuz_region_dropdown.currentData() if service == "qobuz" else self.qobuz_region
    
        self.worker = DownloadWorker(
            tracks_to_download, 
            outpath,
            self.is_single_track, 
            self.is_album, 
            self.is_playlist, 
            self.album_or_playlist_name,
            self.filename_format,
            self.use_track_numbers,
            self.use_album_subfolders,
            self.use_fallback,
            service,
            self.timeout_value,
            qobuz_region=qobuz_region_val
        )
        self.worker.finished.connect(self.on_download_finished)
        self.worker.progress.connect(self.update_progress)
        self.worker.start()
        self.start_timer()
        self.update_ui_for_download_start()

    def update_ui_for_download_start(self):
        self.download_selected_btn.setEnabled(False)
        self.download_all_btn.setEnabled(False)
        self.stop_btn.show()
        self.pause_resume_btn.show()
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        
        self.tab_widget.setCurrentWidget(self.process_tab)

    def update_progress(self, message, percentage):
        if "Download progress:" in message or "Processing metadata..." in message:
            current_text = self.log_output.toPlainText()
            if current_text:
                lines = current_text.split('\n')
                if lines and ("Download progress:" in lines[-1] or "Processing metadata..." in lines[-1]):
                    lines[-1] = message
                    new_text = '\n'.join(lines)
                    self.log_output.setPlainText(new_text)
                    self.log_output.moveCursor(QTextCursor.MoveOperation.End)
                else:
                    self.log_output.append(message)
            else:
                self.log_output.append(message)
        else:
            self.log_output.append(message)
        
        self.progress_bar.setValue(percentage)

    def stop_download(self):
        if hasattr(self, 'worker'):
            self.worker.stop()
        self.stop_timer()
        self.on_download_finished(True, "Download stopped by user.", [])
        
    def on_download_finished(self, success, message, failed_tracks):
        self.progress_bar.hide()
        self.stop_btn.hide()
        self.pause_resume_btn.hide()
        self.pause_resume_btn.setText('Pause')
        self.stop_timer()
        
        self.download_selected_btn.setEnabled(True)
        self.download_all_btn.setEnabled(True)
        
        if success:
            self.log_output.append(f"\nStatus: {message}")
            if failed_tracks:
                self.log_output.append("\nFailed downloads:")
                for title, artists, error in failed_tracks:
                    self.log_output.append(f"• {title} - {artists}")
                    self.log_output.append(f"  Error: {error}\n")
        else:
            self.log_output.append(f"Error: {message}")

        self.tab_widget.setCurrentWidget(self.process_tab)
    
    def toggle_pause_resume(self):
        if hasattr(self, 'worker'):
            if self.worker.is_paused:
                self.worker.resume()
                self.pause_resume_btn.setText('Pause')
                self.timer.start(1000)
            else:
                self.worker.pause()
                self.pause_resume_btn.setText('Resume')

    def remove_selected_tracks(self):
        if not self.is_single_track:
            selected_indices = sorted([self.track_list.row(item) for item in self.track_list.selectedItems()], reverse=True)
            
            for index in selected_indices:
                self.track_list.takeItem(index)
                self.tracks.pop(index)
            
            for i, track in enumerate(self.tracks, 1):
                if self.is_playlist:
                    track.track_number = i
                
                duration = self.format_duration(track.duration_ms)
                display_text = f"{i}. {track.title} - {track.artists} • {duration}"
                list_item = self.track_list.item(i - 1)
                if list_item:
                    list_item.setText(display_text)

    def clear_tracks(self):
        self.reset_state()
        self.reset_ui()
        self.tab_widget.setCurrentIndex(0)

    def start_timer(self):
        self.elapsed_time = QTime(0, 0, 0)
        self.time_label.setText("00:00:00")
        self.time_label.show()
        self.timer.start(1000)
    
    def stop_timer(self):
        self.timer.stop()
        self.time_label.hide()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = SpotiFLACGUI()
    ex.show()
    sys.exit(app.exec())