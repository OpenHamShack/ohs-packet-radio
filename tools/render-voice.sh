#!/usr/bin/env bash
# Render the notification phrases with Piper's en_GB-cori-high voice into
# src/shack_packet_app/sounds/ (0.3.0; notifysound.py plays them).
#
#   tools/render-voice.sh [--keep]
#
# Brian chose the voice on 20/10/2026 (Cori: female, British). Piper and
# the model are needed only here, once: the app ships the three short WAVs
# and never runs Piper. The voice model's card says its dataset is public
# domain; the card is saved beside the clips' record in
# packaging/THIRD-PARTY.txt (by hand, from what this prints).
#
# Everything is fetched into ~/build/piper-voice (a venv and the model),
# removed afterwards unless --keep. The clips are 22050 Hz mono 16-bit (the
# model's own rate), with leading and trailing silence trimmed to 30 ms, and
# no chime: notifysound.py puts its own in front at run time, so a change
# of chime needs no re-render.
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
work=${PIPER_WORK:-$HOME/build/piper-voice}
out=$here/src/shack_packet_app/sounds
voice=en_GB-cori-high
base=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/cori/high
keep=0; [ "${1:-}" = "--keep" ] && keep=1
mkdir -p "$work" "$out"
if [ ! -x "$work/venv/bin/piper" ]; then
    python3 -m venv "$work/venv"
    "$work/venv/bin/pip" -q install piper-tts >"$work/pip.log" 2>&1 || { tail -5 "$work/pip.log"; exit 1; }
fi
for f in "$voice.onnx" "$voice.onnx.json" MODEL_CARD; do
    [ -s "$work/$f" ] || curl -fsSL -o "$work/$f" "$base/$f"
done
# file name | phrase. The names are notifysound.CLIPS'.
while IFS='|' read -r name phrase; do
    echo "$phrase" | "$work/venv/bin/piper" -q --model "$work/$voice.onnx" --output_file "$work/$name.raw.wav" \
        >/dev/null 2>"$work/piper-$name.log" || { cat "$work/piper-$name.log"; exit 1; }
    python3 - "$work/$name.raw.wav" "$out/$name.wav" <<'EOF'
import array, sys, wave
src, dst = sys.argv[1], sys.argv[2]
with wave.open(src) as w:
    rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
    assert ch == 1 and width == 2, (ch, width)
    s = array.array("h", w.readframes(w.getnframes()))
floor = 300                                  # about -40 dBFS
loud = [i for i, v in enumerate(s) if abs(v) > floor]
pad = int(0.03 * rate)
a, b = max(0, loud[0] - pad), min(len(s), loud[-1] + pad)
with wave.open(dst, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
    w.writeframes(s[a:b].tobytes())
print(f"render-voice: {dst.rsplit('/', 1)[-1]}: {(b - a) / rate:.2f} s at {rate} Hz")
EOF
done <<'EOF'
mail|You've got mail!
message|You've got a message!
chat|New chat!
EOF
echo "render-voice: model card ($base/MODEL_CARD):"
sed 's/^/    /' "$work/MODEL_CARD"
echo "render-voice: piper-tts $("$work/venv/bin/pip" show piper-tts | sed -n 's/^Version: //p'), model sha256 $(sha256sum "$work/$voice.onnx" | cut -c1-16)"
[ "$keep" = 1 ] || rm -rf "$work"
echo "render-voice: DONE"
