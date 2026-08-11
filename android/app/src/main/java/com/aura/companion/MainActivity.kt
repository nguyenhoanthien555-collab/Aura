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
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.aura.companion.ui.chat.ChatScreen
import com.aura.companion.ui.chat.ChatViewModel
import com.aura.companion.ui.hub.AuraSection
import com.aura.companion.ui.hub.AwarenessSection
import com.aura.companion.ui.hub.ConnectionSection
import com.aura.companion.ui.hub.GeneralSection
import com.aura.companion.ui.hub.HubRoutes
import com.aura.companion.ui.hub.HubScreen
import com.aura.companion.ui.hub.HubViewModel
import com.aura.companion.ui.hub.MemorySection
import com.aura.companion.ui.hub.ModelsSection
import com.aura.companion.ui.hub.NotificationsSection
import com.aura.companion.ui.hub.ProactiveSection
import com.aura.companion.ui.hub.VisionSection
import com.aura.companion.ui.hub.VoiceSection
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

            // Appearance is device-local, so it comes from the store rather
            // than the server and applies before anything is drawn.
            val device by container.settings.settings.collectAsStateWithLifecycle()

            AuraTheme(
                themeMode = device.themeMode,
                dynamicColour = device.dynamicColour,
            ) {

                val navController = rememberNavController()

                val chatViewModel: ChatViewModel = viewModel(
                    factory = ChatViewModel.factory(
                        container.repository,
                        container.settings,
                    )
                )

                // Scoped to the Activity rather than to a back stack entry,
                // so all eleven hub destinations share one instance - and
                // therefore one copy of the server's config, fetched once
                // on entry instead of per screen.
                val hubViewModel: HubViewModel = viewModel(
                    factory = HubViewModel.factory(
                        container.settings,
                        container.repository,
                    )
                )

                val hubState by hubViewModel.state.collectAsStateWithLifecycle()

                // A notification that led here carries its text, so tapping
                // it lands on the remark rather than on an empty screen.
                LaunchedEffect(pendingMessage) {
                    pendingMessage?.let { message ->
                        chatViewModel.showCompanionMessage(message)
                        pendingMessage = null
                    }
                }

                val back: () -> Unit = { navController.popBackStack() }

                NavHost(navController = navController, startDestination = ROUTE_CHAT) {

                    composable(ROUTE_CHAT) {
                        ChatScreen(
                            viewModel = chatViewModel,
                            onOpenSettings = { navController.navigate(HubRoutes.HUB) },
                        )
                    }

                    composable(HubRoutes.HUB) {
                        HubScreen(
                            viewModel = hubViewModel,
                            onOpenSection = { route -> navController.navigate(route) },
                            onOpenChat = {
                                navController.popBackStack(ROUTE_CHAT, false)
                            },
                            onBack = {
                                // Re-probe on the way out: the user may have
                                // just fixed the thing the chat banner was
                                // complaining about, and the notification
                                // poller may need starting or stopping.
                                chatViewModel.checkConnection()
                                NotificationScheduler.sync(
                                    this@MainActivity,
                                    container.settings.current.notificationsEnabled,
                                )
                                navController.popBackStack()
                            },
                        )
                    }

                    composable(HubRoutes.AURA) {
                        AuraSection(
                            state = hubState,
                            onRefresh = hubViewModel::refresh,
                            onBack = back,
                        )
                    }

                    composable(HubRoutes.MODELS) {
                        ModelsSection(hubState, hubViewModel, back)
                    }

                    composable(HubRoutes.AWARENESS) {
                        AwarenessSection(
                            state = hubState,
                            viewModel = hubViewModel,
                            onOpenAccessibilitySettings = ::openAccessibilitySettings,
                            onBack = back,
                        )
                    }

                    composable(HubRoutes.MEMORY) {
                        MemorySection(hubState, hubViewModel, back)
                    }

                    composable(HubRoutes.PROACTIVE) {
                        ProactiveSection(hubState, hubViewModel, back)
                    }

                    composable(HubRoutes.VISION) {
                        VisionSection(hubState, hubViewModel, back)
                    }

                    composable(HubRoutes.VOICE) {
                        VoiceSection(hubState, hubViewModel, back)
                    }

                    composable(HubRoutes.NOTIFICATIONS) {
                        NotificationsSection(
                            state = hubState,
                            viewModel = hubViewModel,
                            onRequestPermission = ::askForNotifications,
                            onOpenSystemSettings = ::openNotificationSettings,
                            onBack = back,
                        )
                    }

                    composable(HubRoutes.GENERAL) {
                        GeneralSection(hubState, hubViewModel, back)
                    }

                    composable(HubRoutes.CONNECTION) {

                        val settingsViewModel: SettingsViewModel = viewModel(
                            factory = SettingsViewModel.factory(
                                container.settings,
                                container.repository,
                            )
                        )

                        ConnectionSection(
                            hub = hubState,
                            viewModel = settingsViewModel,
                            onBack = {
                                // The URL or token may have changed, which
                                // makes every cached server document stale.
                                hubViewModel.refresh()
                                chatViewModel.checkConnection()
                                navController.popBackStack()
                            },
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

    /**
     * Aura's own page in the system notification settings.
     *
     * The runtime prompt above is a one-shot: after two refusals Android
     * stops showing it and `launch` silently does nothing. Without this
     * there would be no way back from a denied permission, which is how a
     * "tap to allow" row becomes a dead end.
     */
    private fun openNotificationSettings() {
        runCatching {
            startActivity(
                Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                    .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
            )
        }
    }

    companion object {
        const val ROUTE_CHAT = "chat"
        const val EXTRA_MESSAGE = "aura_message"
    }
}
