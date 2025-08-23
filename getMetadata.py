from time import sleep
from urllib.parse import urlparse, parse_qs
import requests
import json
import time
import pyotp
import base64
from random import randrange
from typing import Dict, Any, List, Tuple


# https://github.com/visagenull/Spotify-Free
def get_random_user_agent():
    return f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_{randrange(11, 15)}_{randrange(4, 9)}) AppleWebKit/{randrange(530, 537)}.{randrange(30, 37)} (KHTML, like Gecko) Chrome/{randrange(80, 105)}.0.{randrange(3000, 4500)}.{randrange(60, 125)} Safari/{randrange(530, 537)}.{randrange(30, 36)}"


def generate_totp():
    url = "https://raw.githubusercontent.com/Thereallo1026/spotify-secrets/refs/heads/main/secrets/secretBytes.json"

    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"Failed to fetch TOTP secrets from GitHub. Status: {resp.status_code}")
        secrets_list = resp.json()

        latest_entry = max(secrets_list, key=lambda x: x["version"])
        version = latest_entry["version"]
        secret_cipher = latest_entry["secret"]
    except Exception as e:
        raise Exception(f"Failed to fetch secrets from GitHub: {str(e)}")

    processed = [byte ^ ((i % 33) + 9) for i, byte in enumerate(secret_cipher)]
    processed_str = "".join(map(str, processed))
    utf8_bytes = processed_str.encode('utf-8')
    hex_str = utf8_bytes.hex()
    secret_bytes = bytes.fromhex(hex_str)
    b32_secret = base64.b32encode(secret_bytes).decode('utf-8')
    totp = pyotp.TOTP(b32_secret)

    headers = {
        "Host": "open.spotify.com",
        "User-Agent": get_random_user_agent(),
        "Accept": "*/*",
    }

    try:
        resp = requests.get("https://open.spotify.com/api/server-time", headers=headers, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"Failed to get server time. Status code: {resp.status_code}")
        data = resp.json()
        server_time = data.get("serverTime")
        if server_time is None:
            raise Exception("Failed to fetch server time from Spotify")
        return totp, server_time, version
    except Exception as e:
        raise Exception(f"Error getting server time: {str(e)}")


token_url = 'https://open.spotify.com/api/token'
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


def get_access_token():
    try:
        totp, server_time, totp_version = generate_totp()
        otp_code = totp.at(int(server_time))
        timestamp_ms = int(time.time() * 1000)

        params = {
            'reason': 'init',
            'productType': 'web-player',
            'totp': otp_code,
            'totpServerTime': server_time,
            'totpVer': str(totp_version),
            'sTime': server_time,
            'cTime': timestamp_ms,
            'buildVer': 'web-player_2025-07-02_1720000000000_12345678',
            'buildDate': '2025-07-02'
        }

        req = requests.get(token_url, headers=headers, params=params, timeout=10)
        if req.status_code != 200:
            return {"error": f"Failed to get access token. Status code: {req.status_code}"}
        return req.json()
    except Exception as e:
        return {"error": f"Failed to get access token: {str(e)}"}


def fetch_tracks_in_batches(url: str, access_token: str, batch_size: int = 100, delay: float = 1.0) -> Tuple[
    List[Dict[str, Any]], int]:
    all_tracks = []
    current_batch = 0

    while url:
        print(f"Batch : {current_batch}")

        url_parts = url.split("offset=")
        if len(url_parts) > 1:
            offset_part = url_parts[1].split("&")[0]
            print(f"Offset : {offset_part}")
        print("-------------")

        track_data = get_json_from_api(url, access_token)
        if not track_data:
            break

        items = track_data.get('items', [])
        all_tracks.extend(items)

        url = track_data.get('next')
        if url and "&locale=" in url:
            url = url.split("&locale=")[0]

        if url and delay > 0:
            sleep(delay)

        current_batch += 1

    return all_tracks, current_batch


