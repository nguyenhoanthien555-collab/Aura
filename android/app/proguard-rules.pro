# Release build (R8) rules.
#
# The release build shrinks and obfuscates. Three libraries here rely on
# reflection or generated metadata that R8 cannot see, so each needs its
# rules stated. Without them the app builds cleanly and then fails at
# runtime with a serializer-not-found or a null Retrofit method - the worst
# class of bug, because the build was green.

# ---------------------------------------------------------------------
# kotlinx.serialization
# ---------------------------------------------------------------------
# @Serializable classes get a synthetic Companion holding the serializer.
# R8 sees no direct call to it and removes it.

-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.**

-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}

-keep,includedescriptorclasses class com.aura.companion.**$$serializer { *; }
-keepclassmembers class com.aura.companion.** {
    *** Companion;
}
-keepclasseswithmembers class com.aura.companion.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# ---------------------------------------------------------------------
# Retrofit + OkHttp
# ---------------------------------------------------------------------
# Retrofit reads generic return types from the interface at runtime.

-keepattributes Signature, Exceptions, RuntimeVisibleAnnotations, RuntimeVisibleParameterAnnotations

-keep,allowobfuscation interface com.aura.companion.data.remote.AuraApi
-keep,allowobfuscation,allowshrinking interface retrofit2.Call
-keep,allowobfuscation,allowshrinking class retrofit2.Response

# Suspend functions on a Retrofit interface erase to Continuation.
-keepclassmembers,allowshrinking,allowobfuscation interface * {
    @retrofit2.http.* <methods>;
}

-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**

# OkHttp references these optionally; they are absent on Android.
-dontwarn javax.annotation.**
-dontwarn kotlin.Unit

# ---------------------------------------------------------------------
# WorkManager
# ---------------------------------------------------------------------
# Workers are constructed reflectively from a class name string.

-keep class * extends androidx.work.ListenableWorker {
    public <init>(android.content.Context, androidx.work.WorkerParameters);
}

# ---------------------------------------------------------------------
# Accessibility service
# ---------------------------------------------------------------------
# Named in AndroidManifest.xml and instantiated by the system.

-keep class com.aura.companion.screen.ScreenObservationService { *; }
-keep class com.aura.companion.AuraApplication { *; }
