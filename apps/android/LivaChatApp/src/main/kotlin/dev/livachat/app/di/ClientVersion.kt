package dev.livachat.app.di

internal fun androidClientVersion(versionName: String, versionCode: Int): String =
    "android-$versionName.$versionCode"
