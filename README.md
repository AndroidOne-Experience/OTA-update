# 📦 OTA JSON File Generator & Uploader

A simple Python tool to:

- 🔍 Automatically detect OTA ZIP and recovery image 
- ☁️ Upload OTA packages to Cloudflare R2  
- 🗂️ Upload OTA packages to SourceForge (SF)   
- ⚙️ Generate OTA JSON

---

## 🚀 Installation

### 📥 Clone Repository

    git clone -b 16 https://github.com/AndroidOne-Experience/OTA-update.git OTA

---

### 📦 Install Dependencies

    sudo apt update
    sudo apt install python3
    sudo apt install python3-pip
    sudo apt install sshpass
    pip3 install boto3 tqdm

---

## ⚙️ Configuration

Create file:

    OTA/token.txt

---

### ✏️ Example token.txt

    version="defone the version, like 16, 17"
    account_id=your_r2_account_id
    access_key=your_r2_access_key
    secret_key=your_r2_secret_key
    r2_bucket=your_bucket_name
    pub_dwnld_id=your_public_id

    sf_username=your_sf_username
    sf_pass=your_sf_password
    sf_project=your_project_name
    sf_branch=your_branch_name

---

## 📂 Required Build Output

    out/target/product/<codename>/

Must contain:

- 📦 `AndroidOne-<codename>-OTA-*.zip`  
- 📄 `system/build.prop`  
- 🧩 `recovery.img` (optional)   

---

## ▶️ Usage

### 🔹 Upload to BOTH (default)

    python3 ./OTA/generate_ota.py

---

### ☁️ Upload ONLY to Cloudflare R2

    python3 ./OTA/generate_ota.py --upload R2

👉 R2 = Cloudflare upload only  

---

### 🗂️ Upload ONLY to SourceForge

    python3 ./OTA/generate_ota.py --upload SF

👉 SF = SourceForge upload only  

---

## 📘 Argument Explanation

- ☁️ R2 → Upload only to Cloudflare  
- 🗂️ SF → Upload only to SourceForge  
- 🔁 No argument → Upload to both  

---

## 📄 Generated JSON

    OTA/devices/<codename>.json

---

### 📌 Example JSON

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

---

## ⚠️ Notes

- 🧾 Ensure token.txt is correctly configured  
- 🔐 Requires sshpass for SourceForge upload  
- 🐧 Works best on Linux  

