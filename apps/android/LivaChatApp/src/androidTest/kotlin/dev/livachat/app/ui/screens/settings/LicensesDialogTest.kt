package dev.livachat.app.ui.screens.settings

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import dev.livachat.app.ui.theme.LivaChatTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class LicensesDialogTest {

    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun showsTitleAndInjectedTextAndCloses() {
        var dismissed = false
        composeRule.setContent {
            LivaChatTheme {
                LicensesDialog(
                    onDismiss = { dismissed = true },
                    textProvider = { "license text" },
                )
            }
        }

        composeRule.onNodeWithTag("licenses_title").assertIsDisplayed()
        composeRule.onNodeWithText("license text").assertIsDisplayed()
        composeRule.onNodeWithTag("licenses_close").performClick()
        assertTrue(dismissed)
    }
}
