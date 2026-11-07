package dev.livachat.app.ui.components

import androidx.compose.ui.test.junit4.createComposeRule
import dev.livachat.app.ui.theme.LivaChatTheme
import org.junit.Rule
import org.junit.Test

class AiAvatarAnimationTest {

    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun avatar_renders_in_IDLE_state_without_crash() {
        composeTestRule.setContent {
            LivaChatTheme {
                AiAvatar(state = AvatarState.IDLE)
            }
        }
        composeTestRule.waitForIdle()
    }

    @Test
    fun avatar_renders_in_LISTENING_state_without_crash() {
        composeTestRule.setContent {
            LivaChatTheme {
                AiAvatar(state = AvatarState.LISTENING)
            }
        }
        composeTestRule.waitForIdle()
    }

    @Test
    fun avatar_renders_in_SPEAKING_state_without_crash() {
        composeTestRule.setContent {
            LivaChatTheme {
                AiAvatar(state = AvatarState.SPEAKING)
            }
        }
        composeTestRule.waitForIdle()
    }

    @Test
    fun avatar_renders_in_THINKING_state_without_crash() {
        composeTestRule.setContent {
            LivaChatTheme {
                AiAvatar(state = AvatarState.THINKING)
            }
        }
        composeTestRule.waitForIdle()
    }

    @Test
    fun avatar_renders_in_CONNECTING_state_without_crash() {
        composeTestRule.setContent {
            LivaChatTheme {
                AiAvatar(state = AvatarState.CONNECTING)
            }
        }
        composeTestRule.waitForIdle()
    }
}
