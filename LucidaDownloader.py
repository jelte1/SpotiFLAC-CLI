import requests
import time
import os
import asyncio
from GetMetadata import main as get_metadata

class TrackDownloader:
    def __init__(self):
        self.client = requests.Session()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.progress_callback = None
        self.filename_format = 'title_artist'

    def set_progress_callback(self, callback):
        self.progress_callback = callback
        
    def set_filename_format(self, format_type):
        self.filename_format = format_type

    def generate_filename(self, metadata):
        if self.filename_format == 'artist_title':
            filename = f"{metadata['artists']} - {metadata['title']}.flac"
        else:
            filename = f"{metadata['title']} - {metadata['artists']}.flac"
        return self.sanitize_filename(filename)

    async def get_track_info(self):
        metadata = await get_metadata()
        return metadata

    def sanitize_filename(self, filename):
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '')
            
        filename = ' '.join(filename.split())
        filename = filename.replace(' ,', ',')
        filename = filename.replace(',', ', ')
        while '  ' in filename:
            filename = filename.replace('  ', ' ')
        filename = filename.rsplit('.', 1)
        filename[0] = filename[0].strip()
        return '.'.join(filename)

    def download(self, metadata, output_dir):
        track_url = metadata['url']
        primary_token = metadata['token']
        expiry = metadata['expiry']
        
        print(f"Starting download for: {track_url}")
        
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

        response = self.client.post("https://lucida.to/api/load?url=/api/fetch/stream/v2", 
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

        file_name = self.generate_filename(metadata)

        completion_url = f"https://{server}.lucida.to/api/fetch/request/{handoff}"

        print("Waiting for track processing to complete")
        while True:
            completion_response = self.client.get(completion_url, headers=self.headers).json()
            if completion_response["status"] == "completed":
                break
            elif completion_response["status"] == "error":
                raise Exception(f"API request failed: {completion_response.get('message', 'Unknown error')}")
            time.sleep(1)

        download_url = f"https://{server}.lucida.to/api/fetch/request/{handoff}/download"
        print(f"Starting download of: {file_name}")
        
        response = self.client.get(download_url, stream=True, headers=self.headers)
        total_size = int(response.headers.get('content-length', 0))
        downloaded_size = 0

        file_path = os.path.join(output_dir, file_name)

        try:
            with open(file_path, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file.write(chunk)
                        downloaded_size += len(chunk)
                        if self.progress_callback:
                            progress = int((downloaded_size / total_size) * 100) if total_size > 0 else 0
                            self.progress_callback(progress)
                
                if downloaded_size == 0:
                    raise Exception("No data received from server")
                
            return file_path
            
        except Exception as e:
            if os.path.exists(file_path) and os.path.getsize(file_path) == 0:
                try:
                    os.remove(file_path)
                except:
                    pass
            raise e

async def main():
    downloader = TrackDownloader()
    output_dir = "."
    
    try:
        metadata = await downloader.get_track_info()
        downloaded_file = downloader.download(metadata, output_dir)
        print(f"File downloaded successfully: {downloaded_file}")
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())