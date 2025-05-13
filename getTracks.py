import requests
import time
import os
import asyncio
import re
import base64
from datetime import datetime
from mutagen.flac import FLAC, Picture
from mutagen.id3 import PictureType

class ProgressCallback:
    def __call__(self, current, total):
        if total > 0:
            percent = (current / total) * 100
            print(f"\r{percent:.2f}% ({current}/{total})", end="")
        else:
            print(f"\r{current / (1024 * 1024):.2f} MB", end="")

class LucidaDownloader:
    def __init__(self, domain="to", timeout=30):
        self.client = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.progress_callback = ProgressCallback()
        self.timeout = timeout
        
        if domain not in ["to", "su"]:
            raise ValueError("Domain must be either 'to' or 'su'")
        
        self.base_domain = f"lucida.{domain}"

    def set_progress_callback(self, callback):
        self.progress_callback = callback

    def generate_filename(self, track_id, service):
        return f"{track_id}_{service}.flac"

    async def get_track_info(self, track_id, service="tidal"):
        if service not in ["tidal", "amazon", "deezer"]:
            raise ValueError("Service must be one of 'tidal', 'amazon', or 'deezer'")
            
        spotify_url = f"https://open.spotify.com/track/{track_id}"
        
        result = self._convert_spotify_link(spotify_url, service)
        
        if "error" in result:
            raise Exception(f"Error: {result['error']}")
        
        result["track_id"] = track_id
        
        return result

    def _convert_spotify_link(self, spotify_url, target_service="tidal"):
        track_id_match = re.search(r'track/([a-zA-Z0-9]+)', spotify_url)
        if not track_id_match:
            return {"error": "Invalid Spotify URL"}
        
        base_url = f"https://{self.base_domain}"
        
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "id-ID,id;q=0.9",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Host": self.base_domain,
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        try:
            headers["Referer"] = f"{base_url}/?url={spotify_url}&country=auto"
            
            request_params = {
                "url": spotify_url,
                "country": "auto",
                "to": target_service
            }
            
            session = requests.Session()
            session.verify = True
            
            response = session.get(
                base_url,
                params=request_params,
                headers=headers,
                timeout=self.timeout
            )
            
            html_content = response.text
            
            token_match = re.search(r'token:"([^"]+)"', html_content)
            token_expiry_match = re.search(r'tokenExpiry:(\d+)', html_content)
            
            token = token_match.group(1) if token_match else None
            token_expiry = int(token_expiry_match.group(1)) if token_expiry_match else None
            
            url = None
            url_patterns = [
                r'"url":"([^"]+)"',
                r'href="(https?://[^"]*' + re.escape(target_service) + r'[^"]*track[^"]*)"',
            ]
            
            for pattern in url_patterns:
                url_match = re.search(pattern, html_content)
                if url_match:
                    url = url_match.group(1).replace('\\/', '/')
                    break
            
            if not url:
                redirect_patterns = [
                    r'url=([^&"]+)',
                    r'href="([^"]+)"',
                    r'window\.location\.href\s*=\s*[\'"]([^\'"]+)[\'"]',
                ]
                
                for pattern in redirect_patterns:
                    matches = re.finditer(pattern, html_content)
                    for match in matches:
                        potential_url = match.group(1)
                        if potential_url.startswith('http') and target_service.lower() in potential_url.lower():
                            url = potential_url.replace('\\/', '/')
                            break
                
                if not url:
                    service_urls = re.finditer(r'(https?://[^"\s]+' + re.escape(target_service) + r'[^"\s]+)', html_content)
                    for match in service_urls:
                        url = match.group(1).replace('\\/', '/')
                        break
            
            result = {
                "service": target_service,
                "url": url,
                "token": {
                    "primary": None,
                    "expiry": None
                }
            }
            
            if token:
                try:
                    decoded_once = base64.b64decode(token).decode('latin1')
                    decoded_token = base64.b64decode(decoded_once).decode('latin1')
                    result["token"]["primary"] = decoded_token
                except Exception:
                    result["token"]["primary"] = token
            
            result["token"]["expiry"] = token_expiry
            
            return result
                
        except Exception as error:
            return {"error": str(error)}

    def download(self, metadata, output_dir=".", is_paused_callback=None, is_stopped_callback=None):
        track_url = metadata['url']
        primary_token = metadata['token']['primary']
        expiry = metadata['token']['expiry']
        track_id = metadata['track_id']
        service = metadata['service']
        
        print(f"Starting download: track ID {track_id}")
        
        if is_stopped_callback and is_stopped_callback():
            raise Exception("Download stopped")
        
        file_name = self.generate_filename(track_id, service)
        file_path = os.path.join(output_dir, file_name)
        
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            if file_size > 0:
                print(f"File already exists: {file_path} ({file_size / (1024 * 1024):.2f} MB)")
                return file_path
        
        initial_request = {
            "account": {"id": "auto", "type": "country"},
            "compat": "false",
            "downscale": "original",
            "handoff": True,
            "metadata": True,
            "private": True,
            "token": {
                "expiry": expiry,
                "primary": primary_token
            },
            "upload": {"enabled": False, "service": "pixeldrain"},
            "url": track_url
        }

        response = self.client.post(f"https://{self.base_domain}/api/load?url=/api/fetch/stream/v2", 
                                    json=initial_request, 
                                    headers=self.headers)
        
        csrf_token = response.cookies.get('csrf_token')
        if csrf_token:
            self.headers['X-CSRF-Token'] = csrf_token

        initial_response = response.json()

        if not initial_response.get("success", False):
            raise Exception(f"Request failed: {initial_response.get('error', 'Unknown error')}")

        handoff = initial_response["handoff"]
        server = initial_response["server"]

        file_name = self.generate_filename(track_id, service)

        completion_url = f"https://{server}.{self.base_domain}/api/fetch/request/{handoff}"

        print("Waiting for processing...")
        while True:
            if is_stopped_callback and is_stopped_callback():
                raise Exception("Download stopped")
                
            while is_paused_callback and is_paused_callback():
                time.sleep(0.1)
                if is_stopped_callback and is_stopped_callback():
                    raise Exception("Download stopped")
            
            completion_response = self.client.get(completion_url, headers=self.headers).json()
            
            status = completion_response["status"]
            if status == "completed":
                print("Processing: 100%")
                break
            elif status == "error":
                raise Exception(f"API error: {completion_response.get('message', 'Unknown error')}")
            else:
                progress = completion_response.get("progress", {})
                if progress:
                    current = progress.get("current", 0)
                    total = progress.get("total", 100)
                    percent = int((current / total) * 100) if total > 0 else 0
                    action = progress.get("action", "Processing")
                    print(f"{percent}% - {action}")
                    
                    if action.lower() == "metadata":
                        if self.progress_callback:
                            self.progress_callback(0, 0)
                else:
                    print(f"Status: {status}")
                    if status.lower() == "metadata":
                        if self.progress_callback:
                            self.progress_callback(0, 0)
            
            time.sleep(1)

        download_url = f"https://{server}.{self.base_domain}/api/fetch/request/{handoff}/download"
        print(f"Downloading file...")
        
        response = self.client.get(download_url, stream=True, headers=self.headers)
        total_size = int(response.headers.get('content-length', 0))
        downloaded_size = 0

        file_path = os.path.join(output_dir, file_name)

        try:
            with open(file_path, 'wb') as file:
                start_time = time.time()
                last_update_time = start_time
                
                for chunk in response.iter_content(chunk_size=8192):
                    if is_stopped_callback and is_stopped_callback():
                        file.close()
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        raise Exception("Download stopped")
                        
                    while is_paused_callback and is_paused_callback():
                        time.sleep(0.1)
                        if is_stopped_callback and is_stopped_callback():
                            file.close()
                            if os.path.exists(file_path):
                                os.remove(file_path)
                            raise Exception("Download stopped")
                    
                    if chunk:
                        file.write(chunk)
                        downloaded_size += len(chunk)
                        
                        current_time = time.time()
                        if current_time - last_update_time >= 1:
                            if total_size > 0:
                                progress_percent = (downloaded_size / total_size) * 100
                                elapsed_time = current_time - start_time
                                speed = downloaded_size / (1024 * 1024 * elapsed_time) if elapsed_time > 0 else 0
                                print(f"{progress_percent:.2f}% - {speed:.2f} MB/s")
                            else:
                                print(f"{downloaded_size / (1024 * 1024):.2f} MB")
                            
                            last_update_time = current_time
                            
                        if self.progress_callback:
                            self.progress_callback(downloaded_size, total_size)
                
                if downloaded_size == 0:
                    raise Exception("No data received")
                
            print(f"Complete. File saved: {file_path}")
            return file_path
            
        except Exception as e:
            if os.path.exists(file_path) and os.path.getsize(file_path) == 0:
                try:
                    os.remove(file_path)
                except:
                    pass
            raise e

