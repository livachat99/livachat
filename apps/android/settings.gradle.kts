pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "LivaChatAndroid"

include(":LivaChatSDK")
include(":LivaChatApp")
include(":LivaChatAppMock")

// Protocol DTO module from packages/sdk-kotlin
include(":sdk-kotlin")
project(":sdk-kotlin").projectDir = file("../../packages/sdk-kotlin")
