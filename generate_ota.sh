#!/bin/bash

set -e

echo "🔧 Starting OTA generator..."

# Prompt for device codename
read -p "Enter device codename (e.g. PL2, miatoll): " CODENAME
OTA_DIR="out/target/product/${CODENAME}"

TOKEN_FILE="OTA/token.txt"
if [[ ! -f "$TOKEN_FILE" ]]; then
    echo "❌ Missing OTA/token.txt"
    exit 1
fi

# Load credentials from token.txt
while IFS="=" read -r key value; do
    value=$(echo "$value" | xargs) # Trim whitespace
    case "$key" in
        sf_username) SF_USER="$value" ;;
        sf_project)  SF_PROJ="$value" ;;
        sf_pass)     SF_PASS="$value" ;;
        r2_account_id) R2_ACCOUNT_ID="$value" ;;
        r2_access_key) R2_ACCESS_KEY="$value" ;;
        r2_secret_key) R2_SECRET_KEY="$value" ;;
        r2_bucket)    R2_BUCKET="$value" ;;
        r2_pub_dwnld_id) R2_PUB_DWNLD_ID="$value" ;;
        r2_remote_name) R2_REMOTE_NAME="$value" ;; # Optional
    esac
done < "$TOKEN_FILE"

if [[ -z "$SF_USER" || -z "$SF_PROJ" || -z "$SF_PASS" ]]; then
    echo "❌ Missing credentials in OTA/token.txt"
    exit 1
fi

# Auto-setup rclone remote if needed
setup_rclone_r2() {
    local REMOTE_NAME="${R2_REMOTE_NAME:-r2_remote}"
    local RCLONE_CONF="$HOME/.config/rclone/rclone.conf"
    local R2_ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    echo "🔍 Checking rclone remote '$REMOTE_NAME'..."

    if ! rclone listremotes 2>/dev/null | grep -q "^${REMOTE_NAME}:"; then
        echo "⚙️  Configuring rclone remote '$REMOTE_NAME' for Cloudflare R2..."

        mkdir -p "$(dirname "$RCLONE_CONF")"
        {
            echo "[${REMOTE_NAME}]"
            echo "type = s3"
            echo "provider = Cloudflare"
            echo "env_auth = false"
            echo "access_key_id = ${R2_ACCESS_KEY}"
            echo "secret_access_key = ${R2_SECRET_KEY}"
            echo "endpoint = ${R2_ENDPOINT}"
            echo "acl = public-read"
        } >> "$RCLONE_CONF"

        echo "✅ rclone remote '$REMOTE_NAME' added."
    else
        echo "✅ rclone remote '$REMOTE_NAME' already exists."
    fi
}

# Find OTA zip file
echo "🔎 Searching for OTA ZIP in $OTA_DIR"
OTA_ZIP=$(find "$OTA_DIR" -maxdepth 1 -name "AndroidOne-${CODENAME}-OTA-*.zip" | sort -r | head -n1)
if [[ ! -f "$OTA_ZIP" ]]; then
    echo "❌ No OTA zip found"
    exit 1
fi
OTA_FILENAME=$(basename "$OTA_ZIP")
echo "✅ Found OTA package: $OTA_FILENAME"

# Extract build date and number
if [[ "$OTA_FILENAME" =~ AndroidOne-([a-zA-Z0-9_-]+)-OTA-([0-9]{8})-([0-9]+)\.zip ]]; then
    BUILD_DATE="${BASH_REMATCH[2]}"
    BUILD_NUMBER="${BASH_REMATCH[3]}"
    echo "🔢 Build: $BUILD_NUMBER, Date: $BUILD_DATE"
else
    echo "❌ Invalid OTA filename format"
    exit 1
fi

# Extract from build.prop
BUILD_PROP="$OTA_DIR/system/build.prop"
DATETIME_UTC="UNKNOWN"
SEC_PATCH="UNKNOWN"
if [[ -f "$BUILD_PROP" ]]; then
    DATETIME_UTC=$(grep 'ro.build.date.utc=' "$BUILD_PROP" | cut -d= -f2)
    SEC_PATCH=$(grep 'ro.build.version.security_patch=' "$BUILD_PROP" | cut -d= -f2)
else
    echo "⚠️  build.prop not found, some info will be missing."
fi

# Format security patch
PATCH_FMT="$SEC_PATCH"
if date -d "$SEC_PATCH" "+%B-%Y" &>/dev/null; then
    PATCH_FMT=$(date -d "$SEC_PATCH" "+%B-%Y")
fi

# Check for recovery
RECOVERY_IMG="$OTA_DIR/recovery.img"
HAS_RECOVERY=false
if [[ -f "$RECOVERY_IMG" ]]; then
    echo "✅ Found recovery image"
    HAS_RECOVERY=true
else
    echo "⚠️  No recovery.img found"
fi

# Upload to SourceForge
upload_sourceforge() {
    local LOCAL_FILE=$1
    local REMOTE_DIR="/home/frs/project/$SF_PROJ/$CODENAME/"
    local FILENAME=$(basename "$LOCAL_FILE")

    echo "☁️ Uploading $FILENAME to SourceForge using rsync with progress..."

    sshpass -p "$SF_PASS" rsync -av --info=progress2 -e "ssh" "$LOCAL_FILE" "$SF_USER@frs.sourceforge.net:$REMOTE_DIR"

    if [[ $? -ne 0 ]]; then
        echo "❌ Upload failed: $FILENAME"
        exit 1
    fi
    echo "✅ Uploaded: $FILENAME"
}

