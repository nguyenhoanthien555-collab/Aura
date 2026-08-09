// Top-level build file.
//
// Plugins are declared here with `apply false` and applied in :app, which
// is the standard layout for a single-module project that may grow a
// second module (a Wear or Auto client) without restructuring.

plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}