def get_raw_spotify_data(spotify_url, batch: bool = False, delay: float = 1.0):
    url_info = parse_uri(spotify_url)
    token = get_access_token()

    if "error" in token:
        return token

    access_token = token["accessToken"]
    raw_data = {}

    if url_info['type'] == "playlist":
        try:
            playlist_data = get_json_from_api(
                playlist_base_url.format(url_info["id"]),
                access_token
            )
            if not playlist_data:
                return {"error": "Failed to get playlist data"}

            raw_data = playlist_data
            total_tracks = playlist_data.get('tracks', {}).get('total', 0)

            if batch:
                tracks_url = f'https://api.spotify.com/v1/playlists/{url_info["id"]}/tracks?limit=100'
                tracks, num_batches = fetch_tracks_in_batches(tracks_url, access_token, 100, delay)
                raw_data['tracks']['items'] = tracks
                raw_data['_batch_count'] = num_batches
                raw_data['_batch_enabled'] = True

                if len(tracks) < total_tracks:
                    last_offset = len(tracks)
                    remaining_tracks = []

                    while last_offset < total_tracks:
                        print(f"Batch : {num_batches}")
                        print(f"Offset : {last_offset}")
                        print("-------------")

                        remainder_url = f'https://api.spotify.com/v1/playlists/{url_info["id"]}/tracks?offset={last_offset}&limit=100'
                        track_data = get_json_from_api(remainder_url, access_token)

                        if not track_data or not track_data.get('items'):
                            break

                        items = track_data.get('items', [])
                        remaining_tracks.extend(items)

                        if len(items) < 100:
                            break

                        last_offset += len(items)
                        num_batches += 1

                        if delay > 0:
                            sleep(delay)

                    tracks.extend(remaining_tracks)
                    raw_data['tracks']['items'] = tracks
                    raw_data['_batch_count'] = num_batches
            else:
                tracks = []
                tracks_url = f'https://api.spotify.com/v1/playlists/{url_info["id"]}/tracks?limit=100'
                while tracks_url:
                    track_data = get_json_from_api(tracks_url, access_token)
                    if not track_data:
                        break

                    tracks.extend(track_data['items'])
                    tracks_url = track_data.get('next')
                    if tracks_url and "&locale=" in tracks_url:
                        tracks_url = tracks_url.split("&locale=")[0]

                raw_data['tracks']['items'] = tracks
                raw_data['_batch_enabled'] = False

        except Exception as e:
            return {"error": f"Failed to get playlist data: {str(e)}"}

    elif url_info["type"] == "album":
        try:
            album_data = get_json_from_api(
                album_base_url.format(url_info["id"]),
                access_token
            )
            if not album_data:
                return {"error": "Failed to get album data"}

            album_data['_token'] = access_token
            raw_data = album_data
            total_tracks = album_data.get('total_tracks', 0)

            if batch:
                tracks_url = f'{album_base_url.format(url_info["id"])}/tracks?limit=50'
                tracks, num_batches = fetch_tracks_in_batches(tracks_url, access_token, 50, delay)
                raw_data['tracks']['items'] = tracks
                raw_data['_batch_count'] = num_batches
                raw_data['_batch_enabled'] = True

                if len(tracks) < total_tracks:
                    last_offset = len(tracks)
                    remaining_tracks = []

                    while last_offset < total_tracks:
                        print(f"Batch : {num_batches}")
                        print(f"Offset : {last_offset}")
                        print("-------------")

                        remainder_url = f'{album_base_url.format(url_info["id"])}/tracks?offset={last_offset}&limit=50'
                        track_data = get_json_from_api(remainder_url, access_token)

                        if not track_data or not track_data.get('items'):
                            break

                        items = track_data.get('items', [])
                        remaining_tracks.extend(items)

                        if len(items) < 50:
                            break

                        last_offset += len(items)
                        num_batches += 1

                        if delay > 0:
                            sleep(delay)

                    tracks.extend(remaining_tracks)
                    raw_data['tracks']['items'] = tracks
                    raw_data['_batch_count'] = num_batches
            else:
                tracks = []
                tracks_url = f'{album_base_url.format(url_info["id"])}/tracks?limit=50'
                while tracks_url:
                    track_data = get_json_from_api(tracks_url, access_token)
                    if not track_data:
                        break

                    tracks.extend(track_data['items'])
                    tracks_url = track_data.get('next')
                    if tracks_url and "&locale=" in tracks_url:
                        tracks_url = tracks_url.split("&locale=")[0]

                raw_data['tracks']['items'] = tracks
                raw_data['_batch_enabled'] = False

        except Exception as e:
            return {"error": f"Failed to get album data: {str(e)}"}

    elif url_info["type"] == "track":
        try:
            track_data = get_json_from_api(
                track_base_url.format(url_info["id"]),
                access_token
            )
            if not track_data:
                return {"error": "Failed to get track data"}

            raw_data = track_data
        except Exception as e:
            return {"error": f"Failed to get track data: {str(e)}"}

    return raw_data


def format_track_data(track_data):
    artists = []
    for artist in track_data.get('artists', []):
        if artist.get('name') is None:
            artists.append('Unknown Artist')
        else:
            artists.append(artist['name'])

    image_url = track_data.get('album', {}).get('images', [{}])[0].get('url', '') if track_data.get('album', {}).get(
        'images') else ''

    isrc = track_data.get('external_ids', {}).get('isrc', '')

    return {
        "track": {
            "artists": ", ".join(artists),
            "name": track_data.get('name', ''),
            "album_name": track_data.get('album', {}).get('name', ''),
            "duration_ms": track_data.get('duration_ms', 0),
            "images": image_url,
            "release_date": track_data.get('album', {}).get('release_date', ''),
            "track_number": track_data.get('track_number', 0),
            "external_urls": track_data.get('external_urls', {}).get('spotify', ''),
            "isrc": isrc
        }
    }


