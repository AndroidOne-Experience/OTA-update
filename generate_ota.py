#!/usr/bin/env python3
"""Generate AndroidOne OTA metadata and upload builds to OneDrive."""

import sys
import json
import re
import time
import hashlib
import argparse
import threading
import math
import tempfile
import os
import codecs
import termios
import tty
from email.utils import parsedate_to_datetime
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
import requests
import msal

# Terminal output and transfer settings.
YELLOW = '\x1b[93m'
WHITE = '\x1b[97m'
GREEN = '\x1b[92m'
RED = '\x1b[91m'
BLUE = '\x1b[94m'
RESET = '\x1b[0m'
CLEAR_LINE = '\x1b[2K'
CURSOR_UP = '\x1b[1A'
SEPARATOR = '-' * 101
GRAPH_BASE_URL = 'https://graph.microsoft.com/v1.0'
# Keep fragments sequential and aligned to 320 KiB (40 MiB each).
CHUNK_SIZE = 128 * 320 * 1024
MAX_RETRIES = 5
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 300
TOKEN_EXPIRY_BUFFER = 300
PROGRESS_REFRESH_INTERVAL = 0.2
RETRY_STATUSES = {408, 429, 500, 502, 503, 504}

def clear_previous_line():
    if sys.stdout.isatty():
        print(f'{CURSOR_UP}{CLEAR_LINE}', end='', flush=True)

def clear_current_line():
    if sys.stdout.isatty():
        print(f'\r{CLEAR_LINE}', end='', flush=True)

def human_size(size):
    if size < 1024:
        return f'{size} B'
    if size < 1024 ** 2:
        return f'{size / 1024:.2f} KB'
    if size < 1024 ** 3:
        return f'{size / 1024 ** 2:.2f} MB'
    return f'{size / 1024 ** 3:.2f} GB'

def terminal_ota_size(size):
    return f'{size / 1024 ** 3:.1f} GB'

def format_duration(seconds):
    seconds = max(0, int(seconds))
    (hours, remainder) = divmod(seconds, 3600)
    (minutes, seconds) = divmod(remainder, 60)
    if hours:
        return f'{hours}h {minutes}m {seconds}s'
    if minutes:
        return f'{minutes}m {seconds}s'
    return f'{seconds}s'

def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('device', nargs='?', help='Device codename')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--upload', action='store_true', help='Upload OTA/recovery only')
    mode.add_argument('--json', action='store_true', help='Generate JSON only')
    return parser.parse_args()

def get_device_codename(args):
    if args.device:
        device = args.device.strip()
    else:
        device = input(f'{YELLOW}Enter device codename:{RESET} ').strip()
        clear_previous_line()
    if not device:
        print(f'{RED}Error: Device codename cannot be empty.{RESET}')
        sys.exit(1)
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*', device):
        raise RuntimeError('Invalid device codename: use letters, digits, _, . or -.')
    return device

