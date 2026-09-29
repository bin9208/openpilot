plugins { id("com.android.application"); kotlin("android") }
android {
    namespace = "ai.carrot.jetlink"
    compileSdk = 35
    buildToolsVersion = "35.0.0"
    defaultConfig {
        applicationId = "ai.carrot.jetlink"
        minSdk = 28
        targetSdk = 35
        versionCode = 2
        versionName = "0.1.1-experimental"
        ndk { abiFilters += "arm64-v8a" }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    buildTypes { release { isMinifyEnabled = false } }
    buildFeatures { buildConfig = true }
    packaging { resources.excludes += "META-INF/INDEX.LIST" }
}
dependencies {
    implementation(project(":core"))
    implementation(project(":runtime"))
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.22.0")
}
