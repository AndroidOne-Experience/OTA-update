import os
import sys
import hashlib
import json

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


def print_section(title):
    print()
    print("#" * 24 + f" {title} " + "#" * 24)


def sha256sum(filename):
    h = hashlib.sha256()
    with open(filename, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ================= R2 =================
def upload_to_r2(bucket, file_path, endpoint, access, secret, codename):
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret
    )

    filename = os.path.basename(file_path)
    key = f"{codename}/{filename}"
    size = os.path.getsize(file_path)

    config = TransferConfig(
        multipart_threshold=100 * 1024 * 1024,
        multipart_chunksize=100 * 1024 * 1024,
        use_threads=True
    )

    with tqdm(
        total=size,
        ncols=60,
        bar_format=f"📤 Uploading: {filename} | transferred: {{n_fmt}}/{{total_fmt}} | rate: {{rate_fmt}} ",
        unit='',
        unit_scale=True
    ) as p:

        def cb(x):
            p.update(x)

        s3.upload_file(file_path, bucket, key,
                       ExtraArgs={"ACL": "public-read"},
                       Config=config,
                       Callback=cb)

    print(f"✅ {filename} uploaded successfully to R2: {key}")


# ================= SourceForge =================
def upload_to_sourceforge(file_path, user, password, project, branch, codename):
    filename = os.path.basename(file_path)

    remote_dir = f"/home/frs/project/{project}/{codename}/{branch}/"

    print(f"📤 Uploading: {filename}")

    cmd = (
        f"sshpass -p '{password}' rsync -a "
        f"--info=progress2 --no-inc-recursive "
        f"-e 'ssh -o StrictHostKeyChecking=no' "
        f"'{file_path}' "
        f"{user}@frs.sourceforge.net:{remote_dir}"
    )

    if os.system(cmd) != 0:
        print(f"❌ Failed to upload {filename}")
        sys.exit(1)

    print(f"✅ {filename} uploaded successfully to SourceForge: {codename}/{branch}/{filename}")

def ensure_sourceforge_known_host():
    os.makedirs(os.path.expanduser("~/.ssh"), exist_ok=True)
    os.system("ssh-keyscan frs.sourceforge.net >> ~/.ssh/known_hosts 2>/dev/null")


# ================= CONFIG =================
def load_credentials(base):
    path = os.path.join(base, "OTA/token.txt") if os.path.basename(base) != "OTA" else "token.txt"

    if not os.path.exists(path):
        print("❌ token.txt missing")
        sys.exit(1)

    d = {}
    for line in open(path):
        if "=" in line:
            k, v = line.strip().split("=", 1)
            d[k.strip()] = v.strip()

    keys = [
        "account_id", "access_key", "secret_key",
        "r2_bucket", "pub_dwnld_id",
        "sf_username", "sf_pass", "sf_project", "sf_branch",
        "version"
    ]

    for k in keys:
        if k not in d:
            print(f"❌ Missing {k}")
            sys.exit(1)

    return d


# ================= MAIN =================
def main():
    print("🔧 Starting OTA generator...")

    mode = "ALL"
    if len(sys.argv) > 2 and sys.argv[1] == "--upload":
        m = sys.argv[2].upper()
        if m in ["R2", "SF"]:
            mode = m

    codename = input("📱 Enter device codename: ").strip()

    base = os.path.abspath("..") if os.path.basename(os.getcwd()) == "OTA" else os.getcwd()
    out = os.path.join(base, f"out/target/product/{codename}")

    print(f"🔎 Searching in {out}")

    try:
        files = [
            f for f in os.listdir(out)
            if f.startswith("AndroidOne-") and codename in f and f.endswith(".zip")
        ]
    except:
        print("❌ OTA folder missing")
        sys.exit(1)

    if not files:
        print("❌ AndroidOne OTA not found")
        sys.exit(1)

    files.sort(reverse=True)
    ota = files[0]
    ota_path = os.path.join(out, ota)

    print(f"📦 Found OTA: {ota_path}")

    # ===== DATETIME =====
    build_prop = os.path.join(out, "system/build.prop")
    datetime_utc = "UNKNOWN"

    print("📄 Reading build.prop...")

    if os.path.exists(build_prop):
        try:
            with open(build_prop, "r") as f:
                for line in f:
                    if line.startswith("ro.build.date.utc="):
                        datetime_utc = line.strip().split("=", 1)[1]
                        break
        except Exception:
            pass

    rec = os.path.join(out, "recovery.img")
    creds = load_credentials(base)

    r2_ep = f"https://{creds['account_id']}.r2.cloudflarestorage.com"

    # ===== R2 =====
    if mode in ["ALL", "R2"]:
        print_section("Cloudflare R2")

        upload_to_r2(creds['r2_bucket'], ota_path, r2_ep,
                     creds['access_key'], creds['secret_key'], codename)

        if os.path.exists(rec):
            upload_to_r2(creds['r2_bucket'], rec, r2_ep,
                         creds['access_key'], creds['secret_key'], codename)

        print("#" * 24 + " Cloudflare R2 " + "#" * 24)

    # ===== SF =====
    if mode in ["ALL", "SF"]:
        print_section("SourceForge")

        ensure_sourceforge_known_host()

        upload_to_sourceforge(ota_path,
                              creds['sf_username'],
                              creds['sf_pass'],
                              creds['sf_project'],
                              creds['sf_branch'],
                              codename)

        if os.path.exists(rec):
            upload_to_sourceforge(rec,
                                  creds['sf_username'],
                                  creds['sf_pass'],
                                  creds['sf_project'],
                                  creds['sf_branch'],
                                  codename)

        print("#" * 24 + " SourceForge " + "#" * 24)

    # ===== URL =====
    r2_url = f"https://pub-{creds['pub_dwnld_id']}.r2.dev/{codename}/{ota}"
    sf_url = f"https://downloads.sourceforge.net/project/{creds['sf_project']}/{codename}/{creds['sf_branch']}/{ota}"

    # JSON URL
    if mode == "SF":
        json_url = sf_url
    else:
        json_url = r2_url

    # ===== JSON =====
    data = {
        "response": [{
            "datetime": datetime_utc,
            "filename": ota,
            "id": sha256sum(ota_path),
            "size": os.path.getsize(ota_path),
            "url": json_url,
            "version": creds["version"]
        }]
    }

    dev = os.path.join(base, "OTA/devices")
    os.makedirs(dev, exist_ok=True)

    out_json = os.path.join(dev, f"{codename}.json")
    json.dump(data, open(out_json, "w"), indent=2)

    print("\n📄 JSON:", out_json)

    if mode == "ALL":
        print("🔗 R2 URL:", r2_url)
        print("🔗 SF URL:", sf_url)
    elif mode == "R2":
        print("🔗 URL:", r2_url)
    elif mode == "SF":
        print("🔗 URL:", sf_url)

if __name__ == "__main__":
    main()