def read_token_config(script_dir):
    token_file = script_dir / 'token.txt'
    config = dict.fromkeys(('ANDROID', 'TENANT_ID', 'CLIENT_ID', 'CLIENT_SECRET',
                            'DRIVE_ID', 'FOLDER_ID'))
    if not token_file.is_file():
        return config
    try:
        with token_file.open('r', encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                (key, value) = line.split('=', 1)
                key = key.strip()
                value = value.strip()
                if key in config:
                    config[key] = value if value else None
    except OSError as error:
        raise RuntimeError(f'Failed to read token.txt: {error}') from error
    return config

def get_missing_onedrive_values(token_config):
    required = ('TENANT_ID', 'CLIENT_ID', 'CLIENT_SECRET', 'DRIVE_ID', 'FOLDER_ID')
    return [key for key in required if not token_config.get(key)]

def show_missing_token_error(missing_values):
    missing_text = ', '.join(missing_values)
    print(f'{YELLOW}Required OneDrive credentials are missing from token.txt.{RESET}')
    print(f'{RED}The following values are required to upload OTA files to OneDrive cloud storage:{RESET}')
    print(f'{WHITE}"{missing_text}"{RESET}')

def show_token_tip():
    print(f'{YELLOW}Tip: token.txt requires these details:{RESET}')
    print()
    print(f'{YELLOW}ANDROID=XX{RESET}')
    print()
    print(f'{YELLOW}TENANT_ID=xxxxxxxxxxxxxxxx{RESET}')
    print(f'{YELLOW}CLIENT_ID=xxxxxxxxxxxxxxxx{RESET}')
    print(f'{YELLOW}CLIENT_SECRET=xxxxxxxxxxxxxxxx{RESET}')
    print(f'{YELLOW}DRIVE_ID=xxxxxxxxxxxxxxxx{RESET}')
    print(f'{YELLOW}FOLDER_ID=xxxxxxxxxxxxxxxx{RESET}')

def masked_secret(prompt):
    """Read a terminal secret with visible masks and restore terminal settings."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError('Masked secret entry requires an interactive terminal. '
                           'Set CLIENT_SECRET in token.txt instead.')
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    characters = []
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    escape = ''
    print(prompt, end='', flush=True)
    try:
        tty.setraw(fd)
        while True:
            raw = os.read(fd, 1)
            if not raw:
                raise EOFError
            char = decoder.decode(raw)
            if not char:
                continue
            if char == '\x03':
                raise KeyboardInterrupt
            if char == '\x04':
                raise EOFError
            # Ignore cursor keys and bracketed-paste delimiters, not their payload.
            if escape:
                escape += char
                if len(escape) == 2 and char not in '[O':
                    escape = ''
                elif len(escape) > 2 and '@' <= char <= '~':
                    escape = ''
                continue
            if char == '\x1b':
                escape = char
            elif char in ('\r', '\n'):
                return ''.join(characters).strip()
            elif char in ('\x7f', '\b'):
                if characters:
                    characters.pop()
                    print('\b \b', end='', flush=True)
            elif char == '\x15':  # Ctrl+U clears the entry.
                print('\b \b' * len(characters), end='', flush=True)
                characters.clear()
            elif char.isprintable():
                characters.append(char)
                print('*', end='', flush=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)
        print()


def prompt_config_value(key):
    """Require a nonempty value; never fall back to echoing the client secret."""
    while True:
        prompt = f'{YELLOW}Enter {key}:{RESET} '
        if key == 'CLIENT_SECRET':
            value = masked_secret(prompt)
        else:
            value = input(prompt).strip()
        if not value:
            print(f'{YELLOW}{key} cannot be empty. Press Ctrl+C to cancel.{RESET}')
            continue
        if key == 'ANDROID' and not re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', value):
            print(f'{YELLOW}Enter an Android version such as 16 or 16.1.{RESET}')
            continue
        return value


def complete_token_config(config, upload):
    # A temporary terminal screen keeps long/pasted credentials out of the main
    # screen and scrollback, including when entry is cancelled or raises an error.
    missing = not config['ANDROID'] or (upload and get_missing_onedrive_values(config))
    temporary_screen = missing and sys.stdin.isatty() and sys.stdout.isatty()
    if temporary_screen:
        print('\033[?1049h\033[H\033[2J', end='', flush=True)
    try:
        return collect_token_config(config, upload)
    finally:
        if temporary_screen:
            print('\033[H\033[2J\033[?1049l', end='', flush=True)


def collect_token_config(config, upload):
    """Collect missing values in memory, leaving existing token.txt values intact."""
    if not config['ANDROID']:
        print(f'{YELLOW}ANDROID version was not found in token.txt.{RESET}')
        config['ANDROID'] = prompt_config_value('ANDROID')
    missing = get_missing_onedrive_values(config)
    if not upload or not missing:
        return upload
    show_missing_token_error(missing)
    while True:
        choice = input(
            'Would you like to input the credential details manually? [y/n]: '
        ).strip().lower()
        if choice in ('n', 'no'):
            return False
        if choice in ('y', 'yes'):
            break
        print(f'{YELLOW}Please enter y or n.{RESET}')
    print(f'{YELLOW}Values entered here are used for this run only.{RESET}')
    for key in missing:
        config[key] = prompt_config_value(key)
    return True

def walk_upwards(path):
    current = path.resolve()
    while True:
        yield current
        if current.parent == current:
            break
        current = current.parent

def find_aosp_root(device):
    script_dir = Path(__file__).resolve().parent
    cwd = Path.cwd().resolve()
    checked = set()
    for start in (cwd, script_dir):
        for directory in walk_upwards(start):
            if directory in checked:
                continue
            checked.add(directory)
            product_dir = directory / 'out' / 'target' / 'product' / device
            if product_dir.is_dir():
                return directory
    print(f'{RED}Error: Could not locate AOSP build root.{RESET}')
    print(f'Expected to find: out/target/product/{device}/')
    sys.exit(1)

def find_latest_ota(aosp_root, device):
    product_dir = aosp_root / 'out' / 'target' / 'product' / device
    pattern = re.compile(f'^AndroidOne-{re.escape(device)}-OTA-(\\d{{8}})-(\\d{{4}})\\.zip$', re.IGNORECASE)
    ota_files = []
    for file in product_dir.iterdir():
        match = pattern.fullmatch(file.name)
        if not match or not file.is_file():
            continue
        try:
            timestamp = datetime.strptime(match.group(1) + match.group(2), '%Y%m%d%H%M')
        except ValueError:
            continue
        ota_files.append((timestamp, file))
    if not ota_files:
        print(f'{RED}Error: No valid AndroidOne OTA found.{RESET}')
        print('Expected filename:')
        print(f'AndroidOne-{device}-OTA-YYYYMMDD-HHMM.zip')
        sys.exit(1)
    newest_time = max((item[0] for item in ota_files))
    newest = [item[1] for item in ota_files if item[0] == newest_time]
    if len(newest) != 1:
        print(f'{RED}Error: Multiple OTA files have the same latest timestamp.{RESET}')
        sys.exit(1)
    return newest[0]

def get_build_datetime(aosp_root, device):
    build_prop = aosp_root / 'out' / 'target' / 'product' / device / 'system' / 'build.prop'
    if not build_prop.is_file():
        raise RuntimeError(f'system/build.prop not found: {build_prop}')
    try:
        with build_prop.open('r', encoding='utf-8-sig', errors='replace') as f:
            for line in f:
                line = line.strip()
                if line.startswith('ro.build.date.utc='):
                    value = line.split('=', 1)[1].strip()
                    if not value:
                        raise RuntimeError('ro.build.date.utc is empty.')
                    if not re.fullmatch(r'[0-9]+', value):
                        raise RuntimeError('ro.build.date.utc contains an invalid value.')
                    return value
    except OSError as error:
        raise RuntimeError(f'Failed to read build.prop: {error}')
    raise RuntimeError('ro.build.date.utc was not found.')

def file_signature(file_path):
    stat = file_path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def calculate_sha256(file_path):
    checksum = hashlib.sha256()
    before = file_signature(file_path)
    with file_path.open('rb') as f:
        buffer = bytearray(1024 * 1024)
        view = memoryview(buffer)
        while (count := f.readinto(buffer)):
            checksum.update(view[:count])
    if file_signature(file_path) != before:
        raise RuntimeError('OTA file changed while calculating SHA-256.')
    return checksum.hexdigest()

def get_ota_url(device, android_version, ota_file):
    if not android_version:
        return None
    return ('https://ota.androidone.workers.dev/api/'
            f'{quote(device, safe="")}/android-{quote(android_version, safe="")}/'
            f'{quote(ota_file.name, safe="")}')

def generate_json(script_dir, device, ota_file, build_datetime, ota_hash,
                  ota_size, ota_url, android_version):
    devices_dir = script_dir / 'devices'
    devices_dir.mkdir(parents=True, exist_ok=True)
    json_path = devices_dir / f'{device}.json'
    data = {'response': [{
        'datetime': build_datetime,
        'filename': ota_file.name,
        'id': ota_hash,
        'size': ota_size,
        'url': ota_url,
        'version': android_version,
    }]}
    # Replace only after a complete write; leave existing metadata intact on failure.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=devices_dir, delete=False) as f:
            temporary = Path(f.name)
            json.dump(data, f, indent=2)
            f.write('\n')
        temporary.chmod(json_path.stat().st_mode & 0o777 if json_path.exists() else 0o644)
        temporary.replace(json_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return json_path

class GraphClient:
    def __init__(self, config):
        self.drive_id = config['DRIVE_ID']
        self.root_folder_id = config['FOLDER_ID']
        self.app = msal.ConfidentialClientApplication(
            config['CLIENT_ID'],
            authority=f"https://login.microsoftonline.com/{config['TENANT_ID']}",
            client_credential=config['CLIENT_SECRET'],
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        self.session = requests.Session()
        # Upload URLs are preauthenticated. Never attach the Graph bearer token.
        self.upload_session = requests.Session()
        self.token = None
        self.expiry = 0

    def close(self):
        self.session.close()
        self.upload_session.close()

    def ensure_token(self, force=False):
        if not force and self.token and time.monotonic() + TOKEN_EXPIRY_BUFFER < self.expiry:
            return
        if force or self.token:
            # Expiring/rejected tokens must not be returned from MSAL's cache.
            self.app.remove_tokens_for_client()
        result = self.app.acquire_token_for_client(
            scopes=['https://graph.microsoft.com/.default'])
        if not result.get('access_token'):
            raise RuntimeError(result.get('error_description', 'Microsoft authentication failed.'))
        self.token = result['access_token']
        self.expiry = time.monotonic() + result.get('expires_in', 3600)
        self.session.headers['Authorization'] = f'Bearer {self.token}'

    def request(self, method, endpoint, **kwargs):
        self.ensure_token()
        url = endpoint if endpoint.startswith('http') else GRAPH_BASE_URL + endpoint
        kwargs.setdefault('timeout', (CONNECT_TIMEOUT, READ_TIMEOUT))
        refreshed = False
        for attempt in range(1, MAX_RETRIES + 1):
            response = None
            try:
                response = self.session.request(method, url, **kwargs)
                if response.status_code == 401 and not refreshed:
                    response.close()
                    self.ensure_token(force=True)
                    refreshed = True
                    response = self.session.request(method, url, **kwargs)
                if response.status_code not in RETRY_STATUSES:
                    return response
                # Replaying folder/session creation after an ambiguous error can
                # create duplicate resources. Only retry reads automatically.
                if method != 'GET' or attempt == MAX_RETRIES:
                    return response
            except requests.RequestException:
                if method != 'GET' or attempt == MAX_RETRIES:
                    raise
            delay = retry_delay(response, attempt)
            if response is not None:
                response.close()
            time.sleep(delay)


def retry_delay(response, attempt):
    value = response.headers.get('Retry-After') if response is not None else None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError, AttributeError):
            delay = None
    if delay is not None and math.isfinite(delay):
        return max(0, delay)
    return min(30, 2 ** (attempt - 1))


def graph_error(response):
    try:
        data = response.json()
        error = data.get('error', {}) if isinstance(data, dict) else data
        if isinstance(error, dict):
            code = error.get('code')
            message = error.get('message')
            if code and message:
                return f'{code}: {message}'
            if message:
                return message
        return str(error)
    except ValueError:
        return response.text or response.reason or 'Unknown Graph error'

def get_children(client, parent_id):
    endpoint = f'/drives/{client.drive_id}/items/{parent_id}/children'
    while endpoint:
        response = client.request('GET', endpoint)
        if not response.ok:
            raise RuntimeError(graph_error(response))
        data = response.json()
        yield from data.get('value', [])
        endpoint = data.get('@odata.nextLink')

def get_or_create_folder(client, parent_id, name):
    children = get_children(client, parent_id)
    for item in children:
        if item.get('name') == name and 'folder' in item:
            return item['id']
    response = client.request(
        'POST', f'/drives/{client.drive_id}/items/{parent_id}/children',
        json={'name': name, 'folder': {}})
    if response.status_code == 409:
        # Another invocation may have created the same folder after our lookup.
        for item in get_children(client, parent_id):
            if item.get('name') == name and 'folder' in item:
                return item['id']
    if not response.ok:
        raise RuntimeError(graph_error(response))
    return response.json()['id']

def get_destination_folder(client, device, android_version):
    parent_id = client.root_folder_id
    parent_id = get_or_create_folder(client, parent_id, device)
    parent_id = get_or_create_folder(client, parent_id, f'android-{android_version}')
    return parent_id

def create_upload_session(client, file_path, parent_id):
    filename = quote(file_path.name, safe='')
    response = client.request(
        'POST', f'/drives/{client.drive_id}/items/{parent_id}:/{filename}:/createUploadSession',
        json={'item': {
            '@microsoft.graph.conflictBehavior': 'replace', 'name': file_path.name,
        }})
    if not response.ok:
        raise RuntimeError(graph_error(response))
    upload_url = response.json().get('uploadUrl')
    if not upload_url:
        raise RuntimeError('Microsoft Graph did not return uploadUrl.')
    return upload_url

def next_expected_start(payload):
    if not isinstance(payload, dict):
        return None
    ranges = payload.get('nextExpectedRanges')
    if not ranges:
        return None
    if isinstance(ranges, str):
        ranges = [ranges]
    if not isinstance(ranges, list):
        return None
    starts = []
    for value in ranges:
        match = re.match('^\\s*(\\d+)', str(value))
        if match:
            starts.append(int(match.group(1)))
    return min(starts) if starts else None

def query_upload_position(client, upload_url):
    for attempt in range(1, MAX_RETRIES + 1):
        response = None
        try:
            response = client.upload_session.get(
                upload_url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            if response.status_code == 200:
                return next_expected_start(response.json())
            if response.status_code not in RETRY_STATUSES:
                return None
        except ValueError:
            return None
        except requests.RequestException:
            pass
        finally:
            if response is not None:
                response.close()
        if attempt < MAX_RETRIES:
            time.sleep(retry_delay(response, attempt))
    return None

def cancel_upload_session(client, upload_url):
    try:
        with client.upload_session.delete(upload_url, timeout=(CONNECT_TIMEOUT, 30)):
            pass
    except requests.RequestException:
        pass

def upload_chunk(client, upload_url, data, start, end, total):
    headers = {
        'Content-Length': str(len(data)),
        'Content-Range': f'bytes {start}-{end}/{total}',
        'Content-Type': 'application/octet-stream',
    }
    last_error = 'Unknown error'
    for attempt in range(1, MAX_RETRIES + 1):
        response = None
        try:
            response = client.upload_session.put(
                upload_url, data=data, headers=headers,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            if response.status_code in (200, 201, 202, 409, 416):
                return response
            if response.status_code in RETRY_STATUSES:
                last_error = f'HTTP {response.status_code}: {graph_error(response)}'
            else:
                message = f'HTTP {response.status_code}: {graph_error(response)}'
                response.close()
                raise RuntimeError(message)
        except requests.RequestException as error:
            last_error = str(error)
        if attempt < MAX_RETRIES:
            delay = retry_delay(response, attempt)
            if response is not None:
                response.close()
            time.sleep(delay)
        elif response is not None:
            response.close()
    raise RuntimeError(f'Chunk {start}-{end} failed after {MAX_RETRIES} attempts: {last_error}')

class RealtimeProgress:

    def __init__(self, total_size, started):
        self.total_size = total_size
        self.started = started
        self.confirmed_bytes = 0
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def update(self, confirmed_bytes):
        with self.lock:
            self.confirmed_bytes = min(confirmed_bytes, self.total_size)

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1)

    def snapshot(self):
        with self.lock:
            return self.confirmed_bytes

    def _draw(self):
        done = self.snapshot()
        elapsed = max(time.monotonic() - self.started, 0.001)
        percentage = done / self.total_size * 100 if self.total_size else 100
        speed = done / elapsed
        print(
            f'\r{CLEAR_LINE}{YELLOW}Stats:{RESET} {WHITE}{percentage:6.2f}% | '
            f'{human_size(done)}/{human_size(self.total_size)} | '
            f'{human_size(speed)}/s | Time: {format_duration(elapsed)}{RESET}',
            end='', flush=True)

    def _run(self):
        while not self.stop_event.wait(PROGRESS_REFRESH_INTERVAL):
            self._draw()

def upload_file(client, file_path, parent_id, label, hash_file=False):
    before = file_signature(file_path)
    total_size = before[2]
    if not total_size:
        raise RuntimeError(f'Cannot upload an empty file: {file_path.name}')
    # Open before creating a remote session so unreadable files fail locally.
    with file_path.open('rb') as f:
        if file_signature(file_path) != before:
            raise RuntimeError(f'{file_path.name} changed before upload.')
        upload_url = create_upload_session(client, file_path, parent_id)
        print(SEPARATOR)
        print(f'{YELLOW}Uploading {label} file:{RESET} {WHITE}{file_path.name}{RESET}')
        progress = RealtimeProgress(total_size, time.monotonic())
        progress.start()
        checksum = hashlib.sha256() if hash_file else None
        hashed_until = start = high_water = stalled = 0
        try:
            while start < total_size:
                end = min(start + CHUNK_SIZE, total_size)
                f.seek(start)
                data = f.read(end - start)
                if len(data) != end - start:
                    raise IOError('Short read while reading upload chunk.')
                # Hash only new contiguous bytes, never retry duplicates. If a
                # server skips unread bytes, fall back to a full hash afterward.
                if checksum is not None:
                    if start > hashed_until:
                        checksum = None
                    elif end > hashed_until:
                        checksum.update(memoryview(data)[hashed_until - start:])
                        hashed_until = end
                with upload_chunk(client, upload_url, data, start, end - 1, total_size) as response:
                    status = response.status_code
                    if status in (200, 201):
                        item = response.json()
                        if not isinstance(item, dict) or item.get('size') != total_size:
                            raise RuntimeError('Uploaded file size does not match the local file.')
                        break
                    next_start = None
                    if status == 202:
                        try:
                            next_start = next_expected_start(response.json())
                        except ValueError:
                            pass
                    if next_start is None:
                        next_start = query_upload_position(client, upload_url)
                    if next_start is None or not 0 <= next_start < total_size:
                        raise RuntimeError(
                            f'Upload completion was not confirmed after HTTP {status}.')
                    if next_start % (320 * 1024):
                        raise RuntimeError('Server returned an unaligned upload position.')
                    stalled = stalled + 1 if next_start <= high_water else 0
                    if stalled >= MAX_RETRIES:
                        raise RuntimeError('Upload made no forward progress after repeated recovery attempts.')
                    high_water = max(high_water, next_start)
                    start = next_start
                    progress.update(start)
            if file_signature(file_path) != before:
                raise RuntimeError(f'{file_path.name} changed during upload; retry with a stable build.')
        except (Exception, KeyboardInterrupt):
            progress.stop()
            clear_current_line()
            cancel_upload_session(client, upload_url)
            raise
        finally:
            progress.stop()
    progress.update(total_size)
    progress._draw()
    print()
    print(f'{GREEN}Uploaded successfully.{RESET}')
    digest = checksum.hexdigest() if checksum is not None and hashed_until == total_size else None
    return digest, before

def authenticate(config):
    print(f'{YELLOW}Authenticating with OneDrive...{RESET}', end='', flush=True)
    client = GraphClient(config)
    try:
        client.ensure_token()
    except (Exception, KeyboardInterrupt):
        client.close()
        raise
    clear_current_line()
    print(f'{GREEN}OneDrive authentication successful.{RESET}\n')
    return client

def perform_upload(token_config, aosp_root, device, android_version, ota_file, hash_file=False):
    client = authenticate(token_config)
    try:
        destination_id = get_destination_folder(client, device, android_version)
        metadata = upload_file(client, ota_file, destination_id, 'OTA', hash_file)
        recovery_img = aosp_root / 'out' / 'target' / 'product' / device / 'recovery.img'
        if recovery_img.is_file():
            upload_file(client, recovery_img, destination_id, 'recovery')
        print(SEPARATOR)
        return metadata
    finally:
        client.close()

def perform_json_generation(script_dir, aosp_root, device, android_version, ota_file, metadata=None):
    print(f'{BLUE}Generating...{RESET}', end='', flush=True)
    try:
        build_datetime = get_build_datetime(aosp_root, device)
        before = file_signature(ota_file)
        if metadata is not None and metadata[1] != before:
            raise RuntimeError('OTA file changed after upload; refusing to publish mismatched metadata.')
        ota_size = before[2]
        ota_hash = (metadata[0] if metadata else None) or calculate_sha256(ota_file)
        if file_signature(ota_file) != before:
            raise RuntimeError('OTA file changed during JSON generation.')
        ota_url = get_ota_url(device, android_version, ota_file)
        json_path = generate_json(
            script_dir, device, ota_file, build_datetime, ota_hash,
            ota_size, ota_url, android_version)
    except Exception:
        clear_current_line()
        raise
    clear_current_line()
    print(f'{YELLOW}Device:{RESET}    {WHITE}{device}{RESET}')
    print(f'{YELLOW}OTA:{RESET}       {WHITE}{ota_file.name}{RESET}')
    print(f'{YELLOW}Size:{RESET}      {WHITE}{terminal_ota_size(ota_size)}{RESET}')
    print(f'{YELLOW}SHA-256:{RESET}   {WHITE}{ota_hash}{RESET}')
    print(f"{YELLOW}OTA URL:{RESET}   {WHITE}{(ota_url if ota_url else 'null')}{RESET}")
    print(f'{YELLOW}JSON Path:{RESET} {WHITE}{json_path}{RESET}')
    if not android_version:
        print()
        print(f'{RED}Warning: ANDROID version was not found in token.txt. Value for {YELLOW}"url & version"{RED} were written as null.{RESET}')
    return json_path

def main():
    args = parse_arguments()
    operation = 'Upload' if args.upload else 'Operation'
    try:
        script_dir = Path(__file__).resolve().parent
        device = get_device_codename(args)
        aosp_root = find_aosp_root(device)
        ota_file = find_latest_ota(aosp_root, device)
        config = read_token_config(script_dir)
        upload = complete_token_config(config, upload=not args.json)
        version = config['ANDROID']
        if args.upload and not upload:
            print(f'{YELLOW}Upload skipped: required credentials were not provided.{RESET}')
            return 1
        if not args.json and not upload:
            print(f'{YELLOW}Cloud upload skipped. Continuing with JSON generation.{RESET}\n')
        if not upload:
            operation = 'JSON generation'
        # Catch invalid metadata before spending time uploading a multi-GB OTA.
        if not args.upload:
            get_build_datetime(aosp_root, device)
        metadata = None
        if upload:
            metadata = perform_upload(config, aosp_root, device, version, ota_file,
                                      hash_file=not args.upload)
        if not args.upload:
            perform_json_generation(script_dir, aosp_root, device, version, ota_file, metadata)
        if args.upload:
            message = 'OTA file successfully uploaded to cloud storage'
        elif upload:
            message = 'OTA file successfully uploaded to cloud & JSON file successfully generated.'
        else:
            message = 'JSON file successfully generated.'
        print(f'{GREEN}{message}{RESET}\n')
        return 0
    except (KeyboardInterrupt, EOFError):
        clear_current_line()
        print(f'{RED}{operation} cancelled.{RESET}')
        return 130
    except Exception as error:
        clear_current_line()
        print(f'{RED}{operation} failed: {error}{RESET}')
        return 1
    finally:
        print()
        show_token_tip()
        print(f'{YELLOW}Save these values in token.txt beside this script to avoid '
              f'manual entry. Manually entered values are not saved automatically.{RESET}\n')

if __name__ == '__main__':
    sys.exit(main())
