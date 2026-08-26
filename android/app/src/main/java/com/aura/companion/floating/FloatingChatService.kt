package com.aura.companion.floating

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.unit.dp
import androidx.core.app.NotificationCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.setViewTreeLifecycleOwner
import androidx.lifecycle.setViewTreeViewModelStoreOwner
import androidx.savedstate.SavedStateRegistry
import androidx.savedstate.SavedStateRegistryController
import androidx.savedstate.SavedStateRegistryOwner
import androidx.savedstate.setViewTreeSavedStateRegistryOwner
import com.aura.companion.R
import com.aura.companion.ui.theme.AuraTheme

class FloatingChatService : Service(), LifecycleOwner, ViewModelStoreOwner, SavedStateRegistryOwner {

    private lateinit var windowManager: WindowManager
    private lateinit var composeView: ComposeView
    private lateinit var closeTargetView: ComposeView

    // Required for Compose in Service
    private val lifecycleRegistry = LifecycleRegistry(this)
    private val store = ViewModelStore()
    private val savedStateRegistryController = SavedStateRegistryController.create(this)

    override val viewModelStore: ViewModelStore
        get() = store

    override val savedStateRegistry: SavedStateRegistry
        get() = savedStateRegistryController.savedStateRegistry

    override val lifecycle: Lifecycle
        get() = lifecycleRegistry

    private var screenWidth = 0
    private var screenHeight = 0

    override fun onCreate() {
        super.onCreate()
        savedStateRegistryController.performRestore(null)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_CREATE)

        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        val metrics = resources.displayMetrics
        screenWidth = metrics.widthPixels
        screenHeight = metrics.heightPixels

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            else
                WindowManager.LayoutParams.TYPE_PHONE,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        )
        params.gravity = Gravity.TOP or Gravity.START
        params.x = 0
        params.y = 100

        val closeParams = WindowManager.LayoutParams(
            WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O)
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            else
                WindowManager.LayoutParams.TYPE_PHONE,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE,
            PixelFormat.TRANSLUCENT
        )
        closeParams.gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
        closeParams.y = 100

        closeTargetView = ComposeView(this).apply {
            setViewTreeLifecycleOwner(this@FloatingChatService)
            setViewTreeViewModelStoreOwner(this@FloatingChatService)
            setViewTreeSavedStateRegistryOwner(this@FloatingChatService)
            setContent {
                AuraTheme {
                    var showClose by androidx.compose.runtime.remember { mutableStateOf(false) }
                    // We expose this state to the outer scope later or update it via a flow
                    CloseTargetUI()
                }
            }
        }
        windowManager.addView(closeTargetView, closeParams)
        closeTargetView.visibility = android.view.View.GONE

        composeView = ComposeView(this).apply {
            setViewTreeLifecycleOwner(this@FloatingChatService)
            setViewTreeViewModelStoreOwner(this@FloatingChatService)
            setViewTreeSavedStateRegistryOwner(this@FloatingChatService)
            
            setContent {
                val app = application as com.aura.companion.AuraApplication
                val container = app.container
                val chatViewModel: com.aura.companion.ui.chat.ChatViewModel = androidx.lifecycle.viewmodel.compose.viewModel(
                    factory = com.aura.companion.ui.chat.ChatViewModel.factory(
                        container.repository,
                        container.settings,
                        container.transcript
                    )
                )

                AuraTheme {
                    var isExpanded by androidx.compose.runtime.remember { mutableStateOf(false) }

                    if (isExpanded) {
                        params.width = WindowManager.LayoutParams.MATCH_PARENT
                        params.height = (screenHeight * 0.7).toInt() // 70% of screen
                        params.flags = params.flags and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE.inv()
                        windowManager.updateViewLayout(this, params)
                        closeTargetView.visibility = android.view.View.GONE
                        
                        MiniChatUI(
                            viewModel = chatViewModel,
                            onClose = { 
                                isExpanded = false 
                                params.width = WindowManager.LayoutParams.WRAP_CONTENT
                                params.height = WindowManager.LayoutParams.WRAP_CONTENT
                                params.flags = params.flags or WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                                windowManager.updateViewLayout(this, params)
                            }
                        )
                    } else {
                        FloatingBubbleUI(
                            onDragStart = {
                                closeTargetView.visibility = android.view.View.VISIBLE
                            },
                            onDrag = { dx, dy ->
                                params.x += dx.toInt()
                                params.y += dy.toInt()
                                windowManager.updateViewLayout(this, params)
                            },
                            onDragEnd = {
                                closeTargetView.visibility = android.view.View.GONE
                                // Check if close to bottom
                                if (params.y > screenHeight - 400) {
                                    stopSelf()
                                }
                            },
                            onClick = {
                                isExpanded = true
                            }
                        )
                    }
                }
            }
        }

        windowManager.addView(composeView, params)
        startForegroundService()
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_START)
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_RESUME)
    }

    private fun startForegroundService() {
        val channelId = "aura_floating"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(channelId, "Floating Chat", NotificationManager.IMPORTANCE_LOW)
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }

        val notification = NotificationCompat.Builder(this, channelId)
            .setContentTitle("Aura")
            .setContentText("Chat bubble is active")
            .setSmallIcon(R.mipmap.ic_launcher)
            .build()

        startForeground(1, notification)
    }

    override fun onDestroy() {
        super.onDestroy()
        lifecycleRegistry.handleLifecycleEvent(Lifecycle.Event.ON_DESTROY)
        if (::composeView.isInitialized) {
            windowManager.removeView(composeView)
        }
        if (::closeTargetView.isInitialized) {
            windowManager.removeView(closeTargetView)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null
}

@Composable
fun CloseTargetUI() {
    Box(
        modifier = Modifier.fillMaxWidth().padding(bottom = 50.dp),
        contentAlignment = Alignment.BottomCenter
    ) {
        Box(
            modifier = Modifier
                .size(60.dp)
                .clip(CircleShape)
                .background(Color.Red.copy(alpha = 0.8f)),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = Icons.Filled.Close,
                contentDescription = "Close",
                tint = Color.White,
                modifier = Modifier.size(32.dp)
            )
        }
    }
}

