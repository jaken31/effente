#!/usr/bin/env bash
# Download the SSCC chain-conveyor dataset (arXiv 2603.07130, CC BY 4.0) from its public Google Drive
# folder, one part at a time, resuming partial files and backing off when Drive throttles.
#
#     bash training/record/datasets/sscc_download.sh data/real/sscc/raw >> data/real/sscc/download.log 2>&1
#
# Each part's size comes from Drive itself (the Content-Range of a 1-byte request); a part counts as
# done only when its size on disk matches. Folder:
# https://drive.google.com/drive/folders/1FxJBwPrZy21SBwPBI5rCoRAEEEnD6r7E
set -u
OUT=${1:?usage: sscc_download.sh OUT_DIR}
PARTS="
1nv52vujcHrVM9aflx4ab-iHoWAckS2Uo normal/SSCC_dataset_normal.7z.001
1l7x16p8UAoM4vAH4OUxtD1ufIJxLqEoA normal/SSCC_dataset_normal.7z.002
1FYBby8FP-VzbRaTX1XOM1wb5_TgRg9o- normal/SSCC_dataset_normal.7z.003
1SqR0w6vD0uBRcmQ_vdSvdrI9gzG5j2cr normal/SSCC_dataset_normal.7z.004
1JHXR2CPl0ol59SPge9I2ArJI2gC8dmA4 normal/SSCC_dataset_normal.7z.005
1usoqiicowMdHZols0qiKVerwP-ycRHdB normal/SSCC_dataset_normal.7z.006
1v9AtB4FQUxbw3qra4-nMpkmAZV04v4XY abnormal/SSCC_dataset_abnormal.7z.001
1coj7rrCiYac7puzP-Qt58Veln3x6qk_n abnormal/SSCC_dataset_abnormal.7z.002
1IHwTgTx7YVHSxXVnYpQHeGPn0K8Eyme1 abnormal/SSCC_dataset_abnormal.7z.003
12nvEs-mvxVwKWDSsdidRD_yJWkIs_blc abnormal/SSCC_dataset_abnormal.7z.004
1qdGKQqzvkaUelRv-sdO_0DQWzCl0N47m abnormal/SSCC_dataset_abnormal.7z.005
1CikQyxTCEU6Mn8jjsNcNsAgKuvOp3UWs abnormal/SSCC_dataset_abnormal.7z.006
1HK_91tQKbyQcQqyugUc-_4o1NgfbQ4Rc abnormal/SSCC_dataset_abnormal.7z.007
1W1U1_dhLQxh8ZVgDZr-PlfZYcL5Eji9z abnormal/SSCC_dataset_abnormal.7z.008
1Z3YdIs_J7t0myxIKO0iMyKcQOzIvFq-M abnormal/SSCC_dataset_abnormal.7z.009
1NewHer7ilGaqj8IVyH2_PJiyCiF85ejR abnormal/SSCC_dataset_abnormal.7z.010
1B1UUb3C7pyJDPJXe0L_RZd2iUKbPquhU abnormal/SSCC_dataset_abnormal.7z.011
19dcBlN5t5vQGPw837-1QzNO6PZkTROVb abnormal/SSCC_dataset_abnormal.7z.012
1gej-zSpcDwx4G1hRrrFQfQ59HrAd_eOv abnormal/SSCC_dataset_abnormal.7z.013
1YJO1xc_0VnNNQyeMAFlmKtjH2X1ioB42 abnormal/SSCC_dataset_abnormal.7z.014
1UdnqDu_QnEPoPHnwfVkAXaFbJVdzRvYw abnormal/SSCC_dataset_abnormal.7z.015
"
url() { echo "https://drive.usercontent.google.com/download?id=$1&export=download&confirm=t"; }
log() { echo "$(date '+%F %T') $*"; }
fsize() { stat -f %z "$1" 2>/dev/null || echo 0; }

remote_size() {  # prints the size, or nothing if Drive answered with a page instead of the file
  curl -s -L -r 0-0 -D - -o /dev/null --max-time 60 "$(url "$1")" |
    tr -d '\r' | awk -F/ 'tolower($0) ~ /^content-range:/ {print $NF}' | tail -1
}

echo "$PARTS" | while read -r id path; do
  [ -z "$id" ] && continue
  f="$OUT/$path"; mkdir -p "$(dirname "$f")"
  wait=60
  while :; do
    total=$(remote_size "$id")
    if [ -z "$total" ]; then
      log "$path: no size from Drive (throttled?), waiting ${wait}s"; sleep $wait
      wait=$(( wait < 300 ? wait * 2 : 300 )); continue
    fi
    have=$(fsize "$f")
    if [ "$have" = "$total" ]; then log "$path: done, $total bytes"; break; fi
    if [ "$have" -gt "$total" ]; then log "$path: bigger than Drive's $total, restarting"; rm -f "$f"; have=0; fi
    log "$path: have $have of $total, downloading"
    t0=$(date +%s)
    curl -s -L -C - --fail --retry 5 --retry-delay 30 --speed-limit 10000 --speed-time 120 \
      -o "$f" "$(url "$id")"
    rc=$?
    now=$(fsize "$f"); dt=$(( $(date +%s) - t0 + 1 ))
    log "$path: curl exit $rc, now $now bytes, $(( (now - have) / dt / 1048576 )) MiB/s"
    if [ "$now" = "$total" ]; then log "$path: done, $total bytes"; break; fi
    if [ "$now" -lt 100000 ] && grep -qi "<html" "$f" 2>/dev/null; then
      log "$path: got a web page, not the file (Drive quota?); removing, waiting ${wait}s"; rm -f "$f"; now=0
    fi
    [ "$now" -gt "$have" ] && wait=60 || { sleep $wait; wait=$(( wait < 300 ? wait * 2 : 300 )); }
  done
done
log "all parts checked"
