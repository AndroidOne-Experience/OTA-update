#!/usr/bin/env bash
# Bash port of generate_ota.py. Requires Bash 4+, curl, jq and GNU coreutils.
# token.txt is data, never shell code. Paths are relative to this script/AOSP.
set -o pipefail
YELLOW=$'\033[93m' WHITE=$'\033[97m' GREEN=$'\033[92m'
RED=$'\033[91m' BLUE=$'\033[94m' RESET=$'\033[0m'
printf -v SEPARATOR '%101s' ''; SEPARATOR=${SEPARATOR// /-}
GRAPH_BASE_URL=https://graph.microsoft.com/v1.0
CHUNK_SIZE=$((128 * 320 * 1024))
error='' token='' expiry=0 upload_url='' progress_pid='' reader_pid='' work=''
credential_screen=0 terminal_state=''
clear_current_line() { if [[ -t 1 ]]; then printf '\r\033[2K'; fi; }
clear_previous_line() { if [[ -t 1 ]]; then printf '\033[1A\033[2K'; fi; }
trim() { REPLY=$1; REPLY="${REPLY#"${REPLY%%[![:space:]]*}"}"; REPLY="${REPLY%"${REPLY##*[![:space:]]}"}"; }
fail() { error=$1; return 1; }
usage() { printf 'usage: %s [-h] [--upload | --json] [device]\n' "${0##*/}"; }
arg_error() { usage >&2; printf '%s: error: %s\n' "${0##*/}" "$1" >&2; exit 2; }
mode='' device='' positional=0 end_options=0
for arg in "$@"; do
    if (( !end_options )); then
        case $arg in
            -h|--help) usage; printf '\npositional arguments:\n  device      Device codename\n\noptions:\n  -h, --help  show this help message and exit\n  --upload    Upload OTA/recovery only\n  --json      Generate JSON only\n'; exit 0 ;;
            --) end_options=1; continue ;;
            --upload|--u|--up|--upl|--uplo|--uploa) [[ $mode != json ]] || arg_error 'argument --upload: not allowed with argument --json'; mode=upload; continue ;;
            --json|--j|--js|--jso) [[ $mode != upload ]] || arg_error 'argument --json: not allowed with argument --upload'; mode=json; continue ;;
            -*) arg_error "unrecognized arguments: $arg" ;;
        esac
    fi
    (( positional == 0 )) || arg_error "unrecognized arguments: $arg"
    device=$arg; positional=1
done
if [[ -z $device ]]; then
    printf '%sEnter device codename:%s ' "$YELLOW" "$RESET"
    IFS= read -r device || exit 1
    clear_previous_line
