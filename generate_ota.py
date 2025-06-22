import os
import sys
import hashlib
import json
import re
from datetime import datetime

try:
    import boto3
    from boto3.s3.transfer import TransferConfig
    from tqdm import tqdm
except ImportError:
    print("Installing required packages...")
    os.system(f"{sys.executable} -m pip install --user boto3 tqdm")
    import boto3
    from boto3.s3.transfer import TransferConfig
    from tqdm import tqdm

class ReadFileWithProgress:
    def __init__(self, file_path, progress):
        self.f = open(file_path, "rb")
        self.progress = progress

    def read(self, chunk_size):
        data = self.f.read(chunk_size)
        if not data:
            return b""
        self.progress.update(len(data))
        return data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.f.close()
        self.progress.close()

def sha256sum(filename):
    h = hashlib.sha256()
    with open(filename, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def upload_to_r2(bucket_name, file_path, r2_endpoint, access_key, secret_key):
    session = boto3.session.Session()
    s3 = session.client(
        service_name="s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=r2_endpoint
    )

    config = TransferConfig(
        multipart_threshold=5 * 1024 * 1024,
        multipart_chunksize=5 * 1024 * 1024,
        use_threads=True
    )

    filename = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)

    try:
        print(f"\n☁️  Uploading {file_path} to R2 bucket: {bucket_name}")
        with tqdm(total=file_size, unit="B", unit_scale=True, desc=filename) as progress:
            with ReadFileWithProgress(file_path, progress) as fp:
                s3.upload_fileobj(
                    Fileobj=fp,
                    Bucket=bucket_name,
                    Key=filename,
                    ExtraArgs={"ACL": "public-read"},
                    Config=config
                )
        print(f"✅ Uploaded to R2: {filename}")
    except Exception as e:
        print(f"❌ Failed to upload {filename} to R2: {e}")
        sys.exit(1)

def load_r2_credentials(token_file="OTA/token.txt"):
    if not os.path.exists(token_file):
        print(f"❌ Missing required token file: {token_file}")
        sys.exit(1)

    creds = {}
    with open(token_file, "r") as f:
        for line in f:
            if "=" in line:
                key, value = line.strip().split("=", 1)
                creds[key.strip()] = value.strip()

    required_keys = ["account_id", "access_key", "secret_key", "r2_bucket", "pub_dwnld_id"]
    for key in required_keys:
        if key not in creds:
            print(f"❌ Missing '{key}' in token.txt")
            sys.exit(1)

    return creds

def main():
    print("\U0001f527 Starting OTA generator...")
    codename = input("Enter device codename (e.g. PL2, miatoll): ").strip()
    ota_package_dir = f"out/target/product/{codename}"

    print(f"🔎 Searching for OTA ZIP in {ota_package_dir}")
    try:
        files = [f for f in os.listdir(ota_package_dir) if f.startswith("AndroidOne-") and codename in f and f.endswith(".zip")]
    except FileNotFoundError:
        print(f"❌ OTA package directory not found: {ota_package_dir}")
        sys.exit(1)

    if not files:
        print(f"❌ No OTA zip found for codename '{codename}'")
        sys.exit(1)

    ota_filename = files[0]
    ota_file_path = os.path.join(ota_package_dir, ota_filename)
    print(f"✅ Found OTA package: {ota_file_path}")

    match = re.match(r"AndroidOne-(?P<codename>.+?)-OTA-\d{8}-(?P<build>\d+)\.zip", ota_filename)
    if match:
        extracted_codename = match.group("codename")
        build_number = match.group("build")
        print(f"🔢 Parsed codename: {extracted_codename}, Build: {build_number}")
    else:
        print("❌ Filename pattern incorrect")
        sys.exit(1)

    build_prop_path = os.path.join(ota_package_dir, "system", "build.prop")
    datetime_utc = "UNKNOWN"
    security_patch = "UNKNOWN"

    print("📄 Reading build.prop...")
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

    try:
        parsed_date = datetime.strptime(security_patch, "%Y-%m-%d")
        formatted_patch = parsed_date.strftime("%B-%Y")
    except Exception:
        formatted_patch = security_patch

    # Load R2 credentials
    creds = load_r2_credentials()
    r2_endpoint = f"https://{creds['account_id']}.r2.cloudflarestorage.com"
    r2_bucket = creds['r2_bucket']

    # Upload to R2
    upload_to_r2(r2_bucket, ota_file_path, r2_endpoint, creds['access_key'], creds['secret_key'])

    # Prepare OTA JSON
    download_url = f"https://pub-{creds['pub_dwnld_id']}.r2.dev/{ota_filename}"
    file_id = sha256sum(ota_file_path)
    file_size = os.path.getsize(ota_file_path)

    ota_json = {
        "response": [
            {
                "datetime": datetime_utc,
                "filename": ota_filename,
                "id": file_id,
                "size": file_size,
                "url": download_url,
                "version": "15"
            }
        ]
    }

    os.makedirs("./OTA/devices", exist_ok=True)
    output_path = f"./OTA/devices/{codename}.json"
    with open(output_path, "w") as f:
        json.dump(ota_json, f, indent=2)

    print(f"📄 OTA JSON saved to: {output_path}")
    print(f"🔗 Download URL: {download_url}")

if __name__ == "__main__":
    main()
