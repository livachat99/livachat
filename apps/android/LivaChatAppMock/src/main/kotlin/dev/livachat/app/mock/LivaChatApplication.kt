package dev.livachat.app.mock

import android.app.Application

class LivaChatApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        // SDK initialization happens lazily via ConversationViewModel
    }
}