class SquidWTFDownloader:
    def __init__(self, region="us", timeout=30):
        if region not in ["eu", "us"]:
            raise ValueError("Region must be either 'us' or 'eu'")
            
        self.region = region
        self.timeout = timeout
        self.session = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.base_api_url = f"https://{region}.qobuz.squid.wtf/api"
        self.download_chunk_size = 256 * 1024
        self.progress_callback = ProgressCallback()

    def set_progress_callback(self, callback):
        self.progress_callback = callback

    def sanitize_filename(self, filename):
        if not filename: 
            return "Unknown Track"
        sanitized = re.sub(r'[\\/*?:"<>|]', "", str(filename))
        return re.sub(r'\s+', ' ', sanitized).strip() or "Unnamed Track"

    def get_track_info(self, isrc):
        print(f"Fetching: {isrc}")
        search_url = f"{self.base_api_url}/get-music"
        params = {'q': isrc, 'offset': 0, 'limit': 10}
        
        try:
            response = self.session.get(search_url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()

            selected_track = None
            if data and data.get("success"):
                items = data.get("data", {}).get("tracks", {}).get("items", [])
                priority = {24: 1, 16: 2}
                for track in items:
                    if track.get("isrc") == isrc:
                        current_prio = priority.get(track.get("maximum_bit_depth"), 3)
                        if selected_track is None or current_prio < priority.get(selected_track.get("maximum_bit_depth"), 3):
                            selected_track = track
                            if current_prio == 1: 
                                break
                                
            if not selected_track:
                raise Exception(f"Track not found: {isrc}")
                
            title = selected_track.get('title', 'Unknown')
            bit_depth = selected_track.get('maximum_bit_depth', 'Unknown')
            print(f"Found: {title} ({bit_depth}b)")
            return selected_track
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"Request error: {e}")
        except Exception as e:
            raise Exception(f"Error: {e}")

    def get_download_url(self, track_id):
        print("Fetching URL...")
        download_api_url = f"{self.base_api_url}/download-music"
        params = {'track_id': track_id, 'quality': 27}
        
        try:
            response = self.session.get(download_api_url, params=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            
            if data and data.get("success") and data.get("data", {}).get("url"):
                download_url = data["data"]["url"]
                print("URL found")
                return download_url
            else:
                error_msg = data.get('error', {}).get('message', 'Unknown API error')
                raise Exception(f"API error: {error_msg}")
                
        except requests.exceptions.RequestException as e:
            raise Exception(f"Request error: {e}")
        except Exception as e:
            raise Exception(f"Error: {e}")

    def download(self, isrc, output_dir=".", is_paused_callback=None, is_stopped_callback=None):
        if output_dir != ".":
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as e:
                raise Exception(f"Directory error: {e}")
                
        track_info = self.get_track_info(isrc)
        track_id = track_info.get("id")
        
        if not track_id:
            raise Exception("No track ID found")
            
        artist_name = self.sanitize_filename(track_info.get('performer', {}).get('name'))
        track_title = self.sanitize_filename(track_info.get('title'))
        output_filename = os.path.join(output_dir, f"{artist_name} - {track_title}.flac")
        
        if os.path.exists(output_filename):
            file_size = os.path.getsize(output_filename)
            if file_size > 0:
                print(f"File already exists: {output_filename} ({file_size / (1024 * 1024):.2f} MB)")
                return output_filename
                
        download_url = self.get_download_url(track_id)
        temp_filename = output_filename + ".part"
        
        print(f"Downloading...")
        try:
            with self.session.get(download_url, stream=True, timeout=900) as response, \
                 open(temp_filename, 'wb') as f:
                response.raise_for_status()
                total_size = int(response.headers.get('content-length', 0))
                downloaded_size = 0
                start_time = time.time()
                last_update_time = start_time
                
                for chunk in response.iter_content(chunk_size=self.download_chunk_size):
                    if is_stopped_callback and is_stopped_callback():
                        f.close()
                        if os.path.exists(temp_filename):
                            os.remove(temp_filename)
                        raise Exception("Download stopped")
                        
                    while is_paused_callback and is_paused_callback():
                        time.sleep(0.1)
                        if is_stopped_callback and is_stopped_callback():
                            f.close()
                            if os.path.exists(temp_filename):
                                os.remove(temp_filename)
                            raise Exception("Download stopped")
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    
                    current_time = time.time()
                    if current_time - last_update_time >= 1:
                        if total_size > 0:
                            progress_percent = (downloaded_size / total_size) * 100
                            elapsed_time = current_time - start_time
                            speed = downloaded_size / (1024 * 1024 * elapsed_time) if elapsed_time > 0 else 0
                            print(f"{progress_percent:.2f}% - {speed:.2f} MB/s")
                        else:
                            print(f"{downloaded_size / (1024 * 1024):.2f} MB")
                        
                        last_update_time = current_time
                        
                    if self.progress_callback:
                        self.progress_callback(downloaded_size, total_size)
                        
            os.rename(temp_filename, output_filename)
            print("Download complete")
            
        except requests.exceptions.RequestException as e:
            if os.path.exists(temp_filename): 
                os.remove(temp_filename)
            raise Exception(f"Download failed: {e}")
        except Exception as e:
            if os.path.exists(temp_filename): 
                os.remove(temp_filename)
            raise Exception(f"File error: {e}")
            
        print("Adding metadata...")
        try:
            self._embed_metadata(output_filename, track_info)
            print("Metadata saved")
        except Exception as e:
            print(f"Tagging failed: {e}")
        
        print(f"Done")
        return output_filename

    def _embed_metadata(self, filename, track_info):
        try:
            audio = FLAC(filename)
            audio.delete()
            audio.clear_pictures()

            album_info = track_info.get('album', {})
            artist = track_info.get('performer', {}).get('name')

            if track_info.get('title'): 
                audio['TITLE'] = track_info['title']
            if artist: 
                audio['ARTIST'] = artist
            if album_info.get('title'): 
                audio['ALBUM'] = album_info['title']
            if album_info.get('artist', {}).get('name', artist): 
                audio['ALBUMARTIST'] = album_info.get('artist', {}).get('name', artist)
            if track_info.get('track_number'): 
                audio['TRACKNUMBER'] = str(track_info['track_number'])
            if track_info.get('release_date_original'):
                audio['DATE'] = track_info['release_date_original']
                try: 
                    audio['YEAR'] = str(datetime.strptime(track_info['release_date_original'], '%Y-%m-%d').year)
                except ValueError: 
                    pass
            if album_info.get('genre', {}).get('name'): 
                audio['GENRE'] = album_info['genre']['name']
            if track_info.get('copyright'): 
                audio['COPYRIGHT'] = track_info['copyright']
            if track_info.get('isrc'): 
                audio['ISRC'] = track_info['isrc']
            if album_info.get('label', {}).get('name'): 
                audio['ORGANIZATION'] = album_info['label']['name']

            img_info = album_info.get('image', {})
            cover_url = img_info.get('large') or img_info.get('small') or img_info.get('thumbnail')
            if cover_url:
                try:
                    img_response = self.session.get(cover_url, timeout=30)
                    img_response.raise_for_status()
                    mime_type = img_response.headers.get('Content-Type', 'image/jpeg').lower()
                    if mime_type in ['image/jpeg', 'image/png']:
                        picture = Picture()
                        picture.data = img_response.content
                        picture.type = PictureType.COVER_FRONT
                        picture.mime = mime_type
                        audio.add_picture(picture)
                        print("Cover added")
                except Exception as e:
                    print(f"Cover error: {str(e)}")

            audio.save()

        except Exception as e:
            raise Exception(f"Metadata error: {e}")

async def main():
    print("=== LucidaDownloader ===")
    lucida = LucidaDownloader(domain="to")
    
    track_id = "2plbrEY59IikOBgBGLjaoe"
    service = "tidal"
    output_dir = "."
    
    try:
        print(f"Getting track: {track_id} from {service}")
        metadata = await lucida.get_track_info(track_id, service)
        print("Starting download")
        
        downloaded_file = lucida.download(metadata, output_dir)
        print(f"Success: File saved as {downloaded_file}")
    except Exception as e:
        print(f"Error: {str(e)}")
    
    print("\n\n=== SquidWTFDownloader ===")
    squid = SquidWTFDownloader(region="us")
    
    isrc = "TCAIT2495017"
    output_dir = "."
    
    try:
        downloaded_file = squid.download(isrc, output_dir)
        print(f"Success: File saved as {downloaded_file}")
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    try:
        import sys
        if sys.platform == "win32":
            import os
            os.system("chcp 65001 > nul")
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except:
                pass
    except:
        pass
        
    asyncio.run(main())