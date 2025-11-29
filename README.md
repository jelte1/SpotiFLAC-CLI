<h1>Command Line Interface version of SpotiFLAC.</h1>
<h2>Arguments</h2>
<i>--service {tidal,qobuz,deezer,amazon}</i><br>
Specify the music service to use for downloading FLAC files. Specify multiple services separated by spaces to try them in order. Default is 'tidal'.<br><br>
<i>--filename-format {title_artist,artist_title,title_only}</i><br>
Specify the format for naming downloaded files. Default is 'title_artist'.<br><br>
<i>--use-track-numbers</i><br>
Include track numbers in the filenames.<br><br>
<i>--use-artist-subfolders</i><br>
Organize downloaded files into subfolders by artist.<br><br>
<i>--use-album-subfolders</i><br>
Organize downloaded files into subfolders by album.<br><br>
<i>--loop minutes</i><br>
Specify the duration in minutes to keep retrying downloads in case of failures. Default is 0 (no retries).<br>


<h3>Usage</h3>

```bash
python SpotiFLAC.py [--service {tidal,deezer}]
                    [--filename-format {title_artist,artist_title,title_only}]
                    [--use-track-numbers]
                    [--use-artist-subfolders]
                    [--use-album-subfolders]
                    [--loop minutes]
                    url 
                    output_dir
```

<h3>Example</h3>

```bash
python SpotiFLAC.py --service tidal deezer
                    --filename-format artist_title 
                    --use-track-numbers 
                    --use-artist-subfolders 
                    --use-album-subfolders 
                    --loop 120
                    https://open.spotify.com/album/xyz 
                    /path/to/output_dir
```

<h2>Installation</h2>

To install the required dependencies, run the following command:

```bash
pip install -r requirements.txt
```
