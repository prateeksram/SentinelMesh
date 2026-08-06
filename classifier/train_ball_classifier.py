"""
Ball classifier: tennis / basketball / football / darts
Transfer learning with MobileNetV2 (TensorFlow / Keras).

Setup:
    pip install tensorflow

Data layout (put ~100-300 images in each folder):
    data/
        tennis/
        basketball/
        football/
        darts/

Run:
    python train_ball_classifier.py
"""

import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers

# ---------- Config ----------
DATA_DIR = "data"
IMG_SIZE = 224
BATCH_SIZE = 32
EPOCHS_HEAD = 12       # train only the new classifier head
EPOCHS_FINETUNE = 6    # then fine-tune top of the backbone
SEED = 123

# ---------- Load data ----------
train_ds = keras.utils.image_dataset_from_directory(
    DATA_DIR,
    validation_split=0.2,
    subset="training",
    seed=SEED,
    image_size=(IMG_SIZE, IMG_SIZE),
    batch_size=BATCH_SIZE,
)
val_ds = keras.utils.image_dataset_from_directory(
    DATA_DIR,
    validation_split=0.2,
    subset="validation",
    seed=SEED,
    image_size=(IMG_SIZE, IMG_SIZE),
    batch_size=BATCH_SIZE,
)

class_names = train_ds.class_names
print("Classes:", class_names)

# Save the class order so inference uses the same mapping
with open("class_names.txt", "w") as f:
    f.write("\n".join(class_names))

# Speed up the data pipeline
AUTOTUNE = tf.data.AUTOTUNE
train_ds = train_ds.cache().shuffle(1000).prefetch(AUTOTUNE)
val_ds = val_ds.cache().prefetch(AUTOTUNE)

# ---------- Data augmentation ----------
# Randomly perturbs training images so the model generalizes better.
data_augmentation = keras.Sequential([
    layers.RandomFlip("horizontal"),
    layers.RandomRotation(0.15),
    layers.RandomZoom(0.15),
    layers.RandomBrightness(0.1),
])

# ---------- Build model ----------
base_model = keras.applications.MobileNetV2(
    input_shape=(IMG_SIZE, IMG_SIZE, 3),
    include_top=False,        # drop ImageNet's 1000-class head
    weights="imagenet",
)
base_model.trainable = False  # freeze for the first phase

inputs = keras.Input(shape=(IMG_SIZE, IMG_SIZE, 3))
x = data_augmentation(inputs)
x = keras.applications.mobilenet_v2.preprocess_input(x)
x = base_model(x, training=False)
x = layers.GlobalAveragePooling2D()(x)
x = layers.Dropout(0.2)(x)
outputs = layers.Dense(len(class_names), activation="softmax")(x)
model = keras.Model(inputs, outputs)

model.compile(
    optimizer="adam",
    loss="sparse_categorical_crossentropy",
    metrics=["accuracy"],
)

# ---------- Phase 1: train the head ----------
print("\n=== Phase 1: training classifier head ===")
model.fit(train_ds, validation_data=val_ds, epochs=EPOCHS_HEAD)

# ---------- Phase 2: fine-tune top of backbone ----------
# Unfreezing a few top layers usually squeezes out extra accuracy.
print("\n=== Phase 2: fine-tuning ===")
base_model.trainable = True
for layer in base_model.layers[:-30]:   # keep most layers frozen
    layer.trainable = False

model.compile(
    optimizer=keras.optimizers.Adam(1e-5),  # low LR is important here
    loss="sparse_categorical_crossentropy",
    metrics=["accuracy"],
)
model.fit(train_ds, validation_data=val_ds, epochs=EPOCHS_FINETUNE)

# ---------- Save ----------
model.save("ball_classifier.keras")
print("\nSaved model to ball_classifier.keras")
