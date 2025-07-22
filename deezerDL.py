import requests
import asyncio
import os
import sys
from urllib.parse import urlparse
from mutagen.flac import FLAC
from mutagen.id3 import ID3NoHeaderError
import deezmate

class DeezerDownloader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.progress_callback = None
    
    def set_progress_callback(self, callback):
        self.progress_callback = callback
    
    def get_track_by_isrc(self, isrc):
        try:
            url = f"https://api.deezer.com/2.0/track/isrc:{isrc}"
            response = self.session.get(url)
            response.raise_for_status()
            
            data = response.json()
            
            if 'error' in data:
                print(f"Error from Deezer API: {data['error']['message']}")
                return None
            
            return data
        except requests.exceptions.RequestException as e:
            print(f"Error fetching track data: {e}")
            return None
    
    def extract_metadata(self, track_data):
        metadata = {}
        
        metadata['title'] = track_data.get('title', '')
        metadata['title_short'] = track_data.get('title_short', '')
        metadata['duration'] = track_data.get('duration', 0)
        metadata['track_position'] = track_data.get('track_position', 1)
        metadata['disk_number'] = track_data.get('disk_number', 1)
        metadata['isrc'] = track_data.get('isrc', '')
        metadata['release_date'] = track_data.get('release_date', '')
        metadata['explicit_lyrics'] = track_data.get('explicit_lyrics', False)
        
        if 'artist' in track_data:
            metadata['artist'] = track_data['artist'].get('name', '')
            metadata['artist_id'] = track_data['artist'].get('id', '')
        
        if 'contributors' in track_data:
            artists = []
            for contributor in track_data['contributors']:
                if contributor.get('role') == 'Main':
                    artists.append(contributor.get('name', ''))
            metadata['artists'] = ', '.join(artists) if artists else metadata.get('artist', '')
        
        if 'album' in track_data:
            album = track_data['album']
            metadata['album'] = album.get('title', '')
            metadata['album_id'] = album.get('id', '')
            metadata['cover_url'] = album.get('cover_xl', album.get('cover_big', ''))
            metadata['cover_md5'] = album.get('md5_image', '')
        
        metadata['deezer_link'] = track_data.get('link', '')
        metadata['preview_url'] = track_data.get('preview', '')
        
        return metadata
    
    def download_cover_art(self, cover_url, filename):
        if not cover_url:
            return None
        
        try:
            response = self.session.get(cover_url)
            response.raise_for_status()
            
            cover_path = f"{filename}_cover.jpg"
            with open(cover_path, 'wb') as f:
                f.write(response.content)
            
            return cover_path
        except Exception as e:
            print(f"Error downloading cover art: {e}")
            return None
    
    def embed_metadata(self, file_path, metadata, cover_path=None):
        try:
            audio = FLAC(file_path)
            
            audio.clear()
            
            if metadata.get('title'):
                audio['TITLE'] = metadata['title']
            if metadata.get('artists'):
                audio['ARTIST'] = metadata['artists']
            elif metadata.get('artist'):
                audio['ARTIST'] = metadata['artist']
            if metadata.get('album'):
                audio['ALBUM'] = metadata['album']
            if metadata.get('release_date'):
                audio['DATE'] = metadata['release_date']
            if metadata.get('track_position'):
                audio['TRACKNUMBER'] = str(metadata['track_position'])
            if metadata.get('disk_number'):
                audio['DISCNUMBER'] = str(metadata['disk_number'])
            if metadata.get('isrc'):
                audio['ISRC'] = metadata['isrc']
            
            if cover_path and os.path.exists(cover_path):
                with open(cover_path, 'rb') as f:
                    cover_data = f.read()
                
                from mutagen.flac import Picture
                picture = Picture()
                picture.type = 3
                picture.mime = 'image/jpeg'
                picture.desc = 'Cover'
                picture.data = cover_data
                audio.add_picture(picture)
            
            audio.save()
            print(f"Metadata embedded successfully in {file_path}")
            
        except Exception as e:
            print(f"Error embedding metadata: {e}")
    
    async def download_by_isrc(self, isrc, output_dir=".", initial_delay=7.5):
        print(f"Fetching track info for ISRC: {isrc}")
        
        track_data = self.get_track_by_isrc(isrc)
        if not track_data:
            print("Failed to get track data from Deezer API")
            return False
        
        metadata = self.extract_metadata(track_data)
        print(f"Found track: {metadata.get('artists', 'Unknown')} - {metadata.get('title', 'Unknown')}")
        
        deezer_link = metadata.get('deezer_link')
        if not deezer_link:
            print("No Deezer link found in track data")
            return False
        
        print(f"Using Deezer link: {deezer_link}")
        
        flac_url = await deezmate.main(deezer_link, initial_delay)
        if not flac_url:
            print("Failed to get download URL from deezmate")
            return False
        
        print("Downloading FLAC file...")
        try:
            response = self.session.get(flac_url, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            print(f"File size: {total_size} bytes ({total_size / (1024*1024):.2f} MB)")
            
            safe_title = "".join(c for c in metadata.get('title', 'Unknown') if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_artist = "".join(c for c in metadata.get('artists', 'Unknown') if c.isalnum() or c in (' ', '-', '_')).rstrip()
            filename = f"{safe_artist} - {safe_title}.flac"
            file_path = os.path.join(output_dir, filename)
            
            downloaded = 0
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if self.progress_callback and total_size > 0:
                        current_mb = downloaded / (1024 * 1024)
                        total_mb = total_size / (1024 * 1024)
                        percent = (downloaded / total_size) * 100
                        self.progress_callback(downloaded, total_size)
            
            print(f"Downloaded: {file_path}")
            
            cover_path = None
            if metadata.get('cover_url'):
                print("Downloading cover art...")
                cover_path = self.download_cover_art(metadata['cover_url'], 
                                                   os.path.join(output_dir, f"{safe_artist} - {safe_title}"))
            
            print("Embedding metadata...")
            self.embed_metadata(file_path, metadata, cover_path)
            
            if cover_path and os.path.exists(cover_path):
                os.remove(cover_path)
            
            print(f"Successfully downloaded and tagged: {filename}")
            return True
            
        except Exception as e:
            print(f"Error downloading file: {e}")
            return False

async def main():
    if len(sys.argv) != 2:
        print("Usage: python deezerDL.py <ISRC>")
        print("Example: python deezerDL.py USUM72409273")
        return
    
    isrc = sys.argv[1]
    downloader = DeezerDownloader()
    
    success = await downloader.download_by_isrc(isrc)
    if success:
        print("Download completed successfully!")
    else:
        print("Download failed!")

if __name__ == "__main__":
    asyncio.run(main())