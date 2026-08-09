package com.aura.companion

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.aura.companion.ui.chat.ChatScreen
import com.aura.companion.ui.chat.ChatViewModel
import com.aura.companion.ui.settings.SettingsScreen
import com.aura.companion.ui.settings.SettingsViewModel
import com.aura.companion.ui.theme.AuraTheme
import com.aura.companion.work.NotificationScheduler

class MainActivity : ComponentActivity() {

    private val container by lazy { (application as AuraApplication).container }

    private val notificationPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* Declined is a valid answer; nothing to do. */ }

    /**
     * The remark a notification carried, waiting to be shown.
     *
     * Held as snapshot state and filled from a lifecycle callback rather
     * than read out of `intent` inside composition. `getStringExtra` plus
     * `removeExtra` is a side effect, and composition is not a place for
     * one: it can run again for a recomposition, or be thrown away and
     * re-run, which turns "show this once" into either twice or never.
     *
     * Written in `onCreate`/`onNewIntent`, consumed exactly once by the
     * `LaunchedEffect` below.
     */
    private var pendingMessage by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        NotificationScheduler.sync(this, container.settings.current.notificationsEnabled)

        takeNotificationMessage(intent)

        setContent {
            AuraTheme {

                val navController = rememberNavController()

                val chatViewModel: ChatViewModel = viewModel(
                    factory = ChatViewModel.factory(
                        container.repository,
                        container.settings,
                    )
                )

                // A notification that led here carries its text, so tapping
                // it lands on the remark rather than on an empty screen.
                LaunchedEffect(pendingMessage) {
                    pendingMessage?.let { message ->
                        chatViewModel.showCompanionMessage(message)
                        pendingMessage = null
                    }
                }

                NavHost(navController = navController, startDestination = ROUTE_CHAT) {

                    composable(ROUTE_CHAT) {
                        ChatScreen(
                            viewModel = chatViewModel,
                            onOpenSettings = { navController.navigate(ROUTE_SETTINGS) },
                        )
                    }

                    composable(ROUTE_SETTINGS) {

                        val settingsViewModel: SettingsViewModel = viewModel(
                            factory = SettingsViewModel.factory(
                                container.settings,
                                container.repository,
                            )
                        )

                        SettingsScreen(
                            viewModel = settingsViewModel,
                            onBack = {
                                // Re-probe on the way back: the user may
                                // have just fixed the thing the banner was
                                // complaining about.
                                chatViewModel.checkConnection()
                                NotificationScheduler.sync(
                                    this@MainActivity,
                                    container.settings.current.notificationsEnabled,
                                )
                                navController.popBackStack()
                            },
                            onRequestScreenObservation = ::openAccessibilitySettings,
                            onRequestNotificationPermission = ::askForNotifications,
                        )
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        takeNotificationMessage(intent)
    }

    /**
     * Lift the companion message out of an intent, once.
     *
     * The extra is removed as it is read, so an Activity recreated for a
     * rotation does not replay a notification the user already saw.
     */
    private fun takeNotificationMessage(intent: Intent?) {

        val message = intent?.getStringExtra(EXTRA_MESSAGE) ?: return

        intent.removeExtra(EXTRA_MESSAGE)

        pendingMessage = message
    }

    /**
     * Send the user to the system accessibility screen.
     *
     * The service cannot be enabled programmatically, by design - and that
     * is the right design. Screen observation is a capability the user
     * grants in the system UI, not one an app can grant itself.
     */
    private fun openAccessibilitySettings() {
        runCatching {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }
    }

    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    companion object {
        const val ROUTE_CHAT = "chat"
        const val ROUTE_SETTINGS = "settings"
        const val EXTRA_MESSAGE = "aura_message"
    }
}
