from time import sleep
from urllib.parse import urlparse, parse_qs
import requests
import json
import hmac
import time
import hashlib
from typing import Tuple, Callable

_TOTP_SECRET = bytearray([53,53,48,55,49,52,53,56,53,51,52,56,55,52,57,57,53,57,50,50,52,56,54,51,48,51,50,57,51,52,55])

def generate_totp(
    secret: bytes = _TOTP_SECRET,
    algorithm: Callable[[], object] = hashlib.sha1,
    digits: int = 6,
    counter_factory: Callable[[], int] = lambda: int(time.time()) // 30,
) -> Tuple[str, int]:
    counter = counter_factory()
    hmac_result = hmac.new(
        secret, counter.to_bytes(8, byteorder="big"), algorithm
    ).digest()

    offset = hmac_result[-1] & 15
    truncated_value = (
        (hmac_result[offset] & 127) << 24
        | (hmac_result[offset + 1] & 255) << 16
        | (hmac_result[offset + 2] & 255) << 8
        | (hmac_result[offset + 3] & 255)
    )
    return (
        str(truncated_value % (10**digits)).zfill(digits),
        counter * 30_000,
    )

token_url = 'https://open.spotify.com/get_access_token'
playlist_base_url = 'https://api.spotify.com/v1/playlists/{}'
album_base_url = 'https://api.spotify.com/v1/albums/{}'
track_base_url = 'https://api.spotify.com/v1/tracks/{}'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'Referer': 'https://open.spotify.com/',
    'Origin': 'https://open.spotify.com'
}

class SpotifyInvalidUrlException(Exception):
    pass

class SpotifyWebsiteParserException(Exception):
    pass

def parse_uri(uri):
    u = urlparse(uri)
    if u.netloc == "embed.spotify.com":
        if not u.query:
            raise SpotifyInvalidUrlException("ERROR: url {} is not supported".format(uri))
        qs = parse_qs(u.query)
        return parse_uri(qs['uri'][0])

    if not u.scheme and not u.netloc:
        return {"type": "playlist", "id": u.path}

    if u.scheme == "spotify":
        parts = uri.split(":")
    else:
        if u.netloc != "open.spotify.com" and u.netloc != "play.spotify.com":
            raise SpotifyInvalidUrlException("ERROR: url {} is not supported".format(uri))
        parts = u.path.split("/")

    if parts[1] == "embed":
        parts = parts[1:]

    l = len(parts)
    if l == 3 and parts[1] in ["album", "track", "playlist"]:
        return {"type": parts[1], "id": parts[2]}
    if l == 5 and parts[3] == "playlist":
        return {"type": parts[3], "id": parts[4]}

    raise SpotifyInvalidUrlException("ERROR: unable to determine Spotify URL type or type is unsupported.")

def get_json_from_api(api_url, access_token):
    headers.update({'Authorization': 'Bearer {}'.format(access_token)})
    
    req = requests.get(api_url, headers=headers, timeout=10)

    if req.status_code == 429:
        seconds = int(req.headers.get("Retry-After", "5")) + 1
        print(f"INFO: rate limited! Sleeping for {seconds} seconds")
        sleep(seconds)
        return None

    if req.status_code != 200:
        raise SpotifyWebsiteParserException(f"ERROR: {api_url} gave us not a 200. Instead: {req.status_code}")
        
    return req.json()

def get_raw_spotify_data(spotify_url):
    url_info = parse_uri(spotify_url)
    
    try:
        totp, timestamp = generate_totp()
        
        params = {
            "reason": "init",
            "productType": "web-player",
            "totp": totp,
            "totpVer": 5,
            "ts": timestamp,
        }
        
        req = requests.get(token_url, headers=headers, params=params, timeout=10)
        if req.status_code != 200:
            return {"error": f"Failed to get access token. Status code: {req.status_code}"}
        token = req.json()
    except Exception as e:
        return {"error": f"Failed to get access token: {str(e)}"}
    
    raw_data = {}
    
    if url_info['type'] == "playlist":
        try:
            playlist_data = get_json_from_api(
                playlist_base_url.format(url_info["id"]), 
                token["accessToken"]
            )
            if not playlist_data:
                return {"error": "Failed to get playlist data"}
                
            raw_data = playlist_data
            
            tracks = []
            tracks_url = f'https://api.spotify.com/v1/playlists/{url_info["id"]}/tracks?limit=100'
            while tracks_url:
                track_data = get_json_from_api(tracks_url, token["accessToken"])
                if not track_data:
                    break
                    
                tracks.extend(track_data['items'])
                tracks_url = track_data.get('next')
                
            raw_data['tracks']['items'] = tracks
        except Exception as e:
            return {"error": f"Failed to get playlist data: {str(e)}"}
            
    elif url_info["type"] == "album":
        try:
            album_data = get_json_from_api(
                album_base_url.format(url_info["id"]),
                token["accessToken"]
            )
            if not album_data:
                return {"error": "Failed to get album data"}
                
            album_data['_token'] = token["accessToken"]
            raw_data = album_data
            
            tracks = []
            tracks_url = f'{album_base_url.format(url_info["id"])}/tracks?limit=50'
            while tracks_url:
                track_data = get_json_from_api(tracks_url, token["accessToken"])
                if not track_data:
                    break
                    
                tracks.extend(track_data['items'])
                tracks_url = track_data.get('next')
                
            raw_data['tracks']['items'] = tracks
        except Exception as e:
            return {"error": f"Failed to get album data: {str(e)}"}
                
    elif url_info["type"] == "track":
        try:
            track_data = get_json_from_api(
                track_base_url.format(url_info["id"]),
                token["accessToken"]
            )
            if not track_data:
                return {"error": "Failed to get track data"}
                
            raw_data = track_data
        except Exception as e:
            return {"error": f"Failed to get track data: {str(e)}"}

    return raw_data