fi
trim "$device"; device=$REPLY
if [[ -z $device ]]; then printf '%sError: Device codename cannot be empty.%s\n' "$RED" "$RESET"; exit 1; fi
script_dir=$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")
aosp_root=''
for start_dir in "$PWD" "$script_dir"; do
    directory=$(realpath -- "$start_dir")
    while :; do
        product="$directory/out/target/product/$device"
        [[ $device != /* ]] || product=$device
        if [[ -d $product ]]; then aosp_root=$directory; break 2; fi
        [[ $directory != / ]] || break
        directory=$(dirname -- "$directory")
    done
done
if [[ -z $aosp_root ]]; then
    printf '%sError: Could not locate AOSP build root.%s\nExpected to find: out/target/product/%s/\n' "$RED" "$RESET" "$device"; exit 1
fi
# The Python glob is case sensitive for .zip, but its filename regex is not.
latest='' ota_file='' count=0
shopt -s nullglob
for file in "$product"/*.zip; do
    name=${file##*/}; prefix="AndroidOne-$device-OTA-"
    [[ ${name,,} == "${prefix,,}"* ]] || continue
    suffix=${name:${#prefix}}
    [[ $suffix =~ ^([0-9]{8})-([0-9]{4})\.[zZ][iI][pP]$ ]] || continue
    stamp=${BASH_REMATCH[1]}${BASH_REMATCH[2]}
    valid=$(date -u -d "${stamp:0:4}-${stamp:4:2}-${stamp:6:2} ${stamp:8:2}:${stamp:10:2}:00" +%Y%m%d%H%M 2>/dev/null) || continue
    [[ $valid == "$stamp" && ${stamp:0:4} != 0000 ]] || continue
    if [[ -z $latest || $stamp > $latest ]]; then latest=$stamp; ota_file=$file; count=1
    elif [[ $stamp == "$latest" ]]; then ((count+=1)); fi
done
if (( count == 0 )); then
    printf '%sError: No valid AndroidOne OTA found.%s\nExpected filename:\nAndroidOne-%s-OTA-YYYYMMDD-HHMM.zip\n' "$RED" "$RESET" "$device"; exit 1
elif (( count != 1 )); then
    printf '%sError: Multiple OTA files have the same latest timestamp.%s\n' "$RED" "$RESET"; exit 1
fi
declare -A config=([ANDROID]='' [TENANT_ID]='' [CLIENT_ID]='' [CLIENT_SECRET]='' [DRIVE_ID]='' [FOLDER_ID]='')
if [[ -f $script_dir/token.txt && -r $script_dir/token.txt ]]; then
    while IFS= read -r line || [[ -n $line ]]; do
        trim "$line"; line=$REPLY
        [[ -n $line && $line != \#* && $line == *=* ]] || continue
        trim "${line%%=*}"; key=$REPLY
        case $key in ANDROID|TENANT_ID|CLIENT_ID|CLIENT_SECRET|DRIVE_ID|FOLDER_ID) trim "${line#*=}"; config[$key]=$REPLY ;; esac
    done < "$script_dir/token.txt"
fi
android_version=${config[ANDROID]}
missing=''
for key in TENANT_ID CLIENT_ID CLIENT_SECRET DRIVE_ID FOLDER_ID; do
    [[ -n ${config[$key]} ]] || missing+="${missing:+, }$key"
done
show_missing() {
    printf '%sRequired OneDrive credentials are missing from token.txt.%s\n' "$YELLOW" "$RESET"
    printf '%sThe following values are required to upload OTA files to OneDrive cloud storage:%s\n' "$RED" "$RESET"
    printf '%s"%s"%s\n' "$WHITE" "$missing" "$RESET"
}
show_tip() {
    local key
    printf '%sTip: token.txt requires these details:%s\n\n%sANDROID=XX%s\n\n' "$YELLOW" "$RESET" "$YELLOW" "$RESET"
    for key in TENANT_ID CLIENT_ID CLIENT_SECRET DRIVE_ID FOLDER_ID; do printf '%s%s=xxxxxxxxxxxxxxxx%s\n' "$YELLOW" "$key" "$RESET"; done
}
restore_entry_screen() {
    if [[ -n $terminal_state ]]; then
        stty "$terminal_state" 2>/dev/null || :
        terminal_state=''
    fi
    if (( credential_screen )); then
        printf '\033[H\033[2J\033[?1049l'
        credential_screen=0
    fi
}
masked_secret() {
    local char escape='' value=''
    if [[ ! -t 0 || ! -t 1 ]]; then
        fail 'Masked secret entry requires an interactive terminal. Set CLIENT_SECRET in token.txt instead.'
        return 1
    fi
    terminal_state=$(stty -g) || { fail 'Could not read terminal settings.'; return 1; }
    stty -echo -icanon min 1 time 0 || { fail 'Could not enable masked entry.'; return 1; }
    while :; do
        IFS= read -r -s -N 1 char || cancelled
        if [[ -n $escape ]]; then
            escape+=$char
            if (( ${#escape} == 2 )) && [[ $char != '[' && $char != O ]]; then
                escape=''
            elif (( ${#escape} > 2 )) && [[ $char == [@-~] ]]; then
                escape=''
            fi
            continue
        fi
        case $char in
            $'\033') escape=$char ;;
            $'\n'|$'\r') break ;;
            $'\003'|$'\004') cancelled ;;
            $'\177'|$'\b')
                if [[ -n $value ]]; then value=${value%?}; printf '\b \b'; fi ;;
            $'\025')
                while [[ -n $value ]]; do value=${value%?}; printf '\b \b'; done ;;
            *) if [[ $char == [[:print:]] ]]; then value+=$char; printf '*'; fi ;;
        esac
    done
    stty "$terminal_state" || { fail 'Could not restore terminal settings.'; return 1; }
    terminal_state=''
    printf '\n'
    trim "$value"
}
prompt_config_value() {
    local key=$1 value
    while :; do
        printf '%sEnter %s:%s ' "$YELLOW" "$key" "$RESET"
        if [[ $key == CLIENT_SECRET ]]; then
            masked_secret || return 1
        else
            IFS= read -r value || cancelled
            trim "$value"
        fi
        value=$REPLY
        if [[ -z $value ]]; then
            printf '%s%s cannot be empty. Press Ctrl+C to cancel.%s\n' "$YELLOW" "$key" "$RESET"
            continue
        fi
        if [[ $key == ANDROID && ! $value =~ ^[0-9]+(\.[0-9]+)*$ ]]; then
            printf '%sEnter an Android version such as 16 or 16.1.%s\n' "$YELLOW" "$RESET"
            continue
        fi
        config[$key]=$value
        return 0
    done
}
complete_token_config() {
    local choice key
    if [[ -z $android_version || ( $mode != json && -n $missing ) ]]; then
        if [[ -t 0 && -t 1 ]]; then
            credential_screen=1
            printf '\033[?1049h\033[H\033[2J'
        fi
    fi
    if [[ -z $android_version ]]; then
        printf '%sANDROID version was not found in token.txt.%s\n' "$YELLOW" "$RESET"
        prompt_config_value ANDROID || return 1
        android_version=${config[ANDROID]}
    fi
    if [[ $mode == json || -z $missing ]]; then return 0; fi
    show_missing
    while :; do
        printf 'Would you like to input the credential details manually? [y/n]: '
        IFS= read -r choice || cancelled
        trim "$choice"; choice=${REPLY,,}
        case $choice in
            n|no) upload=0; return 0 ;;
            y|yes) break ;;
        esac
        printf '%sPlease enter y or n.%s\n' "$YELLOW" "$RESET"
    done
    printf '%sValues entered here are used for this run only.%s\n' "$YELLOW" "$RESET"
    for key in TENANT_ID CLIENT_ID CLIENT_SECRET DRIVE_ID FOLDER_ID; do
        if [[ -z ${config[$key]} ]]; then prompt_config_value "$key" || return 1; fi
    done
}
require_command() { command -v "$1" >/dev/null || fail "Required command not found: $1"; }
generate_json() {
    printf '%sGenerating...%s' "$BLUE" "$RESET"
    local prop="$product/system/build.prop" build_datetime='' found=0 line ota_size ota_hash ota_url='' json_path="$script_dir/devices/$device.json"
    if [[ ! -f $prop ]]; then clear_current_line; fail "system/build.prop not found: $prop"; return 1; fi
    while IFS= read -r line || [[ -n $line ]]; do
        trim "$line"; line=$REPLY
        if [[ $line == ro.build.date.utc=* ]]; then trim "${line#*=}"; build_datetime=$REPLY; found=1; break; fi
    done < "$prop"
    if (( !found )); then clear_current_line; fail 'ro.build.date.utc was not found.'; return 1; fi
    if [[ -z $build_datetime ]]; then clear_current_line; fail 'ro.build.date.utc is empty.'; return 1; fi
    if [[ ! $build_datetime =~ ^[0-9]+$ ]]; then clear_current_line; fail 'ro.build.date.utc contains an invalid value.'; return 1; fi
    require_command jq || return 1
    ota_size=$(stat -Lc %s -- "$ota_file") || { fail 'Failed to read OTA size.'; return 1; }
    ota_hash=$(sha256sum < "$ota_file") || { fail 'Failed to calculate SHA-256.'; return 1; }; ota_hash=${ota_hash%% *}
    [[ -z $android_version ]] || ota_url="https://ota.androidone.workers.dev/api/$device/android-$android_version/${ota_file##*/}"
    mkdir -p -- "$script_dir/devices" || { fail 'Failed to create devices directory.'; return 1; }
    jq -na --arg dt "$build_datetime" --arg filename "${ota_file##*/}" --arg id "$ota_hash" --argjson size "$ota_size" --arg url "$ota_url" --arg version "$android_version" \
        '{response: [{datetime: $dt, filename: $filename, id: $id, size: $size, url: (if $url == "" then null else $url end), version: (if $version == "" then null else $version end)}]}' > "$json_path" || { fail 'Failed to write JSON file.'; return 1; }
    clear_current_line
    printf '%sDevice:%s    %s%s%s\n%sOTA:%s       %s%s%s\n' "$YELLOW" "$RESET" "$WHITE" "$device" "$RESET" "$YELLOW" "$RESET" "$WHITE" "${ota_file##*/}" "$RESET"
    printf '%sSize:%s      %s%s GB%s\n' "$YELLOW" "$RESET" "$WHITE" "$(awk -v n="$ota_size" 'BEGIN {printf "%.1f", n/1073741824}')" "$RESET"
    printf '%sSHA-256:%s   %s%s%s\n%sOTA URL:%s   %s%s%s\n%sJSON Path:%s %s%s%s\n' "$YELLOW" "$RESET" "$WHITE" "$ota_hash" "$RESET" "$YELLOW" "$RESET" "$WHITE" "${ota_url:-null}" "$RESET" "$YELLOW" "$RESET" "$WHITE" "$json_path" "$RESET"
    if [[ -z $android_version ]]; then printf '\n%sWarning: ANDROID version was not found in token.txt. Value for %s"url & version"%s were written as null.%s\n' "$RED" "$YELLOW" "$RED" "$RESET"; fi
}
json_only() {
    if ! generate_json; then printf '%sJSON generation failed: %s%s\n' "$RED" "$error" "$RESET"; exit 1; fi
    printf '%sJSON file successfully generated.%s\n\n' "$GREEN" "$RESET"
}
# HTTP results live in private files so token refreshes survive function calls.
http() {
    local method=$1 url=$2; shift 2
    local headers=()
    [[ -z $token ]] || headers=(-H "Authorization: Bearer $token")
    status=$(curl --silent --show-error --location --connect-timeout 15 --speed-limit 1 --speed-time 300 \
        -X "$method" "${headers[@]}" -D "$work/headers" -o "$work/body" -w '%{http_code}' "$@" "$url" 2> "$work/curl-error")
    local rc=$?
    if (( rc )); then error=$(cat "$work/curl-error"); return 1; fi
}
graph_error() {
    error=$(jq -r '.error // {} | if type == "object" then if .code and .message then "\(.code): \(.message)" elif .message then .message else tostring end else tostring end' "$work/body" 2>/dev/null) || error=$(cat "$work/body")
    [[ -n $error ]] || error='Unknown Graph error'
}
ensure_token() {
    local now result
    now=$(date +%s)
    if [[ ${1:-} != force && -n $token ]] && (( now + 300 < expiry )); then return 0; fi
    # OAuth2 client-credentials grant, equivalent to MSAL acquire_token_for_client.
    http POST "https://login.microsoftonline.com/${config[TENANT_ID]}/oauth2/v2.0/token" \
        --data-urlencode "client_id=${config[CLIENT_ID]}" --data-urlencode "client_secret=${config[CLIENT_SECRET]}" \
        --data-urlencode 'scope=https://graph.microsoft.com/.default' --data-urlencode 'grant_type=client_credentials' || return 1
    result=$(jq -r '.access_token // empty' "$work/body")
    if [[ -z $result ]]; then error=$(jq -r '.error_description // "Microsoft authentication failed."' "$work/body"); return 1; fi
    token=$result; expiry=$((now + $(jq -r '.expires_in // 3600' "$work/body")))
}
graph_request() {
    local method=$1 endpoint=$2; shift 2
    [[ $endpoint == http* ]] || endpoint=$GRAPH_BASE_URL$endpoint
    ensure_token || return 1
    http "$method" "$endpoint" "$@" || return 1
    if [[ $status == 401 ]]; then ensure_token force || return 1; http "$method" "$endpoint" "$@" || return 1; fi
}
ok_response() { if (( status >= 400 )); then graph_error; return 1; fi; }
get_folder() {
    local parent=$1 name=$2 endpoint="/drives/${config[DRIVE_ID]}/items/$1/children" candidate='' first=''
    while [[ -n $endpoint ]]; do
        graph_request GET "$endpoint" || return 1; ok_response || return 1
        candidate=$(jq -r --arg name "$name" '[.value[]? | select(.name == $name and has("folder"))][0].id // empty' "$work/body") || return 1
        [[ -n $first ]] || first=$candidate
        endpoint=$(jq -r '."@odata.nextLink" // empty' "$work/body")
    done
    if [[ -n $first ]]; then folder_id=$first; return 0; fi
    graph_request POST "/drives/${config[DRIVE_ID]}/items/$parent/children" -H 'Content-Type: application/json' --data "$(jq -nc --arg name "$name" '{name:$name,folder:{}}')" || return 1
    ok_response || return 1
    folder_id=$(jq -er '.id' "$work/body")
}
next_position() {
    next_start=$(jq -r '[.nextExpectedRanges // [] | if type == "string" then [.] else . end | .[] | tostring | capture("^\\s*(?<n>[0-9]+)") | .n | tonumber] | min // empty' "$work/body" 2>/dev/null) || next_start=''
}
stop_progress() { if [[ -n $progress_pid ]]; then kill "$progress_pid" 2>/dev/null; wait "$progress_pid" 2>/dev/null; progress_pid=''; fi; }
cancel_session() { if [[ -n $upload_url ]]; then http DELETE "$upload_url" --max-time 30 >/dev/null 2>&1 || :; upload_url=''; fi; }
stop_reader() { if [[ -n $reader_pid ]]; then kill "$reader_pid" 2>/dev/null; wait "$reader_pid" 2>/dev/null; reader_pid=''; fi; }
cleanup() {
    local exit_status=$?
    restore_entry_screen
    stop_reader; stop_progress
    [[ -z $work ]] || rm -rf -- "$work"
    printf '\n'
    show_tip
    printf '%sSave these values in token.txt beside this script to avoid manual entry. Manually entered values are not saved automatically.%s\n\n' "$YELLOW" "$RESET"
    return "$exit_status"
}
trap cleanup EXIT
cancelled() { restore_entry_screen; stop_reader; stop_progress; clear_current_line; cancel_session; clear_current_line; printf '%s%s cancelled.%s\n' "$RED" "${cancel_label:-Operation}" "$RESET"; exit 130; }
monotonic() { read -r REPLY _ < /proc/uptime; }
draw_stats() {
    local done=$1 total=$2 started=$3 final=${4:-0}
    monotonic
    awk -v done="$done" -v total="$total" -v elapsed="$(awk -v n="$REPLY" -v s="$started" 'BEGIN {print n-s}')" -v final="$final" -v y="$YELLOW" -v w="$WHITE" -v r="$RESET" '
    function human(n, floating) {if(n<1024) return (floating ? sprintf("%.1f",n) : n) " B"; if(n<1048576) return sprintf("%.2f KB",n/1024); if(n<1073741824) return sprintf("%.2f MB",n/1048576); return sprintf("%.2f GB",n/1073741824)}
    BEGIN {if(elapsed<.001) elapsed=.001; s=int(elapsed); h=int(s/3600); m=int(s%3600/60); s=s%60; duration=(h ? h "h " m "m " s "s" : (m ? m "m " s "s" : s "s")); printf "\r\033[2K%sStats:%s %s%6.2f%% | %s/%s | %s/s | Time: %s%s",y,r,w,(total ? done/total*100 : 100),human(done,0),human(total,0),human(done/elapsed,1),duration,r; if(final) printf "\n"; }'
}
stream_chunk() {
    local file=$1 start=$2 end=$3 total=$4 expected=$(( $3 - $2 + 1 ))
    local stream_fd http_rc=0 reader_rc=0 copied
    # Reopen the exact range on every attempt. curl's upload mode streams stdin
    # instead of buffering --data-binary or copying the range to a scratch file.
    exec {stream_fd}< <(LC_ALL=C dd if="$file" bs=1M iflag=skip_bytes,count_bytes \
        skip="$start" count="$expected" 2> "$work/read-status")
    reader_pid=$!
    http PUT "$upload_url" --http1.1 -H 'Expect:' -H 'Transfer-Encoding:' \
        -H "Content-Length: $expected" -H "Content-Range: bytes $start-$end/$total" \
        -H 'Content-Type: application/octet-stream' --upload-file - <&"$stream_fd" || http_rc=$?
    exec {stream_fd}<&-
    wait "$reader_pid" || reader_rc=$?
    reader_pid=''
    # SIGPIPE is possible when the server rejects a range before reading it;
    # let the existing HTTP retry/resume logic handle that response.
    if ((reader_rc != 0 && reader_rc != 141)); then
        fail 'Short read while reading upload chunk.'; return 2
    fi
    if ((reader_rc == 0)); then
        copied=$(awk '/^[0-9]+ bytes? / {print $1}' "$work/read-status")
        if [[ $copied != "$expected" ]]; then
            fail 'Short read while reading upload chunk.'; return 2
        fi
    fi
    return "$http_rc"
}
upload_file() {
    local file=$1 parent=$2 label=$3 total start=0 end attempt last_error delay next_start started encoded saved_status
    total=$(stat -Lc %s -- "$file") || { fail 'Failed to read upload file size.'; return 1; }
    encoded=$(jq -nr --arg name "${file##*/}" '$name | @uri')
    graph_request POST "/drives/${config[DRIVE_ID]}/items/$parent:/$encoded:/createUploadSession" \
        -H 'Content-Type: application/json' --data "$(jq -nc --arg name "${file##*/}" '{item:{"@microsoft.graph.conflictBehavior":"replace",name:$name}}')" || return 1
    ok_response || return 1
    upload_url=$(jq -r '.uploadUrl // empty' "$work/body")
    [[ -n $upload_url ]] || { fail 'Microsoft Graph did not return uploadUrl.'; return 1; }
    printf '%s\n%sUploading %s file:%s %s%s%s\n' "$SEPARATOR" "$YELLOW" "$label" "$RESET" "$WHITE" "${file##*/}" "$RESET"
    monotonic; started=$REPLY
    printf '0\n' > "$work/progress"
    (trap - EXIT INT TERM; while sleep 0.2; do read -r done < "$work/progress"; draw_stats "${done:-0}" "$total" "$started"; done) & progress_pid=$!
    while (( start < total )); do
        end=$((start + CHUNK_SIZE - 1)); ((end < total)) || end=$((total-1))
        for ((attempt=1; attempt<=5; attempt++)); do
            if stream_chunk "$file" "$start" "$end" "$total"; then
                case $status in
                    200|201|202|409|416) break ;;
                    408|429|500|502|503|504) graph_error; last_error="HTTP $status: $error" ;;
                    *) graph_error; error="HTTP $status: $error"; return 1 ;;
                esac
            else
                [[ $? != 2 ]] || return 1
                last_error=$error
            fi
            if ((attempt < 5)); then
                delay=$(awk 'tolower($0) ~ /^retry-after:/ {sub(/^[^:]*:[[:space:]]*/, ""); sub(/\r$/, ""); if ($0 ~ /^[0-9]+([.][0-9]+)?$/) d=$0} END {print d}' "$work/headers")
                sleep "${delay:-$((2 ** (attempt-1)))}"
            fi
        done
        if ((attempt > 5)); then fail "Chunk $start-$end failed after 5 attempts: $last_error"; return 1; fi
        case $status in
            200|201) start=$total ;;
            202) next_position; if [[ -z $next_start ]] || ((next_start <= start)); then next_start=$((end+1)); fi; start=$next_start ;;
            409|416)
                saved_status=$status; next_start=''
                if http GET "$upload_url" && [[ $status == 200 ]]; then next_position; fi
                [[ -n $next_start ]] || { fail "Could not recover upload position after HTTP $saved_status."; return 1; }
                start=$next_start ;;
        esac
        ((start <= total)) || start=$total
        printf '%s\n' "$start" > "$work/progress"
    done
    stop_progress; draw_stats "$total" "$total" "$started" 1
    upload_url=''
    printf '%sUploaded successfully.%s\n' "$GREEN" "$RESET"
}
perform_upload() {
    require_command curl && require_command jq || return 1
    work=$(mktemp -d) || { fail 'Failed to create temporary directory.'; return 1; }
    chmod 700 "$work"
    trap cancelled INT
    printf '%sAuthenticating with OneDrive...%s' "$YELLOW" "$RESET"
    local destination_id
    ensure_token || return 1
    clear_current_line; printf '%sOneDrive authentication successful.%s\n\n' "$GREEN" "$RESET"
    get_folder "${config[FOLDER_ID]}" "$device" || return 1
    get_folder "$folder_id" "android-$android_version" || return 1
    destination_id=$folder_id
    upload_file "$ota_file" "$destination_id" OTA || return 1
    if [[ -f $product/recovery.img ]]; then upload_file "$product/recovery.img" "$destination_id" recovery || return 1; fi
    printf '%s\n' "$SEPARATOR"
}
cancel_label=Operation; [[ $mode != upload ]] || cancel_label=Upload
trap cancelled INT
trap 'restore_entry_screen; exit 143' TERM
upload=1; [[ $mode != json ]] || upload=0
if ! complete_token_config; then
    restore_entry_screen
    printf '%s%s failed: %s%s\n' "$RED" "$cancel_label" "$error" "$RESET"
    exit 1
fi
restore_entry_screen
if (( !upload )); then
    if [[ $mode == upload ]]; then
        printf '%sUpload skipped: required credentials were not provided.%s\n' "$YELLOW" "$RESET"
        exit 1
    fi
    if [[ $mode != json ]]; then
        printf '%sCloud upload skipped. Continuing with JSON generation.%s\n\n' "$YELLOW" "$RESET"
    fi
    json_only; exit
fi
if ! perform_upload; then
    saved_error=$error; stop_progress; clear_current_line; cancel_session; clear_current_line
    printf '%s%s failed: %s%s\n' "$RED" "$cancel_label" "$saved_error" "$RESET"; exit 1
fi
if [[ $mode == upload ]]; then
    printf '%sOTA file successfully uploaded to cloud storage%s\n\n' "$GREEN" "$RESET"
else
    if ! generate_json; then clear_current_line; printf '%sOperation failed: %s%s\n' "$RED" "$error" "$RESET"; exit 1; fi
    printf '%sOTA file successfully uploaded to cloud & JSON file successfully generated.%s\n\n' "$GREEN" "$RESET"
fi
