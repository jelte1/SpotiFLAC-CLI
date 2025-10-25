import sys
import os
import re
import threading
import time
import argparse
import asyncio

from dataclasses import dataclass
from getMetadata import get_filtered_data, parse_uri, SpotifyInvalidUrlException
from tidalDL import TidalDownloader
from deezerDL import DeezerDownloader

@dataclass
class Config:
    url: str
    output_dir: str
    service: str = "tidal"
    filename_format: str = "title_artist"
    use_track_numbers: bool = False
    use_artist_subfolders: bool = False
    use_album_subfolders: bool = False
    is_album: bool = False
    is_playlist: bool = False
    is_single_track: bool = False
    album_or_playlist_name: str = ""
    tracks = []
    worker = None
    loop: int = 3600

@dataclass
class Track:
    external_urls: str
    title: str
    artists: str
    album: str
    track_number: int
    duration_ms: int
    id: str
    isrc: str = ""

def get_metadata(url):
    try:
        metadata = get_filtered_data(url)
        if "error" in metadata:
            print("Error fetching metadata:", metadata["error"])
        else:
            print("Metadata fetched successfully.")
            return metadata
    except SpotifyInvalidUrlException as e:
        print("Invalid URL:", str(e))
    except Exception as e:
        print("An error occurred while fetching metadata:", str(e))


def fetch_tracks(url):
    if not url:
        print('Warning: Please enter a Spotify URL.')
        return

    try:
        print('Just a moment. Fetching metadata...')

        metadata = get_metadata(url)
        on_metadata_fetched(metadata)

    except Exception as e:
        print(f'Error: Failed to start metadata fetch: {str(e)}')


def on_metadata_fetched(metadata):
    try:
        url_info = parse_uri(config.url)

        if url_info["type"] == "track":
            handle_track_metadata(metadata["track"])
        elif url_info["type"] == "album":
            handle_album_metadata(metadata)
        elif url_info["type"] == "playlist":
            handle_playlist_metadata(metadata)

    except Exception as e:
        print(f'Error: {str(e)}')


def handle_track_metadata(track_data):
    track_id = track_data["external_urls"].split("/")[-1]

    track = Track(
        external_urls=track_data["external_urls"],
        title=track_data["name"],
        artists=track_data["artists"],
        album=track_data["album_name"],
        track_number=1,
        duration_ms=track_data.get("duration_ms", 0),
        id=track_id,
        isrc=track_data.get("isrc", "")
    )

    config.tracks = [track]
    config.is_single_track = True
    config.is_album = config.is_playlist = False
    config.album_or_playlist_name = f"{config.tracks[0].title} - {config.tracks[0].artists}"


def handle_album_metadata(album_data):
    config.album_or_playlist_name = album_data["album_info"]["name"]

    for track in album_data["track_list"]:
        track_id = track["external_urls"].split("/")[-1]

        config.tracks.append(Track(
            external_urls=track["external_urls"],
            title=track["name"],
            artists=track["artists"],
            album=config.album_or_playlist_name,
            track_number=track["track_number"],
            duration_ms=track.get("duration_ms", 0),
            id=track_id,
            isrc=track.get("isrc", "")
        ))

    config.is_album = True
    config.is_playlist = config.is_single_track = False


def handle_playlist_metadata(playlist_data):
    config.album_or_playlist_name = playlist_data["playlist_info"]["owner"]["name"]

    for track in playlist_data["track_list"]:
        track_id = track["external_urls"].split("/")[-1]

        config.tracks.append(Track(
            external_urls=track["external_urls"],
            title=track["name"],
            artists=track["artists"],
            album=track["album_name"],
            track_number=track.get("track_number", len(config.tracks) + 1),
            duration_ms=track.get("duration_ms", 0),
            id=track_id,
            isrc=track.get("isrc", "")
        ))

    config.is_playlist = True
    config.is_album = config.is_single_track = False


def download_tracks(indices):
    raw_outpath = config.output_dir
    outpath = os.path.normpath(raw_outpath)
    if not os.path.exists(outpath):
        print('Warning: Invalid output directory. Please check if the folder exists.')
        return

    tracks_to_download = config.tracks if config.is_single_track else [config.tracks[i] for i in indices]

    if config.is_album or config.is_playlist:
        name = config.album_or_playlist_name.strip()
        folder_name = re.sub(r'[<>:"/\\|?*]', '_', name)
        outpath = os.path.join(outpath, folder_name)
        os.makedirs(outpath, exist_ok=True)

    try:
        start_download_worker(tracks_to_download, outpath)
    except Exception as e:
        print(f"Error: An error occurred while starting the download: {str(e)}")