def format_track_data(track_data):
    artists = []
    for artist in track_data['artists']:
        artists.append(artist['name'])
    
    image_url = track_data.get('album', {}).get('images', [{}])[0].get('url', '')
    
    return {
        "track": {
            "artists": ", ".join(artists),
            "name": track_data.get('name', ''),
            "album_name": track_data.get('album', {}).get('name', ''),
            "duration_ms": track_data.get('duration_ms', 0),
            "images": image_url,
            "release_date": track_data.get('album', {}).get('release_date', ''),
            "track_number": track_data.get('track_number', 0),
            "external_urls": track_data.get('external_urls', {}).get('spotify', '')
        }
    }

def format_album_data(album_data):
    artists = []
    for artist in album_data['artists']:
        artists.append(artist['name'])
    
    image_url = album_data.get('images', [{}])[0].get('url', '')
    
    track_list = []
    for track in album_data.get('tracks', {}).get('items', []):
        track_artists = []
        for artist in track.get('artists', []):
            track_artists.append(artist['name'])
            
        track_list.append({
            "artists": ", ".join(track_artists),
            "name": track.get('name', ''),
            "album_name": album_data.get('name', ''),
            "duration_ms": track.get('duration_ms', 0),
            "images": image_url,
            "release_date": album_data.get('release_date', ''),
            "track_number": track.get('track_number', 0),
            "external_urls": track.get('external_urls', {}).get('spotify', '')
        })
    
    return {
        "album_info": {
            "total_tracks": album_data.get('total_tracks', 0),
            "name": album_data.get('name', ''),
            "release_date": album_data.get('release_date', ''),
            "artists": ", ".join(artists),
            "images": image_url
        },
        "track_list": track_list
    }

def format_playlist_data(playlist_data):
    image_url = playlist_data.get('images', [{}])[0].get('url', '')
    
    track_list = []
    for item in playlist_data.get('tracks', {}).get('items', []):
        track = item.get('track', {})
        artists = []
        for artist in track.get('artists', []):
            artists.append(artist['name'])
            
        track_image = track.get('album', {}).get('images', [{}])[0].get('url', '')
        
        track_list.append({
            "artists": ", ".join(artists),
            "name": track.get('name', ''),
            "album_name": track.get('album', {}).get('name', ''),
            "duration_ms": track.get('duration_ms', 0),
            "images": track_image,
            "release_date": track.get('album', {}).get('release_date', ''),
            "track_number": track.get('track_number', 0),
            "external_urls": track.get('external_urls', {}).get('spotify', '')
        })
    
    return {
        "playlist_info": {
            "tracks": {"total": playlist_data.get('tracks', {}).get('total', 0)},
            "followers": {"total": playlist_data.get('followers', {}).get('total', 0)},
            "owner": {
                "display_name": playlist_data.get('owner', {}).get('display_name', ''),
                "name": playlist_data.get('name', ''),
                "images": image_url
            }
        },
        "track_list": track_list
    }

def process_spotify_data(raw_data, data_type):
    if not raw_data or "error" in raw_data:
        return {"error": "Invalid data provided"}
        
    try:
        if data_type == "track":
            return format_track_data(raw_data)
        elif data_type == "album":
            return format_album_data(raw_data)
        elif data_type == "playlist":
            return format_playlist_data(raw_data)
        else:
            return {"error": "Invalid data type"}
    except Exception as e:
        return {"error": f"Error processing data: {str(e)}"}

def get_filtered_data(spotify_url):
    raw_data = get_raw_spotify_data(spotify_url)
    if raw_data and "error" not in raw_data:
        url_info = parse_uri(spotify_url)
        filtered_data = process_spotify_data(raw_data, url_info['type'])
        return filtered_data
    return {"error": "Failed to get raw data"}

if __name__ == '__main__':
    playlist = "https://open.spotify.com/playlist/37i9dQZEVXbNG2KDcFcKOF"
    album = "https://open.spotify.com/album/7kFyd5oyJdVX2pIi6P4iHE"
    song = "https://open.spotify.com/track/4wJ5Qq0jBN4ajy7ouZIV1c"
    
    filtered_playlist = get_filtered_data(playlist)
    print(json.dumps(filtered_playlist, indent=2))
    
    filtered_album = get_filtered_data(album)
    print(json.dumps(filtered_album, indent=2))
    
    filtered_track = get_filtered_data(song)
    print(json.dumps(filtered_track, indent=2))