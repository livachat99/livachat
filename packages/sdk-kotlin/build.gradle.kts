plugins {
    kotlin("jvm")
    kotlin("plugin.serialization")
}

group = "dev.livachat"
version = "1.0.0"

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.1")
}

kotlin {
    jvmToolchain(17)
}
