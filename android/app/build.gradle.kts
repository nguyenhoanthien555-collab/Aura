plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

android {
    namespace = "com.aura.companion"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.aura.companion"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        // No server URL and no token here, deliberately. Both are entered
        // by the user at first run and stored encrypted on the device, so
        // the APK carries no credential and no deployment assumption.
    }

    buildTypes {
        debug {
            // Cleartext is permitted in debug only, so a phone can reach
            // `http://192.168.x.x:8000` on a home network during
            // development. Release requires HTTPS - see
            // src/main/res/xml/network_security_config.xml.
            isMinifyEnabled = false
        }

        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            // No signingConfig. Release signing needs a keystore the
            // repository must not contain; see docs/ANDROID.md.
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
        compose = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    testOptions {
        unitTests {
            isReturnDefaultValues = true
        }
    }
}

/*
 * The manifest is an input to the unit tests, not just to the APK.
 *
 * OpenAppLaunchabilityTest asserts a property of AndroidManifest.xml -
 * that it declares the MAIN/LAUNCHER <queries> block `open_app` needs to
 * resolve a launch intent on Android 11+. It reads the file at runtime,
 * which Gradle cannot see, so without this the test task stays
 * up-to-date when the manifest changes and the assertion is skipped
 * rather than run. That was observed: deleting the <queries> block and
 * re-running reported BUILD SUCCESSFUL from cache.
 *
 * A regression test that silently does not run when the file it guards
 * is edited is worse than no test, because the green build is read as
 * evidence.
 */
tasks.withType<Test>().configureEach {
    inputs.file(layout.projectDirectory.file("src/main/AndroidManifest.xml"))
        .withPropertyName("androidManifest")
        .withPathSensitivity(PathSensitivity.RELATIVE)
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons)
    implementation(libs.androidx.navigation.compose)

    implementation(libs.androidx.security.crypto)
    implementation(libs.androidx.work.runtime.ktx)

    // No logging-interceptor, in any configuration. It would print the
    // Authorization header, and "debug only" is how a bearer token ends up
    // pasted into a bug report.
    implementation(libs.okhttp)
    implementation(libs.retrofit)
    implementation(libs.retrofit.serialization)
    implementation(libs.kotlinx.serialization.json)

    debugImplementation(libs.androidx.compose.ui.tooling)
    debugImplementation(libs.androidx.compose.ui.test.manifest)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)

    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
}