@Composable
fun MiniChatUI(viewModel: com.aura.companion.ui.chat.ChatViewModel, onClose: () -> Unit) {
    Box(
        modifier = Modifier
            .padding(8.dp)
            .clip(RoundedCornerShape(24.dp))
            .background(Color.White)
            .padding(8.dp)
            .fillMaxWidth()
    ) {
        androidx.compose.foundation.layout.Column(modifier = Modifier.fillMaxWidth()) {
            androidx.compose.foundation.layout.Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = androidx.compose.foundation.layout.Arrangement.SpaceBetween
            ) {
                androidx.compose.material3.Text("Aura Mini", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(8.dp))
                androidx.compose.material3.IconButton(onClick = onClose) {
                    Icon(androidx.compose.material.icons.Icons.Filled.Close, contentDescription = "Close")
                }
            }
            Box(modifier = Modifier.weight(1f, fill = false)) {
                com.aura.companion.ui.chat.ChatScreen(
                    viewModel = viewModel,
                    onOpenSettings = onClose // Or open MainActivity
                )
            }
        }
    }
}

@Composable
fun FloatingBubbleUI(
    onDragStart: () -> Unit = {},
    onDrag: (Float, Float) -> Unit,
    onDragEnd: () -> Unit = {},
    onClick: () -> Unit
) {
    Box(
        modifier = Modifier
            .size(60.dp)
            .clip(CircleShape)
            .background(Color.White.copy(alpha = 0.8f))
            .clickable(onClick = onClick)
            .pointerInput(Unit) {
                detectDragGestures(
                    onDragStart = { onDragStart() },
                    onDragEnd = { onDragEnd() },
                    onDragCancel = { onDragEnd() },
                    onDrag = { change, dragAmount ->
                        change.consume()
                        onDrag(dragAmount.x, dragAmount.y)
                    }
                )
            },
        contentAlignment = Alignment.Center
    ) {
        Icon(
            imageVector = Icons.Filled.ChatBubble,
            contentDescription = "Aura",
            tint = Color(0xFF6B4EE6), // Aura Violet
            modifier = Modifier.size(30.dp)
        )
    }
}
