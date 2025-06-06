import os
import sys
import subprocess
import hashlib
import json
import re
from datetime import datetime

# Helper to install packages if missing
def install_package(pkg):
    print(f"⚠️ Package '{pkg}' not found. Installing it...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

try:
    import requests
except ImportError:
    install_package("requests")
    import requests

try:
    from tqdm import tqdm
except ImportError:
    install_package("tqdm")
    from tqdm import tqdm

def sha256sum(filename):
    h = hashlib.sha256()
    with open(filename, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def read_github_token(token_file=None):
    if token_file is None:
        script_dir = os.path.abspath(os.path.dirname(__file__))
        token_file = os.path.join(script_dir, "token.txt")
    try:
        with open(token_file, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"❌ GitHub token file not found: {token_file}")
        sys.exit(1)

def detect_repo_from_git():
    try:
        git_dir = os.path.abspath(os.path.dirname(__file__))
        result = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=git_dir,
            stderr=subprocess.DEVNULL
        ).decode().strip()
        match = re.search(r"(?:github\.com[:/])([^/]+/[^/.]+)", result)
        if match:
            return match.group(1)
        else:
            print("❌ Could not parse GitHub repo from remote URL.")
            print(f"Remote URL: {result}")
            sys.exit(1)
    except subprocess.CalledProcessError:
        print("❌ Failed to detect git remote. Make sure you're in a Git repo.")
        sys.exit(1)

def get_release_by_tag(repo, tag, headers):
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        return r.json()
    elif r.status_code == 404:
        return None
    else:
        print(f"❌ Failed to get release info: {r.status_code} {r.text}")
        sys.exit(1)

def create_release(repo, tag, release_title, headers):
    url = f"https://api.github.com/repos/{repo}/releases"
    data = {
        "tag_name": tag,
        "name": release_title,
        "body": f"OTA release for {tag}",
        "draft": False,
        "prerelease": False
    }
    r = requests.post(url, headers=headers, json=data)
    if r.status_code == 201:
        return r.json()
    else:
        print(f"❌ Failed to create release: {r.status_code} {r.text}")
        sys.exit(1)

def upload_asset(upload_url, filename, headers, description, existing_assets):
    basename = os.path.basename(filename)
    if any(asset["name"] == basename for asset in existing_assets):
        print(f"⚠️ {description} '{basename}' already uploaded, skipping.")
        return

    file_size = os.path.getsize(filename)
    headers = headers.copy()
    headers["Content-Type"] = "application/octet-stream"
    upload_url = upload_url.split("{")[0] + f"?name={basename}"

    print(f"\nUploading {description}:")
    with open(filename, "rb") as f:
        with tqdm.wrapattr(f, "read", total=file_size, unit="B", unit_scale=True, desc=basename) as wrapped:
            r = requests.post(upload_url, headers=headers, data=wrapped)

    if r.status_code in (200, 201):
        print(f"✅ Upload successful: {basename}")
    else:
        print(f"❌ Upload failed for {basename}: {r.status_code} {r.text}")
        sys.exit(1)

def upload_to_gh_release(repo, tag, files_with_desc, token, release_title):
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    release = get_release_by_tag(repo, tag, headers)
    if release is None:
        release = create_release(repo, tag, release_title, headers)
    else:
        print(f"ℹ️ Tag already exists. Uploading...")

    upload_url = release["upload_url"]
    existing_assets = release.get("assets", [])

    for filename, desc in files_with_desc:
        upload_asset(upload_url, filename, headers, desc, existing_assets)

def main():
    skip_upload = "--no-upload" in sys.argv

    codename = input("Enter device codename (e.g. PL2, miatoll): ").strip()
    ota_package_dir = f"out/target/product/{codename}"

    try:
        files = [f for f in os.listdir(ota_package_dir) if f.startswith("AndroidOne-") and codename in f and f.endswith(".zip")]
    except FileNotFoundError:
        print(f"❌ OTA package directory not found: {ota_package_dir}")
        sys.exit(1)

    if not files:
        print(f"❌ No OTA zip matching codename '{codename}' found in {ota_package_dir}.")
        sys.exit(1)

    ota_filename = files[0]
    ota_file_path = os.path.join(ota_package_dir, ota_filename)
    print(f"✅ Found OTA package: {ota_file_path}")

    recovery_path = os.path.join(ota_package_dir, "recovery.img")
    files_to_upload = []

    if os.path.isfile(recovery_path):
        print(f"✅ Found recovery.img: {recovery_path}")
        files_to_upload.append((recovery_path, "Recovery Image"))
    else:
        print("ℹ️ recovery.img not found, skipping recovery upload.")

    files_to_upload.append((ota_file_path, "ROM"))

    match = re.match(r"AndroidOne-(?P<codename>.+?)-OTA-\d{8}-(?P<build>\d+)\.zip", ota_filename)
    if match:
        extracted_codename = match.group("codename")
        build_number = match.group("build")
        tag = f"{extracted_codename}-{build_number}"
    else:
        print("❌ Failed to parse tag from filename.")
        sys.exit(1)

    build_prop_path = os.path.join(ota_package_dir, "system", "build.prop")
    datetime_utc = "UNKNOWN"
    security_patch = "UNKNOWN"

    if os.path.exists(build_prop_path):
        try:
            with open(build_prop_path, "r") as f:
                for line in f:
                    if line.startswith("ro.build.date.utc="):
                        datetime_utc = line.strip().split("=", 1)[1]
                    elif line.startswith("ro.build.version.security_patch="):
                        security_patch = line.strip().split("=", 1)[1]
        except Exception:
            pass

    if security_patch != "UNKNOWN":
        try:
            parsed_date = datetime.strptime(security_patch, "%Y-%m-%d")
            formatted_patch = parsed_date.strftime("%B-%Y")
        except ValueError:
            formatted_patch = security_patch
    else:
        formatted_patch = "UNKNOWN"

    release_title = f"AndroidOne Experience | {extracted_codename} | ASB: {formatted_patch}"
    id_hash = sha256sum(ota_file_path)
    size = os.path.getsize(ota_file_path)
    version = "15"

    repo = detect_repo_from_git()
    url = f"https://github.com/{repo}/releases/download/{tag}/{ota_filename}"

    data = {
        "response": [
            {
                "datetime": datetime_utc,
                "filename": ota_filename,
                "id": id_hash,
                "size": size,
                "url": url,
                "version": version,
            }
        ]
    }

    output_dir = "./OTA/devices"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, f"{codename}.json")

    try:
        with open(output_file, "w") as f:
            json.dump(data, f, indent=2)
        print(f"✅ OTA JSON saved to {output_file}")
    except Exception as e:
        print(f"❌ Failed to write JSON: {e}")
        sys.exit(1)

    if not skip_upload:
        token = read_github_token()
        upload_to_gh_release(repo, tag, files_to_upload, token, release_title)
    else:
        print("📦 Skipping GitHub upload (because --no-upload was passed)")

if __name__ == "__main__":
    main()
