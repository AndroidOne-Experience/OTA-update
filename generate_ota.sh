#!/bin/bash

# Path to the maintainer info file (not needed anymore in minimal format)

# Prompt user for device codename
read -p "Enter device codename (e.g. PL2, miatoll): " codename

# Prompt user for release tag
read -p "Enter release tag (e.g. mm-yyyy): " release_tag

# OTA package directory and pattern
ota_package_dir="out/target/product/${codename}"
file_path=$(ls ${ota_package_dir}/AndroidOne-*${codename}-*.zip 2>/dev/null)

# Check if the OTA package exists
if [[ -z "$file_path" ]]; then
  echo "OTA package file not found in ${ota_package_dir}."
  echo "Ensure the file follows the pattern: AndroidOne-*${codename}-*.zip"
  exit 1
else
  echo "Found OTA package: $file_path"
fi

# Extract info
filename=$(basename "$file_path")
datetime=$(grep -oP '^ro.build.date.utc=\K\d+' "${ota_package_dir}/system/build.prop" 2>/dev/null || echo "UNKNOWN")
id=$(sha256sum "$file_path" | awk '{ print $1 }')
size=$(stat -c%s "$file_path")
version="15"  # or change based on release/version scheme

# Replace this with your actual storage URL base
base_url="https://storage.googleapis.com/rom"
url="${base_url}/${filename}"

# Output directory
output_dir="./OTA/devices"
mkdir -p "$output_dir"
output_file="${output_dir}/${codename}.json"

# Create the JSON
json=$(cat <<EOF
{
  "response": [
    {
      "datetime": "$datetime",
      "filename": "$filename",
      "id": "$id",
      "size": $size,
      "url": "$url",
      "version": "$version"
    }
  ]
}
EOF
)

# Save prettified if jq is available
if command -v jq &> /dev/null; then
  echo "$json" | jq '.' > "$output_file"
else
  echo "$json" > "$output_file"
fi

echo "Minimal OTA JSON saved to $output_file"
