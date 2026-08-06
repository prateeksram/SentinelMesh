"""
Run the trained ball classifier.

Setup:
    pip install tensorflow opencv-python numpy

Single image:
    python predict.py path/to/photo.jpg

Live webcam (hold a ball up to the camera, press 'q' to quit):
    python predict.py
"""

import sys
import numpy as np
import tensorflow as tf
from tensorflow import keras

IMG_SIZE = 224
model = keras.models.load_model("ball_classifier.keras")

with open("class_names.txt") as f:
    class_names = [line.strip() for line in f if line.strip()]


def predict_array(rgb_array):
    """rgb_array: HxWx3 uint8 RGB image. Returns (label, confidence)."""
    img = tf.image.resize(rgb_array, (IMG_SIZE, IMG_SIZE))
    img = tf.expand_dims(img, 0)  # add batch dimension
    probs = model.predict(img, verbose=0)[0]
    idx = int(np.argmax(probs))
    return class_names[idx], float(probs[idx])


def predict_file(path):
    img = keras.utils.load_img(path, target_size=(IMG_SIZE, IMG_SIZE))
    arr = keras.utils.img_to_array(img)
    label, conf = predict_array(arr)
    print(f"{path}: {label} ({conf * 100:.1f}%)")


def webcam():
    import cv2
    from collections import deque

    SMOOTH_WINDOW = 8      # average predictions over this many frames
    MIN_CONFIDENCE = 0.60  # below this, show "uncertain" instead of guessing

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Could not open webcam.")
        return
    print("Press 'q' to quit.")

    history = deque(maxlen=SMOOTH_WINDOW)  # stores probability vectors
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = tf.image.resize(rgb, (IMG_SIZE, IMG_SIZE))
        img = tf.expand_dims(img, 0)
        probs = model.predict(img, verbose=0)[0]
        history.append(probs)

        # Average over recent frames for a stable prediction
        avg = np.mean(history, axis=0)
        idx = int(np.argmax(avg))
        conf = float(avg[idx])

        if conf >= MIN_CONFIDENCE:
            text = f"{class_names[idx]}  {conf * 100:.0f}%"
            color = (0, 255, 0)
        else:
            text = "uncertain..."
            color = (0, 200, 255)

        cv2.putText(frame, text, (20, 50), cv2.FONT_HERSHEY_SIMPLEX,
                    1.2, color, 3)
        cv2.imshow("Ball classifier", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        predict_file(sys.argv[1])
    else:
        webcam()
