import requests
from mutagen.flac import FLAC, Picture
from datetime import datetime
import sys
import os

def get_track_info(isrc):
    print(f"Search: {isrc}")
    url = f"https://us.qobuz.squid.wtf/api/get-music?q={isrc}&offset=0"
    response = requests.get(url)
    data = response.json()
    
    if not data.get("success"):
        raise Exception("Failed to get track info")
    
    tracks = data["data"]["tracks"]["items"]
    if not tracks:
        print(f"Not Found: {isrc}")
        raise Exception(f"No tracks found for ISRC: {isrc}")
    
    track = None
    for item in tracks:
        if item["isrc"] == isrc:
            track = item
            break
    
    if not track:
        print(f"Not Found: {isrc}")
        raise Exception(f"No track with matching ISRC: {isrc}")
    
    print(f"Found: {track['title']} - {track['performer']['name']}")
    return track

def search_track(title, artist, strict_match=False):
    print(f"Search by title/artist: {title} - {artist}")
    
    search_query = f"{title} {artist}".replace("feat.", "").replace("ft.", "")
    
    url = f"https://us.qobuz.squid.wtf/api/get-music?q={search_query}&offset=0"
    response = requests.get(url)
    data = response.json()
    
    if not data.get("success"):
        raise Exception("Failed to search for track")
    
    tracks = data["data"]["tracks"]["items"]
    if not tracks:
        print(f"Not Found: {title} - {artist}")
        raise Exception(f"No tracks found for: {title} - {artist}")
    
    best_match = None
    title_lower = title.lower()
    artist_lower = artist.lower()
    
    for item in tracks:
        item_title = item["title"].lower()
        item_artist = item["performer"]["name"].lower()
        
        if title_lower == item_title and (artist_lower in item_artist or item_artist in artist_lower):
            best_match = item
            print(f"Found exact title match with artist: {item['title']} - {item['performer']['name']}")
            break
    
    if not best_match and not strict_match:
        for item in tracks:
            item_title = item["title"].lower()
            item_artist = item["performer"]["name"].lower()
            
            if title_lower in item_title and (artist_lower in item_artist or item_artist in artist_lower):
                best_match = item
                print(f"Found partial match: {item['title']} - {item['performer']['name']}")
                break
    
    if strict_match and best_match:
        item_artist = best_match["performer"]["name"].lower()
        if artist_lower not in item_artist and item_artist not in artist_lower:
            print(f"Artist mismatch in strict mode: Expected '{artist}', found '{best_match['performer']['name']}'")
            best_match = None
    
    if not best_match and not strict_match and tracks:
        best_match = tracks[0]
        print(f"No good match, using first result: {best_match['title']} - {best_match['performer']['name']}")
    
    if not best_match:
        print(f"Not Found: {title} - {artist}")
        raise Exception(f"No suitable track found for: {title} - {artist}")
    
    print(f"Found by title search: {best_match['title']} - {best_match['performer']['name']}")
    return best_match

def get_download_url(track_id):
    url = f"https://us.qobuz.squid.wtf/api/download-music?track_id={track_id}&quality=27"
    response = requests.get(url)
    data = response.json()
    
    if not data.get("success"):
        raise Exception("Failed to get download URL")
    
    return data["data"]["url"]

def download_file(url, filename, progress_callback=None):
    directory = os.path.dirname(filename)
    if directory and not os.path.exists(directory):
        try:
            os.makedirs(directory, exist_ok=True)
            print(f"Created directory: {directory}")
        except Exception as e:
            raise Exception(f"Failed to create directory {directory}: {str(e)}")
    
    try:
        with open(filename, 'wb') as test_file:
            pass
    except Exception as e:
        raise Exception(f"Cannot write to file {filename}: {str(e)}")
    
    try:
        response = requests.get(url, stream=True)
        
        if response.status_code != 200:
            raise Exception(f"Failed to download file: {response.status_code}")
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size > 0 and progress_callback:
                        progress_callback(downloaded, total_size)
                    elif total_size > 0:
                        progress = (downloaded / total_size) * 100
                        sys.stdout.write(f"\rProgress Download: {progress:.1f}%")
                        sys.stdout.flush()
        
        if total_size > 0:
            sys.stdout.write("\n")
        
        if not os.path.exists(filename) or os.path.getsize(filename) == 0:
            raise Exception(f"Download failed: File {filename} is empty or does not exist")
        
        return filename
    except Exception as e:
        if os.path.exists(filename):
            try:
                os.remove(filename)
                print(f"Removed incomplete file: {filename}")
            except:
                pass
        raise Exception(f"Download failed: {str(e)}")

def embed_metadata(filename, track_info):
    if not os.path.exists(filename):
        raise Exception(f"Cannot embed metadata: File {filename} does not exist")
    
    try:
        print("Embedding Tags...")
        audio = FLAC(filename)
        audio.clear()
        
        audio["TITLE"] = track_info["title"]
        audio["ARTIST"] = track_info["performer"]["name"]
        audio["ALBUM"] = track_info["album"]["title"]
        audio["ALBUMARTIST"] = track_info["album"]["artist"]["name"]
        audio["TRACKNUMBER"] = str(track_info["track_number"])
        audio["LABEL"] = track_info["album"]["label"]["name"]
        audio["GENRE"] = track_info["album"]["genre"]["name"]
        
        release_date = datetime.fromtimestamp(track_info["album"]["released_at"]).strftime("%Y-%m-%d")
        release_year = release_date.split("-")[0]
        
        audio["DATE"] = release_date
        audio["YEAR"] = release_year
        audio["ISRC"] = track_info["isrc"]
        audio["COPYRIGHT"] = track_info["copyright"]
        
        if track_info["album"]["image"]["large"]:
            try:
                cover_data = download_cover_image(track_info["album"]["image"]["large"])
                picture = Picture()
                picture.type = 3
                picture.mime = "image/jpeg"
                picture.desc = ""
                picture.data = cover_data
                
                audio.add_picture(picture)
            except Exception as e:
                print(f"Warning: Could not add cover image: {str(e)}")
        
        audio.save()
    except Exception as e:
        raise Exception(f"Failed to embed metadata: {str(e)}")

def download_cover_image(url):
    response = requests.get(url)
    
    if response.status_code != 200:
        raise Exception(f"Failed to download cover image: {response.status_code}")
    
    return response.content

def main():
    try:
        isrc = "USUM72409273"
        
        track_info = get_track_info(isrc)
        track_id = track_info["id"]
        
        if track_info["isrc"] != isrc:
            raise Exception(f"ISRC mismatch: {track_info['isrc']} != {isrc}")
        
        download_url = get_download_url(track_id)
        
        filename = f"{track_info['title']} - {track_info['performer']['name']}.flac"
        filename = filename.replace('/', '_').replace('\\', '_')
        
        download_file(download_url, filename)
        embed_metadata(filename, track_info)
        
        print("Downloaded Successfully!")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
