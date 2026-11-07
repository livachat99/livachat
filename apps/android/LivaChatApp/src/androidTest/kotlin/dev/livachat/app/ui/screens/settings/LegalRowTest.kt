package dev.livachat.app.ui.screens.settings

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import dev.livachat.app.ui.theme.LivaChatTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class LegalRowTest {

    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun legalRowExposesTagAndInvokesClick() {
        var clicked = false
        composeRule.setContent {
            LivaChatTheme {
                LegalRow(
                    title = "Privacy policy",
                    tag = "settings_privacy",
                    onClick = { clicked = true },
                )
            }
        }

        composeRule.onNodeWithTag("settings_privacy").assertIsDisplayed().performClick()
        assertTrue(clicked)
    }
}
