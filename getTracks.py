import requests
import time
import os
import asyncio
import re
import base64

class TrackDownloader:
    def __init__(self, use_fallback=False, timeout=30):
        self.client = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.progress_callback = None
        self.use_fallback = use_fallback
        self.timeout = timeout
        self.base_domain = "lucida.su" if use_fallback else "lucida.to"

    def set_progress_callback(self, callback):
        self.progress_callback = callback

    def generate_filename(self, track_id, service):
        return f"{track_id}_{service}.flac"

    async def get_track_info(self, track_id, service="amazon", use_fallback=None):
        if use_fallback is None:
            use_fallback = self.use_fallback
            
        domain_type = "su" if use_fallback else "to"
        
        spotify_url = f"https://open.spotify.com/track/{track_id}"
        
        result = self.convert_spotify_link(spotify_url, service, domain_type)
        
        if "error" in result:
            raise Exception(f"Failed to get track info: {result['error']}")
        
        result["track_id"] = track_id
        
        return result

    def convert_spotify_link(self, spotify_url, target_service="amazon", domain_type="to"):
        track_id_match = re.search(r'track/([a-zA-Z0-9]+)', spotify_url)
        if not track_id_match:
            return {"error": "Invalid Spotify URL"}
        
        domain = "lucida.to" if domain_type == "to" else "lucida.su"
        base_url = f"https://{domain}"
        
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "id-ID,id;q=0.9",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Host": domain,
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

    def download(self, metadata, output_dir, is_paused_callback=None, is_stopped_callback=None):
        track_url = metadata['url']
        primary_token = metadata['token']['primary']
        expiry = metadata['token']['expiry']
        track_id = metadata['track_id']
        service = metadata['service']
        
        print(f"Starting download for: {track_url}")
        
        if is_stopped_callback and is_stopped_callback():
            raise Exception("Download stopped by user")
        
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
            raise Exception(f"Initial request failed: {initial_response.get('error', 'Unknown error')}")

        handoff = initial_response["handoff"]
        server = initial_response["server"]

        file_name = self.generate_filename(track_id, service)

        completion_url = f"https://{server}.{self.base_domain}/api/fetch/request/{handoff}"

        print("Waiting for track processing to complete")
        while True:
            if is_stopped_callback and is_stopped_callback():
                raise Exception("Download stopped by user")
                
            while is_paused_callback and is_paused_callback():
                time.sleep(0.1)
                if is_stopped_callback and is_stopped_callback():
                    raise Exception("Download stopped by user")
            
            completion_response = self.client.get(completion_url, headers=self.headers).json()
            
            status = completion_response["status"]
            if status == "completed":
                print("Processing completed: 100%")
                break
            elif status == "error":
                raise Exception(f"API request failed: {completion_response.get('message', 'Unknown error')}")
            else:
                progress = completion_response.get("progress", {})
                if progress:
                    current = progress.get("current", 0)
                    total = progress.get("total", 100)
                    percent = int((current / total) * 100) if total > 0 else 0
                    action = progress.get("action", "Processing")
                    print(f"Progress: {percent}% - {action} ({current}/{total})")
                    
                    if action.lower() == "metadata":
                        if self.progress_callback:
                            self.progress_callback(0, 0)
                else:
                    print(f"Status: {status} - Waiting for progress information...")
                    if status.lower() == "metadata":
                        if self.progress_callback:
                            self.progress_callback(0, 0)
            
            time.sleep(1)

        download_url = f"https://{server}.{self.base_domain}/api/fetch/request/{handoff}/download"
        print(f"Starting download of: {file_name}")
        
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
                        raise Exception("Download stopped by user")
                        
                    while is_paused_callback and is_paused_callback():
                        time.sleep(0.1)
                        if is_stopped_callback and is_stopped_callback():
                            file.close()
                            if os.path.exists(file_path):
                                os.remove(file_path)
                            raise Exception("Download stopped by user")
                    
                    if chunk:
                        file.write(chunk)
                        downloaded_size += len(chunk)
                        
                        current_time = time.time()
                        if current_time - last_update_time >= 1:
                            if total_size > 0:
                                progress_percent = (downloaded_size / total_size) * 100
                                elapsed_time = current_time - start_time
                                speed = downloaded_size / (1024 * 1024 * elapsed_time) if elapsed_time > 0 else 0
                                print(f"Download progress: {progress_percent:.2f}% ({downloaded_size}/{total_size}) - {speed:.2f} MB/s")
                            else:
                                print(f"Downloaded {downloaded_size / (1024 * 1024):.2f} MB")
                            
                            last_update_time = current_time
                            
                        if self.progress_callback:
                            self.progress_callback(downloaded_size, total_size)
                
                if downloaded_size == 0:
                    raise Exception("No data received from server")
                
            print(f"Download completed: {file_path}")
            return file_path
            
        except Exception as e:
            if os.path.exists(file_path) and os.path.getsize(file_path) == 0:
                try:
                    os.remove(file_path)
                except:
                    pass
            raise e

async def main():
    use_fallback = False  
    downloader = TrackDownloader(use_fallback)
    
    output_dir = "."
    track_id = "2plbrEY59IikOBgBGLjaoe"
    service = "tidal"
    
    def progress_update(current, total):
        if total > 0:
            percent = (current / total) * 100
            print(f"\rDownload progress: {percent:.2f}% ({current}/{total})", end="")
    
    downloader.set_progress_callback(progress_update)
    
    try:
        print(f"Getting track info for ID: {track_id} from {service}")
        metadata = await downloader.get_track_info(track_id, service)
        print(f"Track info received, starting download process")
        
        downloaded_file = downloader.download(metadata, output_dir)
        print(f"\nFile downloaded successfully: {downloaded_file}")
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())