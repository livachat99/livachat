# LivaChatSDK — ProGuard / R8 consumer rules
# These rules protect the public API surface so consuming apps can reference
# LivaChatSDK, LivaChatConfig, LivaChatListener, and the model enums at runtime.

# ── Public API: keep all public classes in dev.livachat.sdk ──────────
-keep class dev.livachat.sdk.LivaChatSDK { public *; }
-keep class dev.livachat.sdk.LivaChatConfig { *; }
-keep class dev.livachat.sdk.LivaChatConfig$AudioSampleRate { *; }
-keep interface dev.livachat.sdk.LivaChatListener { *; }

# ── Public model enums ─────────────────────────────────────────────
-keep class dev.livachat.sdk.model.** { *; }

# ── kotlinx.serialization — keep generated serializers ─────────────
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt

-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}

# ── Keep generated DTOs (protocol messages) ────────────────────────
-keep class dev.livachat.protocol.generated.** { *; }

# ── OkHttp WebSocket (used reflectively) ───────────────────────────
-keep class okhttp3.** { *; }
-dontwarn okhttp3.**
-dontwarn okio.**
