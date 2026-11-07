# LivaChatApp ProGuard Rules
# Phase 0 — Baseline keep rules for SDK, Compose, Hilt, Room

# === LivaChatSDK public API ===
-keep class dev.livachat.sdk.LivaChatSDK { *; }
-keep class dev.livachat.sdk.LivaChatListener { *; }
-keep class dev.livachat.sdk.LivaChatConfig { *; }
-keep class dev.livachat.sdk.LivaChatConfig$Builder { *; }
-keep class dev.livachat.sdk.model.** { *; }

# === Compose ===
-keep class androidx.compose.** { *; }

# === Hilt ===
-keep class dagger.hilt.** { *; }
-keep class javax.inject.** { *; }
-keep class * extends dagger.hilt.android.internal.managers.ViewComponentManager$FragmentContextWrapper { *; }
-keepclasseswithmembers class * {
    @javax.inject.Inject <init>(...);
}

# === Room entities & DAOs ===
-keep class dev.livachat.app.data.local.entity.** { *; }
-keep class dev.livachat.app.data.local.dao.** { *; }
-keep class * extends androidx.room.RoomDatabase { *; }

# === kotlinx.serialization ===
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class dev.livachat.**$$serializer { *; }
-keepclassmembers class dev.livachat.** {
    *** Companion;
}
-keepclasseswithmembers class dev.livachat.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# === General Android ===
-keepattributes Signature
-keepattributes *Annotation*
-dontwarn sun.misc.**
-keep class javax.annotation.** { *; }
