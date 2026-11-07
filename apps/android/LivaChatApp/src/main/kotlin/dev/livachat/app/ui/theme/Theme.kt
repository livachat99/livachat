package dev.livachat.app.ui.theme

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp

private val DarkColorScheme = darkColorScheme(
    primary = BrandIndigo,
    onPrimary = TextPrimary,
    secondary = BrandViolet,
    onSecondary = TextPrimary,
    background = Background,
    onBackground = TextPrimary,
    surface = Surface,
    onSurface = TextPrimary,
    surfaceVariant = SurfaceVariant,
    onSurfaceVariant = TextSecondary,
    // Material3 1.3 起弹窗/菜单容器色改用 surfaceContainer*（1.2.x 均为 surface）：
    // AlertDialog → surfaceContainerHigh，DropdownMenu → surfaceContainer。
    // 不覆盖会落回 M3 默认中性灰 #2B2930，与 App 深蓝灰面板不一致；显式对齐 Surface。
    surfaceContainerHigh = Surface,
    surfaceContainer = Surface,
    error = Error,
    onError = TextPrimary,
    outline = SurfaceVariant,
)

@Composable
fun LivaChatTheme(content: @Composable () -> Unit) {
    val config = LocalConfiguration.current
    val density = LocalDensity.current

    val clampedFontScale = config.fontScale.coerceAtMost(1.3f)

    if (clampedFontScale < config.fontScale) {
        val clampedConfig = Configuration(config).apply {
            fontScale = clampedFontScale
        }
        val clampedDensity = Density(
            density = density.density,
            fontScale = clampedFontScale,
        )
        CompositionLocalProvider(
            LocalConfiguration provides clampedConfig,
            LocalDensity provides clampedDensity,
            LocalAppSpacing provides Spacing,
            LocalAppElevation provides Elevation,
        ) {
            MaterialTheme(
                colorScheme = DarkColorScheme,
                typography = LivaChatTypography,
                shapes = LivaChatShapes,
                content = content,
            )
        }
    } else {
        CompositionLocalProvider(
            LocalAppSpacing provides Spacing,
            LocalAppElevation provides Elevation,
        ) {
            MaterialTheme(
                colorScheme = DarkColorScheme,
                typography = LivaChatTypography,
                shapes = LivaChatShapes,
                content = content,
            )
        }
    }
}

// ── Preview composables ────────────────────────────────────────────

@Preview(showBackground = true)
@Composable
private fun ColorPalettePreview() {
    LivaChatTheme {
        Surface(color = MaterialTheme.colorScheme.background) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(Spacing.md)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(Spacing.sm),
            ) {
                Text("Color Palette", style = MaterialTheme.typography.titleLarge)

                ColorSwatch("BrandIndigo (Primary)", BrandIndigo, TextPrimary)
                ColorSwatch("BrandViolet (Secondary)", BrandViolet, TextPrimary)
                ColorSwatch("Success", Success, TextPrimary)
                ColorSwatch("Warning", Warning, Background)
                ColorSwatch("Error", Error, TextPrimary)
                ColorSwatch("Red", Red, TextPrimary)
                ColorSwatch("Green", Green, TextPrimary)
                ColorSwatch("Gold", Gold, TextPrimary)
                ColorSwatch("TranslucentBlack", TranslucentBlack, TextPrimary)
                ColorSwatch("PillBlack", PillBlack, TextPrimary)
                ColorSwatch("Info", Info, TextPrimary)
                ColorSwatch("Background", Background, TextPrimary)
                ColorSwatch("Surface", Surface, TextPrimary)
                ColorSwatch("SurfaceVariant", SurfaceVariant, TextPrimary)
                ColorSwatch("TextPrimary", TextPrimary, Background)
                ColorSwatch("TextSecondary", TextSecondary, Background)
                ColorSwatch("TextTertiary", TextTertiary, Background)
            }
        }
    }
}

@Composable
private fun ColorSwatch(name: String, color: androidx.compose.ui.graphics.Color, onColor: androidx.compose.ui.graphics.Color) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(color, MaterialTheme.shapes.small)
            .padding(Spacing.md),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(name, color = onColor, style = MaterialTheme.typography.bodyLarge)
        Box(
            modifier = Modifier
                .size(24.dp)
                .background(onColor, CircleShape),
        )
    }
}

@Preview(showBackground = true)
@Composable
private fun TypographyPreview() {
    LivaChatTheme {
        Surface(color = MaterialTheme.colorScheme.background) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(Spacing.md)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(Spacing.sm),
            ) {
                Text("Typography", style = MaterialTheme.typography.titleLarge)
                Text("headlineLarge — 28sp Bold", style = MaterialTheme.typography.headlineLarge)
                Text("titleLarge — 22sp SemiBold", style = MaterialTheme.typography.titleLarge)
                Text("bodyLarge — 16sp Regular", style = MaterialTheme.typography.bodyLarge)
                Text("labelLarge — 14sp Medium", style = MaterialTheme.typography.labelLarge)
                Text("bodySmall — 12sp Regular", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun SpacingPreview() {
    LivaChatTheme {
        Surface(color = MaterialTheme.colorScheme.background) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(Spacing.md)
                    .verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(Spacing.sm),
            ) {
                Text("Spacing Scale", style = MaterialTheme.typography.titleLarge)
                SpacingBar("xs (4dp)", Spacing.xs)
                SpacingBar("sm (8dp)", Spacing.sm)
                SpacingBar("md (16dp)", Spacing.md)
                SpacingBar("lg (24dp)", Spacing.lg)
                SpacingBar("xl (32dp)", Spacing.xl)
                SpacingBar("xxl (48dp)", Spacing.xxl)

                Text("Elevation", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(top = Spacing.md))
                ElevationBar("Level0 (0dp)", Elevation.Level0)
                ElevationBar("Level1 (1dp)", Elevation.Level1)
                ElevationBar("Level2 (3dp)", Elevation.Level2)
                ElevationBar("Level3 (8dp)", Elevation.Level3)
            }
        }
    }
}

@Composable
private fun SpacingBar(label: String, size: androidx.compose.ui.unit.Dp) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, style = MaterialTheme.typography.bodySmall)
        Box(
            modifier = Modifier
                .size(size)
                .background(BrandIndigo, MaterialTheme.shapes.small),
        )
    }
}

@Composable
private fun ElevationBar(label: String, elevation: androidx.compose.ui.unit.Dp) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        Text(label, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
        Surface(
            modifier = Modifier.size(48.dp),
            shape = MaterialTheme.shapes.small,
            shadowElevation = elevation,
        ) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .background(MaterialTheme.colorScheme.surface),
            )
        }
    }
}
