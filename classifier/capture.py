"""
Capture training images straight from your webcam, so your training data
matches what the model sees at inference time.

Setup:
    pip install opencv-python

Usage (run once per class):
    python capture.py tennis
    python capture.py basketball
    python capture.py football
    python capture.py darts
    python capture.py nothing        # background / no object -- highly recommended

Controls:
    SPACE  save the current frame
    b      toggle burst mode (saves every frame while on)
    q      quit

Tip: while capturing, slowly rotate the object, move it near/far, change your
angle, and vary the background. Aim for ~150-300 frames per class.
"""

import os
import sys
import time
import cv2

if len(sys.argv) < 2:
    print("Usage: python capture.py <class_name>")
    sys.exit(1)

class_name = sys.argv[1]
out_dir = os.path.join("data", class_name)
os.makedirs(out_dir, exist_ok=True)

# Continue numbering if the folder already has images
existing = [f for f in os.listdir(out_dir) if f.endswith(".jpg")]
count = len(existing)

cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("Could not open webcam.")
    sys.exit(1)

burst = False
print(f"Saving to {out_dir}/  (starting at {count} images)")
print("SPACE = save,  b = toggle burst,  q = quit")

while True:
    ok, frame = cap.read()
    if not ok:
        break

    # On-screen info (drawn on a copy so we save the clean frame)
    display = frame.copy()
    status = "BURST ON" if burst else "ready"
    cv2.putText(display, f"{class_name}: {count} saved  [{status}]",
                (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
    cv2.imshow("Capture", display)

    key = cv2.waitKey(1) & 0xFF

    save = False
    if key == ord(" "):
        save = True
    elif key == ord("b"):
        burst = not burst
    elif key == ord("q"):
        break

    if burst:
        save = True

    if save:
        fname = os.path.join(out_dir, f"{class_name}_{int(time.time()*1000)}.jpg")
        cv2.imwrite(fname, frame)  # saves the clean frame, no overlay
        count += 1

cap.release()
cv2.destroyAllWindows()
print(f"Done. {count} images in {out_dir}/")
