package dev.livachat.sdk.model

/** Camera frame rotation in degrees (the rotation needed to display the image upright). */
enum class Orientation(val degrees: Int) {
    PORTRAIT_0(0),
    LANDSCAPE_90(90),
    PORTRAIT_180(180),
    LANDSCAPE_270(270),
}
