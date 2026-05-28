#!/usr/bin/env python3
import argparse
import collections
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import xml.etree.ElementTree as ET


class colors:
    green             = "\033[38;5;28m"
    Bogreen           = "\033[1;38;5;28m"
    red               = "\033[31m"
    BoRed             = "\033[1;31m"
    orange            = "\033[38;5;202m"
    yellow            = "\033[38;5;190m"
    pink              = "\033[38;5;200m"
    Lblue             = "\033[38;5;117m"
    rblue             = "\033[38;5;63m"
    lmagenta          = "\033[38;5;95m"
    deep_sky_blue_4c  = "\033[38;5;25m"
    gray              = "\033[38;5;245m"
    magenta_2a        = "\033[38;5;165m"
    medium_purple_3b  = "\033[38;5;98m"
    reset             = "\033[0m"


def natural_sort_key(s):
    """Sort filenames in human-readable (natural) order."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'([0-9]+)', s)]


def format_bytes(size):
    """Convert bytes to human-readable format."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB', 'PB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} EB"

VIDEO_EXTENSIONS = (
    '.mp4', '.avi', '.mov', '.wmv', '.webm', '.flv', '.m4v',
    '.ts', '.m2ts', '.mts', '.mpg', '.mpeg', '.3gp',
    '.ogv', '.ogg', '.rmvb', '.divx', '.vob'
)


def get_duration_seconds(path):
    try:
        result = subprocess.run([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', path
        ], check=True, capture_output=True, text=True)
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return None


def parse_nfo_xml(contents):
    metadata = {}
    try:
        root = ET.fromstring(contents)
    except ET.ParseError:
        return metadata

    def find_text(path):
        value = root.findtext(path)
        return value.strip() if value and value.strip() else None

    title = find_text('.//title')
    if title:
        metadata['title'] = title

    plot = find_text('.//plot')
    if plot:
        metadata['description'] = plot

    aired = find_text('.//aired')
    if aired:
        metadata['date'] = aired

    season = find_text('.//season')
    if season and season.lstrip('-').isdigit():
        metadata['season'] = str(int(season))

    episode = find_text('.//episode')
    if episode and episode.lstrip('-').isdigit():
        metadata['episode'] = str(int(episode))

    tvdb_id = None
    for uid in root.findall('.//uniqueid'):
        if uid.get('type', '').lower() == 'tvdb':
            tvdb_id = uid.text.strip() if uid.text else None
            break
    if tvdb_id:
        metadata['tvdb_id'] = tvdb_id

    imdb_id = None
    for uid in root.findall('.//uniqueid'):
        if uid.get('type', '').lower() == 'imdb':
            imdb_id = uid.text.strip() if uid.text else None
            break
    if imdb_id:
        metadata['imdb_id'] = imdb_id

    actors = find_text('.//actors') or find_text('.//actor')
    if actors:
        metadata['actors'] = actors

    director = find_text('.//director')
    if director:
        metadata['director'] = director

    genre = find_text('.//genre')
    if genre:
        metadata['genre'] = genre

    return metadata