def format_album_data(album_data):
    artists = []
    for artist in album_data.get('artists', []):
        if artist.get('name') is None:
            artists.append('Unknown Artist')
        else:
            artists.append(artist['name'])

    image_url = album_data.get('images', [{}])[0].get('url', '') if album_data.get('images') else ''

    track_list = []
    for track in album_data.get('tracks', {}).get('items', []):
        track_artists = []
        for artist in track.get('artists', []):
            if artist.get('name') is None:
                track_artists.append('Unknown Artist')
            else:
                track_artists.append(artist['name'])

        track_id = track.get('id', '')
        track_isrc = ''

        if track_id and album_data.get('_token'):
            try:
                full_track_data = get_json_from_api(
                    track_base_url.format(track_id),
                    album_data.get('_token')
                )
                if full_track_data:
                    track_isrc = full_track_data.get('external_ids', {}).get('isrc', '')
            except:
                pass

        track_list.append({
            "artists": ", ".join(track_artists),
            "name": track.get('name', ''),
            "album_name": album_data.get('name', ''),
            "duration_ms": track.get('duration_ms', 0),
            "images": image_url,
            "release_date": album_data.get('release_date', ''),
            "track_number": track.get('track_number', 0),
            "external_urls": track.get('external_urls', {}).get('spotify', ''),
            "isrc": track_isrc
        })

    album_info = {
        "total_tracks": album_data.get('total_tracks', 0),
        "name": album_data.get('name', ''),
        "release_date": album_data.get('release_date', ''),
        "artists": ", ".join(artists),
        "images": image_url
    }

    if album_data.get('_batch_enabled', False):
        album_info["batch"] = f"{album_data.get('_batch_count', 1)}"

    return {
        "album_info": album_info,
        "track_list": track_list
    }


def format_playlist_data(playlist_data):
    image_url = playlist_data.get('images', [{}])[0].get('url', '') if playlist_data.get('images') else ''

    track_list = []
    for item in playlist_data.get('tracks', {}).get('items', []):

        track = item.get('track', {})
        if not track:
            continue

        artists = []
        for artist in track.get('artists', []):
            if artist.get('name') is None:
                artists.append('Unknown Artist')
            else:
                artists.append(artist['name'])

        track_image = ''
        if track.get('album', {}).get('images'):
            track_image = track.get('album', {}).get('images', [{}])[0].get('url', '')

        track_isrc = track.get('external_ids', {}).get('isrc', '')

        track_list.append({
            "artists": ", ".join(artists),
            "name": track.get('name', ''),
            "album_name": track.get('album', {}).get('name', ''),
            "duration_ms": track.get('duration_ms', 0),
            "images": track_image,
            "release_date": track.get('album', {}).get('release_date', ''),
            "track_number": track.get('track_number', 0),
            "external_urls": track.get('external_urls', {}).get('spotify', ''),
            "isrc": track_isrc
        })

    playlist_info = {
        "tracks": {"total": playlist_data.get('tracks', {}).get('total', 0)},
        "followers": {"total": playlist_data.get('followers', {}).get('total', 0)},
        "owner": {
            "display_name": playlist_data.get('owner', {}).get('display_name', ''),
            "name": playlist_data.get('name', ''),
            "images": image_url
        }
    }

    if playlist_data.get('_batch_enabled', False):
        playlist_info["batch"] = f"{playlist_data.get('_batch_count', 1)}"

    return {
        "playlist_info": playlist_info,
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
            print("Invalid data type")
            return {"error": "Invalid data type"}
    except Exception as e:
        print("Error processing data")
        return {"error": f"Error processing data: {str(e)}"}


def get_filtered_data(spotify_url, batch=False, delay=1.0):
    raw_data = get_raw_spotify_data(spotify_url, batch=batch, delay=delay)
    if raw_data and "error" not in raw_data:
        url_info = parse_uri(spotify_url)
        filtered_data = process_spotify_data(raw_data, url_info['type'])
        return filtered_data
    return {"error": "Failed to get raw data"}


if __name__ == '__main__':
    playlist = "https://open.spotify.com/playlist/37i9dQZEVXbNG2KDcFcKOF"
    album = "https://open.spotify.com/album/6J84szYCnMfzEcvIcfWMFL"
    song = "https://open.spotify.com/track/7so0lgd0zP2Sbgs2d7a1SZ"

    filtered_playlist = get_filtered_data(playlist, batch=True, delay=0.1)
    print(json.dumps(filtered_playlist, indent=2))

    filtered_album = get_filtered_data(album)
    print(json.dumps(filtered_album, indent=2))

    filtered_track = get_filtered_data(song)
    print(json.dumps(filtered_track, indent=2))