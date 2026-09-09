plugins {
    id("com.android.application")
}

android {
    namespace = "com.oneripple.agentviews"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.oneripple.agentviews"
        minSdk = 28
        targetSdk = 36
        versionCode = 4
        versionName = "0.3.2"

        testInstrumentationRunner = "com.oneripple.agentviews.WifiSmokeInstrumentation"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    // ZXing's camera permission path uses ContextCompat at runtime.
    implementation("androidx.core:core:1.9.0")
    testImplementation("junit:junit:4.13.2")
}
