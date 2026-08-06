"""
Export the trained ball classifier for the phone.

Rebuilds the model with an explicit Rescaling layer instead of the
mobilenet_v2.preprocess_input op (which doesn't convert cleanly), reusing
the trained weights, then exports:
  - web_model/          TensorFlow.js model, served to the phone browser
  - ball_classifier.tflite  (optional, for a native app later)

Setup:
    pip install tensorflowjs

Run (after training):
    python export_model.py
"""

import json
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers

trained = keras.models.load_model("ball_classifier.keras")

# Find the MobileNetV2 backbone and the final Dense layer inside the trained model
base = next(l for l in trained.layers if isinstance(l, keras.Model))
dense = trained.layers[-1]

# Inference-only model: raw 0-255 pixels in, Rescaling replicates
# mobilenet_v2.preprocess_input (x / 127.5 - 1). No augmentation layers.
inp = keras.Input(shape=(224, 224, 3))
x = layers.Rescaling(1.0 / 127.5, offset=-1.0)(inp)
x = base(x, training=False)
x = layers.GlobalAveragePooling2D()(x)
out = dense(x)
inference = keras.Model(inp, out)

# --- TensorFlow.js (for the phone browser) ---
import tensorflowjs as tfjs
tfjs.converters.save_keras_model(inference, "web_model")
print("Wrote web_model/ (TensorFlow.js)")

# --- Class names alongside the model ---
with open("class_names.txt") as f:
    classes = [line.strip() for line in f if line.strip()]
with open("web_model/classes.json", "w") as f:
    json.dump(classes, f)
print("Wrote web_model/classes.json:", classes)

# --- TFLite (optional, for a native mobile app later) ---
converter = tf.lite.TFLiteConverter.from_keras_model(inference)
with open("ball_classifier.tflite", "wb") as f:
    f.write(converter.convert())
print("Wrote ball_classifier.tflite")
