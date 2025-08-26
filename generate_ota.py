import os
import sys
import hashlib
import json
import re
import time
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


def sha256sum(filename):
    h = hashlib.sha256()
    with open(filename, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_to_r2(bucket_name, file_path, r2_endpoint, access_key, secret_key, codename):
    session = boto3.session.Session()
    s3 = session.client(
        service_name="s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=r2_endpoint
    )

    filename = os.path.basename(file_path)
    key_path = f"{codename}/{filename}"
    file_size = os.path.getsize(file_path)

    config = TransferConfig(
        multipart_threshold=1024 * 1024 * 100,  # 100 MB per part
        multipart_chunksize=1024 * 1024 * 100,  # 100 MB per chunk
        use_threads=True
    )

    # Upload with progress bar and emoji
    with tqdm(
        total=file_size,
        ncols=60,
        bar_format=f"📤 Uploading: {filename} | transferred: {{n_fmt}}/{{total_fmt}} | rate: {{rate_fmt}} ",
        leave=True,
        dynamic_ncols=False,
        unit='',            
        unit_scale=True
    ) as progress:
        def progress_callback(bytes_transferred):
            progress.update(bytes_transferred)

        try:
            s3.upload_file(
                Filename=file_path,
                Bucket=bucket_name,
                Key=key_path,
                ExtraArgs={"ACL": "public-read"},
                Config=config,
                Callback=progress_callback
            )
            progress.close()
            print(f"✅ {filename} uploaded successfully to R2: {key_path}")
        except Exception as e:
            progress.close()
            print(f"❌ Failed to upload {filename} to R2: {e}")
            sys.exit(1)


def load_r2_credentials(base_dir, token_file="token.txt"):
    # Determine token path relative to base_dir
    if os.path.basename(base_dir) == "OTA":
        token_path = os.path.join(base_dir, token_file)
    else:
        token_path = os.path.join(base_dir, "OTA", token_file)

    if not os.path.exists(token_path):
        print(f"❌ Missing required token file: {token_path}")
        sys.exit(1)

    creds = {}
    with open(token_path, "r") as f:
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

    codename = input("📱 Enter device codename (e.g. PL2, miatoll): ").strip()

    # Handle running inside OTA/ folder
    cwd = os.getcwd()
    if os.path.basename(cwd) == "OTA":
        base_dir = os.path.abspath(os.path.join(cwd, ".."))
    else:
        base_dir = cwd

    ota_package_dir = os.path.join(base_dir, f"out/target/product/{codename}")

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
    creds = load_r2_credentials(base_dir)
    r2_endpoint = f"https://{creds['account_id']}.r2.cloudflarestorage.com"
    r2_bucket = creds['r2_bucket']

    # Upload to R2
    upload_to_r2(r2_bucket, ota_file_path, r2_endpoint, creds['access_key'], creds['secret_key'], codename)

    # Prepare OTA JSON
    download_url = f"https://pub-{creds['pub_dwnld_id']}.r2.dev/{codename}/{ota_filename}"
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

    # Save OTA JSON inside OTA/devices/
    devices_dir = os.path.join(base_dir, "OTA", "devices")
    os.makedirs(devices_dir, exist_ok=True)
    output_path = os.path.join(devices_dir, f"{codename}.json")
    with open(output_path, "w") as f:
        json.dump(ota_json, f, indent=2)

    print(f"📄 OTA JSON saved to: {output_path}")
    print(f"🔗 Download URL: {download_url}")


if __name__ == "__main__":
    main()
