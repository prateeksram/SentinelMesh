plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.sentinelmesh.gesturefootball"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.sentinelmesh.gesturefootball"
        minSdk = 28
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        viewBinding = true
    }
    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
        // Hexagon DSP must mmap HTP *Skel.so from a real filesystem path (not APK zip).
        jniLibs {
            useLegacyPackaging = true
        }
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")

    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.2.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.activity:activity-ktx:1.9.3")

    // Camera
    val camerax = "1.4.1"
    implementation("androidx.camera:camera-core:$camerax")
    implementation("androidx.camera:camera-camera2:$camerax")
    implementation("androidx.camera:camera-lifecycle:$camerax")
    implementation("androidx.camera:camera-view:$camerax")
    implementation("androidx.camera:camera-video:$camerax")

    // MediaPipe Pose (GPU fallback when Hexagon / QNN fails to load)
    implementation("com.google.mediapipe:tasks-vision:0.10.14")

    // ONNX Runtime + QNN EP → Snapdragon Hexagon NPU (AI Hub pose bundles)
    // Model bundles compiled with QAIRT 2.45 — keep QNN runtime in sync.
    implementation("com.microsoft.onnxruntime:onnxruntime-android-qnn:1.28.0")
    implementation("com.qualcomm.qti:qnn-runtime:2.45.0")

    // WebSocket client — same JSON protocol as phone.html
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // Coroutines
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
}
