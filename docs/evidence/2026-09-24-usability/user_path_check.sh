#!/usr/bin/env bash
# usage: user_path_check.sh OWNER/REPO WORKDIR
# The owner's own path: GitHub "Download ZIP" of main -> Install.exe -> start the app.
set -u
repo=$1; work=$2
py=/c/Users/basam/src/spice-maker/.venv/Scripts/python.exe
rm -rf "$work"; mkdir -p "$work"; cd "$work"
t0=$(date +%s)
curl -sSL -o main.zip "https://github.com/$repo/archive/refs/heads/main.zip" || { echo "FAIL download"; exit 1; }
echo "zip sha256: $(sha256sum main.zip | cut -c1-16)  bytes: $(stat -c %s main.zip)"
"$py" -c "import zipfile,sys; zipfile.ZipFile('main.zip').extractall('x')" || { echo "FAIL extract"; exit 1; }
root=$(ls -d x/*/ | head -1); cd "$root"
echo "extracted: $root"
want=$(cut -d' ' -f1 SHA256SUMS.txt); have=$(sha256sum Install.exe | cut -d' ' -f1)
[ "$want" = "$have" ] && echo "PASS Install.exe matches SHA256SUMS.txt ($(stat -c %s Install.exe) bytes)" || echo "FAIL Install.exe hash $have != $want"
t1=$(date +%s)
./Install.exe --silent --no-launch; rc=$?
t2=$(date +%s)
echo "Install.exe --silent --no-launch: exit $rc in $((t2-t1)) s"
for f in app/SpiceMaker.exe Start.cmd Boardmodeler.cmd .venv/Scripts/python.exe; do [ -e "$f" ] && echo "PASS present: $f" || echo "FAIL missing: $f"; done
./app/SpiceMaker.exe --cli version 2>&1 | head -3
cmd //c "Boardmodeler.cmd version" 2>&1 | head -3
cmd //c "Boardmodeler.cmd doctor --json" > doctor.json 2>&1; "$py" -c "
import json; d=json.load(open('doctor.json')); l=d.get('ltspice',{})
print('doctor: version', d.get('version'), '| ltspice found:', l.get('found'), '| reason:', l.get('reason'))" 2>&1 | tail -2
"$py" /c/Users/basam/src/spice-maker/installer/verify_gui.py "app/SpiceMaker.exe" --screenshot "$work/gui-startup.png" 2>&1 | tail -3
echo "total $(( $(date +%s) - t0 )) s"
