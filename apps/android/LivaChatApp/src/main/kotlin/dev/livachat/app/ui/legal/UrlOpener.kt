package dev.livachat.app.ui.legal

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.browser.customtabs.CustomTabsIntent

/** 可注入的外链打开器：生产用 Custom Tabs，测试可传 fake。 */
interface UrlOpener {
    fun open(url: String)
}

class CustomTabsUrlOpener(private val context: Context) : UrlOpener {
    override fun open(url: String) {
        val uri = Uri.parse(url)
        val launched = runCatching {
            CustomTabsIntent.Builder().build().launchUrl(context, uri)
        }.isSuccess
        if (!launched) {
            runCatching {
                context.startActivity(
                    Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            }
        }
    }
}