def start_download_worker(tracks_to_download, outpath):
    config.worker = DownloadWorker(
        tracks_to_download,
        outpath,
        config.is_single_track,
        config.is_album,
        config.is_playlist,
        config.album_or_playlist_name,
        config.filename_format,
        config.use_track_numbers,
        config.use_artist_subfolders,
        config.use_album_subfolders,
        config.service,
    )
    config.worker.run()


def on_download_finished(success, message, failed_tracks):
    if success:
        print(f"\nStatus: {message}")
        if failed_tracks:
            print("\nFailed downloads:")
            for title, artists, error in failed_tracks:
                print(f"• {title} - {artists}")
                print(f"  Error: {error}\n")
    else:
        print(f"Error: {message}")


def update_progress(message):
    print(message)


def format_minutes(minutes):
    if minutes < 60:
        return f"{minutes} minutes"
    elif minutes < 1440:  # less than a day
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours} hours {mins} minutes"
    else:
        days = minutes // 1440
        hours = (minutes % 1440) // 60
        mins = minutes % 60
        return f"{days} days {hours} hours {mins} minutes"


class DownloadWorker:
    def __init__(self, tracks, outpath, is_single_track=False, is_album=False, is_playlist=False,
                 album_or_playlist_name='', filename_format='title_artist', use_track_numbers=True,
                 use_artist_subfolders=False, use_album_subfolders=False, service="tidal"):
        super().__init__()
        self.tracks = tracks
        self.outpath = outpath
        self.is_single_track = is_single_track
        self.is_album = is_album
        self.is_playlist = is_playlist
        self.album_or_playlist_name = album_or_playlist_name
        self.filename_format = filename_format
        self.use_track_numbers = use_track_numbers
        self.use_artist_subfolders = use_artist_subfolders
        self.use_album_subfolders = use_album_subfolders
        self.service = service
        self.failed_tracks = []

    def get_formatted_filename(self, track):
        if self.filename_format == "artist_title":
            filename = f"{track.artists} - {track.title}.flac"
        elif self.filename_format == "title_only":
            filename = f"{track.title}.flac"
        else:
            filename = f"{track.title} - {track.artists}.flac"
        return re.sub(r'[<>:"/\\|?*]', lambda m: "'" if m.group() == '"' else '_', filename)

    def run(self):
        try:
            if self.service == "tidal":
                downloader = TidalDownloader()
            elif self.service == "deezer":
                downloader = DeezerDownloader()
            else:
                downloader = TidalDownloader()

            def progress_update(current, total):
                if total <= 0:
                    update_progress("Processing metadata...")

            downloader.set_progress_callback(progress_update)

            total_tracks = len(self.tracks)

            for i, track in enumerate(self.tracks):
                update_progress(f"[{i + 1}/{total_tracks}] Starting download: {track.title} - {track.artists}")

                try:
                    if self.is_playlist:
                        track_outpath = self.outpath

                        if self.use_artist_subfolders:
                            artist_name = track.artists.split(', ')[0] if ', ' in track.artists else track.artists
                            artist_folder = re.sub(r'[<>:"/\\|?*]', lambda m: "'" if m.group() == '"' else '_',
                                                   artist_name)
                            track_outpath = os.path.join(track_outpath, artist_folder)

                        if self.use_album_subfolders:
                            album_folder = re.sub(r'[<>:"/\\|?*]', lambda m: "'" if m.group() == '"' else '_',
                                                  track.album)
                            track_outpath = os.path.join(track_outpath, album_folder)

                        os.makedirs(track_outpath, exist_ok=True)
                    else:
                        track_outpath = self.outpath

                    if (self.is_album or self.is_playlist) and self.use_track_numbers:
                        new_filename = f"{track.track_number:02d} - {self.get_formatted_filename(track)}"
                    else:
                        new_filename = self.get_formatted_filename(track)

                    new_filename = re.sub(r'[<>:"/\\|?*]', lambda m: "'" if m.group() == '"' else '_', new_filename)
                    new_filepath = os.path.join(track_outpath, new_filename)

                    if os.path.exists(new_filepath) and os.path.getsize(new_filepath) > 0:
                        update_progress(f"File already exists: {new_filename}. Skipping download.")
                        continue
                    elif self.service == "tidal":
                        if not track.isrc:
                            update_progress(f"[X] No ISRC found for track: {track.title}. Skipping.")
                            self.failed_tracks.append((track.title, track.artists, "No ISRC available"))
                            continue

                        update_progress(f"Searching and downloading from Tidal for ISRC: {track.isrc} - {track.title} - {track.artists}")

                        download_result_details = downloader.download(
                            query=f"{track.title} {track.artists}",
                            isrc=track.isrc,
                            output_dir=track_outpath,
                            quality="LOSSLESS"
                        )

                        if isinstance(download_result_details, str) and os.path.exists(download_result_details):
                            downloaded_file = download_result_details
                        elif isinstance(download_result_details, dict) and download_result_details.get(
                                "success") == False and download_result_details.get(
                            "error") == "Download stopped by user":
                            update_progress(f"Download stopped by user for: {track.title}")
                            return
                        elif isinstance(download_result_details, dict) and download_result_details.get(
                                "success") == False:
                            raise Exception(download_result_details.get("error", "Tidal download failed"))
                        elif isinstance(download_result_details, dict) and (
                                download_result_details.get(
                                    "status") == "all_skipped" or download_result_details.get(
                            "status") == "skipped_exists"):
                            update_progress(f"File already exists or skipped: {new_filename}")
                            downloaded_file = new_filepath
                        else:
                            raise Exception(
                                f"Tidal download failed or returned unexpected result: {download_result_details}")
                    elif self.service == "deezer":
                        if not track.isrc:
                            update_progress(f"[X] No ISRC found for track: {track.title}. Skipping.")
                            self.failed_tracks.append((track.title, track.artists, "No ISRC available"))
                            continue

                        update_progress(f"Downloading from Deezer with ISRC: {track.isrc}")

                        success = asyncio.run(downloader.download_by_isrc(track.isrc, track_outpath))

                        if success:
                            safe_title = "".join(
                                c for c in track.title if c.isalnum() or c in (' ', '-', '_')).rstrip()
                            safe_artist = "".join(
                                c for c in track.artists if c.isalnum() or c in (' ', '-', '_')).rstrip()
                            expected_filename = f"{safe_artist} - {safe_title}.flac"
                            downloaded_file = os.path.join(track_outpath, expected_filename)

                            if not os.path.exists(downloaded_file):
                                import glob
                                flac_files = glob.glob(os.path.join(track_outpath, "*.flac"))
                                if flac_files:
                                    downloaded_file = max(flac_files, key=os.path.getctime)
                                else:
                                    raise Exception("[X] Downloaded file not found")
                        else:
                            raise Exception("[X] Deezer download failed")
                    else:
                        track_id = track.id
                        update_progress(f"Getting track info for ID: {track_id} from {self.service}")

                        try:
                            loop = asyncio.get_event_loop()
                            if loop.is_closed():
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                        except RuntimeError:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)

                        metadata = loop.run_until_complete(downloader.get_track_info(track_id, self.service))
                        update_progress(f"Track info received, starting download process")


                        downloaded_file = downloader.download(
                            metadata,
                            track_outpath
                        )

                    if downloaded_file and os.path.exists(downloaded_file):
                        if downloaded_file == new_filepath:
                            update_progress(f"File already exists: {new_filename}")
                            continue

                        if downloaded_file != new_filepath:
                            try:
                                os.rename(downloaded_file, new_filepath)
                                update_progress(f"File renamed to: {new_filename}")
                            except OSError as e:
                                update_progress( f"[X] Warning: Could not rename file {downloaded_file} to {new_filepath}: {str(e)}")
                                pass
                    else:
                        raise Exception(f"[X] Download failed or file not found: {downloaded_file}")

                    update_progress(f"Successfully downloaded: {track.title} - {track.artists}")
                except Exception as e:
                    self.failed_tracks.append((track.title, track.artists, str(e)))
                    update_progress(f"[X] Failed to download: {track.title} - {track.artists}\nError: {str(e)}")
                    continue

            success_message = "Download completed!"
            if self.failed_tracks:
                success_message += f"\n\nFailed downloads: {len(self.failed_tracks)} tracks"
            on_download_finished(True, success_message, self.failed_tracks)

        except Exception as e:
            on_download_finished(False, str(e), self.failed_tracks)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="Spotify URL")
    parser.add_argument("output_dir", help="Output directory")
    parser.add_argument("--service", choices=["tidal","deezer"], default="tidal")
    parser.add_argument("--filename-format", choices=["title_artist","artist_title","title_only"], default="title_artist")
    parser.add_argument("--use-track-numbers", action="store_true")
    parser.add_argument("--use-artist-subfolders", action="store_true")
    parser.add_argument("--use-album-subfolders", action="store_true")
    parser.add_argument("--loop", type=int, help="Loop delay in minutes")
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    config = Config(**vars(args))

    try:
        fetch_tracks(config.url)
        if config.loop is None:
            download_tracks(range(len(config.tracks)))
        else:
            print(f"Looping download every {format_minutes(config.loop)}.")
            while True:
                download_tracks(range(len(config.tracks)))
                time.sleep(config.loop * 60)
    except KeyboardInterrupt:
        print("\nDownload stopped.")