# Upload to Cloudflare R2
upload_r2() {
    local LOCAL_FILE=$1
    local FILENAME=$(basename "$LOCAL_FILE")
    local REMOTE_NAME="${R2_REMOTE_NAME:-r2_remote}"

    echo "☁️ Uploading $FILENAME to Cloudflare R2..."
    rclone copy "$LOCAL_FILE" "${REMOTE_NAME}:${R2_BUCKET}/$CODENAME/" --stats-one-line -P

    if [[ $? -ne 0 ]]; then
        echo "❌ Upload failed to Cloudflare R2: $FILENAME"
        exit 1
    fi
    echo "✅ Uploaded to Cloudflare R2: $FILENAME"
}

USE_R2=false
if [[ "$1" == "--r2-mirror" ]]; then
    USE_R2=true
fi

if $USE_R2; then
    setup_rclone_r2

    echo
    echo "☁️ Uploading to Cloudflare R2..."
    upload_r2 "$OTA_ZIP"
    if [[ "$HAS_RECOVERY" == true ]]; then
        upload_r2 "$RECOVERY_IMG"
    fi

    echo
    echo "☁️ Uploading to SourceForge..."
    upload_sourceforge "$OTA_ZIP"
    if [[ "$HAS_RECOVERY" == true ]]; then
        upload_sourceforge "$RECOVERY_IMG"
    fi
else
    echo
    echo "☁️ Uploading to SourceForge..."
    upload_sourceforge "$OTA_ZIP"
    if [[ "$HAS_RECOVERY" == true ]]; then
        upload_sourceforge "$RECOVERY_IMG"
    fi
fi

# Generate OTA JSON
OTA_SHA=$(sha256sum "$OTA_ZIP" | awk '{print $1}')
OTA_SIZE=$(stat -c %s "$OTA_ZIP")
OTA_JSON="OTA/devices/${CODENAME}.json"

mkdir -p "$(dirname "$OTA_JSON")"

CLOUDFLARE_URL=""
if $USE_R2 && [[ -n "$R2_PUB_DWNLD_ID" ]]; then
    CLOUDFLARE_URL="https://pub-${R2_PUB_DWNLD_ID}.r2.dev/${R2_BUCKET}/${CODENAME}"
fi

{
    echo "{"
    echo "  \"response\": ["
    echo "    {"
    echo "      \"datetime\": ${DATETIME_UTC:-0},"
    echo "      \"filename\": \"${OTA_FILENAME}\","
    echo "      \"id\": \"${OTA_SHA}\","
    echo "      \"size\": ${OTA_SIZE},"
    if $USE_R2 && [[ -n "$CLOUDFLARE_URL" ]]; then
        echo "      \"url\": \"${CLOUDFLARE_URL}/${OTA_FILENAME}\","
    else
        echo "      \"url\": \"https://downloads.sourceforge.net/project/${SF_PROJ}/${CODENAME}/${OTA_FILENAME}\","
    fi
    echo "      \"version\": \"15\","
    if [[ "$HAS_RECOVERY" == true ]]; then
        echo "      \"recovery\": {"
        echo "        \"filename\": \"recovery.img\","
        if $USE_R2 && [[ -n "$CLOUDFLARE_URL" ]]; then
            echo "        \"url\": \"${CLOUDFLARE_URL}/recovery.img\""
        else
            echo "        \"url\": \"https://downloads.sourceforge.net/project/${SF_PROJ}/${CODENAME}/recovery.img\""
        fi
        echo "      }"
    else
        echo "      \"recovery\": null"
    fi
    echo "    }"
    echo "  ]"
    echo "}"
} > "$OTA_JSON"

echo "📄 OTA JSON saved to: $OTA_JSON"
if $USE_R2 && [[ -n "$CLOUDFLARE_URL" ]]; then
    echo "🔗 OTA ZIP (Cloudflare R2): ${CLOUDFLARE_URL}/${OTA_FILENAME}"
    if [[ "$HAS_RECOVERY" == true ]]; then
        echo "🔗 Recovery (Cloudflare R2): ${CLOUDFLARE_URL}/recovery.img"
    fi
    echo "🔗 OTA ZIP (SourceForge): https://downloads.sourceforge.net/project/${SF_PROJ}/${CODENAME}/${OTA_FILENAME}"
    if [[ "$HAS_RECOVERY" == true ]]; then
        echo "🔗 Recovery (SourceForge): https://downloads.sourceforge.net/project/${SF_PROJ}/${CODENAME}/recovery.img"
    fi
else
    echo "🔗 OTA ZIP: https://downloads.sourceforge.net/project/${SF_PROJ}/${CODENAME}/${OTA_FILENAME}"
    if [[ "$HAS_RECOVERY" == true ]]; then
        echo "🔗 Recovery: https://downloads.sourceforge.net/project/${SF_PROJ}/${CODENAME}/recovery.img"
    fi
fi
