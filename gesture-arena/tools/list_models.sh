#!/data/data/com.termux/files/usr/bin/bash
cd ~/gf/models || exit 1
for z in *.zip; do
  echo "== $z"
  python -m zipfile -l "$z"
done
