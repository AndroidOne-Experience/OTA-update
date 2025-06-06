# OTA json file generator
OTA (Over The Air) update of android customOS
Instructions

Clone this repository with the following command...
```bash
git clone -b 15 https://github.com/AndroidOne-Experience/OTA-update.git OTA
```

### Generate a GitHub token

To interact with the GitHub API for creating releases and uploading assets, you need a **Personal Access Token** (PAT).

1. **Create a GitHub Personal Access Token:**
   - Go to your GitHub account settings:  
     [https://github.com/settings/tokens](https://github.com/settings/tokens)  
   - Click **Generate new token** (classic).
   - Give the token a descriptive name, e.g., `OTA Upload Script`.
   - Select the following scopes:
     - `repo` (Full control of private repositories, also includes public repos)
     > This scope allows the script to create releases and upload assets.
   - Generate the token and **copy it immediately** (you won't see it again).

2. **Save the token:**
   - Create a file named `token.txt` in the same directory as the script.
   - Paste your token into `token.txt` (no extra spaces or new lines).
   - The script will read this token to authenticate with GitHub.

### Run the script:
```bash
python3 ./OTA/generate_OTA.py
```
Parse the --no-upload arg, if want to skip the GitHub upload step:
```bash
python3 ./OTA/generate_OTA.py --no-upload 
```