def parse_nfo_metadata(nfo_dir, source_basename=None):
    """
    Look for a sidecar metadata file in nfo_dir and parse it.

    Only files whose stem exactly matches source_basename are considered
    (<basename>.nfo, then <basename>.xml). Unrelated .nfo/.xml files in the
    same directory are intentionally ignored to avoid picking up metadata
    that belongs to a different title.

    Both XML-format files (Kodi/Jellyfin episodedetails) and plain key:value
    text files are supported.

    Returns a dict with any subset of: title, description, comment,
    imdb_id, tvdb_id, actors, director, genre, date.
    Returns {} when no matching file is found or the file contains no
    recognised tags.
    """
    nfo_file = None
    candidates = []
    if source_basename:
        candidates.extend([f'{source_basename}.nfo', f'{source_basename}.xml'])
    for entry in candidates:
        path = os.path.join(nfo_dir, entry)
        if os.path.isfile(path):
            nfo_file = path
            break
    if not nfo_file:
        return {}

    try:
        with open(nfo_file, 'r', encoding='utf-8', errors='replace') as fh:
            contents = fh.read()
    except OSError:
        return {}

    metadata = {}
    if contents.lstrip().startswith('<?xml') or '<episodedetails' in contents.lower():
        metadata = parse_nfo_xml(contents)
        if metadata:
            return metadata

    for line in contents.splitlines():
        line = line.strip()
        if not line or line.startswith(';'):
            continue
        match = re.match(r'^\s*([^:=]+?)\s*[:=]\s*(.+)$', line)
        if not match:
            continue

        key = match.group(1).strip().lower()
        value = match.group(2).strip()
        if not value:
            continue

        normalized = re.sub(r'[\s_\-]+', '', key)
        if normalized in ('title', 'name'):
            metadata['title'] = value
        elif normalized in ('plot', 'description', 'synopsis'):
            if 'description' in metadata:
                metadata['description'] += ' / ' + value
            else:
                metadata['description'] = value
        elif normalized in ('comment', 'comments'):
            if 'comment' in metadata:
                metadata['comment'] += ' / ' + value
            else:
                metadata['comment'] = value
        elif normalized in ('imdbid', 'imdb', 'imdbidnumber'):
            metadata['imdb_id'] = value
        elif normalized in ('actors', 'actor', 'cast', 'stars', 'starring'):
            existing = metadata.get('actors')
            if existing:
                metadata['actors'] = existing + ', ' + value
            else:
                metadata['actors'] = value
        elif normalized in ('year', 'released', 'releaseyear'):
            metadata['date'] = value
        elif normalized in ('genre', 'genres'):
            metadata['genre'] = value
        elif normalized in ('director', 'directors'):
            metadata['director'] = value

    if 'imdb_id' not in metadata:
        imdb_match = re.search(r'\btt\d{7,8}\b', contents, re.IGNORECASE)
        if imdb_match:
            metadata['imdb_id'] = imdb_match.group(0)

    return metadata


def parse_tv_filename(stem):
    """
    Detect whether *stem* (filename without extension) matches the TV show
    naming pattern ``Series Name - S01E02 - Episode Title`` and, if so,
    return a dict with keys:

        collection    – series / show name
        season        – season number as a bare integer string (no leading zeros)
        episode       – episode number as a bare integer string (no leading zeros)
        episode_title – episode title (may be empty string)

    Returns None when no SxxExx token is found in the stem.
    """
    # Match SxxExx anywhere in the filename
    se_match = re.search(r'[Ss](\d+)[Ee](\d+)', stem)
    if not se_match:
        return None

    season  = str(int(se_match.group(1)))   # strip leading zeros
    episode = str(int(se_match.group(2)))

    # Try to parse "Series - S01E02 - Episode Title" (New-Tags3 convention)
    parts = re.split(r'\s+-\s+', stem, maxsplit=2)
    if len(parts) >= 2:
        collection    = parts[0].strip()
        episode_title = parts[2].strip() if len(parts) == 3 else ''
    else:
        # Fallback: everything before the SxxExx token is the series name
        collection    = stem[:se_match.start()].strip(' -_')
        episode_title = stem[se_match.end():].strip(' -_')

    return {
        'collection':    collection,
        'season':        season,
        'episode':       episode,
        'episode_title': episode_title,
    }


def get_tvshow_name(input_file):
    """
    Walk up the directory tree from input_file looking for a tvshow.nfo.
    If found, parse and return the <title> element (the series name).
    Returns None if not found or unparseable.
    """
    directory = os.path.dirname(os.path.abspath(input_file))
    # Search current dir plus up to 3 parent levels (Season → Series → ...)
    for _ in range(4):
        candidate = os.path.join(directory, 'tvshow.nfo')
        if os.path.isfile(candidate):
            try:
                with open(candidate, 'r', encoding='utf-8', errors='replace') as fh:
                    contents = fh.read()
                root = ET.fromstring(contents)
                title = root.findtext('.//title')
                if title and title.strip():
                    return title.strip()
            except (OSError, ET.ParseError):
                pass
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return None


def get_nfo_metadata_for_file(input_file):
    parent_dir = os.path.dirname(input_file)
    basename = os.path.splitext(os.path.basename(input_file))[0]
    metadata = parse_nfo_metadata(parent_dir, basename)
    if metadata:
        return metadata

    nfo_dir = os.path.join(parent_dir, '.nfo')
    if os.path.isdir(nfo_dir):
        metadata = parse_nfo_metadata(nfo_dir, basename)
    return metadata


def timecode_to_seconds(timecode):
    try:
        parts = timecode.split(':')
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    except Exception:
        return 0.0


