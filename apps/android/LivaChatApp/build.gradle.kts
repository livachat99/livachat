import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.dagger.hilt.android")
    id("org.jetbrains.kotlin.kapt")
    id("org.jetbrains.kotlin.plugin.serialization")
}

// 发布签名（upload key）：keystore 与口令放本地、已 gitignore，见 keystore.properties。
// Play App Signing 启用后，上传统用本文件的 upload key，最终分发由 Play 的 app signing key 重签。
val keystorePropertiesFile = rootProject.file("keystore.properties")
val keystoreProperties = Properties().apply {
    if (keystorePropertiesFile.exists()) {
        keystorePropertiesFile.inputStream().use { load(it) }
    }
}

android {
    namespace = "dev.livachat.app"
    compileSdk = 36

    defaultConfig {
        // 必须与 Play Console 已创建的包名一致（创建后不可更改）；namespace 仍为 dev.livachat.app。
        applicationId = "com.livachat.livachatapp"
        minSdk = 26
        targetSdk = 36
        versionCode = 142
        versionName = "1.0.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        if (keystorePropertiesFile.exists()) {
            create("release") {
                storeFile = rootProject.file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        getByName("release") {
            if (keystorePropertiesFile.exists()) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
            excludes += "/META-INF/LICENSE.md"
            excludes += "/META-INF/LICENSE-notice.md"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlin {
        compilerOptions {
            jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        }
    }

    kapt {
        arguments {
            arg("room.schemaLocation", "$projectDir/schemas")
        }
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = true
        }
    }
}

dependencies {
    // SDK
    implementation(project(":LivaChatSDK"))

    // Kotlin
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.1")

    // Compose
    implementation(platform("androidx.compose:compose-bom:2025.06.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.activity:activity-compose:1.9.0")
    // 外链打开（Privacy / EULA / Support）：Custom Tabs，fallback 系统浏览器
    implementation("androidx.browser:browser:1.8.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.2")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.2")
    // 进程级前后台感知（docs/android_background_support.md §2.1）
    implementation("androidx.lifecycle:lifecycle-process:2.8.2")
    implementation("androidx.navigation:navigation-compose:2.7.7")

    // Markdown (Markwon 4.6.2, see docs/fd_markdown_view.md)
    implementation("io.noties.markwon:core:4.6.2")
    implementation("io.noties.markwon:linkify:4.6.2")
    implementation("io.noties.markwon:ext-tables:4.6.2")
    implementation("io.noties.markwon:ext-tasklist:4.6.2")

    // Hilt
    implementation("com.google.dagger:hilt-android:2.58")
    kapt("com.google.dagger:hilt-compiler:2.58")
    implementation("androidx.hilt:hilt-navigation-compose:1.2.0")

    // Room
    implementation("androidx.room:room-runtime:2.8.5")
    implementation("androidx.room:room-ktx:2.8.5")
    kapt("androidx.room:room-compiler:2.8.5")

    // DataStore
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    // CameraX
    implementation("androidx.camera:camera-core:1.6.2")
    implementation("androidx.camera:camera-camera2:1.6.2")
    implementation("androidx.camera:camera-lifecycle:1.6.2")
    implementation("androidx.camera:camera-view:1.6.2")

    // Permissions helper
    implementation("com.google.accompanist:accompanist-permissions:0.34.0")

    // Security (encrypted prefs/db)
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    // EXIF 方向校正（头像裁剪，docs/fd_avator_customization.md 审查 #1）
    implementation("androidx.exifinterface:exifinterface:1.3.7")

    // MyLife（docs/mylife/mylife-frontend-android.md §4 审批清单）
    // HTTP（版本与 LivaChatSDK 内部 okhttp 4.12.0 对齐，避免双版本）
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    // 图片加载（Coil 2.x；共享 client 经 ImageLoader.Builder.callFactory 注入）
    implementation("io.coil-kt:coil-compose:2.6.0")
    // 音频播放（Media3，OGG Opus + Range seek；datasource 复用自定义 OkHttpClient）
    implementation("androidx.media3:media3-exoplayer:1.4.1")
    implementation("androidx.media3:media3-datasource-okhttp:1.4.1")

    // Test
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
    testImplementation("io.mockk:mockk:1.13.11")
    testImplementation("app.cash.turbine:turbine:1.1.0")
    testImplementation("androidx.arch.core:core-testing:2.2.0")
    testImplementation("org.assertj:assertj-core:3.26.0")
    testImplementation("org.robolectric:robolectric:4.12.2")
    // MyLife：src/test 契约测试需要 mockwebserver（设计文档 §4）
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    // ServiceProbeTest：https MockWebServer 自签证书（docs/setting_server_address.md §5）
    testImplementation("com.squareup.okhttp3:okhttp-tls:4.12.0")

    androidTestImplementation("androidx.test:runner:1.6.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("com.google.dagger:hilt-android-testing:2.58")
    androidTestImplementation("io.mockk:mockk:1.13.11")
    androidTestImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
    kaptAndroidTest("com.google.dagger:hilt-android-compiler:2.58")
    androidTestImplementation(platform("androidx.compose:compose-bom:2025.06.01"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
    debugImplementation("androidx.compose.ui:ui-tooling")
}
