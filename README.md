# 📦 OTA JSON File Generator & Uploader

A simple Python tool to:

- 🔍 Automatically detect OTA ZIP and recovery image
- ☁️ Upload OTA packages to OneDrive
- ⚙️ Generate OTA JSON

---

## 🚀 Installation

### 📥 Clone Repository

```bash
git clone -b 16 https://github.com/AndroidOne-Experience/OTA-update.git OTA
```

---

### 📦 Install Dependencies

```bash
sudo apt update
sudo apt install python3
sudo apt install python3-pip
pip3 install requests msal
```

---

## ⚙️ Configuration

Create file:

```text
OTA/token.txt
```

---

### ✏️ Example token.txt

```ini
ANDROID="define the version, like 16, 17"

TENANT_ID=xxxxxxxxxxxxxxxx
CLIENT_ID=xxxxxxxxxxxxxxxx
CLIENT_SECRET=xxxxxxxxxxxxxxxx
DRIVE_ID=xxxxxxxxxxxxxxxx
FOLDER_ID=xxxxxxxxxxxxxxxx
```

---

## 📂 Required Build Output

```text
out/target/product/<codename>/
```

Must contain:

- 📦 `AndroidOne-<codename>-OTA-*.zip`
- 📄 `system/build.prop`
- 🧩 `recovery.img` (optional)

---

## ▶️ Usage

### 🔹 Upload + JSON file generation (default)

```bash
python3 ./OTA/generate_ota.py
```

---

### ☁️ Upload OTA files ONLY

```bash
python3 ./OTA/generate_ota.py --upload
```

👉 `--upload` = OneDrive upload only

---

### 🗂️ Generate JSON file ONLY

```bash
python3 ./OTA/generate_ota.py --json
```

👉 `--json` = JSON generation only

---

## 📘 Argument Explanation

- ☁️ `--upload` → Upload OTA files only
- 🗂️ `--json` → Generate JSON file only
- 🔁 No argument → Upload + JSON generation

---

## 📄 Generated JSON

```text
OTA/devices/<codename>.json
```

---

### 📌 Example JSON

```json
{
  "response": [
    {
      "datetime": "1712658960",
      "filename": "AndroidOne-miatoll-OTA-xxxx.zip",
      "id": "sha256hash",
      "size": 2697982122,
      "url": "download_url",
      "version": "16"
    }
  ]
}
```

---

## ⚠️ Notes

- 🧾 Ensure `token.txt` is correctly configured
- 🔐 Microsoft Graph API credentials are required for OneDrive upload
- 🐧 Works best on Linux