def format_duration(seconds):
    if seconds is None:
        return 'unknown'
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def _iter_lines_raw(fd):
    """Read raw output from a file descriptor and split on CR or LF immediately."""
    buf = b''
    while True:
        try:
            raw = os.read(fd, 512)
        except OSError:
            break
        if not raw:
            break
        buf += raw
        while True:
            cr = buf.find(b'\r')
            nl = buf.find(b'\n')
            if cr == -1 and nl == -1:
                break
            if cr == -1:
                idx, skip = nl, 1
            elif nl == -1:
                idx, skip = cr, 1
            else:
                if cr < nl:
                    idx, skip = cr, 2 if nl == cr + 1 else 1
                else:
                    idx, skip = nl, 1
            line = buf[:idx].decode('utf-8', errors='replace')
            buf = buf[idx + skip:]
            if line.strip():
                yield line
    if buf.strip():
        yield buf.decode('utf-8', errors='replace')


def print_progress(current, total, elapsed):
    bar_width = 30
    if total:
        percent = min(max(current / total, 0.0), 1.0)
        filled = int(percent * bar_width)
        bar = '#' * filled + '-' * (bar_width - filled)
        status = f"[{bar}] {percent * 100:5.1f}%"
    else:
        bar = '#' * (bar_width // 2) + '-' * (bar_width - bar_width // 2)
        status = f"[{bar}] {format_duration(current)}"
    sys.stdout.write(f"{colors.gray}Progress.......:\t{colors.orange}{status}{colors.medium_purple_3b}  elapsed {format_duration(elapsed)}\r{colors.reset}")
    sys.stdout.flush()


def check_dependencies():
    for tool in ('ffmpeg', 'ffprobe'):
        if not shutil.which(tool):
            print(f"{colors.BoRed}Error: {tool} is not installed or not in your PATH.{colors.reset}")
            sys.exit(1)


def decode_check(output_file, duration=None):
    """
    Perform a full decode pass on output_file using 'ffmpeg -f null'.

    Every frame of every stream (video, audio, subtitles) is decoded and
    discarded. No output file is written and no display window is opened.
    ffmpeg emits any bitstream-level errors to stderr at 'error' log level;
    those are collected and returned.

    A live progress bar is displayed, using the known duration if available.

    Returns:
        (passed: bool, error_lines: list[str])
        passed is True only when ffmpeg produced zero error-level messages.
    """
    error_lines = []
    try:
        proc = subprocess.Popen(
            ['ffmpeg', '-v', 'error', '-stats',
             '-i', output_file, '-map', '0', '-f', 'null', '-'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        start_time = time.time()
        bar_width = 30
        for line in _iter_lines_raw(proc.stderr.fileno()):
            if 'time=' in line:
                match = re.search(r'time=\s*(\d+:\d+:\d+(?:\.\d+)?)', line)
                if match:
                    current = timecode_to_seconds(match.group(1))
                    elapsed = time.time() - start_time
                    if duration:
                        percent = min(max(current / duration, 0.0), 1.0)
                        filled = int(percent * bar_width)
                        bar = '#' * filled + '-' * (bar_width - filled)
                        status = f"[{bar}] {percent * 100:5.1f}%"
                    else:
                        bar = '#' * (bar_width // 2) + '-' * (bar_width - bar_width // 2)
                        status = f"[{bar}] {format_duration(current)}"
                    sys.stdout.write(
                        f"{colors.Lblue}Decode check...:\t{colors.orange}{status}"
                        f"  elapsed {format_duration(elapsed)}\r{colors.reset}"
                    )
                    sys.stdout.flush()
            else:
                if line.strip():
                    error_lines.append(line)
        proc.wait()
        sys.stdout.write('\n')
        sys.stdout.flush()
        return (len(error_lines) == 0), error_lines
    except Exception as e:
        sys.stdout.write('\n')
        sys.stdout.flush()
        return False, [str(e)]


def validate_output(input_file, output_file, input_duration):
    """
    Quick sanity-check the output file before considering the conversion done.

    Checks (in order):
      1. Output file exists.
      2. Output file is non-empty.
      3. Output size >= 90% of input size (guards against truncated writes).
      4. Output duration is within max(2 s, 1% of input duration) of the
         input duration (guards against missing streams or corrupt containers).

    Returns:
        (valid: bool, reason: str | None)
        reason is None on success, or a human-readable failure description.
    """
    if not os.path.exists(output_file):
        return False, 'output file does not exist'
    output_size = os.path.getsize(output_file)
    if output_size == 0:
        return False, 'output file is empty'
    input_size = os.path.getsize(input_file)
    if input_size > 0 and output_size < input_size * 0.90:
        return False, f'output size ({format_bytes(output_size)}) is less than 90% of input ({format_bytes(input_size)})'
    output_duration = get_duration_seconds(output_file)
    if input_duration is not None and output_duration is not None:
        diff = abs(output_duration - input_duration)
        if diff > max(2.0, input_duration * 0.01):
            return False, (f'duration mismatch: input {format_duration(input_duration)} '
                           f'vs output {format_duration(output_duration)}')
    return True, None


def prompt_press_any_key():
    if not sys.stdin.isatty():
        return
    try:
        input(f"{colors.yellow}Press Enter to continue...{colors.reset}")
    except (EOFError, KeyboardInterrupt):
        pass


def scan_source_files(source, depth=0):
    source = os.path.abspath(source)
    if os.path.isfile(source):
        return [(source, os.path.dirname(source))]

    files = []
    if depth == 0:
        for entry in os.listdir(source):
            if entry.lower().endswith(VIDEO_EXTENSIONS):
                files.append((os.path.join(source, entry), source))
    else:
        root_depth = source.rstrip(os.sep).count(os.sep)
        for root, dirs, filenames in os.walk(source):
            current_depth = root.count(os.sep) - root_depth
            if current_depth > depth:
                dirs[:] = []
                continue
            for entry in filenames:
                if entry.lower().endswith(VIDEO_EXTENSIONS):
                    files.append((os.path.join(root, entry), source))
    files.sort(key=lambda pair: natural_sort_key(os.path.basename(pair[0])))
    return files


def convert(source, dest, depth=0, replace=False, max_verify=False):
    if isinstance(source, list):
        sources = source
    else:
        sources = [source]
    return convert_sources(sources, dest, depth=depth,
                           replace=replace, max_verify=max_verify)


def convert_sources(sources, dest, depth=0, replace=False, max_verify=False):
    resolved_files = []
    failures = []

    for source in sources:
        if os.path.isfile(source):
            resolved_files.append((os.path.abspath(source), os.path.dirname(os.path.abspath(source))))
        elif os.path.isdir(source):
            resolved_files.extend(scan_source_files(source, depth))
        else:
            print(f"{colors.BoRed}Error: Source does not exist: {source}{colors.reset}")
            failures.append((source, 'source does not exist'))

    if not resolved_files:
        if not failures:
            print(f"{colors.yellow}No matching files found in: {sources}{colors.reset}")
        return failures

    if dest is not None:
        dest = os.path.abspath(dest)
        os.makedirs(dest, exist_ok=True)

    total = len(resolved_files)
    print(f"{colors.Bogreen}Files to be converted:{colors.reset}")
    for f, _ in resolved_files:
        print(f"  {colors.Lblue}{os.path.basename(f)}{colors.reset}")
    print('=' * 80)

    for i, (input_file, source_root) in enumerate(resolved_files, 1):
        filename = os.path.basename(input_file)
        if dest is None:
            out_dir = os.path.dirname(input_file)
        else:
            rel_dir = os.path.relpath(os.path.dirname(input_file), source_root)
            out_dir = os.path.normpath(os.path.join(dest, rel_dir)) if rel_dir != '.' else dest
        os.makedirs(out_dir, exist_ok=True)
        output_file = os.path.join(out_dir, f"{os.path.splitext(filename)[0]}.mkv")

        print(f"{colors.pink}File {i} of {total}\t{colors.reset}")
        print(f"{colors.Lblue}Input..........:\t{filename}{colors.reset}")
        print(f"{colors.rblue}Output.........:\t{os.path.basename(output_file)}{colors.reset}")
        print(f"{colors.Lblue}Input Dir......:\t{os.path.dirname(input_file)}{colors.reset}")
        print(f"{colors.Lblue}Output Dir.....:\t{out_dir}{colors.reset}")

        input_size = os.path.getsize(input_file)

        # Skip only if output exists AND is at least 95% the size of the input.
        # A simple non-zero check is not enough — a partial file could be many MB
        # yet still be incomplete. Since this is a lossless remux the output
        # should be very close in size to the source.
        if os.path.exists(output_file):
            output_existing_size = os.path.getsize(output_file)
            if input_size > 0 and output_existing_size >= input_size * 0.95:
                print(f"{colors.yellow}Already exists — skipping.{colors.reset}")
                if replace:
                    try:
                        os.remove(input_file)
                        print(f"{colors.lmagenta}Deleted original: {filename}{colors.reset}")
                    except OSError as rm_err:
                        print(f"{colors.BoRed}Could not delete original: {rm_err}{colors.reset}")
                print()
                continue
            elif output_existing_size > 0:
                print(f"{colors.yellow}Incomplete output found ({format_bytes(output_existing_size)}) — reconverting.{colors.reset}")
            else:
                print(f"{colors.yellow}Empty output file found — reconverting.{colors.reset}")

        print(f"{colors.Lblue}Input Size.....:\t{format_bytes(input_size)}{colors.reset}")

        duration = get_duration_seconds(input_file)
        if duration is not None:
            print(f"{colors.Lblue}Duration.......:\t{format_duration(duration)}{colors.reset}")

        nfo_metadata = get_nfo_metadata_for_file(input_file)
        if nfo_metadata:
            print(f"{colors.orange}NFO metadata found:{colors.reset}")
            subsequent_indent = ' ' * 24  # aligns with text after 15-char label + ':' + tab
            for meta_key in sorted(nfo_metadata):
                value = nfo_metadata[meta_key]
                label = meta_key.replace('_', ' ').title().replace(' ', '_').ljust(15, '.')
                if meta_key == 'description':
                    lines = textwrap.wrap(value, width=56)
                    desc_text = ('\n' + subsequent_indent).join(lines)
                    print(f"{colors.gray}{label}:{colors.reset}\t{colors.deep_sky_blue_4c}{desc_text}{colors.reset}")
                else:
                    print(f"{colors.gray}{label}:{colors.reset}\t{colors.yellow}{value}{colors.reset}")

        cmd = [
            'ffmpeg', '-y',
            '-hide_banner',
            '-loglevel', 'info',
            '-i', input_file,
            '-map', '0:v?',   # all video streams incl. cover art (was 0:V which dropped cover art)
            '-map', '0:a?',   # all audio streams
            '-map', '0:s?',   # all subtitle streams
            '-map', '0:t?',   # attachments (fonts, etc.)
            '-map_metadata', '0',
            '-map_chapters', '0',
            '-c', 'copy',
        ]

        stem    = os.path.splitext(filename)[0]
        tv_info = parse_tv_filename(stem)

        # Also treat as TV if the NFO carries a TVDB ID (e.g. filename lacks
        # SxxExx but Jellyfin/Kodi wrote episodedetails with <uniqueid type="tvdb">).
        if tv_info is None and nfo_metadata and nfo_metadata.get('tvdb_id'):
            tvshow_name = get_tvshow_name(input_file)
            tv_info = {
                'collection':    tvshow_name or stem,
                'season':        nfo_metadata.get('season', ''),
                'episode':       nfo_metadata.get('episode', ''),
                'episode_title': nfo_metadata.get('title', ''),
            }

        is_tv = tv_info is not None

        if is_tv:
            se_str = ''
            s, e = tv_info.get('season', ''), tv_info.get('episode', '')
            if s and e:
                se_str = f", S{s.zfill(2)}E{e.zfill(2)}"
            print(f"{colors.magenta_2a}TV show detected — applying TV tags "
                  f"(Collection={tv_info['collection']!r}{se_str}){colors.reset}")
            # ---- TV show tagging (New-Tags3 style) -------------------------
            # Filename-derived tags are always applied; NFO enriches Comment
            # and Released_Date when available.
            tv_tags = {
                'Title':         stem,
                'Collection':    tv_info.get('collection', ''),
                'Season':        tv_info.get('season', ''),
                'Episode':       tv_info.get('episode', ''),
                'Movie':         tv_info.get('episode_title', ''),
                'Comment':       (nfo_metadata or {}).get('description', ''),
                'Released_Date': (nfo_metadata or {}).get('date', ''),
                'tvdb_id':       (nfo_metadata or {}).get('tvdb_id', ''),
            }
            for tag, value in tv_tags.items():
                if not value:
                    continue
                value = ' '.join(value.splitlines()).strip()
                cmd.extend(['-metadata', f'{tag}={value}'])
        elif nfo_metadata:
            # ---- Standard (movie / generic) tagging -----------------------
            metadata_keys = {
                'title':       'title',
                'description': 'description',
                'comment':     'comment',
                'imdb_id':     'IMDB_ID',
                'tvdb_id':     'tvdb_id',
                'actors':      'actor',
                'director':    'director',
                'genre':       'genre',
                'date':        'date',
            }
            for key, tag in metadata_keys.items():
                value = nfo_metadata.get(key)
                if not value:
                    continue
                value = ' '.join(value.splitlines()).strip()
                cmd.extend(['-metadata', f'{tag}={value}'])

        cmd.append(output_file)
        start_time = time.time()
        current_time = 0.0
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            if proc.stdout is None:
                raise RuntimeError('Failed to capture ffmpeg progress output')

            output_lines = collections.deque(maxlen=20)
            for line in _iter_lines_raw(proc.stdout.fileno()):
                output_lines.append(line)
                if 'time=' not in line:
                    continue
                match = re.search(r'time=\s*(\d+:\d+:\d+(?:\.\d+)?)', line)
                if not match:
                    continue
                current_time = timecode_to_seconds(match.group(1))
                print_progress(current_time, duration, time.time() - start_time)
            proc.wait()
            elapsed = time.time() - start_time
            sys.stdout.write('\n')
            sys.stdout.flush()

            if proc.returncode != 0:
                stderr = '\n'.join(output_lines[-20:])
                raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=stderr)

            output_size = os.path.getsize(output_file)
            print(f"{colors.Lblue}Output Size....:\t{format_bytes(output_size)}{colors.reset}")
            valid, reason = validate_output(input_file, output_file, duration)
            if not valid:
                print(f"{colors.BoRed}Validation failed: {reason}{colors.reset}")
                if os.path.exists(output_file):
                    try:
                        os.remove(output_file)
                    except OSError as rm_err:
                        print(f"{colors.BoRed}Could not remove bad output file: {rm_err}{colors.reset}")
                failures.append((filename, reason))
                print(f"{colors.yellow}Keeping original file.{colors.reset}\n")
                prompt_press_any_key()
                print()
                continue
            conversion_ok = True
            if not max_verify:
                print(f"{colors.green}Integrity check:\tPASSED (size + duration OK){colors.reset}")
            if max_verify:
                decode_ok, decode_errors = decode_check(output_file, duration)
                elapsed = time.time() - start_time
                if not decode_ok:
                    conversion_ok = False
                    print(f"{colors.BoRed}Decode check...:\tFAILED            {colors.reset}")
                    for err in decode_errors[:10]:
                        print(f"{colors.red}  {err}{colors.reset}")
                    if os.path.exists(output_file):
                        try:
                            os.remove(output_file)
                        except OSError as rm_err:
                            print(f"{colors.BoRed}Could not remove bad output file: {rm_err}{colors.reset}")
                    failures.append((filename, 'decode check failed'))
                    print(f"{colors.yellow}Keeping original file.{colors.reset}\n")
                    prompt_press_any_key()
                    print()
                    continue
                else:
                    print(f"{colors.green}Decode check...:\tOK                {colors.reset}")
            if conversion_ok:
                print(f"{colors.gray}Elapsed........:\t{colors.Lblue}{format_duration(elapsed)}{colors.reset}")
                if replace:
                    try:
                        os.remove(input_file)
                        print(f"{colors.lmagenta}Deleted original: {filename}{colors.reset}")
                    except OSError as rm_err:
                        print(f"{colors.BoRed}Could not delete original: {rm_err}{colors.reset}")
                print()
        except KeyboardInterrupt:
            sys.stdout.write('\033[2K\033[0m\033[?25h\n')
            sys.stdout.flush()
            if proc is not None and proc.poll() is None:
                proc.kill()
            if os.path.exists(output_file):
                os.remove(output_file)
            print(f"{colors.BoRed}Interrupted by user. Removed incomplete output file: {output_file}{colors.reset}\n")
            return failures
        except subprocess.CalledProcessError as e:
            print(f"{colors.BoRed}Error converting {filename} (exit code {e.returncode}){colors.reset}")
            if e.stderr:
                err_lines = e.stderr.strip().splitlines()
                for line in err_lines[-5:]:
                    print(f"{colors.red}  {line}{colors.reset}")
            if os.path.exists(output_file):
                try:
                    os.remove(output_file)
                except OSError:
                    pass
            failures.append((filename, f'ffmpeg failed (exit code {e.returncode})'))
            prompt_press_any_key()
            print()
        except Exception as e:
            print(f"{colors.BoRed}Error converting {filename}: {e}{colors.reset}")
            if os.path.exists(output_file):
                try:
                    os.remove(output_file)
                except OSError:
                    pass
            failures.append((filename, str(e)))
            prompt_press_any_key()
            print()

        if total > 1:
            print(f"{colors.gray}{'=' * 70}{colors.reset}")

    if failures:
        print(f"{colors.BoRed}Conversion summary: {len(failures)} failed file(s){colors.reset}")
        for failed_file, reason in failures:
            print(f"  {colors.red}{failed_file}:{colors.reset} {reason}")
        print('=' * 80)
        return failures

    print('=' * 80)
    return failures


def main():
    if sys.stdout.isatty():
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

    check_dependencies()

    parser = argparse.ArgumentParser(
        usage='%(prog)s [options] <source> [<source> ...] [--dest DEST]',
        description=(
            "Remux video files into an MKV container using stream copy (no re-encoding).\n"
            "All video, audio, subtitle, and attachment streams are preserved exactly.\n"
            "Metadata from a matching .nfo or .xml sidecar file is embedded when found."
        ),
        epilog=(
            "Examples:\n"
            "  convert_to_mkv.py /path/to/source --dest /path/to/dest\n"
            "  convert_to_mkv.py /path/to/file.mp4 --dest /path/to/dest\n"
            "  convert_to_mkv.py /path/to/source --dest /path/to/dest --depth 2\n"
            "  convert_to_mkv.py /path/to/source          (dest defaults to source dir)\n"
            "  convert_to_mkv.py /src /dest --replace      (delete originals after conversion)\n"
            "  convert_to_mkv.py /src /dest --replace --max-verify  (full decode + delete)\n"
            "  fd -e mp4 -X convert_to_mkv.py {}          (batch files from fd)\n"
            "\n"
            "NFO sidecar lookup:\n"
            "  The script looks for <basename>.nfo or <basename>.xml in the same directory\n"
            "  as the source file, then in a .nfo/ sub-directory. Only files whose stem\n"
            "  exactly matches the source filename are used — unrelated .nfo files in the\n"
            "  same folder are ignored.\n"
            "\n"
            "Output validation (always performed):\n"
            "  - Output file exists and is non-empty\n"
            "  - Output size is at least 90%% of the input size\n"
            "  - Duration matches input within 1%% or 2 seconds (whichever is larger)\n"
            "\n"
            "Note on NFS / network destinations:\n"
            "  ffmpeg's progress reflects data processed, not data flushed to disk.\n"
            "  On NFS the progress bar may reach ~90%% quickly then appear to stall\n"
            "  while the OS drains its write buffer to the network share. This is normal."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('sources', nargs='+',
                        help='Source video file(s) or directory(ies) containing video files.')
    parser.add_argument('--dest', default=None,
                        help=('Shared destination directory for converted MKV files. '
                              'When omitted, each output file is written to its source directory.'))
    parser.add_argument('--depth', type=int, default=0,
                        help=('How many directory levels deep to search for video files. '
                              '0 = top-level only, 1 = one sub-directory level, etc. Default: 0'))
    parser.add_argument('--replace', action='store_true', default=False,
                        help=('Delete the original source file after a successful, validated '
                              'conversion. The file is only removed when all output checks pass. '
                              'If --max-verify is also set, deletion is deferred until the full '
                              'decode pass completes without errors.'))
    parser.add_argument('--max-verify', action='store_true', default=False,
                        help=('After conversion, run a full silent decode pass on the output '
                              'file using ffmpeg -f null to detect bitstream-level errors. '
                              'This reads and decodes every frame of every stream, so it takes '
                              'roughly as long as the conversion itself. Recommended when using '
                              '--replace on important files or when the destination is a '
                              'network share.'))
    args = parser.parse_args()

    sources = args.sources
    dest = args.dest

    if dest is not None:
        dest = os.path.abspath(dest)

    all_failures = convert(sources, dest, depth=args.depth,
                            replace=args.replace, max_verify=args.max_verify)
    if all_failures:
        print(f"{colors.BoRed}Batch summary: {len(all_failures)} failed file(s){colors.reset}")
        for failed_file, reason in all_failures:
            print(f"  {colors.red}{failed_file}:{colors.reset} {reason}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stdout.write('\033[2K\033[0m\033[?25h\n')
        sys.stdout.flush()
